import asyncio
import random
import time
from typing import Any

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from log import get_logger, log_api_call

logger = get_logger(__name__)


class SpotifyService:
    """Spotify Web API wrapper for DJ mode: auth, playback, discovery."""

    SCOPES = " ".join([
        "user-read-playback-state",
        "user-modify-playback-state",
        "user-read-currently-playing",
        "user-read-recently-played",
        "user-top-read",
        "user-library-read",
    ])

    def __init__(self, client_id: str, client_secret: str,
                 redirect_uri: str = "http://127.0.0.1:8888/callback"):
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
        t0 = time.monotonic()
        try:
            devices = self.sp.devices()
            elapsed = (time.monotonic() - t0) * 1000
            log_api_call(logger, "spotify", "/devices",
                         status="ok", duration_ms=elapsed)

            for d in devices.get("devices", []):
                if d.get("is_active"):
                    self._device_id = d["id"]
                    logger.info("Active Spotify device found",
                                extra={"device_name": d.get("name", "unknown")})
                    return d["id"]
            # If no active device, pick the first one
            if devices.get("devices"):
                self._device_id = devices["devices"][0]["id"]
                logger.info("Using first available Spotify device",
                            extra={"device_name": devices["devices"][0].get("name", "unknown")})
                return self._device_id

            logger.warning("No Spotify devices found")
            return None
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Failed to get Spotify devices: %s", e)
            log_api_call(logger, "spotify", "/devices",
                         status="exception", duration_ms=elapsed)
            raise

    # --- User Taste ---

    async def get_top_tracks(self, limit: int = 20,
                              time_range: str = "medium_term") -> list[dict]:
        """Get user's top tracks. time_range: short_term, medium_term, long_term."""
        logger.debug("Fetching top tracks", extra={
            "limit": limit, "time_range": time_range,
        })
        t0 = time.monotonic()
        try:
            result = await asyncio.to_thread(
                self.sp.current_user_top_tracks, limit=limit, time_range=time_range
            )
            elapsed = (time.monotonic() - t0) * 1000
            items = result.get("items", [])
            log_api_call(logger, "spotify", "/me/top/tracks",
                         status="ok", duration_ms=elapsed, count=len(items))
            return items
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Failed to get top tracks: %s", e)
            log_api_call(logger, "spotify", "/me/top/tracks",
                         status="exception", duration_ms=elapsed)
            raise

    async def get_recently_played(self, limit: int = 20) -> list[dict]:
        """Get user's recently played tracks."""
        logger.debug("Fetching recently played", extra={"limit": limit})
        t0 = time.monotonic()
        try:
            result = await asyncio.to_thread(
                self.sp.current_user_recently_played, limit=limit
            )
            elapsed = (time.monotonic() - t0) * 1000
            items = [item["track"] for item in result.get("items", [])]
            log_api_call(logger, "spotify", "/me/player/recently-played",
                         status="ok", duration_ms=elapsed, count=len(items))
            return items
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Failed to get recently played: %s", e)
            log_api_call(logger, "spotify", "/me/player/recently-played",
                         status="exception", duration_ms=elapsed)
            raise

    async def get_top_genres(self) -> list[str]:
        """Infer top genres from user's top artists."""
        logger.debug("Fetching top genres from top artists")
        t0 = time.monotonic()
        try:
            result = await asyncio.to_thread(
                self.sp.current_user_top_artists, limit=10, time_range="medium_term"
            )
            elapsed = (time.monotonic() - t0) * 1000
            log_api_call(logger, "spotify", "/me/top/artists",
                         status="ok", duration_ms=elapsed)

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
            logger.info("Top genres resolved", extra={"count": len(unique[:10])})
            return unique[:10]
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Failed to get top genres: %s", e)
            log_api_call(logger, "spotify", "/me/top/artists",
                         status="exception", duration_ms=elapsed)
            raise

    # --- Discovery (replaces deprecated /recommendations endpoint) ---

    async def get_top_artists(self, limit: int = 10,
                               time_range: str = "medium_term") -> list[dict]:
        """Get user's top artists."""
        logger.debug("Fetching top artists", extra={
            "limit": limit, "time_range": time_range,
        })
        t0 = time.monotonic()
        try:
            result = await asyncio.to_thread(
                self.sp.current_user_top_artists, limit=limit, time_range=time_range
            )
            elapsed = (time.monotonic() - t0) * 1000
            items = result.get("items", [])
            log_api_call(logger, "spotify", "/me/top/artists",
                         status="ok", duration_ms=elapsed, count=len(items))
            return items
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Failed to get top artists: %s", e)
            log_api_call(logger, "spotify", "/me/top/artists",
                         status="exception", duration_ms=elapsed)
            raise

    async def search_tracks(self, query: str, limit: int = 10) -> list[dict]:
        """Search for tracks by query string."""
        logger.debug("Searching tracks", extra={"query": query, "limit": limit})
        t0 = time.monotonic()
        try:
            result = await asyncio.to_thread(
                self.sp.search, q=query, type="track", limit=limit
            )
            elapsed = (time.monotonic() - t0) * 1000
            tracks = result.get("tracks", {}).get("items", [])
            log_api_call(logger, "spotify", "/search",
                         status="ok", duration_ms=elapsed, count=len(tracks))
            return tracks
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Failed to search tracks: %s", e)
            log_api_call(logger, "spotify", "/search",
                         status="exception", duration_ms=elapsed)
            raise

    async def get_saved_tracks(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """Get user's saved/liked tracks."""
        logger.debug("Fetching saved tracks", extra={"limit": limit, "offset": offset})
        t0 = time.monotonic()
        try:
            result = await asyncio.to_thread(
                self.sp.current_user_saved_tracks, limit=limit, offset=offset
            )
            elapsed = (time.monotonic() - t0) * 1000
            items = [item["track"] for item in result.get("items", [])]
            log_api_call(logger, "spotify", "/me/tracks",
                         status="ok", duration_ms=elapsed, count=len(items))
            return items
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Failed to get saved tracks: %s", e)
            log_api_call(logger, "spotify", "/me/tracks",
                         status="exception", duration_ms=elapsed)
            raise

    async def discover_tracks(
        self,
        seed_tracks: list[str] | None = None,
        seed_genres: list[str] | None = None,
        limit: int = 10,
        **kwargs: Any,
    ) -> list[dict]:
        """Discover new tracks using search seeded by user's taste.

        Replaces the deprecated /recommendations, /related-artists,
        and /artist-top-tracks endpoints. Strategy:
        1. Search for tracks by user's top artist names (finds similar music)
        2. Search by genre keywords if provided
        3. Pull from user's saved tracks (random offset for variety)
        4. Mix across time ranges for diversity
        """
        logger.info("Discovering tracks", extra={
            "seed_tracks": len(seed_tracks) if seed_tracks else 0,
            "seed_genres": len(seed_genres) if seed_genres else 0,
            "limit": limit,
        })
        t0 = time.monotonic()

        collected: list[dict] = []
        seen_ids: set[str] = set()

        # Exclude seed tracks from results
        if seed_tracks:
            seen_ids.update(seed_tracks)

        def _add_unique(tracks: list[dict]):
            for t in tracks:
                tid = t.get("id", "")
                if tid and tid not in seen_ids:
                    seen_ids.add(tid)
                    collected.append(t)

        try:
            # Strategy 1: Search by top artist names (best discovery method)
            top_artists = await self.get_top_artists(limit=10, time_range="short_term")
            if not top_artists:
                top_artists = await self.get_top_artists(limit=10, time_range="medium_term")

            if top_artists:
                # Pick random subset to search — finds tracks by and similar to these artists
                sample = random.sample(top_artists, min(4, len(top_artists)))
                for artist in sample:
                    if len(collected) >= limit * 2:  # Collect extra to shuffle from
                        break
                    name = artist.get("name", "")
                    if name:
                        search_results = await self.search_tracks(name, limit=5)
                        # Filter out tracks by the SAME artist to get discovery
                        artist_id = artist.get("id", "")
                        for t in search_results:
                            t_artist_ids = [a.get("id", "") for a in t.get("artists", [])]
                            if artist_id not in t_artist_ids:
                                _add_unique([t])
                            elif len(collected) < limit // 2:
                                # Allow some same-artist tracks if we need more
                                _add_unique([t])

            # Strategy 2: Search by genre keywords
            if seed_genres and len(collected) < limit:
                for genre in seed_genres[:3]:
                    if len(collected) >= limit:
                        break
                    search_results = await self.search_tracks(
                        f"genre:{genre}", limit=5
                    )
                    _add_unique(search_results)

            # Strategy 3: Pull from saved tracks (random offset for variety)
            if len(collected) < limit:
                try:
                    offset = random.randint(0, 50)
                    saved = await self.get_saved_tracks(limit=10, offset=offset)
                    _add_unique(saved)
                except Exception:
                    pass  # saved tracks might be empty

            # Strategy 4: Top tracks from different time range for variety
            if len(collected) < limit:
                long_term = await self.get_top_tracks(limit=10, time_range="long_term")
                _add_unique(long_term)

            # Shuffle and trim
            random.shuffle(collected)
            result = collected[:limit]

            elapsed = (time.monotonic() - t0) * 1000
            log_api_call(logger, "spotify", "discover_tracks",
                         status="ok", duration_ms=elapsed, count=len(result))
            logger.info("Track discovery complete", extra={
                "requested": limit, "found": len(result),
            })
            return result

        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Failed to discover tracks: %s", e)
            log_api_call(logger, "spotify", "discover_tracks",
                         status="exception", duration_ms=elapsed)
            raise

    # Backward compat alias — callers that used get_recommendations now use discover
    async def get_recommendations(
        self,
        seed_tracks: list[str] | None = None,
        seed_genres: list[str] | None = None,
        limit: int = 10,
        **kwargs: Any,
    ) -> list[dict]:
        """Alias for discover_tracks (replaces deprecated Spotify endpoint)."""
        return await self.discover_tracks(
            seed_tracks=seed_tracks, seed_genres=seed_genres,
            limit=limit, **kwargs,
        )

    # --- Playback Control ---

    async def play_track(self, track_uri: str, device_id: str | None = None):
        """Play a specific track."""
        device = device_id or self._device_id or self.get_device_id()
        logger.info("Playing track", extra={"track_uri": track_uri})
        t0 = time.monotonic()
        try:
            await asyncio.to_thread(
                self.sp.start_playback, device_id=device, uris=[track_uri]
            )
            elapsed = (time.monotonic() - t0) * 1000
            log_api_call(logger, "spotify", "/me/player/play",
                         status="ok", duration_ms=elapsed)
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Failed to play track: %s", e)
            log_api_call(logger, "spotify", "/me/player/play",
                         status="exception", duration_ms=elapsed)
            raise

    async def queue_track(self, track_uri: str, device_id: str | None = None):
        """Add a track to the playback queue."""
        device = device_id or self._device_id or self.get_device_id()
        logger.debug("Queueing track", extra={"track_uri": track_uri})
        t0 = time.monotonic()
        try:
            await asyncio.to_thread(
                self.sp.add_to_queue, uri=track_uri, device_id=device
            )
            elapsed = (time.monotonic() - t0) * 1000
            log_api_call(logger, "spotify", "/me/player/queue",
                         status="ok", duration_ms=elapsed)
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Failed to queue track: %s", e)
            log_api_call(logger, "spotify", "/me/player/queue",
                         status="exception", duration_ms=elapsed)
            raise

    async def pause(self, device_id: str | None = None):
        """Pause playback."""
        device = device_id or self._device_id
        logger.info("Pausing playback")
        t0 = time.monotonic()
        try:
            await asyncio.to_thread(self.sp.pause_playback, device_id=device)
            elapsed = (time.monotonic() - t0) * 1000
            log_api_call(logger, "spotify", "/me/player/pause",
                         status="ok", duration_ms=elapsed)
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Failed to pause playback: %s", e)
            log_api_call(logger, "spotify", "/me/player/pause",
                         status="exception", duration_ms=elapsed)
            raise

    async def resume(self, device_id: str | None = None):
        """Resume playback."""
        device = device_id or self._device_id
        logger.info("Resuming playback")
        t0 = time.monotonic()
        try:
            await asyncio.to_thread(self.sp.start_playback, device_id=device)
            elapsed = (time.monotonic() - t0) * 1000
            log_api_call(logger, "spotify", "/me/player/play",
                         status="ok", duration_ms=elapsed)
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Failed to resume playback: %s", e)
            log_api_call(logger, "spotify", "/me/player/play",
                         status="exception", duration_ms=elapsed)
            raise

    async def get_current_playback(self) -> dict | None:
        """Get current playback state (track, progress, duration)."""
        logger.debug("Fetching current playback state")
        t0 = time.monotonic()
        try:
            result = await asyncio.to_thread(self.sp.current_playback)
            elapsed = (time.monotonic() - t0) * 1000
            log_api_call(logger, "spotify", "/me/player",
                         status="ok", duration_ms=elapsed)
            return result
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Failed to get current playback: %s", e)
            log_api_call(logger, "spotify", "/me/player",
                         status="exception", duration_ms=elapsed)
            raise

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
