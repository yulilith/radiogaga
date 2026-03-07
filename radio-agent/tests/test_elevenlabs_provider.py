import asyncio
import sys
import types

from radioagent.voice.base import SynthesisRequest
from radioagent.voice.elevenlabs_provider import ElevenLabsTTSProvider


def test_elevenlabs_provider_passes_speed_to_voice_settings(tmp_path, monkeypatch) -> None:
    recorded: dict[str, object] = {}

    fake_elevenlabs = types.ModuleType("elevenlabs")
    fake_elevenlabs_client = types.ModuleType("elevenlabs.client")

    class VoiceSettings:
        def __init__(self, *, speed: float | None = None, **kwargs) -> None:
            self.speed = speed
            self.extra = kwargs

    class ElevenLabs:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.text_to_speech = self

        def stream(self, **kwargs):
            recorded.update(kwargs)
            yield b"fake-mp3"

    fake_elevenlabs.VoiceSettings = VoiceSettings
    fake_elevenlabs_client.ElevenLabs = ElevenLabs
    monkeypatch.setitem(sys.modules, "elevenlabs", fake_elevenlabs)
    monkeypatch.setitem(sys.modules, "elevenlabs.client", fake_elevenlabs_client)

    provider = ElevenLabsTTSProvider(
        api_key="test-key",
        model_id="eleven_turbo_v2_5",
        output_format="mp3_44100_128",
        speed=1.1,
    )

    artifact = asyncio.run(
        provider.synthesize(
            SynthesisRequest(
                session_id="session_123",
                speaker_id="agent_a",
                voice_id="voice_1",
                text="Fast enough to notice",
                turn_index=1,
                output_dir=tmp_path,
            )
        )
    )

    assert artifact.provider == "elevenlabs"
    assert tmp_path.joinpath("session_123_agent_a_1.mp3").read_bytes() == b"fake-mp3"
    assert recorded["model_id"] == "eleven_turbo_v2_5"
    assert recorded["output_format"] == "mp3_44100_128"
    assert recorded["voice_id"] == "voice_1"
    assert getattr(recorded["voice_settings"], "speed") == 1.1
