from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any


STANDARD_LOG_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": utc_timestamp(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in STANDARD_LOG_FIELDS or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(log_level: str, logs_dir: Path) -> logging.Logger:
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("radioagent")
    logger.setLevel(log_level)
    logger.propagate = False

    if logger.handlers:
        return logger

    console = logging.StreamHandler()
    console.setLevel(log_level)
    console.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )

    file_handler = logging.FileHandler(logs_dir / "app.jsonl")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(JsonFormatter())

    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


@dataclass(slots=True)
class EventRecorder:
    log_path: Path
    logger: logging.Logger
    _lock: Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def record(self, event_name: str, **payload: Any) -> None:
        event = {
            "timestamp": utc_timestamp(),
            "event_name": event_name,
            "payload": payload,
        }
        line = json.dumps(event, default=str)
        with self._lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")
        self.logger.info("event=%s", event_name, extra={"event": event_name, **payload})

