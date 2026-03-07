import asyncio
import logging

import pytest

from radioagent.audio.player import AudioPlayer
from radioagent.models import AudioArtifact


@pytest.mark.asyncio
async def test_audio_player_stop_interrupts_active_process(tmp_path, monkeypatch) -> None:
    audio_path = tmp_path / "clip.mp3"
    audio_path.write_bytes(b"fake-audio")

    started = asyncio.Event()
    finished = asyncio.Event()

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode = None

        async def wait(self) -> int:
            started.set()
            await finished.wait()
            return self.returncode or 0

        def terminate(self) -> None:
            self.returncode = -15
            finished.set()

        def kill(self) -> None:
            self.returncode = -9
            finished.set()

    fake_process = FakeProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(AudioPlayer, "_resolve_command", lambda self, path: ["fake-player", str(path)])

    player = AudioPlayer(enabled=True, logger=logging.getLogger("test-audio-player"))
    artifact = AudioArtifact(provider="elevenlabs", path=str(audio_path))

    play_task = asyncio.create_task(player.play(artifact))
    await started.wait()

    stopped = await player.stop("user_injected")
    await play_task

    assert stopped is True
    assert fake_process.returncode == -15
