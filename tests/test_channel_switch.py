"""Integration tests for the channel switching flow.

Tests the full _switch_channel path without real APIs by mocking
the external services (Anthropic, ElevenLabs, Spotify, mDNS).
"""

import asyncio
import queue
from unittest.mock import AsyncMock, MagicMock

import pytest

from content.agent import ContentChunk


@pytest.fixture
def config():
    return {
        "ANTHROPIC_API_KEY": "test-key",
        "ELEVENLABS_API_KEY": "test-key",
        "VOICES": {
            "news": "voice-news",
            "talkshow": "voice-talk",
            "sports": "voice-sports",
            "dj": "voice-dj",
        },
        "LLM_MODEL": "claude-haiku-4-5-20251001",
        "LLM_MAX_TOKENS": 50,
        "HISTORY_WINDOW": 4,
        "CALLIN_MAX_SECONDS": 15,
        "AGENT_PORT": 19765,
    }


class FakeAudioPlayer:
    """Minimal AudioPlayer substitute that doesn't touch real audio hardware."""

    def __init__(self):
        self.audio_queue = queue.Queue(maxsize=100)
        self._volume = 0.7
        self._muted = False
        self.started = False
        self.enqueued_chunks = []

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, v):
        self._volume = v

    @property
    def muted(self):
        return self._muted

    def toggle_mute(self):
        self._muted = not self._muted

    def start(self):
        self.started = True

    def interrupt(self):
        self.clear_buffer()

    def start_static(self):
        pass

    def stop_static(self):
        pass

    def clear_buffer(self):
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def buffer_level(self):
        return self.audio_queue.qsize()

    def enqueue_mp3(self, mp3_bytes):
        self.enqueued_chunks.append(mp3_bytes)

    def play_file(self, path):
        pass

    def stop(self):
        pass


class FakeChannel:
    """Minimal channel that produces chunks via the background queue pattern."""

    def __init__(self, name):
        self._name = name
        self._cancelled = False
        self._on_air = False
        self._subchannel = ""
        self._output_queue: asyncio.Queue[ContentChunk] = asyncio.Queue(maxsize=2)
        self._warm_queue: asyncio.Queue[ContentChunk] = asyncio.Queue(maxsize=1)
        self._warm_audio: list[bytes] = []
        self._bg_task: asyncio.Task | None = None
        self.chunks_produced = 0

    def channel_name(self):
        return self._name

    def set_on_air(self, on_air):
        self._on_air = on_air

    def set_subchannel(self, sub):
        self._subchannel = sub

    def interrupt(self, callin=None):
        self._cancelled = True

    def cancel(self):
        self.interrupt()

    def reset(self):
        self._cancelled = False

    def get_voice_id(self, sub):
        return "v1"

    async def on_activate(self):
        pass

    async def on_deactivate(self):
        pass

    async def generate_warm_preview(self):
        return []

    async def run_background(self):
        while True:
            self._cancelled = False
            try:
                while not self._cancelled:
                    chunk = ContentChunk(text=f"Hello from {self._name}", voice_id="v1")
                    self.chunks_produced += 1
                    if self._on_air:
                        await self._output_queue.put(chunk)
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break


@pytest.fixture
def fake_discovery():
    d = MagicMock()
    d.update_channel = MagicMock()
    d.register = MagicMock()
    d.start_browsing = MagicMock()
    d.get_peers_on_channel = MagicMock(return_value=[])
    d.shutdown = MagicMock()
    return d


@pytest.fixture
def fake_tts():
    tts = AsyncMock()
    tts.synthesize = AsyncMock(return_value=b"\x00" * 100)
    return tts


def _make_agent(fake_discovery, fake_tts, channels_dict):
    """Wire up a RadioAgent with fake components (no real __init__)."""
    from main import RadioAgent

    agent = RadioAgent.__new__(RadioAgent)
    agent._loop = asyncio.get_event_loop()
    agent._audio_consumer_task = None
    agent._channel_tasks = {}
    agent._warm_tasks = {}
    agent._stop_event = asyncio.Event()
    agent.ALWAYS_ON_CHANNELS = set()
    agent.ON_DEMAND_CHANNELS = set()
    agent.player = FakeAudioPlayer()
    agent.tts = fake_tts
    agent.discovery = fake_discovery
    agent.leds = MagicMock()
    agent.display = MagicMock()
    agent.input = MagicMock(dial_position=50, volume=70)
    agent.peer_client = MagicMock()
    agent.channels = channels_dict
    agent.active_channel = list(channels_dict.keys())[0]
    agent.active_subchannel = "local"
    return agent


