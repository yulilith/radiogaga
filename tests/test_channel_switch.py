"""Integration tests for the channel switching flow.

Tests the full _switch_channel path without real APIs by mocking
the external services (Anthropic, ElevenLabs, Spotify, mDNS).
"""

import asyncio
import queue
from unittest.mock import AsyncMock, MagicMock, patch

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
    """Minimal channel that yields one chunk then waits."""

    def __init__(self, name):
        self._name = name
        self._cancelled = False
        self.stream_called_count = 0

    def channel_name(self):
        return self._name

    async def stream_content(self, subchannel):
        self.stream_called_count += 1
        while not self._cancelled:
            yield ContentChunk(text=f"Hello from {self._name}", voice_id="v1")
            await asyncio.sleep(0.05)

    def cancel(self):
        self._cancelled = True

    def reset(self):
        self._cancelled = False

    def get_voice_id(self, sub):
        return "v1"


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


@pytest.mark.asyncio
async def test_switch_channel_starts_new_content_loop(config, fake_discovery, fake_tts):
    """After switching channels, the new channel's stream_content must be called."""
    from main import RadioAgent

    with patch.object(RadioAgent, "__init__", lambda self: None):
        agent = RadioAgent.__new__(RadioAgent)
        agent._loop = asyncio.get_event_loop()
        agent._generation_task = None
        agent._stop_event = asyncio.Event()
        agent.player = FakeAudioPlayer()
        agent.tts = fake_tts
        agent.discovery = fake_discovery
        agent.leds = MagicMock()
        agent.input = MagicMock(dial_position=50)
        agent.peer_client = MagicMock()

        news = FakeChannel("News & Weather")
        talkshow = FakeChannel("Talk Show")
        agent.channels = {"news": news, "talkshow": talkshow}
        agent.active_channel = "news"
        agent.active_subchannel = "local"

        agent._generation_task = asyncio.create_task(agent._content_loop())
        await asyncio.sleep(0.1)

        assert news.stream_called_count == 1

        await agent._switch_channel("talkshow")
        await asyncio.sleep(0.15)

        assert agent.active_channel == "talkshow"
        assert talkshow.stream_called_count == 1
        assert talkshow._cancelled is False

        if agent._generation_task:
            agent._generation_task.cancel()
            try:
                await agent._generation_task
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.asyncio
async def test_switch_channel_clears_buffer(config, fake_discovery, fake_tts):
    """Switching channels must clear the audio buffer."""
    from main import RadioAgent

    with patch.object(RadioAgent, "__init__", lambda self: None):
        agent = RadioAgent.__new__(RadioAgent)
        agent._loop = asyncio.get_event_loop()
        agent._generation_task = None
        agent._stop_event = asyncio.Event()
        agent.player = FakeAudioPlayer()
        agent.tts = fake_tts
        agent.discovery = fake_discovery
        agent.leds = MagicMock()
        agent.input = MagicMock(dial_position=50)
        agent.peer_client = MagicMock()

        news = FakeChannel("News & Weather")
        talkshow = FakeChannel("Talk Show")
        agent.channels = {"news": news, "talkshow": talkshow}
        agent.active_channel = "news"
        agent.active_subchannel = "local"

        for i in range(5):
            agent.player.audio_queue.put(b"\x00" * 100)
        assert agent.player.buffer_level() == 5

        await agent._switch_channel("talkshow")

        assert agent.player.buffer_level() == 0

        if agent._generation_task:
            agent._generation_task.cancel()
            try:
                await agent._generation_task
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.asyncio
async def test_switch_channel_cancels_old_generation(config, fake_discovery, fake_tts):
    """Switching channels must cancel the previous content loop."""
    from main import RadioAgent

    with patch.object(RadioAgent, "__init__", lambda self: None):
        agent = RadioAgent.__new__(RadioAgent)
        agent._loop = asyncio.get_event_loop()
        agent._generation_task = None
        agent._stop_event = asyncio.Event()
        agent.player = FakeAudioPlayer()
        agent.tts = fake_tts
        agent.discovery = fake_discovery
        agent.leds = MagicMock()
        agent.input = MagicMock(dial_position=50)
        agent.peer_client = MagicMock()

        news = FakeChannel("News & Weather")
        talkshow = FakeChannel("Talk Show")
        agent.channels = {"news": news, "talkshow": talkshow}
        agent.active_channel = "news"
        agent.active_subchannel = "local"

        agent._generation_task = asyncio.create_task(agent._content_loop())
        await asyncio.sleep(0.1)

        await agent._switch_channel("talkshow")

        assert news._cancelled is True

        if agent._generation_task:
            agent._generation_task.cancel()
            try:
                await agent._generation_task
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.asyncio
async def test_rapid_channel_switching(config, fake_discovery, fake_tts):
    """Rapidly switching channels should not leave the agent in a broken state."""
    from main import RadioAgent

    with patch.object(RadioAgent, "__init__", lambda self: None):
        agent = RadioAgent.__new__(RadioAgent)
        agent._loop = asyncio.get_event_loop()
        agent._generation_task = None
        agent._stop_event = asyncio.Event()
        agent.player = FakeAudioPlayer()
        agent.tts = fake_tts
        agent.discovery = fake_discovery
        agent.leds = MagicMock()
        agent.input = MagicMock(dial_position=50)
        agent.peer_client = MagicMock()

        channels = {
            "news": FakeChannel("News"),
            "talkshow": FakeChannel("Talk Show"),
            "sports": FakeChannel("Sports"),
            "dj": FakeChannel("DJ"),
        }
        agent.channels = channels
        agent.active_channel = "news"
        agent.active_subchannel = "local"

        agent._generation_task = asyncio.create_task(agent._content_loop())
        await asyncio.sleep(0.05)

        for ch in ["talkshow", "sports", "dj", "news", "talkshow"]:
            await agent._switch_channel(ch)
            await asyncio.sleep(0.02)

        assert agent.active_channel == "talkshow"
        assert agent._generation_task is not None
        assert not agent._generation_task.done()

        await asyncio.sleep(0.1)
        assert channels["talkshow"].stream_called_count >= 1

        agent._generation_task.cancel()
        try:
            await agent._generation_task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_discovery_update_channel_failure_does_not_block_content(
    config, fake_tts
):
    """Even if discovery.update_channel raises, channel switch must still start content."""
    from main import RadioAgent

    failing_discovery = MagicMock()
    failing_discovery.update_channel = MagicMock(side_effect=RuntimeError("mDNS down"))
    failing_discovery.get_peers_on_channel = MagicMock(return_value=[])

    with patch.object(RadioAgent, "__init__", lambda self: None):
        agent = RadioAgent.__new__(RadioAgent)
        agent._loop = asyncio.get_event_loop()
        agent._generation_task = None
        agent._stop_event = asyncio.Event()
        agent.player = FakeAudioPlayer()
        agent.tts = fake_tts
        agent.discovery = failing_discovery
        agent.leds = MagicMock()
        agent.input = MagicMock(dial_position=50)
        agent.peer_client = MagicMock()

        news = FakeChannel("News")
        talkshow = FakeChannel("Talk Show")
        agent.channels = {"news": news, "talkshow": talkshow}
        agent.active_channel = "news"
        agent.active_subchannel = "local"

        await agent._switch_channel("talkshow")
        await asyncio.sleep(0.15)

        assert agent.active_channel == "talkshow"
        assert talkshow.stream_called_count >= 1, (
            "Content loop must start even when discovery fails"
        )
        assert agent._generation_task is not None
        assert not agent._generation_task.done()

        agent._generation_task.cancel()
        try:
            await agent._generation_task
        except (asyncio.CancelledError, Exception):
            pass
