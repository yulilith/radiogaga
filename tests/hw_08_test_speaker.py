#!/usr/bin/env python3
"""
STEP 8 — Speaker via USB Audio Adapter

Wiring:
  3W 8ohm speaker → JST PH2.0 connector → USB audio adapter → Pi USB port

  No GPIO needed. Audio goes through ALSA/USB.

Prerequisites:
  Plug in the USB audio adapter.
  pip install pyaudio  (or: sudo apt install python3-pyaudio)

Test: Generates a test tone and plays it through the USB audio output.
"""
import sys
import time
import math
import struct

def main():
    print("=== Speaker (USB Audio) Test ===\n")

    try:
        import pyaudio
    except ImportError:
        print("ERROR: pyaudio not installed.")
        print("Run: sudo apt install python3-pyaudio  OR  pip install pyaudio")
        sys.exit(1)

    pa = pyaudio.PyAudio()

    # List output devices
    print("[A] Checking audio output devices...")
    output_devices = []
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info["maxOutputChannels"] > 0:
            output_devices.append(i)
            marker = " <<<" if "usb" in info["name"].lower() else ""
            print(f"    [{i}] {info['name']} (out:{info['maxOutputChannels']}){marker}")

    if not output_devices:
        print("  ERROR: No output devices found. Is the USB audio adapter plugged in?")
        pa.terminate()
        sys.exit(1)

    # Generate a 440Hz test tone (1 second)
    print("\n[B] Playing test tone (440Hz, 1 second)...")
    sample_rate = 22050
    duration = 1.0
    frequency = 440.0
    volume = 0.5

    samples = int(sample_rate * duration)
    tone_data = b""
    for i in range(samples):
        sample = int(volume * 32767 * math.sin(2 * math.pi * frequency * i / sample_rate))
        tone_data += struct.pack("<h", sample)

    try:
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=sample_rate,
            output=True,
        )
        stream.write(tone_data)
        stream.stop_stream()
        stream.close()
    except Exception as e:
        print(f"  ERROR playing: {e}")
        pa.terminate()
        sys.exit(1)

    # Sweep test
    print("[C] Playing frequency sweep (200Hz → 2000Hz)...")
    sweep_data = b""
    sweep_duration = 2.0
    sweep_samples = int(sample_rate * sweep_duration)
    for i in range(sweep_samples):
        t = i / sample_rate
        freq = 200 + (2000 - 200) * (t / sweep_duration)
        sample = int(0.4 * 32767 * math.sin(2 * math.pi * freq * t))
        sweep_data += struct.pack("<h", sample)

    try:
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=sample_rate,
            output=True,
        )
        stream.write(sweep_data)
        stream.stop_stream()
        stream.close()
    except Exception as e:
        print(f"  ERROR: {e}")

    pa.terminate()

    print("\nDid you hear the tone and sweep? (y/n) ", end="")
    answer = input().strip().lower()
    if answer == "y":
        print("STEP 8 COMPLETE")
    else:
        print("Troubleshooting:")
        print("  1. Check USB audio adapter is plugged in")
        print("  2. Check speaker JST connector is seated fully")
        print("  3. Try: speaker-test -D default -t sine -f 440 -l 1")
        print("  4. Set default output: sudo raspi-config → System → Audio")
        sys.exit(1)

if __name__ == "__main__":
    main()
