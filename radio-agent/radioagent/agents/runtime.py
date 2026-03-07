from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import websockets

from radioagent.audio.player import AudioPlayer
from radioagent.config import load_settings
from radioagent.models import (
    ClientRegistration,
    GenerateTurnMessage,
    InterruptTurnMessage,
    LogEventMessage,
    RegisteredMessage,
    SessionUpdateMessage,
    ShutdownMessage,
    TurnResultMessage,
    dump_socket_message,
    parse_socket_message,
)
from radioagent.observability.logging import configure_logging
from radioagent.prompts.loader import load_prompt_config
from radioagent.voice.base import SynthesisRequest, TTSProvider
from radioagent.voice.elevenlabs_provider import ElevenLabsTTSProvider
from radioagent.voice.mock_provider import MockTTSProvider


@dataclass(slots=True)
class RuntimeDependencies:
    turn_generator: "TurnGenerator"
    tts_provider: TTSProvider
    audio_player: AudioPlayer


class TurnGenerator:
    async def generate_turn(self, message: GenerateTurnMessage) -> str:
        raise NotImplementedError


class MockTurnGenerator(TurnGenerator):
    async def generate_turn(self, message: GenerateTurnMessage) -> str:
        latest = message.history[-1].text if message.history else message.topic
        return (
            f"{message.prompt.display_name} says the latest point was: "
            f"{latest[:100]}. My angle is that local-first systems are easier to trust "
            f"when their behavior stays inspectable."
        )


def is_sdk_error_response(text: str) -> bool:
    normalized = text.strip()
    lowered = normalized.lower()
    return normalized.startswith("API Error (") or (
        "provided model identifier is invalid" in lowered
        and "try --model" in lowered
    )


