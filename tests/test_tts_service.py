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
