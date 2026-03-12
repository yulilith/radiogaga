"""Wizard of Oz Demo 1 — Daily News

Flow (each key press advances to the next screen):
  1. Clock (8:00 AM)
  2. "DAILY BRIEF"
  3. Waveform animation
  4. Waveform frozen
  5. "PLAYING SPOTIFY"
  6. Waveform animation
  7. Waveform frozen

No audio — display-only demo.

Controls:
  any key     = Next screen
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

# Screens in order (each key press advances)
SCREENS = [
    "__clock__",
    "DAILY BRIEF",
    "__waveform__",
    "__waveform_frozen__",
    "PLAYING\nSPOTIFY",
    "__waveform__",
    "__waveform_frozen__",
]


class DailyNewsDemo:
    def __init__(self):
        self._epd = None
        self._Image = None
        self._ImageDraw = None
        self._font_small = None
        self._font_large = None
        self._width = 250
        self._height = 122

        self._screen_idx = 0
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
                self._font_large = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
            except OSError:
                self._font_small = ImageFont.load_default()
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
        """Show a static clock face."""
        if not self._epd:
            print("  [DISPLAY] 8:00 AM")
            return

        image = self._Image.new("1", (self._width, self._height), 255)
        draw = self._ImageDraw.Draw(image)

        time_text = "8:00 AM"
        bbox = draw.textbbox((0, 0), time_text, font=self._font_large)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (self._width - tw) // 2
        y = (self._height - th) // 2
        draw.text((x, y), time_text, font=self._font_large, fill=0)

        self._show_image(image)
        logger.info("Displaying clock: 8:00 AM")

    def _show_centered_text(self, text):
        """Show centered text on e-ink. Supports \\n for multiline."""
        if not self._epd:
            display_text = text.replace('\n', ' ')
            print(f"  [DISPLAY] {display_text}")
            return

        image = self._Image.new("1", (self._width, self._height), 255)
        draw = self._ImageDraw.Draw(image)

        lines = text.split('\n')
        font = self._font_large

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
                    channel_name="DAILY BRIEF",
                    freq_text="AM 880",
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

    def _advance(self):
        """Advance to next screen on key press."""
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

        if screen == "__clock__":
            print(f"  {step} Clock (8:00 AM)")
            self._stop_waveform()
            self._show_clock()
        elif screen == "__waveform__":
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
            self._stop_waveform()
            self._show_centered_text(screen)

    # ── Main loop ─────────────────────────────────────────────────

    async def run(self):
        self._running = True

        # Init display
        self._init_display()

        # Show first screen (clock)
        self._show_screen(0)

        print("\n" + "=" * 50)
        print("  RADIOAGENT — Daily News Demo (WOZ)")
        print("=" * 50)
        print("  Press any key to advance to next screen")
        print("  Press q to quit")
        print("=" * 50 + "\n")

        try:
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

        while self._running:
            key = await loop.run_in_executor(None, self._get_key)

            if key == "q":
                break
            else:
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
    demo = DailyNewsDemo()
    try:
        asyncio.run(demo.run())
    except KeyboardInterrupt:
        print("\n  Interrupted — shutting down.")


if __name__ == "__main__":
    main()
