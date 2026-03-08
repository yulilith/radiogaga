from gpiozero import RotaryEncoder, Button
from signal import pause

# Tuning Encoder (GPIO 14, 15) + Switch (GPIO 4)
tuning_enc = RotaryEncoder(14, 15, max_steps=0)
tuning_btn = Button(4, pull_up=True)

# Volume Encoder (GPIO 23, 21) + Switch (GPIO 3)
vol_enc = RotaryEncoder(23, 21, max_steps=0)
vol_btn = Button(3, pull_up=True)

def tuning_rotated():
    print(f"Tuning position: {tuning_enc.steps}")

def vol_rotated():
    print(f"Volume position: {vol_enc.steps}")

def tuning_pressed():
    print("Tuning button pressed (Call-in feature)")

def vol_pressed():
    print("Volume button pressed (NFC feature)")

# Attach callbacks
tuning_enc.when_rotated = tuning_rotated
vol_enc.when_rotated = vol_rotated
tuning_btn.when_pressed = tuning_pressed
vol_btn.when_pressed = vol_pressed

print("Rotary Encoder Test Initialized.")
print("Rotate encoders to see values; press them to test switches.")
print("Press Ctrl+C to exit.")

pause()
