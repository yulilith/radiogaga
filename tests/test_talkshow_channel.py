from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from content.talkshow_channel import GUEST_PERSONALITIES, TalkShowChannel, TalkTurn


class FakeContextProvider:
    def __init__(self, context: dict):
        self._context = context

    async def get_context(self) -> dict:
        return dict(self._context)


class FakeAnthropicClient:
    def __init__(self, responses: list[str]):
        self.messages = FakeMessages(responses)


class FakeMessages:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("No fake LLM responses remaining")
        text = self._responses.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(text=text)])


@pytest.fixture
def config():
    return {
        "ANTHROPIC_API_KEY": "test-key",
        "VOICES": {
            "talk_host": "voice-host",
            "talk_cohost": "voice-guest",
        },
        "LLM_MODEL": "claude-haiku-4-5-20251001",
        "LLM_TEMPERATURE": 0.7,
        "HISTORY_WINDOW": 4,
    }


@pytest.fixture
def base_context():
    return {
        "current_datetime": "Saturday, March 07, 2026 at 08:00 PM",
        "day_of_week": "Saturday",
        "city": "San Francisco",
        "state": "CA",
        "weather": "clear skies",
        "trending_topics": "AI launch drama, celebrity feud, remote work debate",
        "headlines": [
            "AI startup launch sparks privacy backlash overnight",
            "Late-night host feud dominates entertainment news",
        ],
        "reddit_trending": [
            "People are debating whether social apps are frying attention spans",
        ],
        "google_trends": [
            "celebrity feud timeline",
            "AI agent hype",
        ],
        "on_this_day": [
            "A historic radio broadcast changed live media forever",
        ],
    }


def build_channel(config: dict, context: dict, responses: list[str] | None = None) -> TalkShowChannel:
    with patch("content.agent.anthropic.AsyncAnthropic", return_value=FakeAnthropicClient([])):
        channel = TalkShowChannel(FakeContextProvider(context), config)
    channel.client = FakeAnthropicClient(responses or [])
    return channel


@pytest.mark.anyio
async def test_pick_talkshow_topic_prefers_current_headlines(config, base_context):
    channel = build_channel(config, base_context)

    topic = channel._pick_talkshow_topic(base_context, "tech")

    assert topic["source"] == "headline"
    assert topic["text"] == "AI startup launch sparks privacy backlash overnight"


def test_select_guest_persona_rotates_between_segments(config, base_context):
    channel = build_channel(config, base_context)
    topic = {
        "source": "headline",
        "text": "AI startup launch sparks privacy backlash overnight",
        "angle": "Treat the topic like a live tech radio segment.",
    }

    first_guest = channel._select_guest_persona(topic, "tech", advance_rotation=True)
    second_guest = channel._select_guest_persona(topic, "tech", advance_rotation=True)

    assert first_guest.name != second_guest.name
    assert first_guest in GUEST_PERSONALITIES
    assert second_guest in GUEST_PERSONALITIES


@pytest.mark.anyio
async def test_stream_content_yields_alternating_host_and_guest_voices(config, base_context):
    channel = build_channel(
        config,
        base_context,
        responses=[
            "Host opening line",
            "Guest pushback line",
            "Host closing line",
        ],
    )
    channel._sleep_between_segments = AsyncMock()

    generator = channel.stream_content("tech")
    chunks = [await generator.__anext__() for _ in range(3)]

    assert [chunk.text for chunk in chunks] == [
        "Host opening line",
        "Guest pushback line",
        "Host closing line",
    ]
    assert [chunk.voice_id for chunk in chunks] == [
        "voice-host",
        "voice-guest",
        "voice-host",
    ]

    channel.cancel()
    await generator.aclose()


