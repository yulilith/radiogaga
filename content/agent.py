import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncGenerator

import anthropic

from log import get_logger, log_api_call, TranscriptLogger

logger = get_logger(__name__)


@dataclass
class ContentChunk:
    """A chunk of content to be spoken or played."""
    text: str
    voice_id: str
    pause_after: float = 0.0       # Seconds of silence after this chunk
    play_music: str | None = None   # Path or URI to music to play after speech
    flush: bool = False             # Signal to discard pre-fetched audio (agent-level interrupt)
    played_event: asyncio.Event | None = None  # Set by consumer after audio is enqueued to player


@dataclass
class PreparedPreview:
    """Prepared first-snippet preview that can be committed later."""
    text: str
    voice_id: str
    metadata: dict[str, object] = field(default_factory=dict)


class BaseChannel(ABC):
    """Base class for all radio content channels."""

    channel_id = "base"

    def __init__(self, context_provider, config: dict, persona=None):
        self.context = context_provider
        self.config = config
        self.persona = persona
        self.client = anthropic.AsyncAnthropic(api_key=config["ANTHROPIC_API_KEY"])
        self.transcript_logger = TranscriptLogger()
        self.history: list[dict] = []
        self.max_history = config.get("HISTORY_WINDOW", 8)
        self._cancelled = False
        self.session_memory = None

        self._output_queue: asyncio.Queue[ContentChunk] = asyncio.Queue(maxsize=2)
        self._interrupted = asyncio.Event()
        self._pending_callin: str | None = None
        self._subchannel: str = ""
        self._on_air = False

        self._warm_queue: asyncio.Queue[ContentChunk] = asyncio.Queue(maxsize=1)
        self._warm_audio: list[bytes] = []

    def set_persona(self, persona, previous_name: str | None = None):
        """Hot-swap the active persona. Injects a handoff note if replacing someone."""
        self.persona = persona
        if previous_name:
            self.history.append({
                "role": "user",
                "content": (
                    f"[System: {previous_name} has left. You are {persona.name}, "
                    "taking over. The transcript above is from your predecessor.]"
                ),
            })
        self.interrupt()

    @abstractmethod
    def channel_name(self) -> str:
        """Human-readable channel name."""
        ...

    @abstractmethod
    def get_system_prompt(self, subchannel: str, context: dict) -> str:
        """Build the system prompt for this channel + subchannel."""
        ...

    def get_voice_id(self, subchannel: str) -> str:
        """Return the ElevenLabs voice ID for this channel, derived from persona."""
        if self.persona:
            from content.personas import resolve_voice_id
            return resolve_voice_id(self.persona.voice_key, self.config.get("VOICES"))
        return self.config.get("VOICES", {}).get("dj", "iP95p4xoKVk53GoZ742B")

    def set_session_memory(self, session_memory):
        self.session_memory = session_memory

    async def get_prompt_context(self, subchannel: str) -> dict:
        return await self.context.get_context()

    def get_session_guidance(self, subchannel: str) -> str:
        if not self.session_memory:
            return ""

        guidance = self.session_memory.build_prompt(self.channel_id, subchannel)
        return f"""
SESSION MEMORY:
{guidance}
"""

    async def stream_content(self, subchannel: str) -> AsyncGenerator[ContentChunk, None]:
        """Generate a continuous stream of content chunks."""
        logger.info("stream_content started", extra={"channel": self.channel_name(), "subchannel": subchannel})
        while not self._cancelled:
            ctx = await self.get_prompt_context(subchannel)
            system_prompt = self.get_system_prompt(subchannel, ctx)
            voice_id = self.get_voice_id(subchannel)

            messages = [
                *self.history,
                {"role": "user", "content": "Generate the next segment."},
            ]

            model = self.config.get("LLM_MODEL", "claude-haiku-4-5-20251001")
            max_tokens = self.config.get("LLM_MAX_TOKENS", 300)
            logger.debug(
                "starting LLM stream",
                extra={"model": model, "max_tokens": max_tokens, "message_count": len(messages)},
            )

            full_response = ""
            buffer = ""

            t0 = time.monotonic()
            async with self.client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                temperature=self.config.get("LLM_TEMPERATURE", 0.85),
                system=system_prompt,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    if self._cancelled:
                        logger.debug("stream cancelled mid-generation")
                        return
                    buffer += text
                    full_response += text

                    # Yield sentence-sized chunks for TTS
                    while True:
                        # Find sentence boundary
                        end = -1
                        for delim in [". ", "! ", "? ", ".\n", "!\n", "?\n"]:
                            idx = buffer.find(delim)
                            if idx != -1 and (end == -1 or idx < end):
                                end = idx + len(delim)

                        if end == -1:
                            break

                        sentence = buffer[:end].strip()
                        buffer = buffer[end:]
                        if sentence:
                            logger.debug("yielding content chunk", extra={"text_len": len(sentence), "voice_id": voice_id})
                            yield ContentChunk(text=sentence, voice_id=voice_id)

            duration_ms = (time.monotonic() - t0) * 1000
            log_api_call(logger, "anthropic", "messages.stream", status="ok", duration_ms=duration_ms,
                         model=model, response_len=len(full_response))

            if full_response:
                self.transcript_logger.log_llm_response(
                    channel=self.channel_name(),
                    subchannel=subchannel,
                    text=full_response,
                    model=model,
                    duration_ms=duration_ms,
                )

            # Yield any remaining text
            remaining = buffer.strip()
            if remaining and not self._cancelled:
                logger.debug("yielding remaining chunk", extra={"text_len": len(remaining), "voice_id": voice_id})
                yield ContentChunk(text=remaining, voice_id=voice_id, pause_after=1.0)

            # Update conversation history
            if full_response:
                self._remember_message("assistant", full_response)
                logger.info("history updated", extra={"history_size": len(self.history)})

            # Brief pause between segments
            if not self._cancelled:
                await asyncio.sleep(0.5)

    async def run_background(self):
        """Run content generation forever in background. One task per channel."""
        logger.info("channel.background_started", extra={"channel": self.channel_name()})
        while True:
            self._interrupted.clear()
            self._cancelled = False
            try:
                if self._pending_callin:
                    transcript = self._pending_callin
                    self._pending_callin = None
                    logger.info("channel.callin_injected", extra={
                        "channel": self.channel_name(),
                        "transcript_preview": transcript[:60],
                    })
                    async for chunk in self.handle_callin(transcript):
                        if self._interrupted.is_set():
                            break
                        if self._on_air:
                            await self._output_queue.put(chunk)

                gen_task = asyncio.create_task(self._run_generation())
                interrupt_task = asyncio.create_task(self._interrupted.wait())

                done, pending = await asyncio.wait(
                    [gen_task, interrupt_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

                if gen_task in done and not self._interrupted.is_set():
                    await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                logger.info("channel.background_stopped", extra={"channel": self.channel_name()})
                break
            except Exception as e:
                logger.error("channel.background_error: %s", e, exc_info=True,
                             extra={"channel": self.channel_name()})
                await asyncio.sleep(1)

    async def _run_generation(self):
        """Single generation pass -- iterate stream_content and route chunks."""
        async for chunk in self.stream_content(self._subchannel):
            if self._on_air:
                await self._output_queue.put(chunk)
            elif chunk.text:
                while not self._warm_queue.empty():
                    try:
                        self._warm_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                await self._warm_queue.put(chunk)

    def interrupt(self, callin: str | None = None):
        """Interrupt current generation immediately."""
        logger.info("channel.background_interrupted", extra={
            "channel": self.channel_name(),
            "has_callin": callin is not None,
        })
        if callin:
            self._pending_callin = callin
        self._cancelled = True
        self._interrupted.set()

    def set_on_air(self, on_air: bool):
        logger.info("channel.on_air_changed", extra={
            "channel": self.channel_name(), "on_air": on_air,
        })
        self._on_air = on_air

    def set_subchannel(self, subchannel: str):
        self._subchannel = subchannel

    async def on_activate(self):
        """Called when the listener switches TO this channel. Override for custom behavior."""
        pass

    async def on_deactivate(self):
        """Called when the listener switches AWAY from this channel. Override for custom behavior."""
        pass

    async def generate_warm_preview(self) -> list[ContentChunk]:
        """Generate a preview chunk for the warm cache. Override in on-demand channels."""
        return []

    async def handle_callin(self, transcript: str) -> AsyncGenerator[ContentChunk, None]:
        """Handle a call-in from a listener. Override in channels that support it."""
        logger.info("callin received (default handler)", extra={"channel": self.channel_name(), "transcript_len": len(transcript)})
        yield ContentChunk(
            text="Sorry, this channel doesn't take callers right now.",
            voice_id=self.get_voice_id(""),
        )

    def cancel(self):
        """Cancel ongoing generation. Prefer interrupt() for the new architecture."""
        self.interrupt()

    def reset(self):
        """Reset cancellation flag. History is preserved across switches."""
        logger.info("channel reset", extra={"channel": self.channel_name()})
        self._cancelled = False

    def clear_history(self):
        """Clear conversation history."""
        self.history.clear()

    async def build_preview(self, subchannel: str) -> PreparedPreview | None:
        context = await self.get_prompt_context(subchannel)
        preview_text = await self._complete_text(
            system_prompt=self.get_system_prompt(subchannel, context),
            prompt=(
                "Generate the first one or two spoken sentences the listener should hear "
                "immediately after tuning in. Keep it under 45 words and output plain radio copy only."
            ),
            max_tokens=120,
            context_label=f"{self.channel_id}_preview",
            messages=[
                *self.history[-4:],
                {
                    "role": "user",
                    "content": (
                        "Generate the first one or two spoken sentences the listener should hear "
                        "immediately after tuning in."
                    ),
                },
            ],
        )
        return PreparedPreview(
            text=preview_text,
            voice_id=self.get_voice_id(subchannel),
        )

    def commit_preview_playback(self, subchannel: str, preview: PreparedPreview):
        self._remember_message("assistant", preview.text)

    def _remember_message(self, role: str, content: str):
        self.history.append({"role": role, "content": content})
        self._trim_history()

    def _trim_history(self):
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

    async def _complete_text(
        self,
        *,
        system_prompt: str,
        prompt: str,
        max_tokens: int,
        context_label: str,
        temperature: float | None = None,
        messages: list[dict] | None = None,
    ) -> str:
        model = self.config.get("LLM_MODEL", "claude-haiku-4-5-20251001")
        if temperature is None:
            temperature = self.config.get("LLM_TEMPERATURE", 0.85)

        t0 = time.monotonic()
        response = await self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=messages or [{"role": "user", "content": prompt}],
        )
        text = self._extract_text(response)
        duration_ms = (time.monotonic() - t0) * 1000
        log_api_call(
            logger,
            "anthropic",
            "messages.create",
            status="ok",
            duration_ms=duration_ms,
            model=model,
            context=context_label,
            response_len=len(text),
        )
        return text

    @staticmethod
    def _extract_text(response) -> str:
        text_parts = []
        for block in getattr(response, "content", []):
            block_text = getattr(block, "text", None)
            if isinstance(block_text, str):
                text_parts.append(block_text)
        full_text = "".join(text_parts).strip()
        if not full_text:
            raise RuntimeError("Channel generation returned an empty response")
        return full_text


# Shared base system prompt
BASE_SYSTEM_PROMPT = """You are a host on RadioAgent, a pirate AI radio station broadcasting out of Cambridge, Massachusetts.

YOUR AUDIENCE: Smart, curious people in the MIT and Harvard orbit — researchers, students, professors, founders, and the generally overeducated. They care about AI, agents, philosophy, politics, science, and genuinely absurd things. They can smell generic content instantly. They want to be surprised, challenged, and made to laugh. Treat them like the smartest people in the room because they probably are.

CURRENT CONTEXT:
- Date/Time: {current_datetime}
- Day of week: {day_of_week}
- Location: {city}, {state}
- Weather: {weather}
- Trending topics: {trending_topics}

BROADCASTING RULES:
1. Never break character. You ARE a radio host, not an AI assistant.
2. Be OPINIONATED. Take stances. Be wrong sometimes. Be provocative. Lukewarm takes are the enemy.
3. Speak naturally. Use contractions, filler words, mid-sentence corrections. Sound like a real person (or a real AI, if that's your character) thinking out loud.
4. Reference Cambridge, Boston, MIT, Harvard naturally — the river, the T, the eternal construction on Mass Ave, the weather.
5. Mix HIGH and LOW freely: Wittgenstein and TikTok. Arxiv papers and celebrity drama. Kant and Kendrick.
6. Keep segments to 30-60 seconds of speech (~75-150 words). Dense, not padded.
7. End each segment with something that makes the listener want to stay: a cliffhanger, a provocation, a question that sticks.
8. Do NOT use markdown, asterisks, or any text formatting. Output plain spoken text only.
9. Do NOT use stage directions like [pause] or [laughs]. Just write the words to speak.
10. NEVER repeat content from previous segments. If you've said it, it's dead. Move forward.
11. Dare to be weird. The best radio moments are the ones nobody expected.
"""
