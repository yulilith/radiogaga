from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import websockets

from radioagent.debate.orchestrator import DebateOrchestrator
from radioagent.models import (
    ClientRegistration,
    ErrorMessage,
    InjectUserMessage,
    InterruptTurnMessage,
    LogEventMessage,
    RegisteredMessage,
    SessionUpdateMessage,
    ShutdownMessage,
    StartDebateMessage,
    TurnResultMessage,
    dump_socket_message,
    parse_socket_message,
)
from radioagent.observability.logging import EventRecorder
from radioagent.prompts.loader import load_prompt_configs


class WebsocketHub:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        prompt_dir: Path,
        logger: logging.Logger,
        recorder: EventRecorder,
        expected_agent_ids: list[str],
    ) -> None:
        self.host = host
        self.port = port
        self.prompt_configs = load_prompt_configs(prompt_dir)
        self.logger = logger
        self.recorder = recorder
        self.expected_agent_ids = expected_agent_ids
        self.orchestrator = DebateOrchestrator()
        self.server: Any = None
        self.clients: dict[str, Any] = {}
        self.client_roles: dict[str, str] = {}
        self.agent_connections: dict[str, Any] = {}
        self._agents_ready = asyncio.Event()
        self._session_complete = asyncio.Event()

    async def start(self) -> None:
        self.server = await websockets.serve(self._handle_connection, self.host, self.port)
        self.recorder.record(
            "hub_started",
            host=self.host,
            port=self.port,
            expected_agents=self.expected_agent_ids,
        )

    async def stop(self) -> None:
        await self._broadcast(ShutdownMessage(reason="hub_shutdown"))
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        self.recorder.record("hub_stopped")

    async def wait_until_agents_ready(self, timeout_seconds: float = 30.0) -> None:
        await asyncio.wait_for(self._agents_ready.wait(), timeout=timeout_seconds)

    async def wait_until_session_complete(self, timeout_seconds: float | None = None) -> None:
        await asyncio.wait_for(self._session_complete.wait(), timeout=timeout_seconds)

    async def start_debate(self, topic: str, max_turns: int) -> None:
        self._session_complete.clear()
        state = self.orchestrator.start_debate(topic, self.expected_agent_ids, max_turns)
        self.recorder.record(
            "session_started",
            session_id=state.session_id,
            topic=topic,
            max_turns=max_turns,
        )
        await self._broadcast(SessionUpdateMessage(session=state))
        await self._dispatch_next_turn()

    async def _handle_connection(self, websocket: Any) -> None:
        client_id: str | None = None
        try:
            async for raw_message in websocket:
                message = parse_socket_message(raw_message)
                if isinstance(message, ClientRegistration):
                    client_id = await self._register_client(websocket, message)
                    continue
                if isinstance(message, StartDebateMessage):
                    await self.start_debate(message.topic, message.max_turns)
                    continue
                if isinstance(message, InjectUserMessage):
                    await self._handle_injection(message)
                    continue
                if isinstance(message, TurnResultMessage):
                    await self._handle_turn_result(message)
                    continue
                if isinstance(message, LogEventMessage):
                    self.recorder.record(message.event_name, **message.payload)
                    continue
        except websockets.ConnectionClosed:
            pass
        except Exception as exc:
            self.logger.exception("hub connection failure")
            await websocket.send(
                dump_socket_message(
                    ErrorMessage(message=str(exc), code="hub_connection_failure")
                )
            )
        finally:
            if client_id:
                await self._unregister_client(client_id)

    async def _register_client(self, websocket: Any, message: ClientRegistration) -> str:
        client_id = message.client_id
        self.clients[client_id] = websocket
        self.client_roles[client_id] = message.role
        if message.role == "agent" and message.agent_id:
            self.agent_connections[message.agent_id] = websocket
            self.orchestrator.sync_connected_agents(sorted(self.agent_connections))
            if set(self.expected_agent_ids).issubset(self.agent_connections):
                self._agents_ready.set()

        self.recorder.record(
            "client_registered",
            client_id=client_id,
            role=message.role,
            agent_id=message.agent_id,
        )
        await websocket.send(
            dump_socket_message(
                RegisteredMessage(
                    client_id=client_id,
                    session_id=self.orchestrator.state.session_id,
                )
            )
        )
        await self._broadcast(self.orchestrator.session_update())
        return client_id

    async def _unregister_client(self, client_id: str) -> None:
        role = self.client_roles.pop(client_id, None)
        self.clients.pop(client_id, None)
        if role == "agent":
            self.agent_connections.pop(client_id, None)
            self.orchestrator.sync_connected_agents(sorted(self.agent_connections))
        self.recorder.record("client_unregistered", client_id=client_id, role=role)
        await self._broadcast(self.orchestrator.session_update())

    async def _handle_injection(self, message: InjectUserMessage) -> None:
        interrupted_agent_id = self.orchestrator.state.waiting_for_agent_id
        state = self.orchestrator.inject_user_message(message.text, author=message.author)
        self.recorder.record(
            "user_message_injected",
            session_id=state.session_id,
            text=message.text,
            author=message.author,
        )
        await self._broadcast(SessionUpdateMessage(session=state))
        if interrupted_agent_id:
            await self._interrupt_agent(
                interrupted_agent_id,
                session_id=state.session_id or "",
                reason="user_injected",
            )
        if state.status == "running" and state.waiting_for_agent_id is None and state.next_speaker_id:
            await self._dispatch_next_turn()

    async def _handle_turn_result(self, message: TurnResultMessage) -> None:
        if not self.orchestrator.expects_turn_result(message.session_id, message.speaker_id):
            self.recorder.record(
                "turn_result_ignored",
                session_id=message.session_id,
                turn_index=message.turn_index,
                speaker_id=message.speaker_id,
                waiting_for_agent_id=self.orchestrator.state.waiting_for_agent_id,
            )
            return
        state = self.orchestrator.apply_turn_result(message)
        self.recorder.record(
            "turn_completed",
            session_id=message.session_id,
            turn_index=message.turn_index,
            speaker_id=message.speaker_id,
            speaker_name=message.speaker_name,
            text=message.text,
            audio_path=message.audio.path if message.audio else None,
        )
        await self._broadcast(SessionUpdateMessage(session=state))

        if state.status == "complete":
            self.recorder.record("session_completed", session_id=state.session_id)
            self._session_complete.set()
            await self._broadcast(ShutdownMessage(reason="session_complete"))
            return

        await self._dispatch_next_turn()

    async def _dispatch_next_turn(self) -> None:
        next_turn = self.orchestrator.build_next_turn(self.prompt_configs)
        self.recorder.record(
            "turn_requested",
            session_id=next_turn.session_id,
            turn_index=next_turn.turn_index,
            speaker_id=next_turn.speaker_id,
        )
        websocket = self.agent_connections[next_turn.speaker_id]
        await websocket.send(dump_socket_message(next_turn))
        await self._broadcast(self.orchestrator.session_update())

    async def _interrupt_agent(self, agent_id: str, *, session_id: str, reason: str) -> None:
        websocket = self.agent_connections.get(agent_id)
        if websocket is None:
            return
        await websocket.send(
            dump_socket_message(InterruptTurnMessage(session_id=session_id, reason=reason))
        )

    async def _broadcast(self, message: SessionUpdateMessage | ShutdownMessage) -> None:
        if not self.clients:
            return
        payload = dump_socket_message(message)
        await asyncio.gather(
            *(client.send(payload) for client in self.clients.values()),
            return_exceptions=True,
        )

