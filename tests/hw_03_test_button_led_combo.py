#!/usr/bin/env python3
"""
STEP 3 — Button + LED Integration
Verifies buttons and LEDs work together: press a channel button → its LED lights up.

No new wiring needed — just validates Step 1 + Step 2 together.

Test: Press channel buttons to toggle LEDs. Press Ctrl+C to exit.
"""
import sys
import time

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("ERROR: RPi.GPIO not available. Run this on the Raspberry Pi.")
    sys.exit(1)

CHANNEL_MAP = {
    # button_gpio: (name, led_gpio)
    5:  ("Daily Brief", 12),
    6:  ("Talk Show",   22),
    13: ("Music",       23),
    26: ("Memos",       27),
}

CALLIN_BTN = 16
CALLIN_LED = 14  # repurpose tuning LED for visual feedback

def main():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    all_led_pins = [12, 22, 23, 27, 14, 15]
    all_btn_pins = [5, 6, 13, 26, 16, 4]

    for pin in all_led_pins:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
    for pin in all_btn_pins:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    print("=== Button + LED Integration Test ===")
    print("Press channel buttons — the matching LED should light up.")
    print("Hold call-in button — tuning LED stays on while held.")
    print("Press Ctrl+C to exit.\n")

    active_channel = None
    try:
        while True:
            # Channel buttons
            for btn_pin, (name, led_pin) in CHANNEL_MAP.items():
                if GPIO.input(btn_pin) == GPIO.LOW:
                    if active_channel != btn_pin:
                        # Turn off all channel LEDs
                        for _, (_, lp) in CHANNEL_MAP.items():
                            GPIO.output(lp, GPIO.LOW)
                        # Light the selected one
                        GPIO.output(led_pin, GPIO.HIGH)
                        active_channel = btn_pin
                        print(f"  Channel: {name}")
                    # Debounce: wait for release
                    while GPIO.input(btn_pin) == GPIO.LOW:
                        time.sleep(0.02)

            # Call-in button: LED on while held
            if GPIO.input(CALLIN_BTN) == GPIO.LOW:
                GPIO.output(CALLIN_LED, GPIO.HIGH)
            else:
                GPIO.output(CALLIN_LED, GPIO.LOW)

            time.sleep(0.02)

    except KeyboardInterrupt:
        print("\n")

    GPIO.cleanup()
    print("STEP 3 COMPLETE (manual verification)")

if __name__ == "__main__":
    main()
