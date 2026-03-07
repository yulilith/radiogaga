from __future__ import annotations

import asyncio
import logging
import platform
import shutil
import subprocess
from pathlib import Path

from radioagent.models import AudioArtifact


class AudioPlayer:
    def __init__(self, *, enabled: bool, logger: logging.Logger) -> None:
        self.enabled = enabled
        self.logger = logger
        self._current_process: asyncio.subprocess.Process | None = None
        self._current_path: str | None = None
        self._lock = asyncio.Lock()

    async def play(self, artifact: AudioArtifact) -> None:
        if not self.enabled:
            self.logger.info("audio playback disabled", extra={"audio_path": artifact.path})
            return

        if not artifact.mime_type.startswith("audio/"):
            self.logger.info(
                "skipping non-audio artifact",
                extra={"audio_path": artifact.path, "mime_type": artifact.mime_type},
            )
            return

        path = Path(artifact.path)
        command = self._resolve_command(path)
        if not command:
            self.logger.warning(
                "no local audio player available",
                extra={"audio_path": str(path)},
            )
            return

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        async with self._lock:
            self._current_process = process
            self._current_path = str(path)
        completed_returncode = await process.wait()
        async with self._lock:
            if self._current_process is process:
                self._current_process = None
                self._current_path = None

    async def stop(self, reason: str) -> bool:
        async with self._lock:
            process = self._current_process
            path = self._current_path
        if process is None or process.returncode is not None:
            return False

        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        return True

    def _resolve_command(self, path: Path) -> list[str] | None:
        system = platform.system().lower()
        if system == "darwin" and shutil.which("afplay"):
            return ["afplay", str(path)]
        if shutil.which("ffplay"):
            return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]
        if shutil.which("mpg123"):
            return ["mpg123", "-q", str(path)]
        return None

