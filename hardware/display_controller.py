"""E-ink display controller — Waveshare 2.13" V4 HAT.

SPI0 CE0 wiring (shared bus with MCP3008 on CE1):
  DIN  = GPIO 10 (MOSI)    pin 19
  CLK  = GPIO 11 (SCLK)    pin 23
  CS   = GPIO  8 (CE0)     pin 24
  DC   = GPIO 25            pin 22
  RST  = GPIO 17            pin 11
  BUSY = GPIO 24            pin 18

The display always shows the animated waveform.
Calling update() simply refreshes the channel label overlaid on the waveform.
All frames are rotated 180° to correct for physical mounting orientation.
"""

import threading
import time
from log import get_logger

logger = get_logger(__name__)

_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT_REG  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


class DisplayController:
    """Drives the Waveshare 2.13" V4 e-ink display.

    The waveform animation runs permanently in a background thread.
    update() changes the channel/subchannel labels shown above the waves.
    """

    # Animation frame rate (frames per second).
    # e-ink partial refresh takes ~300 ms, so 3 fps is the practical ceiling.
    FPS = 3

    def __init__(self, config: dict):
        self.config = config
        self._epd = None
        self._Image = None
        self._ImageDraw = None
        self._font_small = None
        self._width  = config.get("DISPLAY", {}).get("width",  250)
        self._height = config.get("DISPLAY", {}).get("height", 122)

        # Live labels — updated by update(), read by the render thread
        self._channel    = "RADIO"
        self._subchannel = ""
        self._volume     = 70

        # Animation thread state
        self._active = False
        self._thread = None
        self._t0     = 0.0

        try:
            from waveshare_epd import epd2in13_V4
            from PIL import Image, ImageDraw, ImageFont
            from hardware.waveform_display import WaveformRenderer

            self._epd       = epd2in13_V4.EPD()
            self._Image     = Image
            self._ImageDraw = ImageDraw
            self._renderer  = WaveformRenderer(self._width, self._height)

            self._epd.init()
            self._epd.Clear(0xFF)

            try:
                self._font_small = ImageFont.truetype(_FONT_REG, 12)
            except OSError:
                self._font_small = ImageFont.load_default()

            logger.info("E-ink display initialised (%dx%d, 180° flip)",
                        self._width, self._height)

            # Start the waveform loop immediately
            self._start_loop()

        except (ImportError, Exception) as e:
            logger.info("E-ink display not available: %s", e)
            self._epd = None

    # ── public API ──────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._epd is not None

    def update(self, channel: str, subchannel: str = "",
               now_playing: str = "", volume: int = 70):
        """Update the labels shown on the waveform display.

        The animation keeps running; this just changes the overlay text.
        (now_playing is accepted for API compatibility but not rendered.)
        """
        self._channel    = channel
        self._subchannel = subchannel
        self._volume     = volume
        if not self._epd:
            logger.debug("Display update (no hw): ch=%s sub=%s", channel, subchannel)

    def show_startup(self):
        """Show a brief startup splash, then hand off to the waveform loop."""
        if not self._epd:
            return
        image = self._Image.new("1", (self._width, self._height), 255)
        draw  = self._ImageDraw.Draw(image)
        draw.text((20, 45), "radioagent", font=self._font_small, fill=0)
        self._epd.display(self._epd.getbuffer(image.rotate(180)))
        time.sleep(1.2)
        # Waveform loop was already started in __init__; nothing else needed.

    def clear(self):
        """Clear the display to white."""
        if self._epd:
            self._epd.Clear(0xFF)

    def sleep(self):
        """Put the display into low-power sleep mode."""
        if self._epd:
            self._epd.sleep()

    def cleanup(self):
        """Stop animation, clear display, sleep."""
        self._active = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        if self._epd:
            self._epd.Clear(0xFF)
            self._epd.sleep()

    # ── internals ───────────────────────────────────────────────────

    def _start_loop(self):
        """Spawn the background animation thread."""
        self._active = True
        self._t0     = time.time()
        self._thread = threading.Thread(
            target=self._render_loop, daemon=True, name="waveform")
        self._thread.start()
        logger.info("Waveform animation started (%d fps)", self.FPS)

    def _render_loop(self):
        interval = 1.0 / self.FPS
        while self._active:
            try:
                self._push_frame()
            except Exception as e:
                logger.warning("Waveform frame error: %s", e)
            time.sleep(interval)

    def _push_frame(self):
        t = time.time() - self._t0

        # Build label strings
        freq_text = self._subchannel if self._subchannel else "~"

        image = self._Image.new("1", (self._width, self._height), 255)
        draw  = self._ImageDraw.Draw(image)

        self._renderer.render(
            draw, t,
            channel_name=self._channel,
            freq_text=freq_text,
            font_small=self._font_small,
        )

        # ── 180° rotation to correct physical mounting ──
        image = image.rotate(180)

        self._epd.displayPartial(self._epd.getbuffer(image))
