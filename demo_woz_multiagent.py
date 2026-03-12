"""Wizard of Oz Demo 2 — Multi-Agent

Flow:
  1. Machine starts "off" — e-ink shows clock (3:10 PM)
  2. Volume knob up → turns on
  3. Screen 1: "GOOD AFTERNOON!"
  4. Key press → Screen 2: "CHLOE'S AGENT IS HERE"
  5. Key press → Screen 3: "PLAYING CHLOE'S RADIO"
  6. Key press → Screen 4: Waveform animation (running)
  7. Key press → Screen 5: Waveform freezes in place
  8. Key press → Screen 6: "PLAYING SPOTIFY"
  9. Key press → Screen 7: Waveform animation (running)
  10. Key press → Screen 8: Waveform freezes in place

No audio — display-only demo.

Controls:
  w/↑         = Volume up (triggers turn-on from off state)
  any key     = Advance to next screen (once on)
  q           = Quit
"""

import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import CONFIG
from log import get_logger

logger = get_logger(__name__)

# Volume threshold (0-100) to trigger "turning on"
VOLUME_ON_THRESHOLD = 10

# Screens after turn-on (in order)
SCREENS = [
    "GOOD\nAFTERNOON!",
    "CHLOE'S AGENT\nIS HERE",
    "PLAYING\nCHLOE'S RADIO",
    "__waveform__",
    "__waveform_frozen__",
    "PLAYING\nSPOTIFY",
    "__waveform__",
    "__waveform_frozen__",
]


