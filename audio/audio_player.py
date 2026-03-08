import io
import queue
import random
import struct
import time
import threading
import pyaudio
from pydub import AudioSegment

from log import get_logger

logger = get_logger(__name__)


class AudioPlayer:
    """Manages audio output with buffering, volume control, radio static, and voice filter."""

    CHUNK_SIZE = 1024
    SAMPLE_RATE = 22050
    CHANNELS = 1
    FORMAT = pyaudio.paInt16

    def __init__(self, radio_filter_strength: float = 0.7):
        self.pa = pyaudio.PyAudio()
        device_index = self._find_output_device()
        open_kwargs = dict(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.SAMPLE_RATE,
            output=True,
            frames_per_buffer=self.CHUNK_SIZE,
        )
        if device_index is not None:
            open_kwargs["output_device_index"] = device_index
        self.stream = self.pa.open(**open_kwargs)
        self.audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=100)
        self._playing = False
        self._play_thread: threading.Thread | None = None
        self._volume = 0.7
        self._muted = False
        self._last_underrun_log: float = 0.0

        self._static_mode = False
        self._static_volume = 0.15
        self._radio_filter_strength = radio_filter_strength

    def _find_output_device(self) -> int | None:
        """Find the first available output device, preferring non-HDMI."""
        hdmi_indices = []
        for i in range(self.pa.get_device_count()):
            info = self.pa.get_device_info_by_index(i)
            if info.get("maxOutputChannels", 0) > 0:
                name = info.get("name", "").lower()
                logger.info("Found output device %d: %s", i, info.get("name"))
                if "hdmi" in name:
                    hdmi_indices.append(i)
                else:
                    return i
        if hdmi_indices:
            return hdmi_indices[0]
        return None

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

    # ------------------------------------------------------------------
    # Static noise
    # ------------------------------------------------------------------

    def _generate_static(self, num_samples: int | None = None) -> bytes:
        n = num_samples or self.CHUNK_SIZE
        scale = self._static_volume * 32767
        samples = [int(random.uniform(-scale, scale)) for _ in range(n)]
        return struct.pack(f"<{n}h", *samples)

    def _generate_static_segment(self, duration_ms: int, volume_db: float = -35) -> AudioSegment:
        num_samples = int(self.SAMPLE_RATE * duration_ms / 1000)
        raw = self._generate_static(num_samples)
        seg = AudioSegment(
            data=raw,
            sample_width=2,
            frame_rate=self.SAMPLE_RATE,
            channels=self.CHANNELS,
        )
        if seg.dBFS != float("-inf"):
            seg = seg + (volume_db - seg.dBFS)
        return seg

    def start_static(self):
        self._static_mode = True

    def stop_static(self):
        self._static_mode = False

    # ------------------------------------------------------------------
    # Radio voice filter
    # ------------------------------------------------------------------

    def _apply_radio_filter(self, audio: AudioSegment) -> AudioSegment:
        s = self._radio_filter_strength
        if s <= 0:
            return audio

        low_cut = int(20 + 280 * s)
        high_cut = int(20000 - 17000 * s)

        filtered = audio.high_pass_filter(low_cut)
        filtered = filtered.low_pass_filter(high_cut)
        filtered = filtered + (3 * s)

        static_db = -45 + (15 * s)
        static_layer = self._generate_static_segment(
            duration_ms=len(filtered),
            volume_db=static_db,
        )
        filtered = filtered.overlay(static_layer)

        return filtered

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def start(self):
        """Start the playback thread."""
        logger.info("Audio player starting")
        self._playing = True
        self._play_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._play_thread.start()

    def _playback_loop(self):
        while self._playing:
            try:
                audio_data = self.audio_queue.get(timeout=0.1)
                if self._muted:
                    silence = b"\x00" * len(audio_data)
                    self.stream.write(silence)
                else:
                    adjusted = self._apply_volume(audio_data)
                    self.stream.write(adjusted)
            except queue.Empty:
                if self._static_mode and not self._muted:
                    static_chunk = self._generate_static()
                    adjusted = self._apply_volume(static_chunk)
                    self.stream.write(adjusted)
                else:
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
        samples = struct.unpack(f"<{len(data) // 2}h", data)
        scaled = [int(s * self._volume) for s in samples]
        scaled = [max(-32768, min(32767, s)) for s in scaled]
        return struct.pack(f"<{len(scaled)}h", *scaled)

    def enqueue_mp3(self, mp3_bytes: bytes):
        """Convert MP3 bytes to PCM, apply radio filter, and add to playback queue."""
        if not mp3_bytes or len(mp3_bytes) < 4:
            logger.error("Received empty or too-small MP3 data, skipping")
            return
        try:
            audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
        except Exception as e:
            logger.error("Failed to decode MP3 (%d bytes): %s", len(mp3_bytes), e)
            return
        audio = audio.set_frame_rate(self.SAMPLE_RATE).set_channels(self.CHANNELS)
        audio = self._apply_radio_filter(audio)
        raw_data = audio.raw_data

        chunk_count = 0
        for i in range(0, len(raw_data), self.CHUNK_SIZE * 2):
            chunk = raw_data[i:i + self.CHUNK_SIZE * 2]
            try:
                self.audio_queue.put(chunk, timeout=5.0)
                chunk_count += 1
            except queue.Full:
                logger.warning("Audio queue full, dropping remaining chunks",
                               extra={"enqueued": chunk_count})
                break

        logger.debug("MP3 enqueued", extra={
            "input_bytes": len(mp3_bytes), "chunks": chunk_count,
        })

    def play_file(self, filepath: str):
        """Play an audio file (wav, mp3, etc.) from disk."""
        logger.debug("Playing file from disk", extra={"filepath": filepath})
        audio = AudioSegment.from_file(filepath)
        audio = audio.set_frame_rate(self.SAMPLE_RATE).set_channels(self.CHANNELS)
        raw_data = audio.raw_data
        for i in range(0, len(raw_data), self.CHUNK_SIZE * 2):
            chunk = raw_data[i:i + self.CHUNK_SIZE * 2]
            try:
                self.audio_queue.put(chunk, timeout=5.0)
            except queue.Full:
                break

    def interrupt(self):
        """Immediately stop current audio and flush the buffer."""
        self.clear_buffer()

    def clear_buffer(self):
        """Flush the audio queue (for channel switching)."""
        discarded = 0
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                discarded += 1
            except queue.Empty:
                break
        logger.debug("audio.buffer_cleared", extra={"chunks_discarded": discarded})

    def buffer_level(self) -> int:
        """Return current number of chunks in buffer."""
        return self.audio_queue.qsize()

    def stop(self):
        """Stop playback and clean up."""
        logger.info("Audio player stopping")
        self._playing = False
        if self._play_thread:
            self._play_thread.join(timeout=2.0)
        self.stream.stop_stream()
        self.stream.close()
        self.pa.terminate()
