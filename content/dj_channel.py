import asyncio
import random
import time
from typing import AsyncGenerator

from content.agent import BASE_SYSTEM_PROMPT, BaseChannel, ContentChunk, PreparedPreview
from log import get_logger, log_api_call

logger = get_logger(__name__)


class DJChannel(BaseChannel):
    """DJ & Music channel — curates Spotify tracks with AI DJ banter between songs."""

    channel_id = "dj"

    def __init__(self, context_provider, config: dict,
                 spotify_service=None, music_manager=None):
        super().__init__(context_provider, config)
        self.spotify = spotify_service
        self.music_manager = music_manager
        self._current_track: dict | None = None
        self._set_list: list[dict] = []
        self._preview_set_lists: dict[str, list[dict]] = {}
        self._pending_preview: dict | None = None

    def channel_name(self) -> str:
        return "DJ & Music"

    def get_voice_id(self, subchannel: str) -> str:
        return self.config["VOICES"].get("dj", "iP95p4xoKVk53GoZ742B")

    def get_system_prompt(self, subchannel: str, context: dict) -> str:
        return self._build_system_prompt(
            subchannel,
            context,
            current_track=self._current_track,
            set_list=self._set_list,
        )

    async def build_preview(self, subchannel: str) -> PreparedPreview | None:
        context = await self.get_prompt_context(subchannel)
        if self.spotify:
            try:
                await self.spotify.ensure_device()
            except Exception as exc:
                logger.warning("DJ preview device warm failed: %s", exc)

        preview_set_list = self._preview_set_lists.get(subchannel)
        if preview_set_list is None:
            preview_set_list = await self._generate_set_list(subchannel)
            self._preview_set_lists[subchannel] = [dict(track) for track in preview_set_list]

        preview_text = await self._complete_text(
            system_prompt=self._build_system_prompt(
                subchannel,
                context,
                current_track=self._current_track,
                set_list=preview_set_list,
            ),
            prompt=(
                "Generate the first DJ line the listener should hear immediately after tuning in. "
                "Keep it under 35 words."
            ),
            max_tokens=90,
            context_label="dj_preview",
            temperature=0.9,
            messages=[
                *self.history[-4:],
                {
                    "role": "user",
                    "content": "Generate the first DJ line the listener should hear immediately after tuning in.",
                },
            ],
        )

        return PreparedPreview(
            text=preview_text,
            voice_id=self.get_voice_id(subchannel),
            metadata={
                "set_list": [dict(track) for track in preview_set_list],
            },
        )

    def commit_preview_playback(self, subchannel: str, preview: PreparedPreview):
        self._remember_message("assistant", preview.text)
        warmed_set_list = preview.metadata.get("set_list", [])
        self._pending_preview = {
            "subchannel": subchannel,
            "set_list": [dict(track) for track in warmed_set_list],
        }

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

        pending_preview = None
        if self._pending_preview and self._pending_preview.get("subchannel") == subchannel:
            pending_preview = self._pending_preview
            self._pending_preview = None

        if pending_preview and pending_preview.get("set_list"):
            self._set_list = [dict(track) for track in pending_preview["set_list"]]
        else:
            warmed = self._preview_set_lists.pop(subchannel, None)
            if warmed:
                self._set_list = [dict(track) for track in warmed]
            else:
                await self._build_set_list(subchannel)

        skip_banter_once = pending_preview is not None

        while not self._cancelled:
            if not skip_banter_once:
                ctx = await self.get_prompt_context(subchannel)
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
                log_api_call(
                    logger,
                    "anthropic",
                    "messages.stream",
                    status="ok",
                    duration_ms=duration_ms,
                    model=model,
                    context="dj_banter",
                    response_len=len(banter),
                )

                if banter.strip():
                    logger.debug("DJ banter generated", extra={"banter_len": len(banter.strip())})
                    self._remember_message("assistant", banter.strip())
                    yield ContentChunk(text=banter.strip(), voice_id=voice_id, pause_after=0.5)
            else:
                skip_banter_once = False

            if self._cancelled:
                return

            if self.spotify and self._set_list:
                track = self._set_list.pop(0)
                self._current_track = track
                logger.info(
                    "playing track",
                    extra={"track": track.get("name", "Unknown"), "artist": track.get("artist", "Unknown")},
                )
                try:
                    await self.spotify.play_track(track["uri"])
                    await self._wait_for_song_end()
                    await self.spotify.pause()
                except Exception as e:
                    logger.error("Spotify playback error", exc_info=e, extra={"track": track.get("name", "Unknown")})
                    await asyncio.sleep(5)
            elif self.music_manager and self.music_manager.has_music():
                logger.warning("using local music fallback", extra={"subchannel": subchannel})
                genre = self._subchannel_to_genre(subchannel)
                track_path = self.music_manager.get_track(genre)
                if track_path:
                    self._current_track = {"name": "Track", "artist": "Artist", "album": ""}
                    yield ContentChunk(text="", voice_id=voice_id, play_music=track_path)
                    await asyncio.sleep(15)
                else:
                    await asyncio.sleep(3)
            else:
                logger.warning("no music source available, banter-only mode")
                self._current_track = None
                await asyncio.sleep(3)

            if len(self._set_list) < 3:
                await self._build_set_list(subchannel)

    def _build_system_prompt(
        self,
        subchannel: str,
        context: dict,
        *,
        current_track: dict | None,
        set_list: list[dict],
    ) -> str:
        mode_desc = {
            "top_tracks": "playing the listener's most-loved tracks",
            "discover": "introducing new music based on their taste",
            "genre": "curating a genre-focused set",
            "mood": f"setting the mood for a {context.get('time_of_day', 'chill')} vibe",
            "decade": "taking a trip through musical decades",
        }.get(subchannel, "playing great music")

        track_info = ""
        if current_track:
            track_info = f"""
SONG THAT JUST PLAYED:
- Title: {current_track.get('name', 'Unknown')}
- Artist: {current_track.get('artist', 'Unknown')}
- Album: {current_track.get('album', 'Unknown')}"""

        next_track = ""
        if set_list:
            upcoming = set_list[0]
            next_track = f"""
NEXT SONG COMING UP:
- Title: {upcoming.get('name', 'Unknown')}
- Artist: {upcoming.get('artist', 'Unknown')}"""

        return BASE_SYSTEM_PROMPT.format(**context) + f"""
CHANNEL: DJ & Music - {subchannel.replace('_', ' ').title()}
VOICE STYLE: Upbeat DJ personality. Fun, energetic, music-knowledgeable.
DJ MODE: {mode_desc}

{self.get_session_guidance(subchannel)}

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

    async def _build_set_list(self, subchannel: str):
        self._set_list = await self._generate_set_list(subchannel)

    async def _generate_set_list(self, subchannel: str) -> list[dict]:
        """Build a set list from Spotify based on the subchannel mode."""
        if not self.spotify:
            return []

        logger.info("building set list", extra={"subchannel": subchannel})
        try:
            if subchannel == "top_tracks":
                tracks = await self.spotify.get_top_tracks(limit=10)
            elif subchannel == "discover":
                tracks = await self.spotify.discover_tracks(limit=10)
            elif subchannel == "mood":
                ctx = await self.get_prompt_context(subchannel)
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
                discover = await self.spotify.discover_tracks(limit=5)
                tracks = mood_tracks[:5] + discover[:5]
            elif subchannel == "genre":
                genres = await self.spotify.get_top_genres()
                seed_genres = genres[:2] if genres else ["pop"]
                tracks = await self.spotify.discover_tracks(
                    seed_genres=seed_genres,
                    limit=10,
                )
            elif subchannel == "decade":
                decade = random.choice(["80s", "90s", "2000s", "2010s"])
                decade_tracks = await self.spotify.search_tracks(
                    f"year:{decade} hits classic",
                    limit=10,
                )
                discover = await self.spotify.discover_tracks(limit=5)
                tracks = decade_tracks[:5] + discover[:5]
            else:
                tracks = await self.spotify.get_top_tracks(limit=10)

            formatted = [self.spotify.format_track_info(track) for track in tracks]
            logger.info(
                "set list built",
                extra={"subchannel": subchannel, "track_count": len(formatted)},
            )
            return formatted
        except Exception as e:
            logger.error("error building set list", exc_info=e, extra={"subchannel": subchannel})
            return []

    async def _wait_for_song_end(self, check_interval: float = 5.0):
        """Poll Spotify to know when the current song is ending."""
        while not self._cancelled:
            progress = await self.spotify.get_track_progress()
            if not progress:
                break
            progress_ms, duration_ms = progress
            remaining_ms = duration_ms - progress_ms
            if remaining_ms < 3000:
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
