import io
import queue
import threading
import pyaudio
from pydub import AudioSegment


class AudioPlayer:
    """Manages audio output with buffering, volume control, and channel switching."""

    CHUNK_SIZE = 1024
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
        self.audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=100)
        self._playing = False
        self._play_thread: threading.Thread | None = None
        self._volume = 0.7  # 0.0 to 1.0
        self._muted = False

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = max(0.0, min(1.0, value))

    @property
    def muted(self) -> bool:
        return self._muted

    def toggle_mute(self):
        self._muted = not self._muted

    def start(self):
        """Start the playback thread."""
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
                # Buffer underrun -- play silence
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
        audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
        audio = audio.set_frame_rate(self.SAMPLE_RATE).set_channels(self.CHANNELS)
        raw_data = audio.raw_data

        for i in range(0, len(raw_data), self.CHUNK_SIZE * 2):
            chunk = raw_data[i:i + self.CHUNK_SIZE * 2]
            try:
                self.audio_queue.put(chunk, timeout=5.0)
            except queue.Full:
                break

    def play_file(self, filepath: str):
        """Play an audio file (wav, mp3, etc.) from disk."""
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
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def buffer_level(self) -> int:
        """Return current number of chunks in buffer."""
        return self.audio_queue.qsize()

    def stop(self):
        """Stop playback and clean up."""
        self._playing = False
        if self._play_thread:
            self._play_thread.join(timeout=2.0)
        self.stream.stop_stream()
        self.stream.close()
        self.pa.terminate()
