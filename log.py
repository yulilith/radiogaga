"""Centralized logging for RadioAgent.

Usage in any module:
    from log import get_logger
    logger = get_logger(__name__)
    logger.info("Something happened", extra={"key": "value"})

Log levels:
    DEBUG   - Detailed diagnostic info (API payloads, buffer states, GPIO events)
    INFO    - Normal operations (channel switch, segment generated, peer found)
    WARNING - Recoverable issues (API fallback, buffer underrun, cache miss)
    ERROR   - Failures that affect functionality (API error, playback failure)

Environment variables:
    LOG_LEVEL   - Set log level (default: INFO)
    LOG_FORMAT  - "json" for structured JSON output, "text" for human-readable (default: text)
"""

import json
import logging
import os
import sys
import time
from functools import wraps
from typing import Any


class RadioFormatter(logging.Formatter):
    """Human-readable formatter with module tag, timestamp, and optional extras."""

    COLORS = {
        "DEBUG": "\033[36m",     # cyan
        "INFO": "\033[32m",      # green
        "WARNING": "\033[33m",   # yellow
        "ERROR": "\033[31m",     # red
        "CRITICAL": "\033[35m",  # magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        reset = self.RESET

        # Extract the short module name (e.g., "context.weather" -> "weather")
        module = record.name.replace("context.", "").replace("content.", "") \
                            .replace("audio.", "").replace("network.", "") \
                            .replace("hardware.", "")

        # Build base message
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        ms = f"{record.created % 1:.3f}"[1:]  # .XXX
        base = f"{color}{ts}{ms} [{record.levelname[0]}] {module:>18}{reset}  {record.getMessage()}"

        # Append any extra fields
        extras = {k: v for k, v in record.__dict__.items()
                  if k not in logging.LogRecord("", 0, "", 0, "", (), None).__dict__
                  and k not in ("message", "taskName")}
        if extras:
            extra_str = " | ".join(f"{k}={v}" for k, v in extras.items())
            base += f"  {color}({extra_str}){reset}"

        return base


class JSONFormatter(logging.Formatter):
    """Structured JSON formatter for machine parsing (CI, log aggregation)."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": record.created,
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
        }
        # Include any extras
        extras = {k: v for k, v in record.__dict__.items()
                  if k not in logging.LogRecord("", 0, "", 0, "", (), None).__dict__
                  and k not in ("message", "taskName")}
        if extras:
            entry["data"] = extras

        if record.exc_info and record.exc_info[1]:
            entry["error"] = str(record.exc_info[1])

        return json.dumps(entry, default=str)


def setup_logging(level: str | None = None, fmt: str | None = None):
    """Configure root logger. Called once at startup."""
    level = level or os.getenv("LOG_LEVEL", "INFO")
    fmt = fmt or os.getenv("LOG_FORMAT", "text")

    root = logging.getLogger("radioagent")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    if fmt == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(RadioFormatter())

    root.addHandler(handler)

    # Quiet down noisy third-party loggers
    for lib in ["urllib3", "httpx", "httpcore", "zeroconf", "spotipy", "praw"]:
        logging.getLogger(lib).setLevel(logging.WARNING)

    return root


def get_logger(name: str) -> logging.Logger:
    """Get a logger under the radioagent namespace.

    Usage:
        logger = get_logger(__name__)         # e.g., "context.weather"
        logger = get_logger("tts")            # custom name
    """
    return logging.getLogger(f"radioagent.{name}")


# --- Observability helpers ---

def log_timing(logger: logging.Logger | None = None, label: str = ""):
    """Decorator that logs execution time of async functions.

    Usage:
        @log_timing(logger, "fetch_weather")
        async def fetch_weather():
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            _logger = logger or get_logger(func.__module__ or "unknown")
            start = time.monotonic()
            try:
                result = await func(*args, **kwargs)
                elapsed = (time.monotonic() - start) * 1000
                _logger.debug(f"{label or func.__name__} completed",
                              extra={"duration_ms": f"{elapsed:.0f}"})
                return result
            except Exception as e:
                elapsed = (time.monotonic() - start) * 1000
                _logger.error(f"{label or func.__name__} failed: {e}",
                              extra={"duration_ms": f"{elapsed:.0f}"})
                raise
        return wrapper
    return decorator


class TranscriptLogger:
    """Writes JSONL transcripts to logs/ for observability and dry-run evaluation."""

    def __init__(self, log_dir: str = "logs"):
        self._log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self._logger = get_logger("transcript")

    def _get_file(self):
        date_str = time.strftime("%Y%m%d")
        return os.path.join(self._log_dir, f"transcript_{date_str}.jsonl")

    def _write(self, entry: dict):
        entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            with open(self._get_file(), "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            self._logger.warning("transcript write failed: %s", e)

    def log_llm_response(self, channel: str, subchannel: str, text: str,
                         model: str = "", duration_ms: float = 0):
        self._write({
            "type": "llm_response",
            "channel": channel,
            "subchannel": subchannel,
            "model": model,
            "duration_ms": round(duration_ms),
            "text": text,
        })

    def log_chunk(self, channel: str, subchannel: str, voice_id: str,
                  source: str, text: str):
        self._write({
            "type": "chunk",
            "channel": channel,
            "subchannel": subchannel,
            "voice_id": voice_id,
            "source": source,
            "text": text,
        })


def log_api_call(logger: logging.Logger, service: str, endpoint: str,
                 status: str = "ok", duration_ms: float = 0, **kwargs):
    """Structured log for an external API call.

    Usage:
        log_api_call(logger, "elevenlabs", "/text-to-speech", status="ok",
                     duration_ms=342, chars=150, voice="Adam")
    """
    logger.info(f"API {service} {endpoint} -> {status}",
                extra={"service": service, "duration_ms": f"{duration_ms:.0f}", **kwargs})
