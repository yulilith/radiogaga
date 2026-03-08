import io
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

import pyaudio
from pydub import AudioSegment

from log import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class PlaybackChunk:
    generation: int
    data: bytes
    on_start: Callable[[], None] | None = None


class AudioPlayer:
    """Manages audio output with buffering, volume control, and interruption."""

    CHUNK_SIZE = 1024
    WRITE_SLICE_FRAMES = 256
    SAMPLE_RATE = 22050
    CHANNELS = 1
    FORMAT = pyaudio.paInt16

    def __init__(self):
        self.pa = pyaudio.PyAudio()
        self.stream = self.pa.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.SAMPLE_RATE,
            output=True,
            frames_per_buffer=self.CHUNK_SIZE,
        )
        self.audio_queue: queue.Queue[PlaybackChunk] = queue.Queue(maxsize=100)
        self._playing = False
        self._play_thread: threading.Thread | None = None
        self._volume = 0.7
        self._muted = False
        self._last_underrun_log: float = 0.0
        self._generation = 0

    @property
    def current_generation(self) -> int:
        return self._generation

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, value: float):
        old = self._volume
        self._volume = max(0.0, min(1.0, value))
        logger.info("Volume changed", extra={
            "old_volume": f"{old:.2f}", "new_volume": f"{self._volume:.2f}",
        })

    @property
    def muted(self) -> bool:
        return self._muted

    def toggle_mute(self):
        self._muted = not self._muted
        logger.info("Mute toggled", extra={"muted": self._muted})

    def start(self):
        """Start the playback thread."""
        logger.info("Audio player starting")
        self._playing = True
        self._play_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._play_thread.start()

    def _playback_loop(self):
        while self._playing:
            try:
                item = self.audio_queue.get(timeout=0.1)
                if item.generation != self._generation:
                    continue

                if item.on_start:
                    try:
                        item.on_start()
                    except Exception as exc:
                        logger.warning("Playback on_start callback failed: %s", exc)

                payload = item.data
                if self._muted:
                    payload = b"\x00" * len(payload)
                else:
                    payload = self._apply_volume(payload)

                for offset in range(0, len(payload), self.WRITE_SLICE_FRAMES * 2):
                    if item.generation != self._generation:
                        break
                    chunk = payload[offset:offset + self.WRITE_SLICE_FRAMES * 2]
                    if chunk:
                        self.stream.write(chunk)
            except queue.Empty:
                now = time.monotonic()
                if now - self._last_underrun_log >= 5.0:
                    logger.warning("Buffer underrun, playing silence")
                    self._last_underrun_log = now
                silence = b"\x00" * self.CHUNK_SIZE * 2
                self.stream.write(silence)

    def _apply_volume(self, data: bytes) -> bytes:
        """Scale PCM audio data by volume level."""
        if self._volume >= 0.99:
            return data

        import struct

        samples = struct.unpack(f"<{len(data) // 2}h", data)
        scaled = [int(s * self._volume) for s in samples]
        scaled = [max(-32768, min(32767, s)) for s in scaled]
        return struct.pack(f"<{len(scaled)}h", *scaled)

    def enqueue_mp3(
        self,
        mp3_bytes: bytes,
        *,
        generation: int | None = None,
        on_start: Callable[[], None] | None = None,
    ) -> bool:
        """Convert MP3 bytes to PCM and add to playback queue."""
        if not mp3_bytes or len(mp3_bytes) < 4:
            logger.error("Received empty or too-small MP3 data, skipping")
            return False

        target_generation = self._generation if generation is None else generation
        if target_generation != self._generation:
            logger.debug("Skipping stale MP3 enqueue", extra={"generation": target_generation})
            return False

        try:
            audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
        except Exception as e:
            logger.error("Failed to decode MP3 (%d bytes): %s", len(mp3_bytes), e)
            return False

        audio = audio.set_frame_rate(self.SAMPLE_RATE).set_channels(self.CHANNELS)
        return self._enqueue_raw_audio(
            audio.raw_data,
            generation=target_generation,
            on_start=on_start,
            source="mp3",
            input_size=len(mp3_bytes),
        )

    def play_file(
        self,
        filepath: str,
        *,
        generation: int | None = None,
        on_start: Callable[[], None] | None = None,
    ) -> bool:
        """Play an audio file (wav, mp3, etc.) from disk."""
        target_generation = self._generation if generation is None else generation
        if target_generation != self._generation:
            logger.debug("Skipping stale file playback", extra={"filepath": filepath, "generation": target_generation})
            return False

        logger.debug("Playing file from disk", extra={"filepath": filepath})
        audio = AudioSegment.from_file(filepath)
        audio = audio.set_frame_rate(self.SAMPLE_RATE).set_channels(self.CHANNELS)
        return self._enqueue_raw_audio(
            audio.raw_data,
            generation=target_generation,
            on_start=on_start,
            source="file",
            input_size=len(audio.raw_data),
        )

    def _enqueue_raw_audio(
        self,
        raw_data: bytes,
        *,
        generation: int,
        on_start: Callable[[], None] | None,
        source: str,
        input_size: int,
    ) -> bool:
        chunk_count = 0
        for index in range(0, len(raw_data), self.CHUNK_SIZE * 2):
            if generation != self._generation:
                logger.debug("Stopped queueing stale audio", extra={"generation": generation, "source": source})
                return chunk_count > 0

            chunk = raw_data[index:index + self.CHUNK_SIZE * 2]
            try:
                self.audio_queue.put(
                    PlaybackChunk(
                        generation=generation,
                        data=chunk,
                        on_start=on_start if chunk_count == 0 else None,
                    ),
                    timeout=0.25,
                )
                chunk_count += 1
            except queue.Full:
                logger.warning(
                    "Audio queue full, dropping remaining chunks",
                    extra={"enqueued": chunk_count, "source": source},
                )
                break

        logger.debug("Audio enqueued", extra={
            "input_bytes": input_size, "chunks": chunk_count, "source": source,
        })
        return chunk_count > 0

    def clear_buffer(self):
        """Flush the queued audio chunks."""
        discarded = 0
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                discarded += 1
            except queue.Empty:
                break
        logger.debug("Buffer cleared", extra={"chunks_discarded": discarded})

    def hard_stop(self, reason: str = "interrupt") -> int:
        """Invalidate current playback and flush queued chunks immediately."""
        self._generation += 1
        self.clear_buffer()
        logger.info("Audio hard stop", extra={"reason": reason, "generation": self._generation})
        return self._generation

    def buffer_level(self) -> int:
        """Return current number of chunks in buffer."""
        return self.audio_queue.qsize()

    def stop(self):
        """Stop playback and clean up."""
        logger.info("Audio player stopping")
        self.hard_stop("shutdown")
        self._playing = False
        if self._play_thread:
            self._play_thread.join(timeout=2.0)
        self.stream.stop_stream()
        self.stream.close()
        self.pa.terminate()
