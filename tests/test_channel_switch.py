"""Integration tests for the channel switching flow."""

import asyncio
import queue
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content.agent import ContentChunk, PreparedPreview
from hardware.input_controller import InputEvent


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
        self.current_generation = 0
        self.enqueued_chunks = []
        self.play_file_calls = []
        self.hard_stop_calls = []

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, value):
        self._volume = value

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

    def hard_stop(self, reason="interrupt"):
        self.current_generation += 1
        self.hard_stop_calls.append(reason)
        self.clear_buffer()
        return self.current_generation

    def buffer_level(self):
        return self.audio_queue.qsize()

    def enqueue_mp3(self, mp3_bytes, *, generation=None, on_start=None):
        target_generation = self.current_generation if generation is None else generation
        if target_generation != self.current_generation:
            return False
        self.enqueued_chunks.append((mp3_bytes, target_generation))
        if on_start:
            on_start()
        return True

    def play_file(self, path, *, generation=None, on_start=None):
        target_generation = self.current_generation if generation is None else generation
        if target_generation != self.current_generation:
            return False
        self.play_file_calls.append((path, target_generation))
        if on_start:
            on_start()
        return True

    def stop(self):
        pass


class FakeChannel:
    """Minimal channel that yields one chunk and supports preview warming."""

    def __init__(self, name, *, preview_text=None):
        self._name = name
        self._cancelled = False
        self.stream_called_count = 0
        self.preview_calls = []
        self.committed_previews = []
        self.preview_text = preview_text or f"Preview from {name}"
        self.session_memory = None

    def channel_name(self):
        return self._name

    def set_session_memory(self, session_memory):
        self.session_memory = session_memory

    async def stream_content(self, subchannel):
        self.stream_called_count += 1
        while not self._cancelled:
            yield ContentChunk(text=f"Hello from {self._name}", voice_id="v1")
            await asyncio.sleep(0.05)

    async def build_preview(self, subchannel):
        self.preview_calls.append(subchannel)
        return PreparedPreview(text=self.preview_text, voice_id="v1")

    def commit_preview_playback(self, subchannel, preview):
        self.committed_previews.append((subchannel, preview.text))

    def cancel(self):
        self._cancelled = True

    def reset(self):
        self._cancelled = False

    def get_voice_id(self, subchannel):
        return "v1"


class FakeMic:
    def __init__(self):
        self.is_recording = False
        self.recorded_audio = b""

    def start_recording(self):
        self.is_recording = True

    def stop_recording(self):
        self.is_recording = False
        return self.recorded_audio


@pytest.fixture
def fake_discovery():
    discovery = MagicMock()
    discovery.update_channel = MagicMock()
    discovery.register = MagicMock()
    discovery.start_browsing = MagicMock()
    discovery.get_peers_on_channel = MagicMock(return_value=[])
    discovery.shutdown = MagicMock()
    return discovery


@pytest.fixture
def fake_tts():
    tts = AsyncMock()
    tts.synthesize = AsyncMock(return_value=b"\x00" * 100)
    return tts


def make_agent(discovery, tts, channels, *, active_channel="news", active_subchannel="local"):
    from main import RadioAgent

    with patch.object(RadioAgent, "__init__", lambda self: None):
        agent = RadioAgent.__new__(RadioAgent)

    agent._loop = asyncio.get_event_loop()
    agent._generation_task = None
    agent._stop_event = asyncio.Event()
    agent.player = FakeAudioPlayer()
    agent.tts = tts
    agent.stt = AsyncMock()
    agent.discovery = discovery
    agent.leds = MagicMock()
    agent.input = MagicMock(dial_position=50)
    agent.peer_client = MagicMock()
    agent.peer_server = MagicMock()
    agent.mic = FakeMic()
    agent.spotify = AsyncMock()
    agent.channels = channels
    agent.active_channel = active_channel
    agent.active_subchannel = active_subchannel
    agent.session_memory = None
    agent._transition_lock = None
    agent._transition_request_id = 0
    agent._producer_tasks = set()
    agent._preview_cache = {}
    agent._preview_tasks = {}
    agent._ensure_runtime_state()
    return agent


async def cleanup_agent(agent):
    if agent._generation_task:
        agent._generation_task.cancel()
    for task in list(getattr(agent, "_preview_tasks", {}).values()):
        task.cancel()
    for task in list(getattr(agent, "_producer_tasks", set())):
        task.cancel()
    await asyncio.gather(
        *([agent._generation_task] if agent._generation_task else []),
        *list(getattr(agent, "_preview_tasks", {}).values()),
        *list(getattr(agent, "_producer_tasks", set())),
        return_exceptions=True,
    )


