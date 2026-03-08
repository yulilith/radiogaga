from audio.audio_player import AudioPlayer


class FakeStream:
    def write(self, _data):
        return None

    def stop_stream(self):
        return None

    def close(self):
        return None


class FakePyAudio:
    def open(self, **_kwargs):
        return FakeStream()

    def terminate(self):
        return None


class FakeSegment:
    def __init__(self, raw_data: bytes):
        self.raw_data = raw_data

    def set_frame_rate(self, _rate):
        return self

    def set_channels(self, _channels):
        return self


def test_hard_stop_invalidates_stale_audio(monkeypatch):
    monkeypatch.setattr("audio.audio_player.pyaudio.PyAudio", lambda: FakePyAudio())
    monkeypatch.setattr(
        "audio.audio_player.AudioSegment.from_mp3",
        lambda _source: FakeSegment(b"\x01\x02" * 2048),
    )

    player = AudioPlayer()
    original_generation = player.current_generation

    assert player.enqueue_mp3(b"fake-mp3", generation=original_generation) is True
    assert player.buffer_level() > 0

    next_generation = player.hard_stop("switch")

    assert next_generation == original_generation + 1
    assert player.buffer_level() == 0
    assert player.enqueue_mp3(b"fake-mp3", generation=original_generation) is False

    player.stop()
