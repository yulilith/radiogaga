"""Unit tests for audio/spotify_service.py.

All Spotify Web API calls are mocked — no network or Premium account needed.
"""

import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from audio.spotify_service import SpotifyService, LIBRESPOT_DEVICE_KEYWORDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_device(id="dev-1", name="Pi Speaker", is_active=True):
    return {"id": id, "name": name, "is_active": is_active}


def _make_track(id="t1", name="Test Song", artist="Test Artist",
                album="Test Album", uri="spotify:track:t1", duration_ms=200000):
    return {
        "id": id,
        "name": name,
        "artists": [{"id": f"a-{id}", "name": artist}],
        "album": {"name": album},
        "uri": uri,
        "duration_ms": duration_ms,
    }


def _make_artist(id="a1", name="Test Artist", genres=None):
    return {"id": id, "name": name, "genres": genres or ["pop", "indie"]}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_sp():
    """A mocked spotipy.Spotify instance."""
    sp = MagicMock()
    sp.devices.return_value = {"devices": [_make_device()]}
    sp.current_user_top_tracks.return_value = {
        "items": [_make_track(id=f"t{i}", name=f"Song {i}") for i in range(5)]
    }
    sp.current_user_recently_played.return_value = {
        "items": [{"track": _make_track(id=f"r{i}")} for i in range(3)]
    }
    sp.current_user_top_artists.return_value = {
        "items": [_make_artist(id=f"a{i}", name=f"Artist {i}",
                               genres=["pop", "rock"]) for i in range(3)]
    }
    sp.search.return_value = {
        "tracks": {"items": [_make_track(id=f"s{i}") for i in range(5)]}
    }
    sp.current_user_saved_tracks.return_value = {
        "items": [{"track": _make_track(id=f"sv{i}")} for i in range(5)]
    }
    sp.current_playback.return_value = {
        "is_playing": True,
        "item": _make_track(duration_ms=240000),
        "progress_ms": 120000,
    }
    sp.start_playback.return_value = None
    sp.pause_playback.return_value = None
    sp.add_to_queue.return_value = None
    sp.transfer_playback.return_value = None
    return sp


def _build_svc(mock_sp, mode="mac"):
    with patch("audio.spotify_service.SpotifyOAuth"):
        service = SpotifyService(
            client_id="fake-id",
            client_secret="fake-secret",
            playback_mode=mode,
        )
    service.sp = mock_sp
    return service


@pytest.fixture
def svc(mock_sp):
    """SpotifyService in mac mode with mocked auth and client."""
    return _build_svc(mock_sp, mode="mac")


@pytest.fixture
def pi_svc(mock_sp):
    """SpotifyService in pi mode with mocked auth and client."""
    return _build_svc(mock_sp, mode="pi")


# ---------------------------------------------------------------------------
# Device discovery — _pick_device
# ---------------------------------------------------------------------------

class TestPickDevice:
    def test_mac_mode_prefers_active_device(self, svc):
        devices = [
            _make_device(id="inactive", is_active=False),
            _make_device(id="active", is_active=True),
        ]
        chosen = svc._pick_device(devices)
        assert chosen["id"] == "active"

    def test_mac_mode_falls_back_to_first(self, svc):
        devices = [
            _make_device(id="first", is_active=False),
            _make_device(id="second", is_active=False),
        ]
        chosen = svc._pick_device(devices)
        assert chosen["id"] == "first"

    def test_pi_mode_prefers_librespot(self, pi_svc):
        devices = [
            _make_device(id="desktop", name="MacBook", is_active=True),
            _make_device(id="pi", name="raspotify (RadioAgent)", is_active=False),
        ]
        chosen = pi_svc._pick_device(devices)
        assert chosen["id"] == "pi"

    def test_pi_mode_falls_back_to_active_if_no_librespot(self, pi_svc):
        devices = [
            _make_device(id="desktop", name="MacBook", is_active=True),
        ]
        chosen = pi_svc._pick_device(devices)
        assert chosen["id"] == "desktop"

    def test_empty_devices_returns_none(self, svc):
        assert svc._pick_device([]) is None


class TestIsLibrespotDevice:
    @pytest.mark.parametrize("name", [
        "librespot", "raspotify", "RadioAgent Pi", "Raspberry Pi",
        "My librespot player", "RASPOTIFY",
    ])
    def test_matches_librespot_names(self, name):
        assert SpotifyService._is_librespot_device({"name": name})

    @pytest.mark.parametrize("name", [
        "MacBook Pro", "iPhone", "Web Player", "Living Room Speaker",
    ])
    def test_rejects_non_librespot_names(self, name):
        assert not SpotifyService._is_librespot_device({"name": name})