async def _cleanup_agent(agent):
    if agent._audio_consumer_task and not agent._audio_consumer_task.done():
        agent._audio_consumer_task.cancel()
        try:
            await agent._audio_consumer_task
        except (asyncio.CancelledError, Exception):
            pass
    for t in agent._channel_tasks.values():
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_switch_channel_sets_new_channel_on_air(config, fake_discovery, fake_tts):
    """After switching channels, the new channel must be on-air and producing."""
    news = FakeChannel("News & Weather")
    talkshow = FakeChannel("Talk Show")
    agent = _make_agent(fake_discovery, fake_tts, {"news": news, "talkshow": talkshow})

    news.set_on_air(True)
    agent._channel_tasks["news"] = asyncio.create_task(news.run_background())
    agent._channel_tasks["talkshow"] = asyncio.create_task(talkshow.run_background())
    agent._audio_consumer_task = asyncio.create_task(agent._audio_consumer())
    await asyncio.sleep(0.15)

    await agent._switch_channel("talkshow")
    await asyncio.sleep(0.15)

    assert agent.active_channel == "talkshow"
    assert talkshow._on_air is True
    assert news._on_air is False
    assert talkshow.chunks_produced >= 1

    await _cleanup_agent(agent)


@pytest.mark.asyncio
async def test_switch_channel_clears_buffer(config, fake_discovery, fake_tts):
    """Switching channels must clear the audio buffer."""
    news = FakeChannel("News & Weather")
    talkshow = FakeChannel("Talk Show")
    agent = _make_agent(fake_discovery, fake_tts, {"news": news, "talkshow": talkshow})

    for _ in range(5):
        agent.player.audio_queue.put(b"\x00" * 100)
    assert agent.player.buffer_level() == 5

    agent._channel_tasks["news"] = asyncio.create_task(news.run_background())
    agent._channel_tasks["talkshow"] = asyncio.create_task(talkshow.run_background())

    await agent._switch_channel("talkshow")

    assert agent.player.buffer_level() == 0

    await _cleanup_agent(agent)


@pytest.mark.asyncio
async def test_switch_channel_takes_old_off_air(config, fake_discovery, fake_tts):
    """Switching channels must set the old channel off-air."""
    news = FakeChannel("News & Weather")
    talkshow = FakeChannel("Talk Show")
    agent = _make_agent(fake_discovery, fake_tts, {"news": news, "talkshow": talkshow})

    news.set_on_air(True)
    agent._channel_tasks["news"] = asyncio.create_task(news.run_background())
    agent._channel_tasks["talkshow"] = asyncio.create_task(talkshow.run_background())
    agent._audio_consumer_task = asyncio.create_task(agent._audio_consumer())
    await asyncio.sleep(0.1)

    await agent._switch_channel("talkshow")

    assert news._on_air is False
    assert talkshow._on_air is True

    await _cleanup_agent(agent)


@pytest.mark.asyncio
async def test_rapid_channel_switching(config, fake_discovery, fake_tts):
    """Rapidly switching channels should not leave the agent in a broken state."""
    channels = {
        "dailybrief": FakeChannel("Daily Brief"),
        "talkshow": FakeChannel("Talk Show"),
        "music": FakeChannel("Music"),
        "memos": FakeChannel("Memos"),
    }
    agent = _make_agent(fake_discovery, fake_tts, channels)

    channels["dailybrief"].set_on_air(True)
    for cid, ch in channels.items():
        agent._channel_tasks[cid] = asyncio.create_task(ch.run_background())
    agent._audio_consumer_task = asyncio.create_task(agent._audio_consumer())
    await asyncio.sleep(0.05)

    for ch in ["talkshow", "music", "memos", "dailybrief", "talkshow"]:
        await agent._switch_channel(ch)
        await asyncio.sleep(0.02)

    assert agent.active_channel == "talkshow"
    assert agent._audio_consumer_task is not None
    assert not agent._audio_consumer_task.done()

    await asyncio.sleep(0.15)
    assert channels["talkshow"].chunks_produced >= 1

    await _cleanup_agent(agent)


@pytest.mark.asyncio
async def test_discovery_update_channel_failure_does_not_block_content(
    config, fake_tts
):
    """Even if discovery.update_channel raises, channel switch must still start content."""
    failing_discovery = MagicMock()
    failing_discovery.update_channel = MagicMock(side_effect=RuntimeError("mDNS down"))
    failing_discovery.get_peers_on_channel = MagicMock(return_value=[])

    news = FakeChannel("News")
    talkshow = FakeChannel("Talk Show")
    agent = _make_agent(failing_discovery, fake_tts, {"news": news, "talkshow": talkshow})

    news.set_on_air(True)
    agent._channel_tasks["news"] = asyncio.create_task(news.run_background())
    agent._channel_tasks["talkshow"] = asyncio.create_task(talkshow.run_background())
    agent._audio_consumer_task = asyncio.create_task(agent._audio_consumer())

    await agent._switch_channel("talkshow")
    await asyncio.sleep(0.15)

    assert agent.active_channel == "talkshow"
    assert talkshow._on_air is True
    assert agent._audio_consumer_task is not None
    assert not agent._audio_consumer_task.done()

    await _cleanup_agent(agent)
