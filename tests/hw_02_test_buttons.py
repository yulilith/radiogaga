#!/usr/bin/env python3
"""
STEP 2 — Buttons
Wiring (all 6 buttons are active-low with internal pull-up):
  Button leg A → GPIO pin
  Button leg B → GND

  GPIO  5 (pin 29) — Daily Brief
  GPIO  6 (pin 31) — Talk Show
  GPIO 13 (pin 33) — Music
  GPIO 26 (pin 37) — Memos
  GPIO 16 (pin 36) — Call-in
  GPIO  4 (pin  7) — NFC/System

No external resistor needed — we use the Pi's internal pull-up.
When pressed, the GPIO reads LOW (0). When released, it reads HIGH (1).

Test: Press each button when prompted. 5-second timeout per button.
"""
import sys
import time

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("ERROR: RPi.GPIO not available. Run this on the Raspberry Pi.")
    sys.exit(1)

BUTTONS = {
    "Daily Brief": 5,
    "Talk Show":   6,
    "Music":       13,
    "Memos":       26,
    "Call-in":     16,
    "NFC/System":  4,
}

def main():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    for name, pin in BUTTONS.items():
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    print("=== Button Test ===")
    print("Press each button when prompted (5s timeout).\n")

    passed = 0
    for name, pin in BUTTONS.items():
        print(f"  Press [{name}] (GPIO {pin})...", end=" ", flush=True)
        deadline = time.time() + 5
        detected = False
        while time.time() < deadline:
            if GPIO.input(pin) == GPIO.LOW:
                detected = True
                break
            time.sleep(0.02)

        if detected:
            print("DETECTED — OK")
            passed += 1
            # Wait for release
            while GPIO.input(pin) == GPIO.LOW:
                time.sleep(0.02)
            time.sleep(0.2)
        else:
            print("TIMEOUT — FAIL")

    GPIO.cleanup()
    print(f"\n{passed}/{len(BUTTONS)} buttons working.")
    if passed == len(BUTTONS):
        print("STEP 2 COMPLETE")
    else:
        print("Check wiring: one leg to GPIO, other leg to GND.")
        print("Tactile buttons have 4 pins — make sure you're using pins on OPPOSITE sides.")
        sys.exit(1)

if __name__ == "__main__":
    main()
