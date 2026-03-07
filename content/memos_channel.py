import json
import time
from pathlib import Path
from typing import AsyncGenerator

from content.agent import BaseChannel, ContentChunk, BASE_SYSTEM_PROMPT
from log import get_logger, log_api_call

logger = get_logger(__name__)

MEMOS_DIR = Path(__file__).parent.parent / "data" / "memos"


class MemosChannel(BaseChannel):
    """Memos channel — record, store, and play back voice memos.

    When this channel is active, the radio reads back saved memos
    and provides a summary digest. Users record new memos via the
    call-in button (press-and-hold to speak).
    """

    def __init__(self, context_provider, config: dict):
        super().__init__(context_provider, config)
        MEMOS_DIR.mkdir(parents=True, exist_ok=True)
        self._memos: list[dict] = self._load_memos()

    def channel_name(self) -> str:
        return "Memos"

    def get_voice_id(self, subchannel: str) -> str:
        return self.config["VOICES"].get("memo_host", "pNInz6obpgDQGcFmaJgB")

    def get_system_prompt(self, subchannel: str, context: dict) -> str:
        memo_texts = [m["text"] for m in self._memos[-10:]]
        memo_list = "\n".join(f"- [{m.get('timestamp', '?')}] {m['text']}" for m in self._memos[-10:])

        return BASE_SYSTEM_PROMPT.format(**context) + f"""
CHANNEL: Memos
VOICE STYLE: Warm, personal assistant tone. Like a thoughtful friend reading your notes back.

You are the memo host on RadioAgent. Your job is to read back the listener's saved memos
and provide helpful context, reminders, and gentle commentary.

SAVED MEMOS (most recent):
{memo_list if memo_list else "(No memos saved yet)"}

INSTRUCTIONS:
- Read back memos naturally, not like a robot reading a list
- Add brief context: "You noted this yesterday...", "Here's one from earlier today..."
- If there are no memos, gently let the listener know and suggest they record one
- Group related memos if they share a theme
- End with a prompt: "Press and hold the call-in button to leave a new memo"
- Keep to ~80-120 words per segment
- Be warm and helpful, like a personal radio assistant
"""

    async def stream_content(self, subchannel: str) -> AsyncGenerator[ContentChunk, None]:
        """Read back memos with commentary."""
        logger.info("Memos stream_content started")
        self._memos = self._load_memos()

        while not self._cancelled:
            ctx = await self.context.get_context()
            system_prompt = self.get_system_prompt(subchannel, ctx)
            voice_id = self.get_voice_id(subchannel)

            if not self._memos:
                yield ContentChunk(
                    text="You don't have any memos saved yet. Press and hold the call-in button anytime to leave a voice memo. I'll keep them safe and read them back to you.",
                    voice_id=voice_id,
                    pause_after=5.0,
                )
                # Wait before checking again
                for _ in range(100):
                    if self._cancelled:
                        return
                    await __import__("asyncio").sleep(0.1)
                self._memos = self._load_memos()
                continue

            messages = [
                *self.history,
                {"role": "user", "content": "Read back my memos with helpful context."},
            ]

            model = self.config.get("LLM_MODEL", "claude-haiku-4-5-20251001")
            full_response = ""
            t0 = time.monotonic()
            async with self.client.messages.stream(
                model=model,
                max_tokens=self.config.get("LLM_MAX_TOKENS", 300),
                temperature=0.7,
                system=system_prompt,
                messages=messages,
            ) as stream:
                buffer = ""
                async for text in stream.text_stream:
                    if self._cancelled:
                        return
                    buffer += text
                    full_response += text

                    while True:
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
                            yield ContentChunk(text=sentence, voice_id=voice_id)

            duration_ms = (time.monotonic() - t0) * 1000
            log_api_call(logger, "anthropic", "messages.stream", status="ok", duration_ms=duration_ms,
                         model=model, context="memos_readback", response_len=len(full_response))

            remaining = buffer.strip()
            if remaining and not self._cancelled:
                yield ContentChunk(text=remaining, voice_id=voice_id, pause_after=2.0)

            if full_response:
                self.history.append({"role": "assistant", "content": full_response})
                if len(self.history) > self.max_history:
                    self.history = self.history[-self.max_history:]

            # Long pause between readbacks
            for _ in range(300):
                if self._cancelled:
                    return
                await __import__("asyncio").sleep(0.1)

            self._memos = self._load_memos()

    async def handle_callin(self, transcript: str) -> AsyncGenerator[ContentChunk, None]:
        """Save a new voice memo from the caller."""
        logger.info("memo recorded", extra={"transcript_len": len(transcript)})
        voice_id = self.get_voice_id("")

        if not transcript.strip():
            yield ContentChunk(
                text="I didn't catch that. Try holding the button and speaking again.",
                voice_id=voice_id,
            )
            return

        memo = {
            "text": transcript.strip(),
            "timestamp": time.strftime("%Y-%m-%d %H:%M"),
        }
        self._memos.append(memo)
        self._save_memos()

        yield ContentChunk(
            text=f"Got it. Memo saved: {transcript.strip()}",
            voice_id=voice_id,
            pause_after=1.0,
        )

    def add_memo_from_nfc(self, text: str):
        """Add a memo from NFC tag content."""
        logger.info("memo from NFC", extra={"text_len": len(text)})
        memo = {
            "text": text.strip(),
            "timestamp": time.strftime("%Y-%m-%d %H:%M"),
            "source": "nfc",
        }
        self._memos.append(memo)
        self._save_memos()

    def _load_memos(self) -> list[dict]:
        memo_file = MEMOS_DIR / "memos.json"
        if memo_file.exists():
            try:
                return json.loads(memo_file.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.error("failed to load memos", exc_info=e)
        return []

    def _save_memos(self):
        memo_file = MEMOS_DIR / "memos.json"
        try:
            memo_file.write_text(json.dumps(self._memos, indent=2))
        except OSError as e:
            logger.error("failed to save memos", exc_info=e)
