"""Hardware input controller for buttons and rotary encoders.

On Raspberry Pi: uses RPi.GPIO for real hardware.
On other platforms: provides a keyboard-based simulator for development.
"""

import asyncio
import sys
from dataclasses import dataclass
from typing import Callable

from content.channels import CHANNELS, resolve_subchannel


@dataclass
class InputEvent:
    event_type: str                # "button_press", "dial_change", "dial_click",
                                    # "volume_change", "volume_mute", "callin_start",
                                    # "callin_stop"
    channel: str | None = None      # Channel ID for button_press
    dial_position: int = 50         # 0-100 for dial_change
    subchannel: str | None = None   # Resolved subchannel name
    volume: int = 70                # 0-100 for volume_change


class InputController:
    """Handles physical input from buttons and rotary encoders."""

    BUTTON_MAP = {
        5: "news",
        6: "talkshow",
        13: "sports",
        19: "dj",
        26: "callin",
    }

    def __init__(self, config: dict, callback: Callable[[InputEvent], None]):
        self.config = config
        self.callback = callback
        self.dial_position = 50
        self.volume = 70
        self.active_channel = "news"
        self._callin_active = False
        self._use_gpio = False

        try:
            import RPi.GPIO as GPIO
            self.GPIO = GPIO
            self._use_gpio = True
            self._setup_gpio()
        except (ImportError, RuntimeError):
            print("[Input] RPi.GPIO not available, using keyboard simulator")
            self._use_gpio = False

    def _setup_gpio(self):
        """Set up GPIO pins for buttons and encoders (Raspberry Pi only)."""
        GPIO = self.GPIO
        GPIO.setmode(GPIO.BCM)
        pins = self.config["PINS"]

        # Tuning encoder
        GPIO.setup(pins["tuning_clk"], GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(pins["tuning_dt"], GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(pins["tuning_sw"], GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self._last_tuning_clk = GPIO.input(pins["tuning_clk"])

        GPIO.add_event_detect(pins["tuning_clk"], GPIO.BOTH,
                              callback=self._tuning_callback, bouncetime=2)
        GPIO.add_event_detect(pins["tuning_sw"], GPIO.FALLING,
                              callback=self._tuning_click_callback, bouncetime=300)

        # Volume encoder
        GPIO.setup(pins["volume_clk"], GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(pins["volume_dt"], GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(pins["volume_sw"], GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self._last_volume_clk = GPIO.input(pins["volume_clk"])

        GPIO.add_event_detect(pins["volume_clk"], GPIO.BOTH,
                              callback=self._volume_callback, bouncetime=2)
        GPIO.add_event_detect(pins["volume_sw"], GPIO.FALLING,
                              callback=self._volume_mute_callback, bouncetime=300)

        # Content buttons + call-in
        for pin_name in ["btn_news", "btn_talkshow", "btn_sports", "btn_dj", "btn_callin"]:
            pin = pins[pin_name]
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            if pin_name == "btn_callin":
                # Call-in uses press and release
                GPIO.add_event_detect(pin, GPIO.BOTH,
                                      callback=self._callin_callback, bouncetime=50)
            else:
                GPIO.add_event_detect(pin, GPIO.FALLING,
                                      callback=self._button_callback, bouncetime=300)

    def _tuning_callback(self, channel):
        pins = self.config["PINS"]
        clk = self.GPIO.input(pins["tuning_clk"])
        dt = self.GPIO.input(pins["tuning_dt"])
        if clk != self._last_tuning_clk:
            if dt != clk:
                self.dial_position = min(100, self.dial_position + 2)
            else:
                self.dial_position = max(0, self.dial_position - 2)
            subchannel = resolve_subchannel(self.active_channel, self.dial_position)
            self.callback(InputEvent(
                event_type="dial_change",
                channel=self.active_channel,
                dial_position=self.dial_position,
                subchannel=subchannel,
            ))
        self._last_tuning_clk = clk

    def _tuning_click_callback(self, channel):
        self.callback(InputEvent(event_type="dial_click"))

    def _volume_callback(self, channel):
        pins = self.config["PINS"]
        clk = self.GPIO.input(pins["volume_clk"])
        dt = self.GPIO.input(pins["volume_dt"])
        if clk != self._last_volume_clk:
            if dt != clk:
                self.volume = min(100, self.volume + 3)
            else:
                self.volume = max(0, self.volume - 3)
            self.callback(InputEvent(event_type="volume_change", volume=self.volume))
        self._last_volume_clk = clk

    def _volume_mute_callback(self, channel):
        self.callback(InputEvent(event_type="volume_mute"))

    def _button_callback(self, channel):
        channel_id = self.BUTTON_MAP.get(channel)
        if channel_id:
            self.active_channel = channel_id
            self.callback(InputEvent(
                event_type="button_press",
                channel=channel_id,
            ))

    def _callin_callback(self, channel):
        pin = self.config["PINS"]["btn_callin"]
        pressed = not self.GPIO.input(pin)  # Active low
        if pressed and not self._callin_active:
            self._callin_active = True
            self.callback(InputEvent(event_type="callin_start"))
        elif not pressed and self._callin_active:
            self._callin_active = False
            self.callback(InputEvent(event_type="callin_stop"))

    async def run_keyboard_simulator(self):
        """Keyboard-based input simulator for development without hardware."""
        print("\n--- RadioAgent Keyboard Controls ---")
        print("1-4: Switch channels (News, Talk, Sports, DJ)")
        print("← →: Tune dial (left/right arrow or a/d)")
        print("↑ ↓: Volume (up/down arrow or w/s)")
        print("c:   Call-in (press to start, press again to stop)")
        print("m:   Mute/unmute")
        print("q:   Quit")
        print("------------------------------------\n")

        loop = asyncio.get_event_loop()
        channel_keys = {"1": "news", "2": "talkshow", "3": "sports", "4": "dj"}

        while True:
            key = await loop.run_in_executor(None, self._get_key)
            if key == "q":
                break
            elif key in channel_keys:
                self.active_channel = channel_keys[key]
                self.callback(InputEvent(event_type="button_press", channel=channel_keys[key]))
            elif key in ("a", "left"):
                self.dial_position = max(0, self.dial_position - 10)
                sub = resolve_subchannel(self.active_channel, self.dial_position)
                self.callback(InputEvent(
                    event_type="dial_change", channel=self.active_channel,
                    dial_position=self.dial_position, subchannel=sub,
                ))
            elif key in ("d", "right"):
                self.dial_position = min(100, self.dial_position + 10)
                sub = resolve_subchannel(self.active_channel, self.dial_position)
                self.callback(InputEvent(
                    event_type="dial_change", channel=self.active_channel,
                    dial_position=self.dial_position, subchannel=sub,
                ))
            elif key in ("w", "up"):
                self.volume = min(100, self.volume + 10)
                self.callback(InputEvent(event_type="volume_change", volume=self.volume))
            elif key in ("s", "down"):
                self.volume = max(0, self.volume - 10)
                self.callback(InputEvent(event_type="volume_change", volume=self.volume))
            elif key == "c":
                if not self._callin_active:
                    self._callin_active = True
                    self.callback(InputEvent(event_type="callin_start"))
                    print("[Input] Recording... press 'c' again to stop")
                else:
                    self._callin_active = False
                    self.callback(InputEvent(event_type="callin_stop"))
            elif key == "m":
                self.callback(InputEvent(event_type="volume_mute"))

    @staticmethod
    def _get_key() -> str:
        """Read a single keypress (blocking)."""
        try:
            import termios
            import tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = sys.stdin.read(1)
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

    def cleanup(self):
        """Clean up GPIO resources."""
        if self._use_gpio:
            self.GPIO.cleanup()
