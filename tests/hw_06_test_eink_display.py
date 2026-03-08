#!/usr/bin/env python3
"""
STEP 6 — Waveshare 2.13" E-Ink Display (SPI)

Prerequisites:
  SPI already enabled from Step 4.
  pip install waveshare-epd Pillow

Wiring — Waveshare 2.13" V4 HAT:
  If using the HAT connector, it just plugs onto the 40-pin header directly.

  If wiring manually:
  E-ink   →  Raspberry Pi
  ──────────────────────────
  VCC     →  3.3V  (Pi pin 1 or 17)
  GND     →  GND
  DIN     →  GPIO 10 / MOSI  (Pi pin 19)  — shared with MCP3008
  CLK     →  GPIO 11 / SCLK  (Pi pin 23)  — shared with MCP3008
  CS      →  GPIO  8 / CE0   (Pi pin 24)  — MCP3008 uses CE1
  DC      →  GPIO 25         (Pi pin 22)
  RST     →  GPIO 17         (Pi pin 11)
  BUSY    →  GPIO 24         (Pi pin 18)

  NOTE: The e-ink uses CE0 (GPIO 8), MCP3008 uses CE1 (GPIO 7).
  They share the SPI bus but have separate chip selects — this is fine.

Test: Displays a test pattern with text.
"""
import sys

def main():
    print("=== E-Ink Display Test ===\n")

    # Try to import the waveshare driver
    try:
        from waveshare_epd import epd2in13_V4
    except ImportError:
        print("ERROR: waveshare-epd not installed.")
        print("Install: pip install waveshare-epd")
        print("Or clone: git clone https://github.com/waveshare/e-Paper.git")
        print("  and install from RaspberryPi_JetsonNano/python/")
        sys.exit(1)

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("ERROR: Pillow not installed. Run: pip install Pillow")
        sys.exit(1)

    print("[A] Initializing display...")
    try:
        epd = epd2in13_V4.EPD()
        epd.init()
        print(f"  Display size: {epd.width}x{epd.height}")
    except Exception as e:
        print(f"  ERROR: {e}")
        print("  Check SPI is enabled and wiring matches pin assignments.")
        sys.exit(1)

    print("[B] Clearing display...")
    try:
        epd.Clear(0xFF)
    except Exception as e:
        print(f"  ERROR during clear: {e}")
        sys.exit(1)

    print("[C] Drawing test pattern...")
    try:
        # Note: width/height may be swapped for landscape
        image = Image.new("1", (epd.height, epd.width), 255)
        draw = ImageDraw.Draw(image)

        # Try to load a font, fall back to default
        try:
            font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
            font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
        except Exception:
            font_lg = ImageFont.load_default()
            font_sm = font_lg

        # Draw test content
        draw.rectangle([0, 0, epd.height - 1, epd.width - 1], outline=0)
        draw.text((10, 5), "RadioAgent", font=font_lg, fill=0)
        draw.text((10, 30), "E-Ink Display Test", font=font_sm, fill=0)
        draw.text((10, 50), "If you can read this,", font=font_sm, fill=0)
        draw.text((10, 68), "the display works!", font=font_sm, fill=0)

        # Draw a progress bar
        draw.rectangle([10, 90, 230, 105], outline=0)
        draw.rectangle([10, 90, 150, 105], fill=0)

        epd.display(epd.getbuffer(image))
        print("  Test pattern displayed — OK")
    except Exception as e:
        print(f"  ERROR drawing: {e}")
        sys.exit(1)

    print("[D] Putting display to sleep...")
    epd.sleep()

    print("\nPASS: Can you see 'RadioAgent' and a progress bar on the display? (y/n) ", end="")
    answer = input().strip().lower()
    if answer == "y":
        print("STEP 6 COMPLETE")
    else:
        print("Check wiring: DC, RST, BUSY pins. Also check CS is on CE0 (GPIO 8).")
        sys.exit(1)

if __name__ == "__main__":
    main()
