from __future__ import annotations

import asyncio

from radioagent.models import AudioArtifact
from radioagent.voice.base import SynthesisRequest, TTSProvider


class ElevenLabsTTSProvider(TTSProvider):
    provider_name = "elevenlabs"

    def __init__(
        self,
        api_key: str,
        *,
        model_id: str = "eleven_turbo_v2_5",
        output_format: str = "mp3_44100_128",
        speed: float = 1.1,
    ) -> None:
        self.api_key = api_key
        self.model_id = model_id
        self.output_format = output_format
        self.speed = speed

    async def synthesize(self, request: SynthesisRequest) -> AudioArtifact:
        return await asyncio.to_thread(self._synthesize_sync, request)

    def _synthesize_sync(self, request: SynthesisRequest) -> AudioArtifact:
        if not self.api_key:
            raise ValueError("ELEVENLABS_API_KEY is required for the ElevenLabs provider")

        from elevenlabs import VoiceSettings
        from elevenlabs.client import ElevenLabs

        request.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = (
            request.output_dir
            / f"{request.session_id}_{request.speaker_id}_{request.turn_index}.mp3"
        )
        client = ElevenLabs(api_key=self.api_key)
        stream = client.text_to_speech.stream(
            text=request.text,
            voice_id=request.voice_id,
            model_id=self.model_id,
            output_format=self.output_format,
            voice_settings=VoiceSettings(speed=self.speed),
        )
        with output_path.open("wb") as handle:
            for chunk in stream:
                if isinstance(chunk, bytes):
                    handle.write(chunk)
        return AudioArtifact(provider=self.provider_name, path=str(output_path))

