#!/usr/bin/env python3
"""
STEP 4 — MCP3008 ADC + Slide Potentiometers (SPI)

Prerequisites:
  sudo raspi-config → Interface Options → SPI → Enable
  (reboot if just enabled)

Wiring — MCP3008 (16-pin DIP):
  MCP3008 Pin  →  Connection
  ──────────────────────────────
  VDD  (pin 16) → 5V (Pi pin 2 or 4)
  VREF (pin 15) → 5V (Pi pin 2 or 4)
  AGND (pin 14) → GND
  CLK  (pin 13) → GPIO 11 / SCLK (Pi pin 23)
  DOUT (pin 12) → GPIO  9 / MISO (Pi pin 21)
  DIN  (pin 11) → GPIO 10 / MOSI (Pi pin 19)
  CS   (pin 10) → GPIO  7 / CE1  (Pi pin 26)
  DGND (pin  9) → GND

Wiring — HW-233 Slide Potentiometers:
  Tuning pot:
    OTB (outer pin) → 5V
    GND (outer pin) → GND
    OTA (middle/wiper) → MCP3008 CH0 (pin 1)

  Volume pot:
    OTB (outer pin) → 5V
    GND (outer pin) → GND
    OTA (middle/wiper) → MCP3008 CH1 (pin 2)

Test: Reads both channels for 10 seconds. Slide the pots and watch values change.
"""
import sys
import time

try:
    import spidev
except ImportError:
    print("ERROR: spidev not installed. Run: pip install spidev")
    sys.exit(1)

SPI_BUS = 0
SPI_DEVICE = 1  # CE1
TUNING_CH = 0
VOLUME_CH = 1

def read_channel(spi, channel):
    """Read a single MCP3008 channel (0-7). Returns 0-1023."""
    cmd = [1, (8 + channel) << 4, 0]
    reply = spi.xfer2(cmd)
    value = ((reply[1] & 0x03) << 8) | reply[2]
    return value

def main():
    spi = spidev.SpiDev()
    try:
        spi.open(SPI_BUS, SPI_DEVICE)
    except FileNotFoundError:
        print("ERROR: SPI device not found. Enable SPI in raspi-config and reboot.")
        sys.exit(1)

    spi.max_speed_hz = 1000000
    spi.mode = 0

    print("=== MCP3008 ADC + Slide Potentiometer Test ===")
    print("Slide the potentiometers — values should change (0-1023).")
    print("Running for 10 seconds...\n")

    tuning_min, tuning_max = 1023, 0
    volume_min, volume_max = 1023, 0

    start = time.time()
    while time.time() - start < 10:
        t = read_channel(spi, TUNING_CH)
        v = read_channel(spi, VOLUME_CH)

        tuning_min = min(tuning_min, t)
        tuning_max = max(tuning_max, t)
        volume_min = min(volume_min, v)
        volume_max = max(volume_max, v)

        t_bar = "#" * (t * 30 // 1023)
        v_bar = "#" * (v * 30 // 1023)

        print(f"\r  Tuning: {t:4d} [{t_bar:<30}]  |  Volume: {v:4d} [{v_bar:<30}]", end="", flush=True)
        time.sleep(0.1)

    spi.close()

    print("\n")
    print(f"  Tuning range seen: {tuning_min} — {tuning_max}")
    print(f"  Volume range seen: {volume_min} — {volume_max}")

    errors = []
    # Check that we saw at least some range (they moved the pots)
    if tuning_max - tuning_min < 100:
        errors.append("Tuning pot: barely any change detected. Check CH0 wiring.")
    if volume_max - volume_min < 100:
        errors.append("Volume pot: barely any change detected. Check CH1 wiring.")
    # Check we're getting real data (not all zeros or all 1023)
    if tuning_max == 0 and volume_max == 0:
        errors.append("Both channels read 0. Check MCP3008 VDD/VREF (should be 5V).")
    if tuning_min == 1023 and volume_min == 1023:
        errors.append("Both channels stuck at 1023. Check MCP3008 GND connections.")

    if errors:
        print("\nISSUES:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("\nPASS: Both potentiometers responding.")
        print("STEP 4 COMPLETE")

if __name__ == "__main__":
    main()
