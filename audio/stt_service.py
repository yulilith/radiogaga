import asyncio
import tempfile
import time
import os

from log import get_logger, log_api_call

logger = get_logger(__name__)


class STTService:
    """Speech-to-text via OpenAI Whisper API, with local faster-whisper fallback."""

    def __init__(self, openai_key: str | None = None, use_local: bool = False):
        self.openai_key = openai_key
        self.use_local = use_local
        self._local_model = None

    async def transcribe(self, audio_bytes: bytes, format: str = "wav") -> str:
        """Transcribe audio bytes to text."""
        logger.info("Transcription started", extra={
            "audio_size_bytes": len(audio_bytes), "format": format,
        })

        if self.use_local:
            logger.debug("Using local faster-whisper for transcription")
            return await self._transcribe_local(audio_bytes, format)

        logger.debug("Using OpenAI Whisper API for transcription")
        return await self._transcribe_openai(audio_bytes, format)

    async def _transcribe_openai(self, audio_bytes: bytes, format: str) -> str:
        """Transcribe via OpenAI Whisper API."""
        import openai
        client = openai.AsyncOpenAI(api_key=self.openai_key)

        # Write to temp file (Whisper API needs a file-like object)
        with tempfile.NamedTemporaryFile(suffix=f".{format}", delete=False) as f:
            f.write(audio_bytes)
            temp_path = f.name

        t0 = time.monotonic()
        try:
            with open(temp_path, "rb") as audio_file:
                transcript = await client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text",
                )
            elapsed = (time.monotonic() - t0) * 1000
            result = transcript.strip()
            log_api_call(logger, "openai", "/audio/transcriptions",
                         status="ok", duration_ms=elapsed,
                         audio_size=len(audio_bytes))
            logger.info("Transcription complete", extra={
                "transcript_length": len(result),
            })
            return result
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("OpenAI Whisper transcription failed: %s", e)
            log_api_call(logger, "openai", "/audio/transcriptions",
                         status="exception", duration_ms=elapsed,
                         audio_size=len(audio_bytes))
            raise
        finally:
            os.unlink(temp_path)

    async def _transcribe_local(self, audio_bytes: bytes, format: str) -> str:
        """Transcribe locally using faster-whisper (tiny model)."""
        if self._local_model is None:
            from faster_whisper import WhisperModel
            logger.info("Loading local faster-whisper model (tiny)")
            self._local_model = WhisperModel("tiny", device="cpu", compute_type="int8")

        with tempfile.NamedTemporaryFile(suffix=f".{format}", delete=False) as f:
            f.write(audio_bytes)
            temp_path = f.name

        t0 = time.monotonic()
        try:
            segments, _ = await asyncio.to_thread(
                self._local_model.transcribe, temp_path
            )
            text = " ".join(seg.text for seg in segments)
            result = text.strip()
            elapsed = (time.monotonic() - t0) * 1000
            logger.info("Local transcription complete", extra={
                "transcript_length": len(result), "duration_ms": f"{elapsed:.0f}",
            })
            return result
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Local transcription failed: %s", e, extra={
                "duration_ms": f"{elapsed:.0f}",
            })
            raise
        finally:
            os.unlink(temp_path)