@pytest.mark.anyio
async def test_stream_content_keeps_same_host_for_same_subchannel(config, base_context):
    channel = build_channel(
        config,
        base_context,
        responses=[
            "Host opening one",
            "Guest reply one",
            "Host close one",
            "Host opening two",
            "Guest reply two",
            "Host close two",
        ],
    )
    channel._sleep_between_segments = AsyncMock()

    generator = channel.stream_content("tech")
    for _ in range(6):
        await generator.__anext__()

    host_calls = [
        call for call in channel.client.messages.calls
        if "YOUR ROLE: host" in call["system"]
    ]

    assert len(host_calls) == 4
    assert all("YOUR NAME: Alex Circuit" in call["system"] for call in host_calls)
    assert all("CHANNEL: Talk Show - The Digital Pulse" in call["system"] for call in host_calls)

    channel.cancel()
    await generator.aclose()


def test_reset_preserves_talkshow_specific_state_for_switch_resume(config, base_context):
    channel = build_channel(config, base_context)
    channel.cancel()
    channel._active_subchannel = "popculture"
    channel._current_topic = {"text": "Celebrity drama", "source": "headline"}
    channel._current_guest = GUEST_PERSONALITIES[0]
    channel._guest_rotation_index = 3
    channel.history = [{"role": "assistant", "content": "Maya Buzz: wild stuff tonight"}]
    channel._turn_history = [TalkTurn(speaker_role="host", speaker_name="Maya Buzz", text="wild stuff tonight")]

    channel.reset()

    assert channel._cancelled is False
    assert channel._active_subchannel == "popculture"
    assert channel._current_topic == {"text": "Celebrity drama", "source": "headline"}
    assert channel._current_guest == GUEST_PERSONALITIES[0]
    assert channel._guest_rotation_index == 3
    assert channel.history == [{"role": "assistant", "content": "Maya Buzz: wild stuff tonight"}]
    assert channel._turn_history == [TalkTurn(speaker_role="host", speaker_name="Maya Buzz", text="wild stuff tonight")]


def test_hard_reset_clears_talkshow_specific_state(config, base_context):
    channel = build_channel(config, base_context)
    channel.cancel()
    channel._active_subchannel = "popculture"
    channel._current_topic = {"text": "Celebrity drama", "source": "headline"}
    channel._current_guest = GUEST_PERSONALITIES[0]
    channel._guest_rotation_index = 3
    channel.history = [{"role": "assistant", "content": "Maya Buzz: wild stuff tonight"}]
    channel._turn_history = [TalkTurn(speaker_role="host", speaker_name="Maya Buzz", text="wild stuff tonight")]

    channel.hard_reset()

    assert channel._cancelled is False
    assert channel._active_subchannel == "tech"
    assert channel._current_topic is None
    assert channel._current_guest is None
    assert channel._guest_rotation_index == 0
    assert channel.history == []
    assert channel._turn_history == []


@pytest.mark.anyio
async def test_build_preview_is_safe_and_does_not_mutate_live_state(config, base_context):
    channel = build_channel(
        config,
        base_context,
        responses=["Host preview opener"],
    )
    channel._active_subchannel = "advice"
    channel._guest_rotation_index = 2

    preview = await channel.build_preview("tech")

    assert preview.text == "Host preview opener"
    assert preview.voice_id == "voice-host"
    assert channel._active_subchannel == "advice"
    assert channel._guest_rotation_index == 2
    assert channel._current_topic is None
    assert channel._current_guest is None


@pytest.mark.anyio
async def test_handle_callin_uses_active_subchannel_host(config, base_context):
    channel = build_channel(
        config,
        base_context,
        responses=["We have a caller, and honestly they're onto something."],
    )
    channel._active_subchannel = "popculture"
    channel._current_topic = {
        "source": "headline",
        "text": "Late-night host feud dominates entertainment news",
        "angle": "Treat the topic like a culture and drama segment.",
    }
    channel._current_guest = GUEST_PERSONALITIES[1]

    generator = channel.handle_callin("I think this feud is obviously staged")
    chunk = await generator.__anext__()

    assert chunk.voice_id == "voice-host"
    assert chunk.text == "We have a caller, and honestly they're onto something."
    assert "HOST NAME: Maya Buzz" in channel.client.messages.calls[0]["system"]

    await generator.aclose()