class TestGetDeviceId:
    def test_returns_best_device(self, svc, mock_sp):
        mock_sp.devices.return_value = {
            "devices": [
                _make_device(id="inactive-1", is_active=False),
                _make_device(id="active-1", is_active=True),
            ]
        }
        assert svc.get_device_id() == "active-1"
        assert svc._device_id == "active-1"

    def test_returns_none_when_no_devices(self, svc, mock_sp):
        mock_sp.devices.return_value = {"devices": []}
        assert svc.get_device_id() is None

    def test_raises_on_api_error(self, svc, mock_sp):
        mock_sp.devices.side_effect = Exception("network error")
        with pytest.raises(Exception, match="network error"):
            svc.get_device_id()


# ---------------------------------------------------------------------------
# ensure_device
# ---------------------------------------------------------------------------

class TestEnsureDevice:
    @pytest.mark.asyncio
    async def test_mac_mode_finds_desktop_app(self, svc, mock_sp):
        mock_sp.devices.return_value = {
            "devices": [_make_device(id="desktop", name="MacBook")]
        }
        device_id = await svc.ensure_device(retries=1)
        assert device_id == "desktop"
        assert svc._device_ready is True
        mock_sp.transfer_playback.assert_called_once()

    @pytest.mark.asyncio
    async def test_pi_mode_finds_librespot(self, pi_svc, mock_sp):
        mock_sp.devices.return_value = {
            "devices": [
                _make_device(id="desktop", name="MacBook", is_active=True),
                _make_device(id="pi-dev", name="raspotify", is_active=False),
            ]
        }
        with patch.object(SpotifyService, "is_librespot_running", return_value=True):
            device_id = await pi_svc.ensure_device(retries=1)
        assert device_id == "pi-dev"

    @pytest.mark.asyncio
    async def test_pi_mode_starts_librespot_if_not_running(self, pi_svc, mock_sp):
        mock_sp.devices.return_value = {
            "devices": [_make_device(id="pi-dev", name="raspotify")]
        }
        with patch.object(SpotifyService, "is_librespot_running", return_value=False) as mock_check, \
             patch.object(SpotifyService, "start_librespot", return_value=True) as mock_start:
            device_id = await pi_svc.ensure_device(retries=1, delay=0.01)
        mock_check.assert_called_once()
        mock_start.assert_called_once()
        assert device_id == "pi-dev"

    @pytest.mark.asyncio
    async def test_returns_none_after_retries_exhausted(self, svc, mock_sp):
        mock_sp.devices.return_value = {"devices": []}
        device_id = await svc.ensure_device(retries=2, delay=0.01)
        assert device_id is None
        assert svc._device_ready is False

    @pytest.mark.asyncio
    async def test_skips_discovery_if_already_ready(self, svc, mock_sp):
        svc._device_id = "cached"
        svc._device_ready = True
        mock_sp.devices.return_value = {
            "devices": [_make_device(id="cached")]
        }
        device_id = await svc.ensure_device()
        assert device_id == "cached"
        mock_sp.transfer_playback.assert_not_called()

    @pytest.mark.asyncio
    async def test_re_discovers_if_cached_device_gone(self, svc, mock_sp):
        svc._device_id = "old-gone"
        svc._device_ready = True
        mock_sp.devices.return_value = {
            "devices": [_make_device(id="new-dev", name="New Device")]
        }
        device_id = await svc.ensure_device(retries=1)
        assert device_id == "new-dev"

    @pytest.mark.asyncio
    async def test_transfer_failure_is_non_fatal(self, svc, mock_sp):
        mock_sp.devices.return_value = {
            "devices": [_make_device(id="dev-1")]
        }
        mock_sp.transfer_playback.side_effect = Exception("transfer failed")
        device_id = await svc.ensure_device(retries=1)
        assert device_id == "dev-1"
        assert svc._device_ready is True

    @pytest.mark.asyncio
    async def test_api_403_returns_none_not_raises(self, svc, mock_sp):
        """A 403 (user not registered) must not crash — returns None."""
        mock_sp.devices.side_effect = Exception(
            "http status: 403, code: -1 - The user is not registered"
        )
        device_id = await svc.ensure_device(retries=1, delay=0.01)
        assert device_id is None
        assert svc._device_ready is False

    @pytest.mark.asyncio
    async def test_api_error_on_cached_check_returns_none(self, svc, mock_sp):
        """If API fails while verifying a cached device, return None."""
        svc._device_id = "old-dev"
        svc._device_ready = True
        mock_sp.devices.side_effect = Exception("network timeout")
        device_id = await svc.ensure_device(retries=1, delay=0.01)
        assert device_id is None
        assert svc._device_ready is False