@pytest.mark.asyncio
async def test_switch_channel_starts_new_content_loop(fake_discovery, fake_tts):
    agent = make_agent(
        fake_discovery,
        fake_tts,
        {
            "news": FakeChannel("News & Weather"),
            "talkshow": FakeChannel("Talk Show"),
        },
    )

    try:
        agent._generation_task = asyncio.create_task(agent._content_loop())
        await asyncio.sleep(0.1)

        assert agent.channels["news"].stream_called_count == 1

        await agent._switch_channel("talkshow")
        await asyncio.sleep(0.1)

        assert agent.active_channel == "talkshow"
        assert agent.channels["talkshow"].stream_called_count == 1
        assert agent.channels["talkshow"]._cancelled is False
    finally:
        await cleanup_agent(agent)


@pytest.mark.asyncio
async def test_switch_channel_clears_buffer(fake_discovery, fake_tts):
    agent = make_agent(
        fake_discovery,
        fake_tts,
        {
            "news": FakeChannel("News & Weather"),
            "talkshow": FakeChannel("Talk Show"),
        },
    )

    try:
        for _ in range(5):
            agent.player.audio_queue.put(b"\x00" * 100)
        assert agent.player.buffer_level() == 5

        await agent._switch_channel("talkshow")

        assert agent.player.buffer_level() == 0
        assert agent.player.hard_stop_calls
    finally:
        await cleanup_agent(agent)


@pytest.mark.asyncio
async def test_switch_channel_cancels_old_generation(fake_discovery, fake_tts):
    agent = make_agent(
        fake_discovery,
        fake_tts,
        {
            "news": FakeChannel("News & Weather"),
            "talkshow": FakeChannel("Talk Show"),
        },
    )

    try:
        agent._generation_task = asyncio.create_task(agent._content_loop())
        await asyncio.sleep(0.1)

        await agent._switch_channel("talkshow")

        assert agent.channels["news"]._cancelled is True
    finally:
        await cleanup_agent(agent)


@pytest.mark.asyncio
async def test_switch_interrupts_audio_before_old_task_finishes(fake_discovery, fake_tts):
    agent = make_agent(
        fake_discovery,
        fake_tts,
        {
            "news": FakeChannel("News"),
            "talkshow": FakeChannel("Talk Show"),
        },
    )
    release_old_task = asyncio.Event()

    async def stubborn_generation():
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await release_old_task.wait()
            raise

    try:
        agent._generation_task = asyncio.create_task(stubborn_generation())
        switch_task = asyncio.create_task(agent._switch_channel("talkshow"))
        await asyncio.sleep(0.05)

        assert agent.player.hard_stop_calls == ["switch:news->talkshow"]
        assert switch_task.done() is False

        release_old_task.set()
        await switch_task
    finally:
        await cleanup_agent(agent)


@pytest.mark.asyncio
async def test_rapid_channel_switching(fake_discovery, fake_tts):
    channels = {
        "news": FakeChannel("News"),
        "talkshow": FakeChannel("Talk Show"),
        "sports": FakeChannel("Sports"),
        "dj": FakeChannel("DJ"),
    }
    agent = make_agent(fake_discovery, fake_tts, channels)

    try:
        agent._generation_task = asyncio.create_task(agent._content_loop())
        await asyncio.sleep(0.05)

        for channel in ["talkshow", "sports", "dj", "news", "talkshow"]:
            await agent._switch_channel(channel)
            await asyncio.sleep(0.02)

        assert agent.active_channel == "talkshow"
        assert agent._generation_task is not None
        assert not agent._generation_task.done()
        assert channels["talkshow"].stream_called_count >= 1
    finally:
        await cleanup_agent(agent)


@pytest.mark.asyncio
async def test_discovery_update_channel_failure_does_not_block_content(fake_tts):
    failing_discovery = MagicMock()
    failing_discovery.update_channel = MagicMock(side_effect=RuntimeError("mDNS down"))
    failing_discovery.get_peers_on_channel = MagicMock(return_value=[])

    agent = make_agent(
        failing_discovery,
        fake_tts,
        {
            "news": FakeChannel("News"),
            "talkshow": FakeChannel("Talk Show"),
        },
    )

    try:
        await agent._switch_channel("talkshow")
        await asyncio.sleep(0.1)

        assert agent.active_channel == "talkshow"
        assert agent.channels["talkshow"].stream_called_count >= 1
        assert agent._generation_task is not None
        assert not agent._generation_task.done()
    finally:
        await cleanup_agent(agent)


