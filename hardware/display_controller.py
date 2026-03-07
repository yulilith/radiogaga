"""E-ink display controller — Waveshare 2.13" V4 HAT.

SPI0 CE0 wiring (shared bus with MCP3008 on CE1):
  DIN  = GPIO 10 (MOSI)    pin 19
  CLK  = GPIO 11 (SCLK)    pin 23
  CS   = GPIO  8 (CE0)     pin 24
  DC   = GPIO 25            pin 22
  RST  = GPIO 17            pin 11
  BUSY = GPIO 24            pin 18
"""

import time
from log import get_logger

logger = get_logger(__name__)


class DisplayController:
    """Drives the Waveshare 2.13" V4 e-ink display for station info."""

    def __init__(self, config: dict):
        self.config = config
        self._epd = None
        self._font = None
        self._font_small = None
        self._width = config.get("DISPLAY", {}).get("width", 250)
        self._height = config.get("DISPLAY", {}).get("height", 122)

        try:
            from waveshare_epd import epd2in13_V4
            from PIL import Image, ImageDraw, ImageFont

            self._epd = epd2in13_V4.EPD()
            self._epd.init()
            self._epd.Clear(0xFF)
            self._Image = Image
            self._ImageDraw = ImageDraw
            self._ImageFont = ImageFont

            # Load fonts
            try:
                self._font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
                self._font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
            except OSError:
                self._font = ImageFont.load_default()
                self._font_small = self._font

            logger.info("E-ink display initialized (%dx%d)", self._width, self._height)
        except (ImportError, Exception) as e:
            logger.info("E-ink display not available: %s", e)
            self._epd = None

    @property
    def available(self) -> bool:
        return self._epd is not None

    def update(self, channel: str, subchannel: str = "",
               now_playing: str = "", volume: int = 70):
        """Refresh the display with current station info."""
        if not self._epd:
            logger.debug("Display update (no hardware): ch=%s sub=%s", channel, subchannel)
            return

        Image = self._Image
        ImageDraw = self._ImageDraw

        # Create image (landscape: width x height)
        image = Image.new("1", (self._width, self._height), 255)
        draw = ImageDraw.Draw(image)

        # Channel name (large, top)
        draw.text((5, 5), channel, font=self._font, fill=0)

        # Subchannel (medium, below channel)
        if subchannel:
            draw.text((5, 30), subchannel, font=self._font_small, fill=0)

        # Divider line
        draw.line([(5, 50), (self._width - 5, 50)], fill=0, width=1)

        # Now playing / info area
        if now_playing:
            # Truncate long text
            if len(now_playing) > 35:
                now_playing = now_playing[:32] + "..."
            draw.text((5, 55), now_playing, font=self._font_small, fill=0)

        # Volume bar (bottom)
        bar_y = self._height - 20
        draw.text((5, bar_y), "VOL", font=self._font_small, fill=0)
        bar_x = 35
        bar_w = self._width - 45
        draw.rectangle([(bar_x, bar_y + 2), (bar_x + bar_w, bar_y + 12)], outline=0)
        fill_w = int(bar_w * volume / 100)
        if fill_w > 0:
            draw.rectangle([(bar_x, bar_y + 2), (bar_x + fill_w, bar_y + 12)], fill=0)

        # Time (top right)
        time_str = time.strftime("%H:%M")
        draw.text((self._width - 45, 5), time_str, font=self._font_small, fill=0)

        # Push to display
        self._epd.displayPartial(self._epd.getbuffer(image))

    def show_startup(self):
        """Show startup splash screen."""
        if not self._epd:
            return

        Image = self._Image
        ImageDraw = self._ImageDraw

        image = Image.new("1", (self._width, self._height), 255)
        draw = ImageDraw.Draw(image)
        draw.text((30, 40), "RadioAgent", font=self._font, fill=0)
        draw.text((60, 70), "Powering on...", font=self._font_small, fill=0)

        self._epd.display(self._epd.getbuffer(image))

    def clear(self):
        """Clear the display."""
        if self._epd:
            self._epd.Clear(0xFF)

    def sleep(self):
        """Put the display into low-power sleep mode."""
        if self._epd:
            self._epd.sleep()

    def cleanup(self):
        """Clean up display resources."""
        if self._epd:
            self._epd.Clear(0xFF)
            self._epd.sleep()
