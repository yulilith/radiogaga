from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest

from audio.stt_service import STTService


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        return self._payload


class FakeSession:
    def __init__(self, captured_request, response):
        self.captured_request = captured_request
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, data, headers):
        self.captured_request["url"] = url
        self.captured_request["data"] = data
        self.captured_request["headers"] = headers
        return self.response


@pytest.mark.anyio
async def test_transcribe_sends_audio_to_deepgram_and_returns_transcript():
    captured_request = {}
    response = FakeResponse(200, {
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {"transcript": "hello radio"}
                    ]
                }
            ]
        }
    })
    service = STTService(deepgram_key="test-key", model="nova-3")

    with patch("audio.stt_service.aiohttp.ClientSession", return_value=FakeSession(captured_request, response)):
        result = await service.transcribe(b"wav-bytes", format="wav")

    parsed_url = urlparse(captured_request["url"])
    query = parse_qs(parsed_url.query)

    assert result == "hello radio"
    assert parsed_url.path == "/v1/listen"
    assert query["model"] == ["nova-3"]
    assert query["smart_format"] == ["true"]
    assert query["punctuate"] == ["true"]
    assert captured_request["data"] == b"wav-bytes"
    assert captured_request["headers"]["Authorization"] == "Token test-key"
    assert captured_request["headers"]["Content-Type"] == "audio/wav"


@pytest.mark.anyio
async def test_transcribe_requires_deepgram_key():
    service = STTService()

    with pytest.raises(ValueError, match="DEEPGRAM_API_KEY"):
        await service.transcribe(b"wav-bytes", format="wav")
