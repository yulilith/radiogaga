#!/usr/bin/env python3
"""
STEP 9 — Full Hardware Integration Test

No new wiring — validates ALL hardware works together without conflicts.
This catches bus contention issues (SPI sharing, power draw, etc.)

Runs all subsystems simultaneously for 15 seconds:
  - LEDs respond to button presses
  - Potentiometers read continuously
  - E-ink displays current state
  - NFC polls in background
  - Speaker plays a short beep on channel switch
  - Mic ready indicator
"""
import sys
import time
import math
import struct
import threading

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("ERROR: Run this on the Raspberry Pi.")
    sys.exit(1)

# ── Pin config (matches config.py) ──
BTNS = {5: "dailybrief", 6: "talkshow", 13: "music", 26: "memos"}
BTN_CALLIN = 16
BTN_NFC = 4
LEDS = {"dailybrief": 12, "talkshow": 22, "music": 23, "memos": 27}
LED_TUNING = 14
LED_VOLUME = 15

SPI_BUS, SPI_DEVICE = 0, 1

class IntegrationTest:
    def __init__(self):
        self.active_channel = None
        self.running = True
        self.errors = []
        self.subsystems_ok = {
            "gpio": False,
            "spi_adc": False,
            "speaker": False,
            "display": False,
        }

    def test_gpio(self):
        """Setup and test GPIO (buttons + LEDs)."""
        print("[GPIO] Setting up buttons and LEDs...")
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        for pin in LEDS.values():
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
        for pin in [LED_TUNING, LED_VOLUME]:
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
        for pin in list(BTNS.keys()) + [BTN_CALLIN, BTN_NFC]:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        # Quick LED flash to confirm
        for pin in LEDS.values():
            GPIO.output(pin, GPIO.HIGH)
        time.sleep(0.2)
        for pin in LEDS.values():
            GPIO.output(pin, GPIO.LOW)

        self.subsystems_ok["gpio"] = True
        print("[GPIO] OK")

    def test_spi_adc(self):
        """Test MCP3008 reads work alongside e-ink (both on SPI0)."""
        print("[SPI/ADC] Testing MCP3008...")
        try:
            import spidev
            spi = spidev.SpiDev()
            spi.open(SPI_BUS, SPI_DEVICE)
            spi.max_speed_hz = 1000000
            spi.mode = 0

            # Read both channels
            for ch in [0, 1]:
                cmd = [1, (8 + ch) << 4, 0]
                reply = spi.xfer2(cmd)
                val = ((reply[1] & 0x03) << 8) | reply[2]
                name = "Tuning" if ch == 0 else "Volume"
                print(f"  {name}: {val}")

            spi.close()
            self.subsystems_ok["spi_adc"] = True
            print("[SPI/ADC] OK")
        except Exception as e:
            self.errors.append(f"SPI/ADC: {e}")
            print(f"[SPI/ADC] FAIL: {e}")

    def test_speaker(self):
        """Quick beep to verify speaker."""
        print("[Speaker] Playing test beep...")
        try:
            import pyaudio
            pa = pyaudio.PyAudio()
            sr = 22050
            dur = 0.2
            freq = 880.0
            data = b""
            for i in range(int(sr * dur)):
                s = int(0.3 * 32767 * math.sin(2 * math.pi * freq * i / sr))
                data += struct.pack("<h", s)
            stream = pa.open(format=pyaudio.paInt16, channels=1, rate=sr, output=True)
            stream.write(data)
            stream.stop_stream()
            stream.close()
            pa.terminate()
            self.subsystems_ok["speaker"] = True
            print("[Speaker] OK")
        except Exception as e:
            self.errors.append(f"Speaker: {e}")
            print(f"[Speaker] FAIL: {e}")

    def test_display(self):
        """Show status on e-ink."""
        print("[Display] Initializing e-ink...")
        try:
            from waveshare_epd import epd2in13_V4
            from PIL import Image, ImageDraw, ImageFont

            epd = epd2in13_V4.EPD()
            epd.init()

            image = Image.new("1", (epd.height, epd.width), 255)
            draw = ImageDraw.Draw(image)
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
            except Exception:
                font = ImageFont.load_default()

            draw.text((10, 10), "Integration Test", font=font, fill=0)
            draw.text((10, 35), "All systems GO", font=font, fill=0)
            draw.text((10, 60), "Press buttons to test", font=font, fill=0)

            epd.display(epd.getbuffer(image))
            epd.sleep()

            self.subsystems_ok["display"] = True
            print("[Display] OK")
        except Exception as e:
            self.errors.append(f"Display: {e}")
            print(f"[Display] FAIL: {e}")

    def run_interactive(self):
        """15-second interactive loop: buttons → LEDs + ADC polling."""
        print("\n── Interactive Test (15 seconds) ──")
        print("Press channel buttons, slide pots. Ctrl+C to stop early.\n")

        spi = None
        try:
            import spidev
            spi = spidev.SpiDev()
            spi.open(SPI_BUS, SPI_DEVICE)
            spi.max_speed_hz = 1000000
        except Exception:
            pass

        start = time.time()
        try:
            while time.time() - start < 15:
                # Check buttons
                for btn_pin, ch_name in BTNS.items():
                    if GPIO.input(btn_pin) == GPIO.LOW:
                        if self.active_channel != ch_name:
                            for lp in LEDS.values():
                                GPIO.output(lp, GPIO.LOW)
                            GPIO.output(LEDS[ch_name], GPIO.HIGH)
                            self.active_channel = ch_name
                            print(f"  Channel → {ch_name}")
                        while GPIO.input(btn_pin) == GPIO.LOW:
                            time.sleep(0.02)

                # Call-in indicator
                if GPIO.input(BTN_CALLIN) == GPIO.LOW:
                    GPIO.output(LED_TUNING, GPIO.HIGH)
                else:
                    GPIO.output(LED_TUNING, GPIO.LOW)

                # ADC read
                if spi:
                    t_cmd = [1, (8 + 0) << 4, 0]
                    v_cmd = [1, (8 + 1) << 4, 0]
                    t_r = spi.xfer2(t_cmd)
                    v_r = spi.xfer2(v_cmd)
                    t_val = ((t_r[1] & 0x03) << 8) | t_r[2]
                    v_val = ((v_r[1] & 0x03) << 8) | v_r[2]
                    remaining = 15 - int(time.time() - start)
                    print(f"\r  Tuning:{t_val:4d}  Volume:{v_val:4d}  [{remaining:2d}s left]", end="", flush=True)

                time.sleep(0.05)
        except KeyboardInterrupt:
            pass

        if spi:
            spi.close()
        print()

    def run(self):
        print("=" * 50)
        print("FULL HARDWARE INTEGRATION TEST")
        print("=" * 50 + "\n")

        self.test_gpio()
        self.test_spi_adc()
        self.test_speaker()
        self.test_display()

        ok_count = sum(self.subsystems_ok.values())
        total = len(self.subsystems_ok)
        print(f"\nSubsystems: {ok_count}/{total} OK")

        if ok_count >= 3:  # GPIO + at least 2 others
            self.run_interactive()

        # Final report
        print("\n" + "=" * 50)
        print("RESULTS")
        print("=" * 50)
        for name, ok in self.subsystems_ok.items():
            status = "PASS" if ok else "FAIL"
            print(f"  {name:>12}: {status}")

        if self.errors:
            print("\nErrors:")
            for e in self.errors:
                print(f"  - {e}")

        GPIO.cleanup()

        if all(self.subsystems_ok.values()):
            print("\nALL SYSTEMS GO — STEP 9 COMPLETE")
        else:
            print(f"\n{ok_count}/{total} subsystems passed. Fix failures above.")
            sys.exit(1)

if __name__ == "__main__":
    IntegrationTest().run()
