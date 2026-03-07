"""USB microphone controller for call-in recording."""

import io
import wave
import pyaudio

from log import get_logger

logger = get_logger(__name__)


class MicController:
    """Records audio from USB microphone for call-in feature."""

    RATE = 16000        # 16kHz for speech recognition
    CHANNELS = 1
    CHUNK = 1024
    FORMAT = pyaudio.paInt16

    def __init__(self, max_seconds: int = 15):
        self.max_seconds = max_seconds
        self.pa = pyaudio.PyAudio()
        self._stream = None
        self._frames: list[bytes] = []
        self._recording = False
        self._input_device = self._find_mic()

    def _find_mic(self) -> int | None:
        """Find a USB microphone input device."""
        for i in range(self.pa.get_device_count()):
            info = self.pa.get_device_info_by_index(i)
            if info["maxInputChannels"] > 0:
                name = info.get("name", "").lower()
                # Prefer USB mic over built-in
                if "usb" in name or "microphone" in name:
                    logger.info("Found mic: %s (index %d)", info['name'], i)
                    return i
        # Fallback to default input
        try:
            default = self.pa.get_default_input_device_info()
            logger.info("Using default input: %s", default['name'])
            return default["index"]
        except IOError:
            logger.error("No input device found!")
            return None

    def start_recording(self):
        """Start recording from the microphone."""
        if self._input_device is None:
            logger.error("No input device available")
            return

        self._frames = []
        self._recording = True

        self._stream = self.pa.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.RATE,
            input=True,
            input_device_index=self._input_device,
            frames_per_buffer=self.CHUNK,
            stream_callback=self._callback,
        )
        self._stream.start_stream()
        logger.info("Recording started")

    def _callback(self, in_data, frame_count, time_info, status):
        if self._recording:
            self._frames.append(in_data)
            # Auto-stop at max duration
            total_frames = len(self._frames) * self.CHUNK
            if total_frames / self.RATE >= self.max_seconds:
                self._recording = False
                return (in_data, pyaudio.paComplete)
        return (in_data, pyaudio.paContinue if self._recording else pyaudio.paComplete)

    def stop_recording(self) -> bytes:
        """Stop recording and return WAV audio bytes."""
        self._recording = False

        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None

        if not self._frames:
            return b""

        # Convert frames to WAV bytes
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(self.pa.get_sample_size(self.FORMAT))
            wf.setframerate(self.RATE)
            wf.writeframes(b"".join(self._frames))

        wav_bytes = buffer.getvalue()
        duration = len(self._frames) * self.CHUNK / self.RATE
        logger.info("Recorded %.1fs (%d bytes)", duration, len(wav_bytes))
        self._frames = []
        return wav_bytes

    @property
    def is_recording(self) -> bool:
        return self._recording

    def cleanup(self):
        if self._stream:
            self._stream.close()
        self.pa.terminate()
