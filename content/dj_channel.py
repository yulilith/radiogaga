import asyncio
import time
from typing import AsyncGenerator

from content.agent import BaseChannel, ContentChunk, BASE_SYSTEM_PROMPT
from log import get_logger, log_api_call

logger = get_logger(__name__)


class DJChannel(BaseChannel):
    """DJ & Music channel — curates Spotify tracks with AI DJ banter between songs."""

    def __init__(self, context_provider, config: dict,
                 spotify_service=None, music_manager=None):
        super().__init__(context_provider, config)
        self.spotify = spotify_service
        self.music_manager = music_manager
        self._current_track: dict | None = None
        self._set_list: list[dict] = []

    def channel_name(self) -> str:
        return "DJ & Music"

    def get_voice_id(self, subchannel: str) -> str:
        return self.config["VOICES"].get("dj", "iP95p4xoKVk53GoZ742B")

    def get_system_prompt(self, subchannel: str, context: dict) -> str:
        mode_desc = {
            "top_tracks": "playing the listener's most-loved tracks",
            "discover": "introducing new music based on their taste",
            "genre": "curating a genre-focused set",
            "mood": f"setting the mood for a {context.get('time_of_day', 'chill')} vibe",
            "decade": "taking a trip through musical decades",
        }.get(subchannel, "playing great music")

        track_info = ""
        if self._current_track:
            track_info = f"""
SONG THAT JUST PLAYED:
- Title: {self._current_track.get('name', 'Unknown')}
- Artist: {self._current_track.get('artist', 'Unknown')}
- Album: {self._current_track.get('album', 'Unknown')}"""

        next_track = ""
        if self._set_list:
            nt = self._set_list[0]
            next_track = f"""
NEXT SONG COMING UP:
- Title: {nt.get('name', 'Unknown')}
- Artist: {nt.get('artist', 'Unknown')}"""

        return BASE_SYSTEM_PROMPT.format(**context) + f"""
CHANNEL: DJ & Music - {subchannel.replace('_', ' ').title()}
VOICE STYLE: Upbeat DJ personality. Fun, energetic, music-knowledgeable.
DJ MODE: {mode_desc}

You are DJ Spark on RadioAgent's music channel.
{track_info}
{next_track}

INSTRUCTIONS:
- Generate a SHORT DJ segment (this plays BETWEEN songs)
- React to the song that just played (if any)
- Chat briefly about something trending, time-relevant, or music-related
- Tease or introduce the next track coming up
- Keep it VERY SHORT: 30-50 words max. DJs talk between songs, not over them.
- Be fun, high-energy, and music-savvy
- Reference the listener's taste when relevant
"""

    async def stream_content(self, subchannel: str) -> AsyncGenerator[ContentChunk, None]:
        """DJ channel alternates between banter and music."""
        logger.info("DJ stream_content started", extra={"subchannel": subchannel})
        voice_id = self.get_voice_id(subchannel)

        if self.spotify:
            device = await self.spotify.ensure_device()
            if device:
                logger.info("Spotify device ready for DJ", extra={"device_id": device})
            else:
                logger.warning("No Spotify device found, will fall back to local music or banter-only")

        # Build initial set list
        await self._build_set_list(subchannel)

        while not self._cancelled:
            # 1. Play DJ intro/banter
            ctx = await self.context.get_context()
            system_prompt = self.get_system_prompt(subchannel, ctx)

            messages = [
                *self.history[-4:],
                {"role": "user", "content": "Generate DJ banter between songs."},
            ]

            model = self.config.get("LLM_MODEL", "claude-haiku-4-5-20251001")
            banter = ""
            t0 = time.monotonic()
            async with self.client.messages.stream(
                model=model,
                max_tokens=100,
                temperature=0.9,
                system=system_prompt,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    if self._cancelled:
                        return
                    banter += text
            duration_ms = (time.monotonic() - t0) * 1000
            log_api_call(logger, "anthropic", "messages.stream", status="ok", duration_ms=duration_ms,
                         model=model, context="dj_banter", response_len=len(banter))

            if banter.strip():
                logger.debug("DJ banter generated", extra={"banter_len": len(banter.strip())})
                self.history.append({"role": "assistant", "content": banter.strip()})
                yield ContentChunk(text=banter.strip(), voice_id=voice_id, pause_after=0.5)

            if self._cancelled:
                return

            # 2. Play a song
            if self.spotify and self._set_list:
                track = self._set_list.pop(0)
                self._current_track = track
                logger.info("playing track", extra={"track": track.get("name", "Unknown"), "artist": track.get("artist", "Unknown")})
                try:
                    await self.spotify.play_track(track["uri"])
                    # Wait for song to play (check progress periodically)
                    await self._wait_for_song_end()
                    await self.spotify.pause()
                except Exception as e:
                    logger.error("Spotify playback error", exc_info=e, extra={"track": track.get("name", "Unknown")})
                    # Fallback: just pause as if a song played
                    await asyncio.sleep(5)
            elif self.music_manager and self.music_manager.has_music():
                # Fallback: play a local music clip
                logger.warning("using local music fallback", extra={"subchannel": subchannel})
                genre = self._subchannel_to_genre(subchannel)
                track_path = self.music_manager.get_track(genre)
                if track_path:
                    self._current_track = {"name": "Track", "artist": "Artist", "album": ""}
                    yield ContentChunk(text="", voice_id=voice_id, play_music=track_path)
                    await asyncio.sleep(15)  # Assume 15s clip
                else:
                    await asyncio.sleep(3)
            else:
                # No music available — just generate banter
                logger.warning("no music source available, banter-only mode")
                self._current_track = None
                await asyncio.sleep(3)

            # Refill set list if running low
            if len(self._set_list) < 3:
                await self._build_set_list(subchannel)

    async def _build_set_list(self, subchannel: str):
        """Build a set list from Spotify based on the subchannel mode.

        Uses discover_tracks (related artists + search) since
        the /recommendations endpoint was deprecated by Spotify.
        """
        if not self.spotify:
            return

        logger.info("building set list", extra={"subchannel": subchannel})
        try:
            if subchannel == "top_tracks":
                tracks = await self.spotify.get_top_tracks(limit=10)
            elif subchannel == "discover":
                tracks = await self.spotify.discover_tracks(limit=10)
            elif subchannel == "mood":
                # Time-of-day mood: search for mood-appropriate music
                ctx = await self.context.get_context()
                hour = ctx.get("hour", 12)
                if hour >= 21 or hour < 6:
                    mood_query = "chill night relaxing"
                elif 6 <= hour < 12:
                    mood_query = "upbeat morning energy"
                elif 12 <= hour < 17:
                    mood_query = "afternoon focus"
                else:
                    mood_query = "evening vibes sunset"
                mood_tracks = await self.spotify.search_tracks(mood_query, limit=10)
                # Mix mood search results with user taste via discover
                discover = await self.spotify.discover_tracks(limit=5)
                tracks = mood_tracks[:5] + discover[:5]
            elif subchannel == "genre":
                genres = await self.spotify.get_top_genres()
                seed_genres = genres[:2] if genres else ["pop"]
                tracks = await self.spotify.discover_tracks(
                    seed_genres=seed_genres, limit=10
                )
            elif subchannel == "decade":
                # Search for decade-specific music mixed with user taste
                import random
                decade = random.choice(["80s", "90s", "2000s", "2010s"])
                decade_tracks = await self.spotify.search_tracks(
                    f"year:{decade} hits classic", limit=10
                )
                discover = await self.spotify.discover_tracks(limit=5)
                tracks = decade_tracks[:5] + discover[:5]
            else:
                tracks = await self.spotify.get_top_tracks(limit=10)

            self._set_list = [self.spotify.format_track_info(t) for t in tracks]
            logger.info("set list built", extra={"subchannel": subchannel, "track_count": len(self._set_list)})
        except Exception as e:
            logger.error("error building set list", exc_info=e, extra={"subchannel": subchannel})
            self._set_list = []

    async def _wait_for_song_end(self, check_interval: float = 5.0):
        """Poll Spotify to know when the current song is ending."""
        while not self._cancelled:
            progress = await self.spotify.get_track_progress()
            if not progress:
                break
            progress_ms, duration_ms = progress
            remaining_ms = duration_ms - progress_ms
            if remaining_ms < 3000:  # Less than 3s left
                break
            await asyncio.sleep(min(check_interval, remaining_ms / 1000 - 2))

    @staticmethod
    def _subchannel_to_genre(subchannel: str) -> str:
        return {
            "top_tracks": "pop",
            "discover": "indie",
            "genre": "electronic",
            "mood": "ambient",
            "decade": "classic",
        }.get(subchannel, "pop")
