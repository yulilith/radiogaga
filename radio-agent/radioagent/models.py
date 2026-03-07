from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Literal, TypeAlias
from uuid import uuid4

from pydantic import BaseModel, Field, TypeAdapter


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class AudioArtifact(BaseModel):
    provider: str
    path: str
    mime_type: str = "audio/mpeg"
    created_at: datetime = Field(default_factory=utc_now)


class PromptConfig(BaseModel):
    agent_id: str
    display_name: str
    voice_id: str
    system_prompt: str
    model: str | None = None


class HistoryEntry(BaseModel):
    entry_id: str = Field(default_factory=lambda: new_id("history"))
    source: Literal["agent", "user", "system"]
    speaker_id: str
    speaker_name: str
    text: str
    created_at: datetime = Field(default_factory=utc_now)


class ClientRegistration(BaseModel):
    type: Literal["register_client"] = "register_client"
    client_id: str
    role: Literal["agent", "injector"]
    agent_id: str | None = None
    display_name: str | None = None


class RegisteredMessage(BaseModel):
    type: Literal["registered"] = "registered"
    client_id: str
    session_id: str | None = None


class StartDebateMessage(BaseModel):
    type: Literal["start_debate"] = "start_debate"
    topic: str
    max_turns: int = 8


class GenerateTurnMessage(BaseModel):
    type: Literal["generate_turn"] = "generate_turn"
    session_id: str
    topic: str
    turn_index: int
    speaker_id: str
    prompt: PromptConfig
    history: list[HistoryEntry]


class TurnResultMessage(BaseModel):
    type: Literal["turn_result"] = "turn_result"
    session_id: str
    turn_index: int
    speaker_id: str
    speaker_name: str
    text: str
    audio: AudioArtifact | None = None


class InjectUserMessage(BaseModel):
    type: Literal["inject_user_message"] = "inject_user_message"
    text: str
    author: str = "user"


class InterruptTurnMessage(BaseModel):
    type: Literal["interrupt_turn"] = "interrupt_turn"
    session_id: str
    reason: str = "user_injected"


class SessionState(BaseModel):
    session_id: str | None = None
    topic: str | None = None
    status: Literal["idle", "running", "complete"] = "idle"
    max_turns: int = 0
    turn_index: int = 0
    next_speaker_id: str | None = None
    waiting_for_agent_id: str | None = None
    connected_agents: list[str] = Field(default_factory=list)
    history: list[HistoryEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SessionUpdateMessage(BaseModel):
    type: Literal["session_update"] = "session_update"
    session: SessionState


class LogEventMessage(BaseModel):
    type: Literal["log_event"] = "log_event"
    event_name: str
    payload: dict[str, object] = Field(default_factory=dict)


class ShutdownMessage(BaseModel):
    type: Literal["shutdown"] = "shutdown"
    reason: str = "session_complete"


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    message: str
    code: str = "runtime_error"


SocketMessage: TypeAlias = Annotated[
    ClientRegistration
    | RegisteredMessage
    | StartDebateMessage
    | GenerateTurnMessage
    | TurnResultMessage
    | InjectUserMessage
    | InterruptTurnMessage
    | SessionUpdateMessage
    | LogEventMessage
    | ShutdownMessage
    | ErrorMessage,
    Field(discriminator="type"),
]

SOCKET_MESSAGE_ADAPTER = TypeAdapter(SocketMessage)


def parse_socket_message(payload: str | bytes) -> SocketMessage:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    return SOCKET_MESSAGE_ADAPTER.validate_json(payload)


def dump_socket_message(message: SocketMessage | BaseModel) -> str:
    if isinstance(message, BaseModel):
        return message.model_dump_json()
    return json.dumps(message)