# ---------------------------------------------------------------------------
# Librespot health (static methods, mocked subprocess)
# ---------------------------------------------------------------------------

class TestLibrespotHealth:
    def test_is_running_true(self):
        with patch("audio.spotify_service.shutil.which", return_value="/usr/bin/systemctl"), \
             patch("audio.spotify_service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert SpotifyService.is_librespot_running() is True

    def test_is_running_false_not_active(self):
        with patch("audio.spotify_service.shutil.which", return_value="/usr/bin/systemctl"), \
             patch("audio.spotify_service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=3)
            assert SpotifyService.is_librespot_running() is False

    def test_is_running_false_no_systemctl(self):
        with patch("audio.spotify_service.shutil.which", return_value=None):
            assert SpotifyService.is_librespot_running() is False

    def test_start_librespot_success(self):
        with patch("audio.spotify_service.shutil.which", return_value="/usr/bin/systemctl"), \
             patch("audio.spotify_service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert SpotifyService.start_librespot() is True

    def test_start_librespot_no_systemctl(self):
        with patch("audio.spotify_service.shutil.which", return_value=None):
            assert SpotifyService.start_librespot() is False

    def test_start_librespot_command_fails(self):
        import subprocess
        with patch("audio.spotify_service.shutil.which", return_value="/usr/bin/systemctl"), \
             patch("audio.spotify_service.subprocess.run",
                   side_effect=subprocess.CalledProcessError(1, "cmd")):
            assert SpotifyService.start_librespot() is False


# ---------------------------------------------------------------------------
# User taste APIs
# ---------------------------------------------------------------------------

class TestTopTracks:
    @pytest.mark.asyncio
    async def test_returns_items(self, svc, mock_sp):
        tracks = await svc.get_top_tracks(limit=5)
        assert len(tracks) == 5
        mock_sp.current_user_top_tracks.assert_called_once_with(
            limit=5, time_range="medium_term"
        )

    @pytest.mark.asyncio
    async def test_custom_time_range(self, svc, mock_sp):
        await svc.get_top_tracks(limit=3, time_range="short_term")
        mock_sp.current_user_top_tracks.assert_called_once_with(
            limit=3, time_range="short_term"
        )

    @pytest.mark.asyncio
    async def test_raises_on_error(self, svc, mock_sp):
        mock_sp.current_user_top_tracks.side_effect = Exception("403 forbidden")
        with pytest.raises(Exception, match="403 forbidden"):
            await svc.get_top_tracks()


class TestRecentlyPlayed:
    @pytest.mark.asyncio
    async def test_extracts_track_from_items(self, svc, mock_sp):
        tracks = await svc.get_recently_played(limit=3)
        assert len(tracks) == 3
        assert all("id" in t for t in tracks)

    @pytest.mark.asyncio
    async def test_raises_on_error(self, svc, mock_sp):
        mock_sp.current_user_recently_played.side_effect = Exception("timeout")
        with pytest.raises(Exception, match="timeout"):
            await svc.get_recently_played()


class TestTopGenres:
    @pytest.mark.asyncio
    async def test_deduplicates_genres(self, svc, mock_sp):
        mock_sp.current_user_top_artists.return_value = {
            "items": [
                _make_artist(genres=["pop", "rock"]),
                _make_artist(genres=["rock", "indie"]),
                _make_artist(genres=["pop", "electronic"]),
            ]
        }
        genres = await svc.get_top_genres()
        assert genres == ["pop", "rock", "indie", "electronic"]

    @pytest.mark.asyncio
    async def test_limits_to_10(self, svc, mock_sp):
        mock_sp.current_user_top_artists.return_value = {
            "items": [
                _make_artist(genres=[f"genre-{i}" for i in range(j * 5, j * 5 + 5)])
                for j in range(5)
            ]
        }
        genres = await svc.get_top_genres()
        assert len(genres) == 10

    @pytest.mark.asyncio
    async def test_raises_on_error(self, svc, mock_sp):
        mock_sp.current_user_top_artists.side_effect = Exception("api error")
        with pytest.raises(Exception, match="api error"):
            await svc.get_top_genres()


# ---------------------------------------------------------------------------
# Search & saved tracks
# ---------------------------------------------------------------------------

class TestSearchTracks:
    @pytest.mark.asyncio
    async def test_returns_track_items(self, svc, mock_sp):
        results = await svc.search_tracks("chill vibes", limit=5)
        assert len(results) == 5
        mock_sp.search.assert_called_once_with(
            q="chill vibes", type="track", limit=5
        )

    @pytest.mark.asyncio
    async def test_empty_results(self, svc, mock_sp):
        mock_sp.search.return_value = {"tracks": {"items": []}}
        results = await svc.search_tracks("nonexistent")
        assert results == []


class TestSavedTracks:
    @pytest.mark.asyncio
    async def test_extracts_tracks(self, svc, mock_sp):
        tracks = await svc.get_saved_tracks(limit=5, offset=0)
        assert len(tracks) == 5

    @pytest.mark.asyncio
    async def test_passes_offset(self, svc, mock_sp):
        await svc.get_saved_tracks(limit=10, offset=20)
        mock_sp.current_user_saved_tracks.assert_called_once_with(
            limit=10, offset=20
        )


# ---------------------------------------------------------------------------
# Discovery pipeline
# ---------------------------------------------------------------------------

class TestDiscoverTracks:
    @pytest.mark.asyncio
    async def test_returns_up_to_limit(self, svc):
        tracks = await svc.discover_tracks(limit=5)
        assert len(tracks) <= 5

    @pytest.mark.asyncio
    async def test_excludes_seed_tracks(self, svc, mock_sp):
        mock_sp.current_user_top_artists.return_value = {"items": []}
        mock_sp.current_user_saved_tracks.return_value = {
            "items": [
                {"track": _make_track(id="keep-me")},
                {"track": _make_track(id="exclude-me")},
            ]
        }
        mock_sp.current_user_top_tracks.return_value = {"items": []}
        tracks = await svc.discover_tracks(
            seed_tracks=["exclude-me"], limit=10
        )
        ids = [t.get("id") for t in tracks]
        assert "exclude-me" not in ids

    @pytest.mark.asyncio
    async def test_uses_genre_search(self, svc, mock_sp):
        mock_sp.current_user_top_artists.return_value = {"items": []}
        mock_sp.current_user_saved_tracks.return_value = {"items": []}
        mock_sp.current_user_top_tracks.return_value = {"items": []}
        await svc.discover_tracks(seed_genres=["jazz", "blues"], limit=5)
        search_calls = [
            c for c in mock_sp.search.call_args_list
            if "genre:" in str(c)
        ]
        assert len(search_calls) >= 1

    @pytest.mark.asyncio
    async def test_raises_on_error(self, svc, mock_sp):
        mock_sp.current_user_top_artists.side_effect = Exception("boom")
        with pytest.raises(Exception, match="boom"):
            await svc.discover_tracks(limit=5)

    @pytest.mark.asyncio
    async def test_backward_compat_alias(self, svc):
        tracks = await svc.get_recommendations(limit=3)
        assert len(tracks) <= 3


# ---------------------------------------------------------------------------
# Playback control
# ---------------------------------------------------------------------------

class TestPlayTrack:
    @pytest.mark.asyncio
    async def test_plays_with_cached_device(self, svc, mock_sp):
        svc._device_id = "cached-dev"
        svc._device_ready = True
        await svc.play_track("spotify:track:abc")
        mock_sp.start_playback.assert_called_once_with(
            device_id="cached-dev", uris=["spotify:track:abc"]
        )

    @pytest.mark.asyncio
    async def test_discovers_device_when_none_cached(self, svc, mock_sp):
        svc._device_id = None
        svc._device_ready = False
        mock_sp.devices.return_value = {
            "devices": [_make_device(id="discovered")]
        }
        await svc.play_track("spotify:track:xyz")
        mock_sp.start_playback.assert_called_once_with(
            device_id="discovered", uris=["spotify:track:xyz"]
        )

    @pytest.mark.asyncio
    async def test_explicit_device_id(self, svc, mock_sp):
        svc._device_id = "cached"
        svc._device_ready = True
        await svc.play_track("spotify:track:abc", device_id="override")
        mock_sp.start_playback.assert_called_once_with(
            device_id="override", uris=["spotify:track:abc"]
        )

    @pytest.mark.asyncio
    async def test_raises_on_playback_error(self, svc, mock_sp):
        svc._device_id = "dev"
        svc._device_ready = True
        mock_sp.start_playback.side_effect = Exception("premium required")
        with pytest.raises(Exception, match="premium required"):
            await svc.play_track("spotify:track:abc")


class TestQueueTrack:
    @pytest.mark.asyncio
    async def test_adds_to_queue(self, svc, mock_sp):
        svc._device_id = "dev-1"
        svc._device_ready = True
        await svc.queue_track("spotify:track:q1")
        mock_sp.add_to_queue.assert_called_once_with(
            uri="spotify:track:q1", device_id="dev-1"
        )


class TestPauseResume:
    @pytest.mark.asyncio
    async def test_pause(self, svc, mock_sp):
        svc._device_id = "dev-1"
        await svc.pause()
        mock_sp.pause_playback.assert_called_once_with(device_id="dev-1")

    @pytest.mark.asyncio
    async def test_resume(self, svc, mock_sp):
        svc._device_id = "dev-1"
        await svc.resume()
        mock_sp.start_playback.assert_called_once_with(device_id="dev-1")


# ---------------------------------------------------------------------------
# Playback state
# ---------------------------------------------------------------------------

class TestPlaybackState:
    @pytest.mark.asyncio
    async def test_get_current_playback(self, svc, mock_sp):
        result = await svc.get_current_playback()
        assert result["is_playing"] is True
        assert result["progress_ms"] == 120000

    @pytest.mark.asyncio
    async def test_get_current_playback_none(self, svc, mock_sp):
        mock_sp.current_playback.return_value = None
        result = await svc.get_current_playback()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_track_progress(self, svc, mock_sp):
        progress = await svc.get_track_progress()
        assert progress == (120000, 240000)

    @pytest.mark.asyncio
    async def test_get_track_progress_no_playback(self, svc, mock_sp):
        mock_sp.current_playback.return_value = None
        progress = await svc.get_track_progress()
        assert progress is None

    @pytest.mark.asyncio
    async def test_get_track_progress_no_item(self, svc, mock_sp):
        mock_sp.current_playback.return_value = {"is_playing": False, "item": None}
        progress = await svc.get_track_progress()
        assert progress is None


# ---------------------------------------------------------------------------
# format_track_info
# ---------------------------------------------------------------------------

class TestFormatTrackInfo:
    def test_extracts_all_fields(self, svc):
        track = _make_track(
            id="x1", name="Bohemian Rhapsody", artist="Queen",
            album="Night at the Opera", uri="spotify:track:x1",
            duration_ms=354000,
        )
        info = svc.format_track_info(track)
        assert info["name"] == "Bohemian Rhapsody"
        assert info["artist"] == "Queen"
        assert info["album"] == "Night at the Opera"
        assert info["uri"] == "spotify:track:x1"
        assert info["duration_ms"] == 354000
        assert info["id"] == "x1"

    def test_handles_missing_fields(self, svc):
        info = svc.format_track_info({})
        assert info["name"] == "Unknown"
        assert info["artist"] == ""
        assert info["album"] == "Unknown"
        assert info["uri"] == ""
        assert info["duration_ms"] == 0
        assert info["id"] == ""

    def test_multiple_artists(self, svc):
        track = {
            "id": "collab",
            "name": "Collab Track",
            "artists": [{"name": "Artist A"}, {"name": "Artist B"}],
            "album": {"name": "Collabs"},
            "uri": "spotify:track:collab",
            "duration_ms": 180000,
        }
        info = svc.format_track_info(track)
        assert info["artist"] == "Artist A, Artist B"


# ---------------------------------------------------------------------------
# Playback mode init
# ---------------------------------------------------------------------------

class TestPlaybackModeInit:
    def test_defaults_to_pi(self, mock_sp):
        svc = _build_svc(mock_sp, mode="pi")
        assert svc.playback_mode == "pi"

    def test_mac_mode(self, mock_sp):
        svc = _build_svc(mock_sp, mode="mac")
        assert svc.playback_mode == "mac"

    def test_normalizes_case(self, mock_sp):
        svc = _build_svc(mock_sp, mode="MAC")
        assert svc.playback_mode == "mac"
