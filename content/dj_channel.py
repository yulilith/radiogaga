import asyncio
from typing import AsyncGenerator

from content.agent import BaseChannel, ContentChunk, BASE_SYSTEM_PROMPT


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
        voice_id = self.get_voice_id(subchannel)

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

            banter = ""
            async with self.client.messages.stream(
                model=self.config.get("LLM_MODEL", "claude-haiku-4-5-20251001"),
                max_tokens=100,
                temperature=0.9,
                system=system_prompt,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    if self._cancelled:
                        return
                    banter += text

            if banter.strip():
                self.history.append({"role": "assistant", "content": banter.strip()})
                yield ContentChunk(text=banter.strip(), voice_id=voice_id, pause_after=0.5)

            if self._cancelled:
                return

            # 2. Play a song
            if self.spotify and self._set_list:
                track = self._set_list.pop(0)
                self._current_track = track
                try:
                    await self.spotify.play_track(track["uri"])
                    # Wait for song to play (check progress periodically)
                    await self._wait_for_song_end()
                    await self.spotify.pause()
                except Exception as e:
                    print(f"[DJ] Spotify playback error: {e}")
                    # Fallback: just pause as if a song played
                    await asyncio.sleep(5)
            elif self.music_manager and self.music_manager.has_music():
                # Fallback: play a local music clip
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
                self._current_track = None
                await asyncio.sleep(3)

            # Refill set list if running low
            if len(self._set_list) < 3:
                await self._build_set_list(subchannel)

    async def _build_set_list(self, subchannel: str):
        """Build a set list from Spotify based on the subchannel mode."""
        if not self.spotify:
            return

        try:
            if subchannel == "top_tracks":
                tracks = await self.spotify.get_top_tracks(limit=10)
            elif subchannel == "discover":
                top = await self.spotify.get_top_tracks(limit=5)
                seed_ids = [t["id"] for t in top[:3]]
                tracks = await self.spotify.get_recommendations(
                    seed_tracks=seed_ids, limit=10
                )
            elif subchannel == "mood":
                # Time-of-day mood mapping
                ctx = await self.context.get_context()
                hour = ctx.get("hour", 12)
                energy = 0.3 if hour >= 21 or hour < 6 else 0.7 if 6 <= hour < 12 else 0.5
                top = await self.spotify.get_top_tracks(limit=5)
                seed_ids = [t["id"] for t in top[:3]]
                tracks = await self.spotify.get_recommendations(
                    seed_tracks=seed_ids, limit=10,
                    target_energy=energy,
                    target_valence=0.5 if hour >= 21 else 0.7,
                )
            elif subchannel == "genre":
                genres = await self.spotify.get_top_genres()
                seed_genres = genres[:2] if genres else ["pop"]
                tracks = await self.spotify.get_recommendations(
                    seed_genres=seed_genres, limit=10
                )
            elif subchannel == "decade":
                top = await self.spotify.get_top_tracks(limit=5)
                seed_ids = [t["id"] for t in top[:3]]
                tracks = await self.spotify.get_recommendations(
                    seed_tracks=seed_ids, limit=10
                )
            else:
                tracks = await self.spotify.get_top_tracks(limit=10)

            self._set_list = [self.spotify.format_track_info(t) for t in tracks]
        except Exception as e:
            print(f"[DJ] Error building set list: {e}")
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