class AnthropicApiTurnGenerator(TurnGenerator):
    def __init__(
        self,
        *,
        api_key: str,
        default_model: str,
        logger: logging.Logger,
    ) -> None:
        self.api_key = api_key
        self.default_model = default_model
        self.logger = logger

    async def generate_turn(self, message: GenerateTurnMessage) -> str:
        from anthropic import AsyncAnthropic

        transcript = "\n".join(
            f"{entry.speaker_name}: {entry.text}" for entry in message.history[-10:]
        )
        prompt = (
            f"You are in a live debate about: {message.topic}\n\n"
            f"Recent transcript:\n{transcript}\n\n"
            f"It is now {message.prompt.display_name}'s turn. Respond naturally in 1-3 "
            f"short sentences. Make one sharp point, sound opinionated, and hand the "
            f"conversation back quickly. Directly address the latest point. Do not use "
            f"bullet points."
        )
        selected_model = self.default_model

        client = AsyncAnthropic(api_key=self.api_key)
        try:
            response = await client.messages.create(
                model=selected_model,
                max_tokens=220,
                system=message.prompt.system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception:
            raise

        text_parts: list[str] = []
        for block in response.content:
            block_text = getattr(block, "text", None)
            if isinstance(block_text, str):
                text_parts.append(block_text)
        result_text = "".join(text_parts).strip()
        if not result_text:
            raise RuntimeError("Anthropic API returned an empty response")
        return result_text


class ClaudeSdkTurnGenerator(TurnGenerator):
    def __init__(
        self,
        *,
        workspace_dir: Path,
        logger: logging.Logger,
        default_model: str,
    ) -> None:
        self.workspace_dir = workspace_dir
        self.logger = logger
        self.default_model = default_model

    async def generate_turn(self, message: GenerateTurnMessage) -> str:
        from claude_agent_sdk import ClaudeAgentOptions, query
        from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

        transcript = "\n".join(
            f"{entry.speaker_name}: {entry.text}" for entry in message.history[-10:]
        )
        prompt = (
            f"You are in a live debate about: {message.topic}\n\n"
            f"Recent transcript:\n{transcript}\n\n"
            f"It is now {message.prompt.display_name}'s turn. Respond naturally in 1-3 "
            f"short sentences. Make one sharp point, sound opinionated, and hand the "
            f"conversation back quickly. Directly address the latest point. Do not use "
            f"bullet points."
        )
        selected_model = self.default_model

        def handle_sdk_stderr(line: str) -> None:
            _ = line

        options = ClaudeAgentOptions(
            allowed_tools=[],
            tools=[],
            system_prompt=message.prompt.system_prompt,
            permission_mode="bypassPermissions",
            cwd=self.workspace_dir,
            max_turns=1,
            model=selected_model,
            stderr=handle_sdk_stderr,
        )

        text_blocks: list[str] = []
        result_text: str | None = None
        try:
            async for sdk_message in query(prompt=prompt, options=options):
                sdk_data = getattr(sdk_message, "data", None) or {}
                if isinstance(sdk_message, AssistantMessage):
                    for block in sdk_message.content:
                        if isinstance(block, TextBlock):
                            text_blocks.append(block.text)
                if isinstance(sdk_message, ResultMessage) and sdk_message.result:
                    result_text = sdk_message.result
        except Exception as exc:
            if not (result_text or "".join(text_blocks).strip()):
                raise

        response = result_text or "".join(text_blocks).strip()
        if not response:
            raise RuntimeError("Claude Agent SDK returned an empty response")
        if is_sdk_error_response(response):
            raise RuntimeError(
                "Claude Agent SDK returned an API error response instead of a debate turn"
            )
        return response


class AgentRuntime:
    def __init__(
        self,
        *,
        agent_id: str,
        prompt_path: Path,
        websocket_uri: str,
        dependencies: RuntimeDependencies,
        logger: logging.Logger,
        audio_dir: Path,
    ) -> None:
        self.agent_id = agent_id
        self.prompt = load_prompt_config(prompt_path)
        self.websocket_uri = websocket_uri
        self.dependencies = dependencies
        self.logger = logger
        self.audio_dir = audio_dir
        self._active_turn_task: asyncio.Task[None] | None = None
        self._send_lock = asyncio.Lock()

    async def run(self) -> None:
        async with websockets.connect(self.websocket_uri) as websocket:
            registration = ClientRegistration(
                client_id=self.agent_id,
                role="agent",
                agent_id=self.agent_id,
                display_name=self.prompt.display_name,
            )
            await self._send_message(websocket, registration)
            self.logger.info(
                "agent connected",
                extra={"agent_id": self.agent_id, "uri": self.websocket_uri},
            )
            try:
                async for raw_message in websocket:
                    message = parse_socket_message(raw_message)
                    if isinstance(message, RegisteredMessage):
                        self.logger.info(
                            "agent registered",
                            extra={"agent_id": self.agent_id, "session_id": message.session_id},
                        )
                        continue
                    if isinstance(message, SessionUpdateMessage):
                        continue
                    if isinstance(message, InterruptTurnMessage):
                        await self._interrupt_active_turn(
                            websocket,
                            reason=message.reason,
                            session_id=message.session_id,
                        )
                        continue
                    if isinstance(message, GenerateTurnMessage):
                        await self._start_turn(websocket, message)
                        continue
                    if isinstance(message, ShutdownMessage):
                        await self._interrupt_active_turn(
                            websocket,
                            reason=message.reason,
                            session_id=None,
                        )
                        self.logger.info(
                            "agent shutting down",
                            extra={"agent_id": self.agent_id, "reason": message.reason},
                        )
                        return
            finally:
                await self._interrupt_active_turn(
                    websocket,
                    reason="agent_runtime_closed",
                    session_id=None,
                )

    async def _start_turn(
        self,
        websocket: websockets.WebSocketClientProtocol,
        message: GenerateTurnMessage,
    ) -> None:
        await self._interrupt_active_turn(
            websocket,
            reason="superseded_turn",
            session_id=message.session_id,
        )
        self._active_turn_task = asyncio.create_task(
            self._run_turn_task(websocket, message),
            name=f"{self.agent_id}-turn-{message.turn_index}",
        )

    async def _interrupt_active_turn(
        self,
        websocket: websockets.WebSocketClientProtocol,
        *,
        reason: str,
        session_id: str | None,
    ) -> None:
        active_task = self._active_turn_task
        if active_task is None:
            await self.dependencies.audio_player.stop(reason)
            return

        active_task.cancel()
        await self.dependencies.audio_player.stop(reason)
        try:
            await active_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.logger.exception(
                "interrupted turn finished with error",
                extra={"agent_id": self.agent_id, "reason": reason},
            )
            if session_id:
                await self._emit_log(
                    websocket,
                    "turn_failed",
                    session_id=session_id,
                    speaker_id=self.agent_id,
                    error=str(exc),
                )
        finally:
            if self._active_turn_task is active_task:
                self._active_turn_task = None

    async def _run_turn_task(
        self,
        websocket: websockets.WebSocketClientProtocol,
        message: GenerateTurnMessage,
    ) -> None:
        try:
            await self._handle_generate_turn(websocket, message)
        except asyncio.CancelledError:
            await self._emit_log(
                websocket,
                "turn_interrupted",
                session_id=message.session_id,
                turn_index=message.turn_index,
                speaker_id=self.agent_id,
            )
            raise
        except Exception as exc:
            self.logger.exception(
                "turn handling failed",
                extra={
                    "agent_id": self.agent_id,
                    "session_id": message.session_id,
                    "turn_index": message.turn_index,
                },
            )
            await self._emit_log(
                websocket,
                "turn_failed",
                session_id=message.session_id,
                turn_index=message.turn_index,
                speaker_id=self.agent_id,
                error=str(exc),
            )
        finally:
            current_task = asyncio.current_task()
            if current_task is not None and self._active_turn_task is current_task:
                self._active_turn_task = None

    async def _handle_generate_turn(
        self,
        websocket: websockets.WebSocketClientProtocol,
        message: GenerateTurnMessage,
    ) -> None:
        await self._emit_log(
            websocket,
            "turn_generation_started",
            session_id=message.session_id,
            turn_index=message.turn_index,
            speaker_id=self.agent_id,
        )
        text = await self.dependencies.turn_generator.generate_turn(message)
        await self._emit_log(
            websocket,
            "turn_generation_completed",
            session_id=message.session_id,
            turn_index=message.turn_index,
            speaker_id=self.agent_id,
            text_preview=text[:120],
        )

        await self._emit_log(
            websocket,
            "tts_started",
            session_id=message.session_id,
            turn_index=message.turn_index,
            speaker_id=self.agent_id,
        )
        audio = await self.dependencies.tts_provider.synthesize(
            SynthesisRequest(
                session_id=message.session_id,
                speaker_id=self.agent_id,
                voice_id=self.prompt.voice_id,
                text=text,
                turn_index=message.turn_index,
                output_dir=self.audio_dir,
            )
        )
        await self._emit_log(
            websocket,
            "tts_completed",
            session_id=message.session_id,
            turn_index=message.turn_index,
            speaker_id=self.agent_id,
            audio_path=audio.path,
            tts_provider=audio.provider,
        )

        await self._emit_log(
            websocket,
            "playback_started",
            session_id=message.session_id,
            turn_index=message.turn_index,
            speaker_id=self.agent_id,
            audio_path=audio.path,
        )
        await self.dependencies.audio_player.play(audio)
        await self._emit_log(
            websocket,
            "playback_completed",
            session_id=message.session_id,
            turn_index=message.turn_index,
            speaker_id=self.agent_id,
            audio_path=audio.path,
        )

        result = TurnResultMessage(
            session_id=message.session_id,
            turn_index=message.turn_index,
            speaker_id=self.agent_id,
            speaker_name=self.prompt.display_name,
            text=text,
            audio=audio,
        )
        await self._send_message(websocket, result)

    async def _emit_log(self, websocket: websockets.WebSocketClientProtocol, event_name: str, **payload: object) -> None:
        await self._send_message(
            websocket,
            LogEventMessage(event_name=event_name, payload=payload),
        )

    async def _send_message(
        self,
        websocket: websockets.WebSocketClientProtocol,
        message: ClientRegistration | TurnResultMessage | LogEventMessage,
    ) -> None:
        async with self._send_lock:
            await websocket.send(dump_socket_message(message))


def build_turn_generator(
    backend: str,
    logger: logging.Logger,
    workspace_dir: Path,
    default_model: str,
    anthropic_api_key: str | None,
) -> TurnGenerator:
    if backend == "mock":
        return MockTurnGenerator()
    if backend == "anthropic_api":
        if not anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when RADIO_AGENT_BACKEND=anthropic_api")
        return AnthropicApiTurnGenerator(
            api_key=anthropic_api_key,
            default_model=default_model,
            logger=logger,
        )
    return ClaudeSdkTurnGenerator(
        workspace_dir=workspace_dir,
        logger=logger,
        default_model=default_model,
    )


def build_tts_provider(
    provider_name: str,
    *,
    api_key: str | None,
    model_id: str,
    output_format: str,
    speed: float,
) -> TTSProvider:
    if provider_name == "elevenlabs":
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY is required when RADIO_TTS_PROVIDER=elevenlabs")
        return ElevenLabsTTSProvider(
            api_key,
            model_id=model_id,
            output_format=output_format,
            speed=speed,
        )
    if provider_name == "mock":
        return MockTTSProvider()
    raise ValueError(f"Unsupported TTS provider: {provider_name}")


async def run_agent(agent_id: str, prompt_path: Path, websocket_uri: str, mute: bool) -> None:
    settings = load_settings()
    logger = configure_logging(settings.log_level, settings.logs_dir).getChild(agent_id)
    prompt_config = load_prompt_config(prompt_path)
    dependencies = RuntimeDependencies(
        turn_generator=build_turn_generator(
            settings.agent_backend,
            logger,
            settings.workspace_dir,
            settings.anthropic_model,
            settings.anthropic_api_key,
        ),
        tts_provider=build_tts_provider(
            settings.tts_provider,
            api_key=settings.elevenlabs_api_key,
            model_id=settings.elevenlabs_model,
            output_format=settings.elevenlabs_output_format,
            speed=settings.elevenlabs_speed,
        ),
        audio_player=AudioPlayer(enabled=settings.audio_enabled and not mute, logger=logger),
    )
    runtime = AgentRuntime(
        agent_id=agent_id,
        prompt_path=prompt_path,
        websocket_uri=websocket_uri,
        dependencies=dependencies,
        logger=logger,
        audio_dir=settings.audio_dir / agent_id,
    )
    await runtime.run()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one RadioAgent debate participant")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--prompt-path", required=True)
    parser.add_argument("--uri", required=True)
    parser.add_argument("--mute", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(
        run_agent(
            agent_id=args.agent_id,
            prompt_path=Path(args.prompt_path),
            websocket_uri=args.uri,
            mute=args.mute,
        )
    )


if __name__ == "__main__":
    main()

