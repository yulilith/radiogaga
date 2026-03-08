"""Hardware input controller for buttons and slide potentiometers.

On Raspberry Pi 5: uses RPi.GPIO for buttons, spidev + MCP3008 for analog pots.
On other platforms: provides a keyboard-based simulator for development.
"""

import asyncio
import sys
from dataclasses import dataclass
from typing import Callable

from content.channels import CHANNELS, resolve_subchannel
from log import get_logger

logger = get_logger(__name__)


@dataclass
class InputEvent:
    event_type: str                # "button_press", "dial_change",
                                    # "volume_change", "callin_start",
                                    # "callin_stop", "nfc_press",
                                    # "swap_slot"
    channel: str | None = None      # Channel ID for button_press
    dial_position: int = 50         # 0-100 for dial_change
    subchannel: str | None = None   # Resolved subchannel name
    volume: int = 70                # 0-100 for volume_change
    slot_index: int = -1            # Slot index for swap_slot


class InputController:
    """Handles physical input from buttons and slide potentiometers."""

    BUTTON_MAP = {
        5: "dailybrief",
        6: "talkshow",
        13: "music",
        26: "memos",
    }

    def __init__(self, config: dict, callback: Callable[[InputEvent], None]):
        self.config = config
        self.callback = callback
        self.dial_position = 50
        self.volume = 70
        self.active_channel = "music"
        self._callin_active = False
        self._use_gpio = False
        self._adc = None

        try:
            import RPi.GPIO as GPIO
            self.GPIO = GPIO
            self._use_gpio = True
            self._setup_gpio()
            self._setup_adc()
            logger.info("GPIO hardware initialized successfully")
        except (ImportError, RuntimeError):
            logger.info("RPi.GPIO not available, using keyboard simulator")
            self._use_gpio = False

    def _setup_gpio(self):
        """Set up GPIO pins for buttons (Raspberry Pi 5)."""
        GPIO = self.GPIO
        GPIO.setmode(GPIO.BCM)
        pins = self.config["PINS"]

        # Channel buttons (4)
        for pin_name in ["btn_music", "btn_talkshow", "btn_dailybrief", "btn_memos"]:
            pin = pins[pin_name]
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(pin, GPIO.FALLING,
                                  callback=self._button_callback, bouncetime=300)

        # Call-in button (press-and-hold)
        pin = pins["btn_callin"]
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_detect(pin, GPIO.BOTH,
                              callback=self._callin_callback, bouncetime=50)

        # NFC / system update button
        pin = pins["btn_nfc"]
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_detect(pin, GPIO.FALLING,
                              callback=self._nfc_button_callback, bouncetime=300)

    def _setup_adc(self):
        """Set up MCP3008 ADC over SPI for HW-233 slide potentiometers."""
        try:
            import spidev
            adc_cfg = self.config.get("ADC", {})
            self._adc = spidev.SpiDev()
            self._adc.open(adc_cfg.get("spi_bus", 0), adc_cfg.get("spi_device", 1))
            self._adc.max_speed_hz = 1_000_000
            self._adc.mode = 0
            logger.info("MCP3008 ADC initialized on SPI0 CE1")
        except (ImportError, OSError) as e:
            logger.warning("SPI ADC not available: %s", e)
            self._adc = None

    def _read_adc(self, channel: int) -> int:
        """Read a 10-bit value from MCP3008 channel (0-7). Returns 0-1023."""
        if not self._adc:
            return 512
        cmd = [1, (8 + channel) << 4, 0]
        result = self._adc.xfer2(cmd)
        return ((result[1] & 0x03) << 8) | result[2]

    def _adc_to_percent(self, raw: int) -> int:
        """Convert 10-bit ADC value (0-1023) to 0-100."""
        return max(0, min(100, int(raw * 100 / 1023)))

    async def start_adc_polling(self):
        """Poll slide potentiometers and emit events on change."""
        if not self._adc:
            return

        adc_cfg = self.config.get("ADC", {})
        interval = adc_cfg.get("poll_interval_ms", 50) / 1000.0
        deadzone = adc_cfg.get("deadzone", 2)
        tuning_ch = adc_cfg.get("tuning_channel", 0)
        volume_ch = adc_cfg.get("volume_channel", 1)

        last_tuning = self.dial_position
        last_volume = self.volume

        logger.info("ADC polling started",
                    extra={"interval_ms": adc_cfg.get("poll_interval_ms", 50),
                           "deadzone": deadzone})

        while True:
            raw_tuning = self._read_adc(tuning_ch)
            tuning = self._adc_to_percent(raw_tuning)
            if abs(tuning - last_tuning) > deadzone:
                last_tuning = tuning
                self.dial_position = tuning
                subchannel = resolve_subchannel(self.active_channel, tuning)
                self.callback(InputEvent(
                    event_type="dial_change",
                    channel=self.active_channel,
                    dial_position=tuning,
                    subchannel=subchannel,
                ))

            raw_volume = self._read_adc(volume_ch)
            volume = self._adc_to_percent(raw_volume)
            if abs(volume - last_volume) > deadzone:
                last_volume = volume
                self.volume = volume
                self.callback(InputEvent(
                    event_type="volume_change",
                    volume=volume,
                ))

            await asyncio.sleep(interval)

    def _button_callback(self, channel):
        channel_id = self.BUTTON_MAP.get(channel)
        if channel_id:
            logger.debug("Button press", extra={"channel": channel_id, "gpio_pin": channel})
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
            logger.debug("Call-in button pressed")
            self.callback(InputEvent(event_type="callin_start"))
        elif not pressed and self._callin_active:
            self._callin_active = False
            logger.debug("Call-in button released")
            self.callback(InputEvent(event_type="callin_stop"))

    def _nfc_button_callback(self, channel):
        logger.debug("NFC/system button pressed")
        self.callback(InputEvent(event_type="nfc_press"))

    async def run_keyboard_simulator(self):
        """Keyboard-based input simulator for development without hardware."""
        logger.info("Keyboard simulator started")
        logger.info("Controls: 1-4=channels, a/d=tune, w/s=volume, c=call-in, n=nfc, 7/8/9=swap slot 0/1/2, q=quit")

        loop = asyncio.get_event_loop()
        channel_keys = {"1": "music", "2": "talkshow", "3": "dailybrief", "4": "memos"}

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
                    logger.info("Recording... press 'c' again to stop")
                else:
                    self._callin_active = False
                    self.callback(InputEvent(event_type="callin_stop"))
            elif key == "n":
                self.callback(InputEvent(event_type="nfc_press"))
            elif key in ("7", "8", "9"):
                slot = int(key) - 7
                logger.info("Swap slot %d requested", slot)
                self.callback(InputEvent(event_type="swap_slot", slot_index=slot))

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

    def cleanup(self):
        """Clean up GPIO and SPI resources."""
        if self._use_gpio:
            self.GPIO.cleanup()
        if self._adc:
            self._adc.close()
