import time
from urllib.parse import urlencode

import aiohttp

from log import get_logger, log_api_call

logger = get_logger(__name__)


class STTService:
    """Speech-to-text via the Deepgram API."""

    CONTENT_TYPES = {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "mpeg": "audio/mpeg",
        "m4a": "audio/mp4",
        "aac": "audio/aac",
        "flac": "audio/flac",
        "ogg": "audio/ogg",
        "opus": "audio/ogg",
        "webm": "audio/webm",
    }

    def __init__(self, deepgram_key: str | None = None, model: str = "nova-3"):
        self.deepgram_key = deepgram_key
        self.model = model

    async def transcribe(self, audio_bytes: bytes, format: str = "wav") -> str:
        """Transcribe audio bytes to text."""
        if not audio_bytes:
            return ""

        if not self.deepgram_key:
            raise ValueError("DEEPGRAM_API_KEY is required for call-in transcription")

        logger.info("Transcription started", extra={
            "audio_size_bytes": len(audio_bytes), "format": format,
        })

        logger.debug("Using Deepgram API for transcription")
        return await self._transcribe_deepgram(audio_bytes, format)

    async def _transcribe_deepgram(self, audio_bytes: bytes, format: str) -> str:
        """Transcribe via Deepgram's prerecorded audio API."""
        content_type = self.CONTENT_TYPES.get(format.lower(), "application/octet-stream")
        query = urlencode({
            "model": self.model,
            "smart_format": "true",
            "punctuate": "true",
        })
        url = f"https://api.deepgram.com/v1/listen?{query}"
        headers = {
            "Authorization": f"Token {self.deepgram_key}",
            "Content-Type": content_type,
        }

        t0 = time.monotonic()
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, data=audio_bytes, headers=headers) as response:
                    payload = await response.json(content_type=None)

                    if response.status >= 400:
                        error_message = payload.get("err_msg") or payload.get("message") or str(payload)
                        raise RuntimeError(
                            f"Deepgram transcription failed with status {response.status}: {error_message}"
                        )

            elapsed = (time.monotonic() - t0) * 1000
            result = self._extract_transcript(payload)
            log_api_call(logger, "deepgram", "/v1/listen",
                         status="ok", duration_ms=elapsed,
                         audio_size=len(audio_bytes), model=self.model)
            logger.info("Transcription complete", extra={
                "transcript_length": len(result),
            })
            return result
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Deepgram transcription failed: %s", e)
            log_api_call(logger, "deepgram", "/v1/listen",
                         status="exception", duration_ms=elapsed,
                         audio_size=len(audio_bytes), model=self.model)
            raise

    @staticmethod
    def _extract_transcript(payload: dict) -> str:
        channels = payload.get("results", {}).get("channels", [])
        if not channels:
            return ""

        alternatives = channels[0].get("alternatives", [])
        if not alternatives:
            return ""

        transcript = alternatives[0].get("transcript", "")
        if isinstance(transcript, str):
            return transcript.strip()

        return ""
