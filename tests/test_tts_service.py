from unittest.mock import patch

import pytest

from audio.tts_service import TTSService


class FakeContent:
    async def iter_chunked(self, _chunk_size):
        yield b"fake-audio"


class FakeResponse:
    def __init__(self):
        self.status = 200
        self.content = FakeContent()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return "ok"


class FakeSession:
    def __init__(self, captured_request):
        self.captured_request = captured_request

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, json, headers):
        self.captured_request["url"] = url
        self.captured_request["payload"] = json
        self.captured_request["headers"] = headers
        return FakeResponse()


@pytest.mark.anyio
async def test_stream_speech_sends_configured_speed_to_elevenlabs():
    captured_request = {}
    service = TTSService(elevenlabs_key="test-key", speed=1.2)

    with patch("audio.tts_service.aiohttp.ClientSession", return_value=FakeSession(captured_request)):
        chunks = [chunk async for chunk in service.stream_speech("hello world", "voice-123")]

    assert chunks == [b"fake-audio"]
    assert captured_request["url"].endswith("/voice-123/stream")
    assert captured_request["payload"]["voice_settings"]["speed"] == 1.2


@pytest.mark.anyio
async def test_synthesize_reuses_cached_audio_for_identical_requests(monkeypatch):
    service = TTSService(elevenlabs_key="test-key", speed=1.1)
    calls = []

    async def fake_stream_speech(text, voice_id):
        calls.append((text, voice_id))
        yield b"cached-audio"

    monkeypatch.setattr(service, "stream_speech", fake_stream_speech)

    first = await service.synthesize("hello world", "voice-123")
    second = await service.synthesize("hello world", "voice-123")

    assert first == b"cached-audio"
    assert second == b"cached-audio"
    assert calls == [("hello world", "voice-123")]


@pytest.mark.anyio
async def test_synthesize_cache_key_changes_when_voice_changes(monkeypatch):
    service = TTSService(elevenlabs_key="test-key", speed=1.1)
    calls = []

    async def fake_stream_speech(text, voice_id):
        calls.append((text, voice_id))
        yield f"{voice_id}:{text}".encode()

    monkeypatch.setattr(service, "stream_speech", fake_stream_speech)

    first = await service.synthesize("hello world", "voice-123")
    second = await service.synthesize("hello world", "voice-456")

    assert first != second
    assert calls == [
        ("hello world", "voice-123"),
        ("hello world", "voice-456"),
    ]
