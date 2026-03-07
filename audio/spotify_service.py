import asyncio
from typing import Any

import spotipy
from spotipy.oauth2 import SpotifyOAuth


class SpotifyService:
    """Spotify Web API wrapper for DJ mode: auth, playback, recommendations."""

    SCOPES = " ".join([
        "user-read-playback-state",
        "user-modify-playback-state",
        "user-read-currently-playing",
        "user-read-recently-played",
        "user-top-read",
        "user-library-read",
    ])

    def __init__(self, client_id: str, client_secret: str,
                 redirect_uri: str = "http://localhost:8888/callback"):
        self.auth_manager = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=self.SCOPES,
            cache_path=".spotify_cache",
        )
        self.sp = spotipy.Spotify(auth_manager=self.auth_manager)
        self._device_id: str | None = None

    def get_device_id(self) -> str | None:
        """Find the active Spotify device (or librespot device on Pi)."""
        devices = self.sp.devices()
        for d in devices.get("devices", []):
            if d.get("is_active"):
                self._device_id = d["id"]
                return d["id"]
        # If no active device, pick the first one
        if devices.get("devices"):
            self._device_id = devices["devices"][0]["id"]
            return self._device_id
        return None

    # --- User Taste ---

    async def get_top_tracks(self, limit: int = 20,
                              time_range: str = "medium_term") -> list[dict]:
        """Get user's top tracks. time_range: short_term, medium_term, long_term."""
        result = await asyncio.to_thread(
            self.sp.current_user_top_tracks, limit=limit, time_range=time_range
        )
        return result.get("items", [])

    async def get_recently_played(self, limit: int = 20) -> list[dict]:
        """Get user's recently played tracks."""
        result = await asyncio.to_thread(
            self.sp.current_user_recently_played, limit=limit
        )
        return [item["track"] for item in result.get("items", [])]

    async def get_top_genres(self) -> list[str]:
        """Infer top genres from user's top artists."""
        result = await asyncio.to_thread(
            self.sp.current_user_top_artists, limit=10, time_range="medium_term"
        )
        genres: list[str] = []
        for artist in result.get("items", []):
            genres.extend(artist.get("genres", []))
        # Deduplicate preserving order
        seen = set()
        unique = []
        for g in genres:
            if g not in seen:
                seen.add(g)
                unique.append(g)
        return unique[:10]

    # --- Recommendations ---

    async def get_recommendations(
        self,
        seed_tracks: list[str] | None = None,
        seed_genres: list[str] | None = None,
        limit: int = 10,
        **kwargs: Any,
    ) -> list[dict]:
        """Get song recommendations. kwargs can include target_energy, target_valence, etc."""
        result = await asyncio.to_thread(
            self.sp.recommendations,
            seed_tracks=seed_tracks[:5] if seed_tracks else None,
            seed_genres=seed_genres[:5] if seed_genres else None,
            limit=limit,
            **kwargs,
        )
        return result.get("tracks", [])

    async def get_audio_features(self, track_ids: list[str]) -> list[dict]:
        """Get audio features (BPM, energy, key, etc.) for tracks."""
        result = await asyncio.to_thread(
            self.sp.audio_features, tracks=track_ids
        )
        return [f for f in result if f is not None]

    # --- Playback Control ---

    async def play_track(self, track_uri: str, device_id: str | None = None):
        """Play a specific track."""
        device = device_id or self._device_id or self.get_device_id()
        await asyncio.to_thread(
            self.sp.start_playback, device_id=device, uris=[track_uri]
        )

    async def queue_track(self, track_uri: str, device_id: str | None = None):
        """Add a track to the playback queue."""
        device = device_id or self._device_id or self.get_device_id()
        await asyncio.to_thread(
            self.sp.add_to_queue, uri=track_uri, device_id=device
        )

    async def pause(self, device_id: str | None = None):
        """Pause playback."""
        device = device_id or self._device_id
        await asyncio.to_thread(self.sp.pause_playback, device_id=device)

    async def resume(self, device_id: str | None = None):
        """Resume playback."""
        device = device_id or self._device_id
        await asyncio.to_thread(self.sp.start_playback, device_id=device)

    async def get_current_playback(self) -> dict | None:
        """Get current playback state (track, progress, duration)."""
        result = await asyncio.to_thread(self.sp.current_playback)
        return result

    async def get_track_progress(self) -> tuple[int, int] | None:
        """Return (progress_ms, duration_ms) of current track, or None."""
        playback = await self.get_current_playback()
        if not playback or not playback.get("item"):
            return None
        return (
            playback.get("progress_ms", 0),
            playback["item"].get("duration_ms", 0),
        )

    def format_track_info(self, track: dict) -> dict:
        """Extract useful info from a Spotify track object."""
        return {
            "name": track.get("name", "Unknown"),
            "artist": ", ".join(a["name"] for a in track.get("artists", [])),
            "album": track.get("album", {}).get("name", "Unknown"),
            "uri": track.get("uri", ""),
            "duration_ms": track.get("duration_ms", 0),
            "id": track.get("id", ""),
        }
