"""Wizard of Oz demo — play pre-recorded MP3s with channel switching.

Channels:
  1 (key "2" / talkshow button) → talkshow.mp3
  2 (key "1" / music button)    → dj_music.mp3 → dragonball.mp3
  3 (key "3" / dailybrief btn)  → daily_brief.mp3

Controls:
  Keyboard: 1=music/dj, 2=talkshow, 3=dailybrief, a/d=tune(switch channel), q=quit
  Hardware: channel buttons, tuning dial/encoder

Starts on talkshow channel with radio static intro.
"""

import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import CONFIG
from audio.audio_player import AudioPlayer
from log import get_logger

logger = get_logger(__name__)

DEMO_DIR = Path(__file__).parent / "demo_output"

# Channel definitions: name → list of MP3 files to play in sequence
CHANNELS = {
    "talkshow":   [DEMO_DIR / "talkshow.mp3"],
    "music":      [DEMO_DIR / "dj_music.mp3", DEMO_DIR / "dragonball.mp3"],
    "dailybrief": [DEMO_DIR / "daily_brief.mp3"],
}

CHANNEL_ORDER = ["talkshow", "music", "dailybrief"]

CHANNEL_LABELS = {
    "talkshow":   "TALK SHOW",
    "music":      "DJ SPARK",
    "dailybrief": "DAILY BRIEF",
}


class WozDemo:
    def __init__(self):
        self.player = AudioPlayer(
            radio_filter_strength=CONFIG.get("RADIO_FILTER_STRENGTH", 0.7),
        )
        self.display = None
        self.current_channel = "talkshow"
        self._channel_idx = 0  # index into CHANNEL_ORDER
        self._running = False

    def _init_display(self):
        """Try to initialize e-ink display (Raspberry Pi only)."""
        try:
            from hardware.display_controller import DisplayController
            self.display = DisplayController(CONFIG)
            if self.display.available:
                self.display.show_startup()
                logger.info("E-ink display initialized")
            else:
                self.display = None
                logger.info("E-ink display not available, continuing without it")
        except Exception as e:
            logger.info("E-ink display init failed: %s", e)
            self.display = None

    def _update_display(self):
        if self.display and self.display.available:
            label = CHANNEL_LABELS.get(self.current_channel, self.current_channel)
            self.display.update(channel=label)

    def switch_channel(self, channel: str):
        """Switch to a channel, play static burst, then play its MP3 files."""
        if channel == self.current_channel:
            return
        if channel not in CHANNELS:
            return

        self.current_channel = channel
        self._channel_idx = CHANNEL_ORDER.index(channel)
        logger.info("Switching to channel: %s", channel)

        # Interrupt current playback
        self.player.interrupt()
        gen = self.player.current_generation

        # Update display
        self._update_display()

        # Play static burst then channel content in background
        threading.Thread(
            target=self._play_channel_content, args=(channel, gen),
            daemon=True,
        ).start()

    def _play_channel_content(self, channel: str, generation: int):
        """Play static burst followed by channel MP3 files (runs in thread)."""
        # Transition static burst (~1s) so users know they're switching channels
        self.player.start_static(transition=True)
        time.sleep(1.0)
        self.player.stop_static()

        # Play each MP3 file in sequence
        for mp3_path in CHANNELS[channel]:
            if generation != self.player.current_generation:
                return  # Channel was switched again
            if not mp3_path.exists():
                logger.warning("Missing MP3: %s", mp3_path)
                continue
            logger.info("Playing: %s", mp3_path.name)
            self.player.play_file(str(mp3_path), generation=generation)

        # After all files finish, loop back to static
        # (The queue will drain and static_mode will provide silence)

    def switch_next(self):
        """Switch to next channel in order."""
        idx = (self._channel_idx + 1) % len(CHANNEL_ORDER)
        self.switch_channel(CHANNEL_ORDER[idx])

    def switch_prev(self):
        """Switch to previous channel in order."""
        idx = (self._channel_idx - 1) % len(CHANNEL_ORDER)
        self.switch_channel(CHANNEL_ORDER[idx])

    async def run(self):
        self._running = True

        # Verify demo files exist
        missing = []
        for ch, files in CHANNELS.items():
            for f in files:
                if not f.exists():
                    missing.append(str(f))
        if missing:
            print(f"ERROR: Missing demo files: {', '.join(missing)}")
            print("Run `python demo_snippets.py` first to generate them.")
            return

        # Init display (e-ink, may not be available)
        self._init_display()

        # Start audio player
        self.player.start()
        self.player.start_static()

        print("\n" + "=" * 50)
        print("  RADIOAGENT — Wizard of Oz Demo")
        print("=" * 50)
        print("  Controls:")
        print("    1 = Music (DJ + Dragonball)")
        print("    2 = Talk Show")
        print("    3 = Daily Brief")
        print("    a/← = Previous channel")
        print("    d/→ = Next channel")
        print("    w/↑ = Volume up")
        print("    s/↓ = Volume down")
        print("    q   = Quit")
        print("=" * 50 + "\n")

        # Start on talkshow
        gen = self.player.current_generation
        self._update_display()
        print(f"  ▶ Starting on: TALK SHOW")

        # Play initial channel after brief static
        threading.Thread(
            target=self._play_channel_content,
            args=("talkshow", gen),
            daemon=True,
        ).start()

        # Input handling (keyboard + optional GPIO)
        try:
            # Start GPIO/ADC polling if available
            try:
                from hardware.input_controller import InputController
                gpio_controller = InputController(CONFIG, self._handle_input_event)
                if gpio_controller._use_gpio:
                    asyncio.create_task(gpio_controller.start_adc_polling())
                    logger.info("GPIO input active")
            except Exception:
                pass

            # Keyboard input loop
            await self._keyboard_loop()
        finally:
            self._running = False
            self.player.stop()
            if self.display:
                self.display.cleanup()
            print("\n  Demo stopped.")

    def _handle_input_event(self, event):
        """Handle GPIO/hardware input events."""
        if event.event_type == "button_press" and event.channel:
            self.switch_channel(event.channel)
        elif event.event_type == "dial_change":
            # Treat dial movement as channel switching
            if event.dial_position < -5:
                self.switch_channel("talkshow")
            elif event.dial_position < 5:
                self.switch_channel("music")
            else:
                self.switch_channel("dailybrief")

    async def _keyboard_loop(self):
        loop = asyncio.get_event_loop()
        channel_keys = {"1": "music", "2": "talkshow", "3": "dailybrief"}

        while self._running:
            key = await loop.run_in_executor(None, self._get_key)

            if key == "q":
                break
            elif key in channel_keys:
                ch = channel_keys[key]
                print(f"  ▶ Switching to: {CHANNEL_LABELS[ch]}")
                self.switch_channel(ch)
            elif key in ("d", "right"):
                self.switch_next()
                print(f"  ▶ Switching to: {CHANNEL_LABELS[self.current_channel]}")
            elif key in ("a", "left"):
                self.switch_prev()
                print(f"  ▶ Switching to: {CHANNEL_LABELS[self.current_channel]}")
            elif key in ("w", "up"):
                self.player.volume = min(1.0, self.player.volume + 0.1)
                print(f"  🔊 Volume: {int(self.player.volume * 100)}%")
            elif key in ("s", "down"):
                self.player.volume = max(0.0, self.player.volume - 0.1)
                print(f"  🔊 Volume: {int(self.player.volume * 100)}%")

    @staticmethod
    def _get_key() -> str:
        """Read a single keypress (blocking)."""
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
    demo = WozDemo()
    asyncio.run(demo.run())


if __name__ == "__main__":
    main()
