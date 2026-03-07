from __future__ import annotations

from radioagent.models import AudioArtifact
from radioagent.voice.base import SynthesisRequest, TTSProvider


class MockTTSProvider(TTSProvider):
    provider_name = "mock"

    async def synthesize(self, request: SynthesisRequest) -> AudioArtifact:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = (
            request.output_dir
            / f"{request.session_id}_{request.speaker_id}_{request.turn_index}.txt"
        )
        output_path.write_text(request.text, encoding="utf-8")
        return AudioArtifact(
            provider=self.provider_name,
            path=str(output_path),
            mime_type="text/plain",
        )

