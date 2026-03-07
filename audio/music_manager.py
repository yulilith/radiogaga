import random
from pathlib import Path


class MusicManager:
    """Fallback music manager for when Spotify isn't available.
    Manages royalty-free music clips from assets/music/."""

    def __init__(self, music_dir: str = "assets/music"):
        self.music_dir = Path(music_dir)
        self.library: dict[str, list[str]] = {}
        self._recently_played: dict[str, list[str]] = {}
        self._load_library()

    def _load_library(self):
        """Scan music directory for genre subdirectories with MP3 files."""
        if not self.music_dir.exists():
            return
        for genre_dir in self.music_dir.iterdir():
            if genre_dir.is_dir():
                tracks = [str(f) for f in genre_dir.glob("*.mp3")]
                if tracks:
                    self.library[genre_dir.name] = tracks

    def get_track(self, genre: str) -> str | None:
        """Get a random track from a genre, avoiding recent repeats."""
        tracks = self.library.get(genre, [])
        if not tracks:
            return None

        recent = self._recently_played.get(genre, [])
        available = [t for t in tracks if t not in recent]
        if not available:
            self._recently_played[genre] = []
            available = tracks

        track = random.choice(available)
        self._recently_played.setdefault(genre, []).append(track)
        return track

    def list_genres(self) -> list[str]:
        """Return available genres."""
        return list(self.library.keys())

    def has_music(self) -> bool:
        """Check if any music is loaded."""
        return bool(self.library)
