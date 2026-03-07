import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncGenerator

import anthropic

from log import get_logger, log_api_call

logger = get_logger(__name__)


@dataclass
class ContentChunk:
    """A chunk of content to be spoken or played."""
    text: str
    voice_id: str
    pause_after: float = 0.0       # Seconds of silence after this chunk
    play_music: str | None = None   # Path or URI to music to play after speech


class BaseChannel(ABC):
    """Base class for all radio content channels."""

    def __init__(self, context_provider, config: dict):
        self.context = context_provider
        self.config = config
        self.client = anthropic.AsyncAnthropic(api_key=config["ANTHROPIC_API_KEY"])
        self.history: list[dict] = []
        self.max_history = config.get("HISTORY_WINDOW", 8)
        self._cancelled = False

    @abstractmethod
    def channel_name(self) -> str:
        """Human-readable channel name."""
        ...

    @abstractmethod
    def get_system_prompt(self, subchannel: str, context: dict) -> str:
        """Build the system prompt for this channel + subchannel."""
        ...

    @abstractmethod
    def get_voice_id(self, subchannel: str) -> str:
        """Return the ElevenLabs voice ID for this channel."""
        ...

    async def stream_content(self, subchannel: str) -> AsyncGenerator[ContentChunk, None]:
        """Generate a continuous stream of content chunks."""
        logger.info("stream_content started", extra={"channel": self.channel_name(), "subchannel": subchannel})
        while not self._cancelled:
            ctx = await self.context.get_context()
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

            # Yield any remaining text
            remaining = buffer.strip()
            if remaining and not self._cancelled:
                logger.debug("yielding remaining chunk", extra={"text_len": len(remaining), "voice_id": voice_id})
                yield ContentChunk(text=remaining, voice_id=voice_id, pause_after=1.0)

            # Update conversation history
            if full_response:
                self.history.append({"role": "assistant", "content": full_response})
                if len(self.history) > self.max_history:
                    self.history = self.history[-self.max_history:]
                logger.info("history updated", extra={"history_size": len(self.history)})

            # Brief pause between segments
            if not self._cancelled:
                await asyncio.sleep(0.5)

    async def handle_callin(self, transcript: str) -> AsyncGenerator[ContentChunk, None]:
        """Handle a call-in from a listener. Override in channels that support it."""
        logger.info("callin received (default handler)", extra={"channel": self.channel_name(), "transcript_len": len(transcript)})
        yield ContentChunk(
            text="Sorry, this channel doesn't take callers right now.",
            voice_id=self.get_voice_id(""),
        )

    def cancel(self):
        """Cancel ongoing generation (called when switching channels)."""
        logger.debug("channel cancelled", extra={"channel": self.channel_name()})
        self._cancelled = True

    def reset(self):
        """Reset cancellation flag (called when switching back to this channel)."""
        logger.info("channel reset", extra={"channel": self.channel_name()})
        self._cancelled = False

    def clear_history(self):
        """Clear conversation history."""
        self.history.clear()


# Shared base system prompt
BASE_SYSTEM_PROMPT = """You are a radio broadcaster for RadioAgent, a personalized AI radio station.

CURRENT CONTEXT:
- Date/Time: {current_datetime}
- Day of week: {day_of_week}
- Location: {city}, {state}
- Weather: {weather}
- Trending topics: {trending_topics}

BROADCASTING RULES:
1. Never break character. You ARE a radio host, not an AI assistant.
2. Speak naturally and conversationally. Use contractions, occasional filler words.
3. Reference the time of day naturally ("Good evening", "Happy Friday night").
4. Reference local context when relevant (weather, local events, sports teams).
5. Keep individual segments to 30-60 seconds of speech (~75-150 words).
6. End each segment with a natural transition: tease next segment, ask a rhetorical question, or do a "station break".
7. Do NOT use markdown, asterisks, or any text formatting. Output plain spoken text only.
8. Do NOT use stage directions like [pause] or [laughs]. Just write the words to speak.
9. Occasionally mention that listeners can "turn the dial" to find other content.
10. NEVER repeat content from previous segments. Always bring something new.
"""
