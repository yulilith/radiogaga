"""Wizard of Oz Demo 1 — Daily News

Flow:
  1. Machine starts "off" — e-ink shows a clock face (8:00 AM)
  2. User rotates volume knob up — machine "turns on"
  3. E-ink shows "DAILY BRIEF" splash for 5 seconds
  4. Enters waveform animation display
  5. Plays daily_brief.mp3

Controls:
  Hardware: volume dial triggers on, channel buttons switch channels
  Keyboard: w/↑ = volume up (triggers on), s/↓ = volume down, q = quit
"""

import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import CONFIG
from log import get_logger

# Audio is optional — allows testing display/flow on Mac without pyaudio
try:
    from audio.audio_player import AudioPlayer
    _HAS_AUDIO = True
except ImportError:
    _HAS_AUDIO = False

logger = get_logger(__name__)

DEMO_DIR = Path(__file__).parent / "demo_output"
DAILY_BRIEF_MP3 = DEMO_DIR / "daily_brief.mp3"

# Volume threshold (0-100) to trigger "turning on"
VOLUME_ON_THRESHOLD = 10


class _StubPlayer:
    """No-op audio player for testing without pyaudio."""
    volume = 0.7
    current_generation = 0
    def start(self): print("  [AUDIO] player started (stub)")
    def stop(self): pass
    def start_static(self, **kw): pass
    def stop_static(self): pass
    def play_file(self, *a, **kw): print(f"  [AUDIO] playing {a[0]} (stub)")
    def interrupt(self): pass


class DailyNewsDemo:
    def __init__(self):
        if _HAS_AUDIO:
            self.player = AudioPlayer(
                radio_filter_strength=CONFIG.get("RADIO_FILTER_STRENGTH", 0.7),
            )
        else:
            logger.info("pyaudio not available — running without audio")
            self.player = _StubPlayer()
        self.display = None
        self._epd = None
        self._Image = None
        self._ImageDraw = None
        self._font_small = None
        self._font_large = None
        self._width = 250
        self._height = 122

        # State machine: "off" → "splash" → "playing"
        self._state = "off"
        self._running = False
        self._waveform_thread = None
        self._waveform_active = False

    # ── Display helpers ───────────────────────────────────────────

    def _init_display(self):
        """Initialize e-ink display directly (low-level, no DisplayController)."""
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
        """Show a static clock face — the 'off' state."""
        if not self._epd:
            print("  [DISPLAY] 8:00 AM  (machine off)")
            return

        image = self._Image.new("1", (self._width, self._height), 255)
        draw = self._ImageDraw.Draw(image)

        # Center the time text
        time_text = "8:00 AM"
        bbox = draw.textbbox((0, 0), time_text, font=self._font_large)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (self._width - tw) // 2
        y = (self._height - th) // 2
        draw.text((x, y), time_text, font=self._font_large, fill=0)

        # Use full refresh for clean initial display
        self._epd.displayPartial(self._epd.getbuffer(image.rotate(180)))
        logger.info("Displaying clock: 8:00 AM")

    def _show_splash(self):
        """Show 'DAILY BRIEF' splash screen for 5 seconds."""
        if not self._epd:
            print("  [DISPLAY] ═══ DAILY BRIEF ═══  (5 seconds)")
            time.sleep(5)
            return

        image = self._Image.new("1", (self._width, self._height), 255)
        draw = self._ImageDraw.Draw(image)

        title = "DAILY BRIEF"
        bbox = draw.textbbox((0, 0), title, font=self._font_large)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (self._width - tw) // 2
        y = (self._height - th) // 2
        draw.text((x, y), title, font=self._font_large, fill=0)

        self._show_image(image)
        logger.info("Displaying splash: DAILY BRIEF")
        time.sleep(5)

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

    # ── State transitions ─────────────────────────────────────────

    def _turn_on(self):
        """Transition from off → splash → playing."""
        if self._state != "off":
            return
        self._state = "splash"
        logger.info("Machine turning on!")
        print("  ▶ Machine turning on...")

        def _sequence():
            # Splash screen
            self._show_splash()

            # Transition to playing
            self._state = "playing"
            print("  ▶ Now playing: DAILY BRIEF")

            # Start waveform display
            if self._epd:
                self._start_waveform()
            else:
                print("  [DISPLAY] ═══ waveform animation ═══")

            # Start audio
            self.player.start()
            gen = self.player.current_generation

            # Brief static then play the daily brief
            self.player.start_static(transition=True)
            time.sleep(0.5)
            self.player.stop_static()

            if DAILY_BRIEF_MP3.exists():
                self.player.play_file(str(DAILY_BRIEF_MP3), generation=gen)
            else:
                logger.warning("Missing: %s", DAILY_BRIEF_MP3)
                print(f"  WARNING: {DAILY_BRIEF_MP3} not found")

        threading.Thread(target=_sequence, daemon=True).start()

    # ── Input handling ────────────────────────────────────────────

    def _handle_input_event(self, event):
        """Handle GPIO/hardware input events."""
        if event.event_type == "volume_change":
            if self._state == "off" and event.volume > VOLUME_ON_THRESHOLD:
                self._turn_on()
            elif self._state == "playing":
                self.player.volume = event.volume / 100.0

    # ── Main loop ─────────────────────────────────────────────────

    async def run(self):
        self._running = True

        # Check demo file
        if not DAILY_BRIEF_MP3.exists():
            print(f"WARNING: {DAILY_BRIEF_MP3} not found — audio will be skipped")

        # Init display
        self._init_display()

        # Show the clock (off state)
        self._show_clock()

        print("\n" + "=" * 50)
        print("  RADIOAGENT — Daily News Demo (WOZ)")
        print("=" * 50)
        print("  Machine is OFF — showing 8:00 AM clock")
        print("  Turn the volume knob up to start!")
        print()
        print("  Keyboard controls:")
        print("    w/↑ = Volume up (triggers turn-on)")
        print("    s/↓ = Volume down")
        print("    q   = Quit")
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
            self.player.stop()
            if self._epd:
                self._epd.Clear(0xFF)
                self._epd.sleep()
            print("\n  Demo stopped.")

    async def _keyboard_loop(self):
        loop = asyncio.get_event_loop()
        volume = 0  # Start at 0 (machine is off)

        while self._running:
            key = await loop.run_in_executor(None, self._get_key)

            if key == "q":
                break
            elif key in ("w", "up"):
                volume = min(100, volume + 15)
                if self._state == "off" and volume > VOLUME_ON_THRESHOLD:
                    self._turn_on()
                elif self._state == "playing":
                    self.player.volume = volume / 100.0
                print(f"  Volume: {volume}%")
            elif key in ("s", "down"):
                volume = max(0, volume - 15)
                if self._state == "playing":
                    self.player.volume = volume / 100.0
                print(f"  Volume: {volume}%")

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