@pytest.mark.asyncio
async def test_preview_cache_warms_on_switch_away_and_commits_only_on_playback(fake_discovery, fake_tts):
    news = FakeChannel("News", preview_text="News preview")
    talkshow = FakeChannel("Talk Show", preview_text="Talkshow preview")
    agent = make_agent(
        fake_discovery,
        fake_tts,
        {
            "news": news,
            "talkshow": talkshow,
        },
    )

    try:
        await agent._warm_preview("talkshow", "philosophy")

        assert talkshow.committed_previews == []
        assert agent.session_memory.recent_channel_items("talkshow", "philosophy") == []

        await agent._switch_channel("talkshow")
        await asyncio.sleep(0.1)

        assert talkshow.committed_previews == [("philosophy", "Talkshow preview")]
        heard = agent.session_memory.recent_channel_items("talkshow", "philosophy")
        assert heard[0] == "Talkshow preview"
        assert "Talkshow preview" in heard

        await asyncio.gather(*list(agent._preview_tasks.values()), return_exceptions=True)
        assert news.preview_calls == ["local"]
    finally:
        await cleanup_agent(agent)


@pytest.mark.asyncio
async def test_stale_forwarded_callin_cannot_enqueue_after_generation_changes(fake_discovery):
    tts_release = asyncio.Event()

    async def delayed_synthesize(_text, _voice_id):
        await tts_release.wait()
        return b"late-audio"

    fake_tts = AsyncMock()
    fake_tts.synthesize = AsyncMock(side_effect=delayed_synthesize)

    class ForwardingChannel(FakeChannel):
        async def handle_callin(self, transcript):
            yield ContentChunk(text=f"Replying to {transcript}", voice_id="v1")

    agent = make_agent(
        fake_discovery,
        fake_tts,
        {
            "news": ForwardingChannel("News"),
            "talkshow": FakeChannel("Talk Show"),
        },
    )

    try:
        call_task = asyncio.create_task(agent._handle_callin_forward({"transcript": "hello"}))
        await asyncio.sleep(0.05)

        agent.player.hard_stop("switch")
        tts_release.set()
        await call_task

        assert agent.player.enqueued_chunks == []
    finally:
        await cleanup_agent(agent)


@pytest.mark.asyncio
async def test_callin_start_interrupts_audio_and_stays_silent_while_recording(fake_discovery, fake_tts):
    agent = make_agent(
        fake_discovery,
        fake_tts,
        {
            "news": FakeChannel("News"),
        },
    )

    try:
        agent._generation_task = asyncio.create_task(agent._content_loop())
        await asyncio.sleep(0.1)
        enqueued_before = len(agent.player.enqueued_chunks)

        await agent._handle_event(InputEvent(event_type="callin_start"))
        await asyncio.sleep(0.1)

        assert agent.player.hard_stop_calls[-1] == "callin:start"
        assert agent.mic.is_recording is True
        assert agent._callin_active is True
        assert agent._generation_task is None
        assert len(agent.player.enqueued_chunks) == enqueued_before
    finally:
        await cleanup_agent(agent)


@pytest.mark.asyncio
async def test_callin_stop_replies_before_resuming_content(fake_discovery):
    class CallinChannel(FakeChannel):
        async def handle_callin(self, transcript):
            yield ContentChunk(text=f"Replying to {transcript}", voice_id="v1")

    fake_tts = AsyncMock()

    async def synthesize(text, _voice_id):
        return text.encode("utf-8")

    fake_tts.synthesize = AsyncMock(side_effect=synthesize)

    agent = make_agent(
        fake_discovery,
        fake_tts,
        {
            "news": CallinChannel("News"),
        },
    )
    agent.stt.transcribe = AsyncMock(return_value="my comment")

    try:
        agent._generation_task = asyncio.create_task(agent._content_loop())
        await asyncio.sleep(0.1)
        before_callin = len(agent.player.enqueued_chunks)

        await agent._handle_event(InputEvent(event_type="callin_start"))
        agent.mic.recorded_audio = b"wav-data"
        callin_task = agent._track_task(asyncio.create_task(agent._handle_callin()))
        await callin_task
        await asyncio.sleep(0.1)

        payloads = [payload.decode("utf-8") for payload, _generation in agent.player.enqueued_chunks]
        assert payloads[before_callin] == "Replying to my comment"
        assert payloads[before_callin + 1] == "Hello from News"
        assert agent._generation_task is not None
        assert agent._generation_task.done() is False
    finally:
        await cleanup_agent(agent)
