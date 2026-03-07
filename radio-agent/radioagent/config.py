from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def str_to_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def package_root() -> Path:
    return Path(__file__).resolve().parent


def workspace_root() -> Path:
    return package_root().parent


@dataclass(slots=True)
class Settings:
    host: str
    port: int
    topic: str
    max_turns: int
    anthropic_model: str
    tts_provider: str
    elevenlabs_model: str
    elevenlabs_output_format: str
    elevenlabs_speed: float
    agent_backend: str
    audio_enabled: bool
    log_level: str
    workspace_dir: Path
    prompt_dir: Path
    logs_dir: Path
    audio_dir: Path
    anthropic_api_key: str | None
    elevenlabs_api_key: str | None

    @property
    def websocket_uri(self) -> str:
        return f"ws://{self.host}:{self.port}"


def load_settings() -> Settings:
    root = workspace_root()
    load_dotenv(root / ".env", override=False)

    settings = Settings(
        host=os.getenv("RADIO_WS_HOST", "127.0.0.1"),
        port=int(os.getenv("RADIO_WS_PORT", "8765")),
        topic=os.getenv(
            "RADIO_DEBATE_TOPIC",
            "Should local-first AI devices prioritize privacy over personalization?",
        ),
        max_turns=int(os.getenv("RADIO_MAX_TURNS", "8")),
        anthropic_model=os.getenv("RADIO_ANTHROPIC_MODEL", "claude-haiku-4-5"),
        tts_provider=os.getenv("RADIO_TTS_PROVIDER", "elevenlabs"),
        elevenlabs_model=os.getenv("RADIO_ELEVENLABS_MODEL", "eleven_turbo_v2_5"),
        elevenlabs_output_format=os.getenv(
            "RADIO_ELEVENLABS_OUTPUT_FORMAT",
            "mp3_44100_128",
        ),
        elevenlabs_speed=float(os.getenv("RADIO_ELEVENLABS_SPEED", "1.1")),
        agent_backend=os.getenv("RADIO_AGENT_BACKEND", "anthropic_api"),
        audio_enabled=str_to_bool(os.getenv("RADIO_AUDIO_ENABLED"), default=True),
        log_level=os.getenv("RADIO_LOG_LEVEL", "INFO").upper(),
        workspace_dir=root,
        prompt_dir=root / "radioagent" / "prompts",
        logs_dir=root / ".radioagent" / "logs",
        audio_dir=root / ".radioagent" / "audio",
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY"),
    )
    return settings

