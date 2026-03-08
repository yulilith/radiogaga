"""
Prerequisites:
  sudo raspi-config → Interface Options → SPI → Enable
  (reboot if just enabled)
  pip install spidev mfrc522

Wiring — RFID-RC522 module:
  RC522   →  Raspberry Pi
  ────────────────────────
  NSS     →  GPIO 8 (CE0)  — Pin 24
  SCK     →  GPIO 11 (SCLK) — Pin 23
  MOSI    →  GPIO 10 (MOSI) — Pin 19
  MISO    →  GPIO 9 (MISO)  — Pin 21
  IRQ     →  Not connected
  GND     →  GND — Pin 6
  RST     →  GPIO 25 — Pin 22
  VCC     →  3.3V — Pin 1

Test: Detects the RC522, then waits for you to tap an RFID tag.
"""
import sys
import time

def main():
    print("=== RFID Reader (MFRC522 SPI) Test ===\n")

    # Step A: Initialize RC522
    print("[A] Initializing MFRC522...")
    try:
        from mfrc522 import SimpleMFRC522
        import RPi.GPIO as GPIO

        reader = SimpleMFRC522()
        print("  MFRC522 initialized — OK")
    except Exception as e:
        print(f"  ERROR: {e}")
        print("  Check: SPI enabled in raspi-config? spidev and mfrc522 installed?")
        sys.exit(1)

    # Step B: Read a tag
    print("\n[B] Tap an RFID tag (hold it on the reader)...")
    print("    Press Ctrl+C to cancel.\n")
    try:
        id, text = reader.read()
        print(f"  TAG DETECTED")
        print(f"  UID:  {id}")
        print(f"  Data: {text.strip() if text else '(empty)'}")
        print("\nPASS: RFID reader working.")
    except KeyboardInterrupt:
        print("\n  Cancelled by user.")
    except Exception as e:
        print(f"  ERROR reading tag: {e}")
    finally:
        GPIO.cleanup()

if __name__ == "__main__":
    main()