from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from radioagent.models import AudioArtifact


@dataclass(slots=True)
class SynthesisRequest:
    session_id: str
    speaker_id: str
    voice_id: str
    text: str
    turn_index: int
    output_dir: Path


class TTSProvider(ABC):
    provider_name: str

    @abstractmethod
    async def synthesize(self, request: SynthesisRequest) -> AudioArtifact:
        raise NotImplementedError

