import asyncio
import random
import shutil
import subprocess
import time
from typing import Any

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from log import get_logger, log_api_call

logger = get_logger(__name__)

LIBRESPOT_DEVICE_KEYWORDS = ("librespot", "raspotify", "radioagent", "raspberry")


class SpotifyService:
    """Spotify Web API wrapper for DJ mode: auth, playback, discovery.

    Supports two playback modes controlled by SPOTIFY_PLAYBACK_MODE:
      - "mac"  : expects the Spotify desktop app (or web player) as Connect target
      - "pi"   : expects librespot / raspotify running as a system service
    """

    SCOPES = " ".join([
        "user-read-playback-state",
        "user-modify-playback-state",
        "user-read-currently-playing",
        "user-read-recently-played",
        "user-top-read",
        "user-library-read",
    ])

    def __init__(self, client_id: str, client_secret: str,
                 redirect_uri: str = "http://127.0.0.1:8888/callback",
                 playback_mode: str = "pi"):
        self.auth_manager = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=self.SCOPES,
            cache_path=".spotify_cache",
        )
        self.sp = spotipy.Spotify(auth_manager=self.auth_manager)
        self._device_id: str | None = None
        self.playback_mode = playback_mode.lower()
        self._device_ready = False
        logger.info("Spotify playback mode: %s", self.playback_mode)

    # ------------------------------------------------------------------
    # Device discovery
    # ------------------------------------------------------------------

    def _list_devices(self) -> list[dict]:
        """Fetch available Spotify Connect devices."""
        t0 = time.monotonic()
        try:
            resp = self.sp.devices()
            elapsed = (time.monotonic() - t0) * 1000
            log_api_call(logger, "spotify", "/devices",
                         status="ok", duration_ms=elapsed)
            return resp.get("devices", [])
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Failed to list Spotify devices: %s", e)
            log_api_call(logger, "spotify", "/devices",
                         status="exception", duration_ms=elapsed)
            raise

    @staticmethod
    def _is_librespot_device(device: dict) -> bool:
        name = (device.get("name") or "").lower()
        return any(kw in name for kw in LIBRESPOT_DEVICE_KEYWORDS)

    def _pick_device(self, devices: list[dict]) -> dict | None:
        """Select the best device for the current playback mode."""
        if not devices:
            return None

        if self.playback_mode == "pi":
            for d in devices:
                if self._is_librespot_device(d):
                    return d

        for d in devices:
            if d.get("is_active"):
                return d

        return devices[0]

    def get_device_id(self) -> str | None:
        """Find the best Spotify Connect device for the current mode."""
        devices = self._list_devices()
        chosen = self._pick_device(devices)
        if chosen:
            self._device_id = chosen["id"]
            logger.info("Spotify device selected",
                        extra={"device_name": chosen.get("name"),
                               "mode": self.playback_mode})
            return self._device_id

        logger.warning("No Spotify devices found", extra={"mode": self.playback_mode})
        return None

    # ------------------------------------------------------------------
    # Librespot lifecycle (Pi mode only)
    # ------------------------------------------------------------------

    @staticmethod
    def is_librespot_running() -> bool:
        """Check whether a librespot / raspotify process is alive."""
        if not shutil.which("systemctl"):
            return False
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "--quiet", "raspotify"],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    @staticmethod
    def start_librespot() -> bool:
        """Attempt to start the raspotify systemd service."""
        if not shutil.which("systemctl"):
            logger.warning("systemctl not available — cannot manage raspotify")
            return False
        try:
            subprocess.run(
                ["sudo", "systemctl", "start", "raspotify"],
                capture_output=True, timeout=10, check=True,
            )
            logger.info("raspotify service started")
            return True
        except subprocess.CalledProcessError as e:
            logger.error("Failed to start raspotify: %s", e)
            return False
        except Exception as e:
            logger.error("Unexpected error starting raspotify: %s", e)
            return False

    async def ensure_device(self, retries: int = 3, delay: float = 2.0) -> str | None:
        """Make sure a Spotify Connect device is available and ready.

        On Pi mode: checks librespot health, starts it if needed, waits
        for the device to appear, then transfers playback to it.

        On Mac mode: just discovers the desktop app / web player device.

        Returns the device id, or None if no device could be found.
        Never raises — all API errors are caught and result in None.
        """
        try:
            return await self._ensure_device_inner(retries, delay)
        except Exception as e:
            logger.error("ensure_device failed: %s", e)
            self._device_ready = False
            return None

    async def _ensure_device_inner(self, retries: int, delay: float) -> str | None:
        if self._device_ready and self._device_id:
            devices = self._list_devices()
            if any(d["id"] == self._device_id for d in devices):
                return self._device_id
            self._device_ready = False

        if self.playback_mode == "pi":
            if not self.is_librespot_running():
                logger.info("librespot not running, attempting to start")
                self.start_librespot()
                await asyncio.sleep(delay)

        for attempt in range(retries):
            device_id = await asyncio.to_thread(self.get_device_id)
            if device_id:
                try:
                    await asyncio.to_thread(
                        self.sp.transfer_playback, device_id, force_play=False
                    )
                    logger.info("Playback transferred to device",
                                extra={"device_id": device_id, "attempt": attempt + 1})
                except Exception as e:
                    logger.warning("transfer_playback failed (non-fatal): %s", e)

                self._device_ready = True
                return device_id

            if attempt < retries - 1:
                logger.info("No device yet, retrying in %.1fs (attempt %d/%d)",
                            delay, attempt + 1, retries)
                await asyncio.sleep(delay)

        logger.error("Could not find a Spotify device after %d attempts", retries)
        return None

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

    async def _resolve_device(self, device_id: str | None = None) -> str | None:
        """Return a usable device id: explicit > cached > ensure_device."""
        if device_id:
            return device_id
        if self._device_id and self._device_ready:
            return self._device_id
        return await self.ensure_device()

    async def play_track(self, track_uri: str, device_id: str | None = None):
        """Play a specific track."""
        device = await self._resolve_device(device_id)
        logger.info("Playing track", extra={"track_uri": track_uri, "device": device})
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
        device = await self._resolve_device(device_id)
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
