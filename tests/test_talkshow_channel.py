from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from content.personas import PERSONA_REGISTRY, DEFAULT_SLOTS
from content.talkshow_channel import (
    TalkShowChannel, TalkTurn, LiveConversation, TalkShowAgent,
)


class FakeContextProvider:
    def __init__(self, context: dict):
        self._context = context

    async def get_context(self) -> dict:
        return dict(self._context)


class FakeStream:
    """Simulates an Anthropic streaming response with text events."""
    def __init__(self, text: str):
        self._text = text

    async def __aiter__(self):
        for char in self._text:
            yield SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text=char),
            )


class FakeMessages:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("No fake LLM responses remaining")
        text = self._responses.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])

    @asynccontextmanager
    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("No fake LLM responses remaining")
        text = self._responses.pop(0)
        yield FakeStream(text)


class FakeAnthropicClient:
    def __init__(self, responses: list[str]):
        self.messages = FakeMessages(responses)


@pytest.fixture
def config():
    return {
        "ANTHROPIC_API_KEY": "test-key",
        "VOICES": {
            "dj":                 "voice-dj",
            "wacky_gymbro":       "voice-gymbro",
            "wacky_conspiracy":   "voice-conspiracy",
            "wacky_grandpa":      "voice-grandpa",
            "wacky_theater":      "voice-theater",
            "wacky_techbro":      "voice-techbro",
            "wacky_grandma":      "voice-grandma",
            "wacky_weather":      "voice-weather",
            "wacky_alien":        "voice-alien",
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
    personas = [PERSONA_REGISTRY[pid] for pid in DEFAULT_SLOTS]
    with patch("content.agent.anthropic.AsyncAnthropic", return_value=FakeAnthropicClient([])):
        channel = TalkShowChannel(FakeContextProvider(context), config, personas=personas)
    fake_client = FakeAnthropicClient(responses or [])
    channel.client = fake_client
    for agent in channel.agents:
        agent.client = fake_client
    return channel


# -- LiveConversation tests --

def test_live_conversation_add_and_format():
    conv = LiveConversation()
    conv.add_turn("Cornelius", "Hello!")
    conv.add_turn("Peggy", "Hey there, dear.")
    result = conv.format_recent()
    assert "Cornelius: Hello!" in result
    assert "Peggy: Hey there, dear." in result


def test_live_conversation_callin_tracking():
    conv = LiveConversation()
    assert not conv.had_callers
    conv.add_callin("I think AI is overhyped")
    assert conv.had_callers
    assert conv.callin_history[-1] == "I think AI is overhyped"


def test_live_conversation_truncates():
    conv = LiveConversation(max_turns=3)
    for i in range(5):
        conv.add_turn(f"Speaker{i}", f"Turn {i}")
    assert len(conv.transcript) == 3
    assert conv.transcript[0].speaker_name == "Speaker2"


# -- Topic selection tests --

@pytest.mark.anyio
async def test_pick_talkshow_topic_prefers_current_headlines(config, base_context):
    channel = build_channel(config, base_context)
    topic = channel._pick_talkshow_topic(base_context, "tech")
    assert topic["source"] == "headline"
    assert topic["text"] == "AI startup launch sparks privacy backlash overnight"


# -- Channel basics --

def test_channel_has_three_agents(config, base_context):
    channel = build_channel(config, base_context)
    assert len(channel.agents) == 3
    names = [a.name for a in channel.agents]
    assert len(set(names)) == 3


def test_default_slots_produce_correct_personas(config, base_context):
    channel = build_channel(config, base_context)
    agent_ids = [a.persona.id for a in channel.agents]
    assert agent_ids == list(DEFAULT_SLOTS)


def test_build_turn_order_rotates(config, base_context):
    channel = build_channel(config, base_context)

    order1 = channel._build_turn_order()
    assert order1[0][1] == "open"
    assert order1[-1][1] == "close"
    opener1 = order1[0][0]

    channel._segment_opener_idx += 1
    order2 = channel._build_turn_order()
    opener2 = order2[0][0]
    assert opener1 != opener2


# -- Streaming speech --

@pytest.mark.anyio
async def test_stream_content_yields_chunks_from_all_agents(config, base_context):
    channel = build_channel(
        config,
        base_context,
        responses=[
            "Opening line from first agent.",
            "React from second agent.",
            "React from third agent.",
            "Closing from first agent.",
        ],
    )
    channel._sleep_between_segments = AsyncMock()

    generator = channel.stream_content("tech")
    chunks = []
    for _ in range(4):
        chunk = await generator.__anext__()
        chunks.append(chunk)

    assert len(chunks) == 4
    assert all(c.text for c in chunks)
    voice_ids = [c.voice_id for c in chunks]
    assert voice_ids[0] == voice_ids[3]

    channel.cancel()
    await generator.aclose()


# -- Call-in --

@pytest.mark.anyio
async def test_handle_callin_yields_multi_participant_response(config, base_context):
    channel = build_channel(
        config,
        base_context,
        responses=[
            "Great call, you're onto something!",
            "Yeah, I agree with the caller.",
        ],
    )
    channel._current_topic = {
        "source": "headline",
        "text": "AI startup launch sparks privacy backlash overnight",
        "angle": "tech segment",
    }

    chunks = []
    async for chunk in channel.handle_callin("AI is overhyped"):
        chunks.append(chunk)

    assert len(chunks) == 2
    assert channel._callin_count == 1
    assert channel.conversation.had_callers


# -- Swap slot --

def test_swap_slot_changes_agent_persona(config, base_context):
    channel = build_channel(config, base_context)
    old_name = channel.agents[1].name

    current_ids = {a.persona.id for a in channel.agents}
    new_pid = next(pid for pid in PERSONA_REGISTRY if pid not in current_ids)
    new_persona = PERSONA_REGISTRY[new_pid]

    channel.swap_slot(1, new_persona)

    assert channel.agents[1].name == new_persona.name
    assert channel.agents[1].name != old_name


def test_swap_slot_injects_handoff_note(config, base_context):
    channel = build_channel(config, base_context)
    old_name = channel.agents[0].name

    current_ids = {a.persona.id for a in channel.agents}
    new_pid = next(pid for pid in PERSONA_REGISTRY if pid not in current_ids)
    new_persona = PERSONA_REGISTRY[new_pid]

    channel.swap_slot(0, new_persona)

    last_turn = channel.conversation.transcript[-1]
    assert last_turn.speaker_name == "System"
    assert old_name in last_turn.text
    assert new_persona.name in last_turn.text


def test_swap_noop_for_invalid_slot(config, base_context):
    channel = build_channel(config, base_context)
    original_names = [a.name for a in channel.agents]
    channel.swap_slot(99, PERSONA_REGISTRY["brax_ironclad"])
    assert [a.name for a in channel.agents] == original_names


# -- Registry validation --

def test_all_default_slots_exist_in_registry():
    for pid in DEFAULT_SLOTS:
        assert pid in PERSONA_REGISTRY, f"DEFAULT_SLOTS persona '{pid}' not in registry"


def test_all_personas_have_required_fields():
    for pid, persona in PERSONA_REGISTRY.items():
        assert persona.id == pid
        assert persona.name
        assert persona.title
        assert persona.personality
        assert persona.voice_key
