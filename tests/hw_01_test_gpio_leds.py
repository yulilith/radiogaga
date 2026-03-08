#!/usr/bin/env python3
"""
STEP 1 — LEDs
Wiring:
  GPIO 12 → 220Ω → LED anode (+) → LED cathode (-) → GND   (Daily Brief)
  GPIO 22 → 220Ω → LED anode (+) → LED cathode (-) → GND   (Talk Show)
  GPIO 23 → 220Ω → LED anode (+) → LED cathode (-) → GND   (Music)
  GPIO 27 → 220Ω → LED anode (+) → LED cathode (-) → GND   (Memos)
  GPIO 14 → 220Ω → LED anode (+) → LED cathode (-) → GND   (Tuning slider)
  GPIO 15 → 220Ω → LED anode (+) → LED cathode (-) → GND   (Volume slider)

Use any GND pin: physical pins 6, 9, 14, 20, 25, 30, 34, 39.

Test: Each LED lights up for 1 second in sequence, then all blink 3x.
"""
import sys
import time

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("ERROR: RPi.GPIO not available. Run this on the Raspberry Pi.")
    sys.exit(1)

LEDS = {
    "Daily Brief": 12,
    "Talk Show":   22,
    "Music":       23,
    "Memos":       27,
    "Tuning":      14,
    "Volume":      15,
}

def main():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    for name, pin in LEDS.items():
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

    print("=== LED Test ===")
    print("Each LED should light up for 1 second.\n")

    # Individual test
    for name, pin in LEDS.items():
        print(f"  [{pin:>2}] {name}...", end=" ", flush=True)
        GPIO.output(pin, GPIO.HIGH)
        time.sleep(1)
        GPIO.output(pin, GPIO.LOW)
        print("OK")
        time.sleep(0.2)

    # All blink
    print("\nAll LEDs blinking 3x...")
    all_pins = list(LEDS.values())
    for i in range(3):
        for p in all_pins:
            GPIO.output(p, GPIO.HIGH)
        time.sleep(0.3)
        for p in all_pins:
            GPIO.output(p, GPIO.LOW)
        time.sleep(0.3)

    GPIO.cleanup()
    print("\nPASS: All 6 LEDs tested. Did they all light up? (y/n) ", end="")
    answer = input().strip().lower()
    if answer == "y":
        print("STEP 1 COMPLETE")
    else:
        print("Check wiring: LED long leg (anode) → resistor → GPIO, short leg (cathode) → GND")
        sys.exit(1)

if __name__ == "__main__":
    main()