class MultiAgentDemo:
    def __init__(self):
        self._epd = None
        self._Image = None
        self._ImageDraw = None
        self._font_small = None
        self._font_large = None
        self._font_med = None
        self._width = 250
        self._height = 122

        # State: "off" → "on" (stepping through SCREENS)
        self._state = "off"
        self._screen_idx = -1
        self._running = False
        self._waveform_thread = None
        self._waveform_active = False

    # ── Display helpers ───────────────────────────────────────────

    def _init_display(self):
        """Initialize e-ink display directly."""
        try:
            from waveshare_epd import epd2in13_V4
            from PIL import Image, ImageDraw, ImageFont

            self._epd = epd2in13_V4.EPD()
            self._Image = Image
            self._ImageDraw = ImageDraw
            self._epd.init()
            self._epd.Clear(0xFF)

            try:
                self._font_small = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
                self._font_med = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
                self._font_large = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
            except OSError:
                self._font_small = ImageFont.load_default()
                self._font_med = ImageFont.load_default()
                self._font_large = ImageFont.load_default()

            logger.info("E-ink display initialized for demo")
        except Exception as e:
            logger.info("E-ink display not available: %s", e)
            self._epd = None

    def _show_image(self, image):
        """Push a full image to the e-ink display (with 180° rotation)."""
        if not self._epd:
            return
        self._epd.displayPartial(self._epd.getbuffer(image.rotate(180)))

    def _show_clock(self):
        """Show a static clock face — the 'off' state."""
        if not self._epd:
            print("  [DISPLAY] 3:10 PM  (machine off)")
            return

        image = self._Image.new("1", (self._width, self._height), 255)
        draw = self._ImageDraw.Draw(image)

        time_text = "3:10 PM"
        bbox = draw.textbbox((0, 0), time_text, font=self._font_large)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (self._width - tw) // 2
        y = (self._height - th) // 2
        draw.text((x, y), time_text, font=self._font_large, fill=0)

        self._epd.displayPartial(self._epd.getbuffer(image.rotate(180)))
        logger.info("Displaying clock: 3:10 PM")

    def _show_centered_text(self, text):
        """Show centered text on e-ink. Supports \\n for multiline."""
        if not self._epd:
            display_text = text.replace('\n', ' ')
            print(f"  [DISPLAY] {display_text}")
            return

        image = self._Image.new("1", (self._width, self._height), 255)
        draw = self._ImageDraw.Draw(image)

        lines = text.split('\n')

        # Pick font — use medium font for multi-line, large for single
        font = self._font_large if len(lines) == 1 else self._font_med

        # Measure total height
        line_heights = []
        line_widths = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_widths.append(bbox[2] - bbox[0])
            line_heights.append(bbox[3] - bbox[1])

        line_spacing = 8
        total_h = sum(line_heights) + line_spacing * (len(lines) - 1)
        y_start = (self._height - total_h) // 2

        y = y_start
        for i, line in enumerate(lines):
            x = (self._width - line_widths[i]) // 2
            draw.text((x, y), line, font=font, fill=0)
            y += line_heights[i] + line_spacing

        self._show_image(image)
        logger.info("Displaying: %s", text.replace('\n', ' | '))

    def _start_waveform(self):
        """Start the waveform animation in a background thread."""
        from hardware.waveform_display import WaveformRenderer

        renderer = WaveformRenderer(self._width, self._height)
        self._waveform_active = True
        t0 = time.time()

        def loop():
            while self._waveform_active:
                t = time.time() - t0
                image = self._Image.new("1", (self._width, self._height), 255)
                draw = self._ImageDraw.Draw(image)
                renderer.render(
                    draw, t,
                    channel_name="CHLOE'S RADIO",
                    freq_text="FM 101.3",
                    font_small=self._font_small,
                )
                self._show_image(image)
                time.sleep(1.0 / 3)  # ~3 fps

        self._waveform_thread = threading.Thread(target=loop, daemon=True, name="waveform")
        self._waveform_thread.start()
        logger.info("Waveform animation started")

    def _stop_waveform(self):
        self._waveform_active = False
        if self._waveform_thread:
            self._waveform_thread.join(timeout=2)
            self._waveform_thread = None

    def _freeze_waveform(self):
        """Stop the animation loop — last rendered frame stays on e-ink."""
        self._waveform_active = False
        if self._waveform_thread:
            self._waveform_thread.join(timeout=2)
            self._waveform_thread = None
        logger.info("Waveform frozen")

    # ── State transitions ─────────────────────────────────────────

    def _turn_on(self):
        """Volume knob triggered — show first screen."""
        if self._state != "off":
            return
        self._state = "on"
        self._screen_idx = 0
        logger.info("Machine turning on!")
        print("  ▶ Machine turning on...")
        self._show_screen(0)

    def _advance(self):
        """Advance to next screen on key press."""
        if self._state != "on":
            return
        next_idx = self._screen_idx + 1
        if next_idx >= len(SCREENS):
            print("  (already on last screen)")
            return
        self._screen_idx = next_idx
        self._show_screen(next_idx)

    def _show_screen(self, idx):
        """Display the screen at the given index."""
        screen = SCREENS[idx]
        step = f"[{idx + 1}/{len(SCREENS)}]"

        if screen == "__waveform__":
            print(f"  {step} Waveform animation")
            if self._epd:
                self._start_waveform()
            else:
                print("  [DISPLAY] ═══ waveform animation ═══")
        elif screen == "__waveform_frozen__":
            print(f"  {step} Waveform frozen")
            if self._waveform_active:
                self._freeze_waveform()
                print("  [DISPLAY] ═══ waveform frozen ═══")
            else:
                print("  [DISPLAY] ═══ waveform frozen (was not running) ═══")
        else:
            display_text = screen.replace('\n', ' ')
            print(f"  {step} {display_text}")
            # Stop waveform if it was running
            self._stop_waveform()
            self._show_centered_text(screen)

    # ── Input handling ────────────────────────────────────────────

    def _handle_input_event(self, event):
        """Handle GPIO/hardware input events."""
        if event.event_type == "volume_change":
            if self._state == "off" and event.volume > VOLUME_ON_THRESHOLD:
                self._turn_on()
        elif event.event_type == "button_press":
            self._advance()

    # ── Main loop ─────────────────────────────────────────────────

    async def run(self):
        self._running = True

        # Init display
        self._init_display()

        # Show the clock (off state)
        self._show_clock()

        print("\n" + "=" * 50)
        print("  RADIOAGENT — Multi-Agent Demo (WOZ)")
        print("=" * 50)
        print("  Machine is OFF — showing 3:10 PM clock")
        print("  Turn the volume knob up to start!")
        print()
        print("  Keyboard controls:")
        print("    any key   = Turn on / next screen")
        print("    q         = Quit")
        print("=" * 50 + "\n")

        try:
            # Start GPIO/ADC polling if on Pi
            try:
                from hardware.input_controller import InputController
                gpio = InputController(CONFIG, self._handle_input_event)
                if gpio._use_gpio:
                    asyncio.create_task(gpio.start_adc_polling())
                    logger.info("GPIO input active")
            except Exception:
                pass

            # Keyboard input loop
            await self._keyboard_loop()
        finally:
            self._running = False
            self._stop_waveform()
            if self._epd:
                self._epd.Clear(0xFF)
                self._epd.sleep()
            print("\n  Demo stopped.")

    async def _keyboard_loop(self):
        loop = asyncio.get_event_loop()
        volume = 0

        while self._running:
            key = await loop.run_in_executor(None, self._get_key)

            if key == "q":
                break
            elif self._state == "off":
                # Any key turns on the machine
                self._turn_on()
            elif self._state == "on":
                # Any key advances to next screen
                self._advance()

    @staticmethod
    def _get_key() -> str:
        try:
            import termios
            import tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                ch = sys.stdin.read(1)
                if ch == "\x03":
                    return "q"
                if ch == "\x1b":
                    ch2 = sys.stdin.read(2)
                    if ch2 == "[A": return "up"
                    if ch2 == "[B": return "down"
                    if ch2 == "[C": return "right"
                    if ch2 == "[D": return "left"
                return ch
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except (ImportError, termios.error):
            return input("> ").strip()


def main():
    demo = MultiAgentDemo()
    try:
        asyncio.run(demo.run())
    except KeyboardInterrupt:
        print("\n  Interrupted — shutting down.")


if __name__ == "__main__":
    main()
