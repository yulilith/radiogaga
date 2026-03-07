import asyncio

from radioagent.voice.base import SynthesisRequest
from radioagent.voice.mock_provider import MockTTSProvider


def test_mock_tts_writes_artifact(tmp_path) -> None:
    provider = MockTTSProvider()
    artifact = asyncio.run(
        provider.synthesize(
            SynthesisRequest(
                session_id="session_123",
                speaker_id="agent_a",
                voice_id="voice_1",
                text="Hello from the mock provider",
                turn_index=1,
                output_dir=tmp_path,
            )
        )
    )

    assert artifact.provider == "mock"
    assert artifact.mime_type == "text/plain"
    assert tmp_path.joinpath("session_123_agent_a_1.txt").read_text() == (
        "Hello from the mock provider"
    )

