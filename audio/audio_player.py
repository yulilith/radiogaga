import io
import queue
import time
import threading
import pyaudio
from pydub import AudioSegment

from log import get_logger

logger = get_logger(__name__)


class AudioPlayer:
    """Manages audio output with buffering, volume control, and channel switching."""

    CHUNK_SIZE = 1024
    SAMPLE_RATE = 22050
    CHANNELS = 1
    FORMAT = pyaudio.paInt16

    def __init__(self):
        self.pa = pyaudio.PyAudio()
        self.stream = self._open_output_stream()
        self.audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=100)
        self._playing = False
        self._play_thread: threading.Thread | None = None
        self._volume = 0.7  # 0.0 to 1.0
        self._muted = False
        self._last_underrun_log: float = 0.0  # rate-limit underrun warnings

    def _open_output_stream(self):
        """Try each output device until one opens successfully."""
        non_hdmi = []
        hdmi = []
        for i in range(self.pa.get_device_count()):
            info = self.pa.get_device_info_by_index(i)
            if info.get("maxOutputChannels", 0) > 0:
                name = info.get("name", "").lower()
                logger.info("Found output device %d: %s (rate=%.0f, channels=%d)",
                            i, info.get("name"), info.get("defaultSampleRate", 0),
                            info.get("maxOutputChannels", 0))
                if "hdmi" in name:
                    hdmi.append(i)
                else:
                    non_hdmi.append(i)

        # Try non-HDMI first, then HDMI, then default (no index)
        candidates = non_hdmi + hdmi + [None]
        for device_index in candidates:
            for rate in (self.SAMPLE_RATE, 44100, 48000):
                try:
                    kwargs = dict(
                        format=self.FORMAT,
                        channels=self.CHANNELS,
                        rate=rate,
                        output=True,
                        frames_per_buffer=self.CHUNK_SIZE,
                    )
                    if device_index is not None:
                        kwargs["output_device_index"] = device_index
                    stream = self.pa.open(**kwargs)
                    if rate != self.SAMPLE_RATE:
                        self.SAMPLE_RATE = rate
                    logger.info("Opened output device %s at %d Hz", device_index, rate)
                    return stream
                except OSError as e:
                    logger.debug("Device %s at %d Hz failed: %s", device_index, rate, e)

        raise RuntimeError(
            "No audio output device could be opened. "
            "Plug in a USB audio adapter, HDMI monitor with speakers, or enable the headphone jack."
        )

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
                audio_data = self.audio_queue.get(timeout=0.1)
                if self._muted:
                    # Play silence when muted
                    silence = b"\x00" * len(audio_data)
                    self.stream.write(silence)
                else:
                    # Apply volume
                    adjusted = self._apply_volume(audio_data)
                    self.stream.write(adjusted)
            except queue.Empty:
                # Buffer underrun -- play silence, log at most once per 5 seconds
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
        # Clamp to int16 range
        scaled = [max(-32768, min(32767, s)) for s in scaled]
        return struct.pack(f"<{len(scaled)}h", *scaled)

    def enqueue_mp3(self, mp3_bytes: bytes):
        """Convert MP3 bytes to PCM and add to playback queue."""
        if not mp3_bytes or len(mp3_bytes) < 4:
            logger.error("Received empty or too-small MP3 data, skipping")
            return
        try:
            audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
        except Exception as e:
            logger.error("Failed to decode MP3 (%d bytes): %s", len(mp3_bytes), e)
            return
        audio = audio.set_frame_rate(self.SAMPLE_RATE).set_channels(self.CHANNELS)
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

    def clear_buffer(self):
        """Flush the audio queue (for channel switching)."""
        discarded = 0
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                discarded += 1
            except queue.Empty:
                break
        logger.debug("Buffer cleared", extra={"chunks_discarded": discarded})

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
