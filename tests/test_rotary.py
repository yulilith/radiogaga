import sys
import threading

from gpiozero import RotaryEncoder, Button
from signal import pause

# Pin assignments must match config.py:
#   Tuning: enc_tuning_clk=14 (A), enc_tuning_dt=15 (B)
#   Volume: enc_volume_clk=23 (A), enc_volume_dt=21 (B)
# Buttons: btn_callin=4 (tuning), btn_nfc=3 (volume)
# If rotation direction feels reversed, swap the two pin args for that encoder.
tuning_enc = RotaryEncoder(14, 15, max_steps=0)
tuning_btn = Button(4, pull_up=True)

vol_enc = RotaryEncoder(23, 21, max_steps=0)
vol_btn = Button(3, pull_up=True)


def print_encoder_values():
    """Print current tuning and volume encoder step values."""
    print(f"  [Encoders] Tuning: {tuning_enc.steps}  |  Volume: {vol_enc.steps}")


def tuning_rotated():
    print_encoder_values()

def vol_rotated():
    print_encoder_values()

def tuning_pressed():
    print("Tuning button pressed (Call-in feature)")

def vol_pressed():
    print("Volume button pressed (NFC feature)")

# Attach callbacks
tuning_enc.when_rotated = tuning_rotated
vol_enc.when_rotated = vol_rotated
tuning_btn.when_pressed = tuning_pressed
vol_btn.when_pressed = vol_pressed

def _keyboard_listener():
    """Print encoder values when user types 'v' + Enter."""
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            if line.strip().lower() == "v":
                print_encoder_values()
        except (EOFError, KeyboardInterrupt):
            break

print("Rotary Encoder Test Initialized.")
print("Rotate encoders to see values; press them to test switches.")
print("Type 'v' + Enter to print encoder values on demand.")
print("Press Ctrl+C to exit.")

threading.Thread(target=_keyboard_listener, daemon=True).start()
pause()
