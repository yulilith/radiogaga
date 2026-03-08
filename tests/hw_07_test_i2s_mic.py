#!/usr/bin/env python3
"""
STEP 7 — INMP441 I2S Microphone

Prerequisites:
  Enable I2S overlay. Add to /boot/firmware/config.txt:
    dtoverlay=i2s-mmap
    dtoverlay=googlevoicehat-soundcard
  OR for generic I2S input:
    dtparam=i2s=on
  Then reboot.

  pip install sounddevice numpy

Wiring — INMP441:
  INMP441  →  Raspberry Pi
  ────────────────────────────
  VDD      →  3.3V  (Pi pin 1 or 17)
  GND      →  GND
  SCK      →  GPIO 18  (Pi pin 12) — I2S bit clock
  WS       →  GPIO 19  (Pi pin 35) — I2S word select (LRCK)
  SD       →  GPIO 20  (Pi pin 38) — I2S data in
  L/R      →  GND  (selects left channel)

Test: Records 3 seconds of audio and checks signal level.
"""
import sys
import time

def main():
    print("=== I2S Microphone (INMP441) Test ===\n")

    # Check for I2S device
    print("[A] Checking audio devices...")
    try:
        import sounddevice as sd
        import numpy as np
    except ImportError:
        print("ERROR: sounddevice/numpy not installed.")
        print("Run: pip install sounddevice numpy")
        sys.exit(1)

    devices = sd.query_devices()
    print(f"  Found {len(devices)} audio device(s):")
    input_devices = []
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0:
            input_devices.append(i)
            marker = " <<<" if "i2s" in d["name"].lower() or "google" in d["name"].lower() else ""
            print(f"    [{i}] {d['name']} (in:{d['max_input_channels']}){marker}")

    if not input_devices:
        print("  ERROR: No input devices found.")
        print("  Check: dtoverlay in /boot/firmware/config.txt, then reboot.")
        sys.exit(1)

    # Try to record
    print("\n[B] Recording 3 seconds... Speak or make noise!")
    sample_rate = 16000
    duration = 3

    try:
        recording = sd.rec(
            int(duration * sample_rate),
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
        )
        sd.wait()
    except Exception as e:
        print(f"  ERROR recording: {e}")
        print("  If 'Invalid number of channels': the I2S overlay may not be loaded.")
        print("  Try: arecord -l  to list ALSA capture devices.")
        sys.exit(1)

    # Analyze
    print("\n[C] Analyzing recording...")
    peak = int(np.max(np.abs(recording)))
    rms = int(np.sqrt(np.mean(recording.astype(float) ** 2)))
    print(f"  Peak amplitude: {peak} / 32767")
    print(f"  RMS level:      {rms}")

    if peak < 50:
        print("\n  WARNING: Very low signal — might be silence or no data.")
        print("  Check: L/R pin to GND? SD pin to GPIO 20? I2S overlay loaded?")
        print("  Try: arecord -D hw:1,0 -f S16_LE -r 16000 -c 1 -d 3 test.wav")
        sys.exit(1)
    elif peak < 500:
        print("\n  Low signal — mic might be working but very quiet.")
        print("  Try speaking loudly right next to the mic.")
    else:
        print("\n  Signal looks good!")

    # Save test recording
    try:
        import wave
        with wave.open("/tmp/mic_test.wav", "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(recording.tobytes())
        print("  Saved test recording to /tmp/mic_test.wav")
        print("  Play it back: aplay /tmp/mic_test.wav")
    except Exception:
        pass

    print("\nPASS: Microphone captured audio.")
    print("STEP 7 COMPLETE")

if __name__ == "__main__":
    main()
