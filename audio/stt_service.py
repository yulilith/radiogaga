import asyncio
import tempfile
import os


class STTService:
    """Speech-to-text via OpenAI Whisper API, with local faster-whisper fallback."""

    def __init__(self, openai_key: str | None = None, use_local: bool = False):
        self.openai_key = openai_key
        self.use_local = use_local
        self._local_model = None

    async def transcribe(self, audio_bytes: bytes, format: str = "wav") -> str:
        """Transcribe audio bytes to text."""
        if self.use_local:
            return await self._transcribe_local(audio_bytes, format)
        return await self._transcribe_openai(audio_bytes, format)

    async def _transcribe_openai(self, audio_bytes: bytes, format: str) -> str:
        """Transcribe via OpenAI Whisper API."""
        import openai
        client = openai.AsyncOpenAI(api_key=self.openai_key)

        # Write to temp file (Whisper API needs a file-like object)
        with tempfile.NamedTemporaryFile(suffix=f".{format}", delete=False) as f:
            f.write(audio_bytes)
            temp_path = f.name

        try:
            with open(temp_path, "rb") as audio_file:
                transcript = await client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text",
                )
            return transcript.strip()
        finally:
            os.unlink(temp_path)

    async def _transcribe_local(self, audio_bytes: bytes, format: str) -> str:
        """Transcribe locally using faster-whisper (tiny model)."""
        if self._local_model is None:
            from faster_whisper import WhisperModel
            self._local_model = WhisperModel("tiny", device="cpu", compute_type="int8")

        with tempfile.NamedTemporaryFile(suffix=f".{format}", delete=False) as f:
            f.write(audio_bytes)
            temp_path = f.name

        try:
            segments, _ = await asyncio.to_thread(
                self._local_model.transcribe, temp_path
            )
            text = " ".join(seg.text for seg in segments)
            return text.strip()
        finally:
            os.unlink(temp_path)
