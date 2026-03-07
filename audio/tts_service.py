import asyncio
import time
import aiohttp
from typing import AsyncGenerator

from log import get_logger, log_api_call

logger = get_logger(__name__)


class TTSService:
    """ElevenLabs streaming TTS with OpenAI TTS fallback."""

    def __init__(self, elevenlabs_key: str, openai_key: str | None = None,
                 model: str = "eleven_flash_v2_5",
                 output_format: str = "mp3_22050_32"):
        self.elevenlabs_key = elevenlabs_key
        self.openai_key = openai_key
        self.model = model
        self.output_format = output_format
        self.base_url = "https://api.elevenlabs.io/v1"
        self._use_fallback = False

    async def stream_speech(
        self, text: str, voice_id: str | None = None
    ) -> AsyncGenerator[bytes, None]:
        """Stream TTS audio as chunks of MP3 bytes."""
        voice = voice_id or "pNInz6obpgDQGcFmaJgB"  # Default: Adam
        logger.debug("Synthesizing speech", extra={
            "voice_id": voice, "text_length": len(text),
        })

        if self._use_fallback and self.openai_key:
            async for chunk in self._openai_tts(text, voice_id):
                yield chunk
            return

        url = f"{self.base_url}/text-to-speech/{voice}/stream"

        headers = {
            "xi-api-key": self.elevenlabs_key,
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "model_id": self.model,
            "output_format": self.output_format,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            },
            "optimize_streaming_latency": 3,
        }

        t0 = time.monotonic()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    elapsed = (time.monotonic() - t0) * 1000

                    if resp.status == 429:
                        logger.warning(
                            "ElevenLabs quota exceeded, switching to OpenAI TTS"
                        )
                        log_api_call(logger, "elevenlabs", "/text-to-speech",
                                     status="quota_exceeded", duration_ms=elapsed,
                                     chars=len(text))
                        self._use_fallback = True
                        if self.openai_key:
                            async for chunk in self._openai_tts(text, voice_id):
                                yield chunk
                        return

                    if resp.status != 200:
                        error = await resp.text()
                        logger.error(
                            "ElevenLabs API error",
                            extra={"status": resp.status, "error": error},
                        )
                        log_api_call(logger, "elevenlabs", "/text-to-speech",
                                     status=f"error_{resp.status}",
                                     duration_ms=elapsed, chars=len(text))
                        return

                    total_bytes = 0
                    async for chunk in resp.content.iter_chunked(1024):
                        total_bytes += len(chunk)
                        yield chunk

                    elapsed = (time.monotonic() - t0) * 1000
                    log_api_call(logger, "elevenlabs", "/text-to-speech",
                                 status="ok", duration_ms=elapsed,
                                 chars=len(text), voice=voice)
                    logger.info("ElevenLabs synthesis complete",
                                extra={"bytes": total_bytes})

        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("ElevenLabs request failed: %s", e)
            log_api_call(logger, "elevenlabs", "/text-to-speech",
                         status="exception", duration_ms=elapsed,
                         chars=len(text))
            if self.openai_key:
                logger.warning("Falling back to OpenAI TTS")
                self._use_fallback = True
                async for chunk in self._openai_tts(text, voice_id):
                    yield chunk

    async def synthesize(self, text: str, voice_id: str | None = None) -> bytes:
        """Get complete TTS audio as bytes (non-streaming)."""
        chunks = []
        async for chunk in self.stream_speech(text, voice_id):
            chunks.append(chunk)
        result = b"".join(chunks)
        if not result:
            logger.error("TTS returned empty audio data")
            raise RuntimeError("TTS synthesis returned no audio data")
        logger.info("Synthesis complete", extra={"bytes": len(result)})
        return result

    async def _openai_tts(self, text: str, _voice_id: str | None = None) -> AsyncGenerator[bytes, None]:
        """Fallback: OpenAI TTS API."""
        import openai

        t0 = time.monotonic()
        try:
            client = openai.AsyncOpenAI(api_key=self.openai_key)
            response = await client.audio.speech.create(
                model="tts-1",
                voice="alloy",
                input=text,
                response_format="mp3",
            )
            elapsed = (time.monotonic() - t0) * 1000
            log_api_call(logger, "openai", "/audio/speech",
                         status="ok", duration_ms=elapsed, chars=len(text))
            logger.info("OpenAI TTS synthesis complete",
                        extra={"bytes": len(response.content)})
            yield response.content
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("OpenAI TTS failed: %s", e)
            log_api_call(logger, "openai", "/audio/speech",
                         status="exception", duration_ms=elapsed,
                         chars=len(text))
