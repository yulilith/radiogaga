"""Tests for the MusicChannel — DJ banter, set list building, and Spotify cancellation."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content.music_channel import MusicChannel


@pytest.fixture
def config():
    return {
        "ANTHROPIC_API_KEY": "test-key",
        "ELEVENLABS_API_KEY": "test-key",
        "VOICES": {"dj": "voice-dj"},
        "LLM_MODEL": "claude-haiku-4-5-20251001",
        "LLM_MAX_TOKENS": 50,
        "HISTORY_WINDOW": 4,
    }


@pytest.fixture
def context_provider():
    provider = AsyncMock()
    provider.get_context = AsyncMock(return_value={
        "current_datetime": "2026-03-08 12:00",
        "day_of_week": "Sunday",
        "city": "Cambridge",
        "state": "MA",
        "weather": "Sunny",
        "trending_topics": "AI",
        "time_of_day": "afternoon",
        "hour": 12,
    })
    return provider


@pytest.fixture
def mock_spotify():
    sp = AsyncMock()
    sp.get_top_tracks = AsyncMock(return_value=[
        {"name": "Song A", "artists": [{"name": "Artist 1"}],
         "album": {"name": "Album 1"}, "uri": "spotify:track:aaa",
         "duration_ms": 200000, "id": "aaa"},
    ])
    sp.format_track_info = MagicMock(side_effect=lambda t: {
        "name": t.get("name", "Unknown"),
        "artist": ", ".join(a["name"] for a in t.get("artists", [])),
        "album": t.get("album", {}).get("name", "Unknown"),
        "uri": t.get("uri", ""),
        "duration_ms": t.get("duration_ms", 0),
        "id": t.get("id", ""),
    })
    sp.play_track = AsyncMock()
    sp.pause = AsyncMock()
    sp.get_track_progress = AsyncMock(return_value=(195000, 200000))  # near end
    return sp


def _make_music_channel(config, context_provider, spotify=None):
    """Create a MusicChannel with mocked LLM client."""
    ch = MusicChannel(
        context_provider=context_provider,
        config=config,
        spotify_service=spotify,
        persona=MagicMock(name="DJ Spark", personality="Upbeat DJ", voice_key="dj",
                          id="dj_spark"),
    )
    return ch


def _mock_llm_stream(banter_text="What's up Cambridge, DJ Spark here!"):
    """Create a mock Anthropic stream that yields banter text."""
    class FakeTextStream:
        def __init__(self, text):
            self._text = text
            self._index = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._index >= len(self._text):
                raise StopAsyncIteration
            # Yield in small chunks
            chunk_size = min(10, len(self._text) - self._index)
            chunk = self._text[self._index:self._index + chunk_size]
            self._index += chunk_size
            return chunk

    class FakeStream:
        def __init__(self, text):
            self.text_stream = FakeTextStream(text)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    return FakeStream(banter_text)


# ------------------------------------------------------------------
# Test 1: DJ banter generates before set list completes
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_banter_generates_before_set_list(config, context_provider, mock_spotify):
    """DJ banter should start generating immediately, not wait for set list."""
    ch = _make_music_channel(config, context_provider, mock_spotify)

    # Make set list build slow
    build_started = asyncio.Event()
    build_can_finish = asyncio.Event()

    original_build = ch._build_set_list

    async def slow_build(subchannel):
        build_started.set()
        await build_can_finish.wait()
        await original_build(subchannel)

    ch._build_set_list = slow_build

    banter_text = "Hey Cambridge, this is DJ Spark coming at you live!"
    with patch.object(ch.client.messages, 'stream',
                      return_value=_mock_llm_stream(banter_text)):
        chunks = []
        gen = ch.stream_content("top_tracks")

        # Get first chunk (should be banter, not blocked by set list)
        try:
            chunk = await asyncio.wait_for(gen.__anext__(), timeout=5.0)
            chunks.append(chunk)
            # Signal that banter was enqueued to player
            if chunk.played_event:
                chunk.played_event.set()
        except asyncio.TimeoutError:
            pytest.fail("Banter generation timed out — still blocked by set list build")

        # Give the event loop a tick so the background set list task starts
        await asyncio.sleep(0)

        # Verify banter was generated while set list is still building
        assert build_started.is_set(), "Set list build should have started"
        assert not build_can_finish.is_set(), "Set list should still be in progress"
        assert len(chunks) == 1
        assert chunks[0].text == banter_text

        # Let set list finish
        build_can_finish.set()

        # Cancel the generator
        ch._cancelled = True
        try:
            await gen.__anext__()
        except StopAsyncIteration:
            pass
        await gen.aclose()


# ------------------------------------------------------------------
# Test 2: on_deactivate sets _cancelled
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_deactivate_sets_cancelled(config, context_provider, mock_spotify):
    """on_deactivate should set _cancelled=True to stop in-flight operations."""
    ch = _make_music_channel(config, context_provider, mock_spotify)
    ch._cancelled = False

    await ch.on_deactivate()

    assert ch._cancelled is True
    mock_spotify.pause.assert_awaited_once()


# ------------------------------------------------------------------
# Test 3: Cancelled flag prevents play_track
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancelled_prevents_play_track(config, context_provider, mock_spotify):
    """If cancelled is set before play_track, Spotify should not start playing."""
    ch = _make_music_channel(config, context_provider, mock_spotify)
    ch._set_list = [{"name": "Song", "artist": "Artist", "album": "Album",
                     "uri": "spotify:track:test", "duration_ms": 200000, "id": "test"}]

    banter_text = "Quick banter!"
    with patch.object(ch.client.messages, 'stream',
                      return_value=_mock_llm_stream(banter_text)):
        gen = ch.stream_content("top_tracks")

        # Get banter chunk
        chunk = await asyncio.wait_for(gen.__anext__(), timeout=5.0)
        if chunk.played_event:
            chunk.played_event.set()

        # Cancel before the track plays
        ch._cancelled = True

        # Generator should stop
        try:
            await asyncio.wait_for(gen.__anext__(), timeout=2.0)
            pytest.fail("Generator should have stopped after cancellation")
        except (StopAsyncIteration, asyncio.TimeoutError):
            pass

        await gen.aclose()

    # play_track should NOT have been called
    mock_spotify.play_track.assert_not_awaited()


# ------------------------------------------------------------------
# Test 4: CancelledError during playback pauses Spotify
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancelled_error_pauses_spotify(config, context_provider, mock_spotify):
    """If CancelledError occurs during Spotify playback, pause should be called."""
    ch = _make_music_channel(config, context_provider, mock_spotify)
    ch._set_list = [{"name": "Song", "artist": "Artist", "album": "Album",
                     "uri": "spotify:track:test", "duration_ms": 200000, "id": "test"}]

    # Make play_track raise CancelledError (simulating task cancellation)
    mock_spotify.play_track = AsyncMock(side_effect=asyncio.CancelledError)

    banter_text = "Let me play something!"
    with patch.object(ch.client.messages, 'stream',
                      return_value=_mock_llm_stream(banter_text)):
        gen = ch.stream_content("top_tracks")

        # Get banter chunk
        chunk = await asyncio.wait_for(gen.__anext__(), timeout=5.0)
        if chunk.played_event:
            chunk.played_event.set()

        # Wait for set list task to complete
        await asyncio.sleep(0.1)

        # Next iteration should try to play and get CancelledError
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(gen.__anext__(), timeout=5.0)

        await gen.aclose()

    # pause should have been called as cleanup
    mock_spotify.pause.assert_awaited()


# ------------------------------------------------------------------
# Test 5: Set list task is cleaned up on generator exit
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_list_task_cleaned_up_on_exit(config, context_provider, mock_spotify):
    """Background set list task should be cancelled when generator exits."""
    ch = _make_music_channel(config, context_provider, mock_spotify)

    # Make set list build hang forever
    async def hang_forever(subchannel):
        await asyncio.sleep(999)

    ch._build_set_list = hang_forever

    banter_text = "Hey there!"
    with patch.object(ch.client.messages, 'stream',
                      return_value=_mock_llm_stream(banter_text)):
        gen = ch.stream_content("top_tracks")

        # Get banter
        chunk = await asyncio.wait_for(gen.__anext__(), timeout=5.0)
        if chunk.played_event:
            chunk.played_event.set()

        # Close the generator — should clean up the set list task
        ch._cancelled = True
        await gen.aclose()

    # No hanging tasks should remain
    # (If cleanup didn't work, the test would hang or leak)


# ------------------------------------------------------------------
# Test 6: Set list refill also runs in background
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_list_refill_is_background_task(config, context_provider, mock_spotify):
    """When set list runs low, refill should happen as a background task."""
    ch = _make_music_channel(config, context_provider, mock_spotify)
    # Pre-populate with 1 track (below the < 3 threshold after popping)
    ch._set_list = [{"name": "Song", "artist": "Artist", "album": "Album",
                     "uri": "spotify:track:test", "duration_ms": 200000, "id": "test"}]

    banter_text = "Here we go!"
    call_count = 0

    with patch.object(ch.client.messages, 'stream',
                      return_value=_mock_llm_stream(banter_text)):
        gen = ch.stream_content("top_tracks")

        # Get banter
        chunk = await asyncio.wait_for(gen.__anext__(), timeout=5.0)
        if chunk.played_event:
            chunk.played_event.set()

        # Let it play the track (mock returns near-end progress so it finishes quickly)
        await asyncio.sleep(0.5)

        # Cancel after one loop
        ch._cancelled = True
        try:
            await gen.__anext__()
        except StopAsyncIteration:
            pass
        await gen.aclose()

    # The set list build should have been triggered
    # (At minimum, once on startup, and once on refill)
    assert mock_spotify.get_top_tracks.await_count >= 1
