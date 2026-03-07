import asyncio
import aiohttp
from typing import AsyncGenerator


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
        if self._use_fallback and self.openai_key:
            async for chunk in self._openai_tts(text, voice_id):
                yield chunk
            return

        voice = voice_id or "pNInz6obpgDQGcFmaJgB"  # Default: Adam
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

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 429:
                        print("[TTS] ElevenLabs quota exceeded, switching to OpenAI TTS")
                        self._use_fallback = True
                        if self.openai_key:
                            async for chunk in self._openai_tts(text, voice_id):
                                yield chunk
                        return

                    if resp.status != 200:
                        error = await resp.text()
                        print(f"[TTS] ElevenLabs error {resp.status}: {error}")
                        return

                    async for chunk in resp.content.iter_chunked(1024):
                        yield chunk
        except Exception as e:
            print(f"[TTS] ElevenLabs error: {e}")
            if self.openai_key:
                self._use_fallback = True
                async for chunk in self._openai_tts(text, voice_id):
                    yield chunk

    async def synthesize(self, text: str, voice_id: str | None = None) -> bytes:
        """Get complete TTS audio as bytes (non-streaming)."""
        chunks = []
        async for chunk in self.stream_speech(text, voice_id):
            chunks.append(chunk)
        return b"".join(chunks)

    async def _openai_tts(self, text: str, _voice_id: str | None = None) -> AsyncGenerator[bytes, None]:
        """Fallback: OpenAI TTS API."""
        import openai
        client = openai.AsyncOpenAI(api_key=self.openai_key)
        response = await client.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input=text,
            response_format="mp3",
        )
        yield response.content
