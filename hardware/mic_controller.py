"""Microphone controller for call-in recording.

Supports two backends:
  - I2S: INMP441 via ALSA (arecord / sounddevice) — preferred on Pi 5
  - USB: USB audio adapter via PyAudio — fallback
"""

import io
import wave

from log import get_logger

logger = get_logger(__name__)


class MicController:
    """Records audio from INMP441 (I2S) or USB microphone for call-in feature."""

    RATE = 16000        # 16 kHz for speech recognition
    CHANNELS = 1
    CHUNK = 1024
    SAMPLE_WIDTH = 2    # 16-bit = 2 bytes

    def __init__(self, config: dict | None = None, max_seconds: int = 15):
        self.max_seconds = max_seconds
        self._frames: list[bytes] = []
        self._recording = False
        self._stream = None
        self._backend = "none"

        mic_cfg = (config or {}).get("MIC", {})
        mic_type = mic_cfg.get("type", "usb")
        self.RATE = mic_cfg.get("sample_rate", self.RATE)
        self.CHANNELS = mic_cfg.get("channels", self.CHANNELS)
        self.CHUNK = mic_cfg.get("chunk_size", self.CHUNK)

        if mic_type == "i2s":
            self._try_i2s()
        if self._backend == "none":
            self._try_pyaudio()

    # ── I2S backend (INMP441 via sounddevice / ALSA) ──

    def _try_i2s(self):
        """Try to initialise I2S input via the sounddevice library."""
        try:
            import sounddevice as sd
            # Find I2S capture device (usually "snd_rpi_i2s" or card name)
            devices = sd.query_devices()
            i2s_idx = None
            for i, d in enumerate(devices):
                if d["max_input_channels"] > 0:
                    name = d["name"].lower()
                    if "i2s" in name or "inmp" in name or "snd_rpi" in name:
                        i2s_idx = i
                        break
            if i2s_idx is None:
                # Fall back to default input
                default = sd.default.device[0]
                if default is not None and default >= 0:
                    i2s_idx = int(default)
            if i2s_idx is not None:
                self._sd = sd
                self._sd_device = i2s_idx
                self._backend = "sounddevice"
                logger.info("I2S mic ready via sounddevice (device %d: %s)",
                            i2s_idx, devices[i2s_idx]["name"])
        except (ImportError, Exception) as e:
            logger.debug("sounddevice not available: %s", e)

    # ── USB / PyAudio backend ──

    def _try_pyaudio(self):
        """Fallback to PyAudio for USB mic."""
        try:
            import pyaudio
            self._pa = pyaudio.PyAudio()
            self._pa_format = pyaudio.paInt16
            self._pa_device = self._find_usb_mic()
            if self._pa_device is not None:
                self._backend = "pyaudio"
                logger.info("USB mic ready via PyAudio (device %d)", self._pa_device)
            else:
                logger.warning("No input device found via PyAudio")
        except (ImportError, Exception) as e:
            logger.warning("PyAudio not available: %s", e)

    def _find_usb_mic(self) -> int | None:
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            if info["maxInputChannels"] > 0:
                name = info.get("name", "").lower()
                if "usb" in name or "microphone" in name:
                    return i
        try:
            default = self._pa.get_default_input_device_info()
            return default["index"]
        except IOError:
            return None

    # ── Recording interface ──

    def start_recording(self):
        """Start recording from the microphone."""
        self._frames = []
        self._recording = True

        if self._backend == "sounddevice":
            self._start_sounddevice()
        elif self._backend == "pyaudio":
            self._start_pyaudio()
        else:
            logger.error("No mic backend available")
            self._recording = False
            return

        logger.info("Recording started (backend=%s)", self._backend)

    def _start_sounddevice(self):
        import numpy as np

        def _sd_callback(indata, frames, time_info, status):
            if status:
                logger.debug("sounddevice status: %s", status)
            if self._recording:
                self._frames.append(indata.copy().tobytes())
                total = len(self._frames) * self.CHUNK
                if total / self.RATE >= self.max_seconds:
                    self._recording = False
                    raise self._sd.CallbackAbort()

        self._stream = self._sd.InputStream(
            samplerate=self.RATE,
            channels=self.CHANNELS,
            dtype="int16",
            blocksize=self.CHUNK,
            device=self._sd_device,
            callback=_sd_callback,
        )
        self._stream.start()

    def _start_pyaudio(self):
        import pyaudio

        def _pa_callback(in_data, frame_count, time_info, status):
            if self._recording:
                self._frames.append(in_data)
                total = len(self._frames) * self.CHUNK
                if total / self.RATE >= self.max_seconds:
                    self._recording = False
                    return (in_data, pyaudio.paComplete)
            return (in_data, pyaudio.paContinue if self._recording else pyaudio.paComplete)

        self._stream = self._pa.open(
            format=self._pa_format,
            channels=self.CHANNELS,
            rate=self.RATE,
            input=True,
            input_device_index=self._pa_device,
            frames_per_buffer=self.CHUNK,
            stream_callback=_pa_callback,
        )
        self._stream.start_stream()

    def stop_recording(self) -> bytes:
        """Stop recording and return WAV audio bytes."""
        self._recording = False

        if self._stream is not None:
            try:
                if self._backend == "sounddevice":
                    self._stream.stop()
                    self._stream.close()
                else:
                    self._stream.stop_stream()
                    self._stream.close()
            except Exception as e:
                logger.debug("stream close: %s", e)
            self._stream = None

        if not self._frames:
            return b""

        # Convert frames to WAV bytes
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(self.SAMPLE_WIDTH)
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
        if self._stream is not None:
            try:
                if self._backend == "sounddevice":
                    self._stream.close()
                else:
                    self._stream.close()
            except Exception:
                pass
        if self._backend == "pyaudio" and hasattr(self, "_pa"):
            self._pa.terminate()
