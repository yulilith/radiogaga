"""LED indicator controller for channel status and slider feedback."""

from log import get_logger

logger = get_logger(__name__)


class LEDController:
    """Manages 6 LEDs: 4 channel indicators + 2 slider indicators."""

    CHANNEL_LED_MAP = {
        "music": 23,            # GPIO 23, pin 16
        "talkshow": 22,         # GPIO 22, pin 15
        "dailybrief": 12,       # GPIO 12, pin 32
        "memos": 27,            # GPIO 27, pin 13
    }

    SLIDER_LED_MAP = {
        "tuning": 14,           # GPIO 14, pin 8
        "volume": 15,           # GPIO 15, pin 10
    }

    def __init__(self, config: dict):
        self.config = config
        self._use_gpio = False
        self._active = None

        try:
            import RPi.GPIO as GPIO
            self.GPIO = GPIO
            self._use_gpio = True
            self._setup()
            logger.info("GPIO LED controller initialized",
                        extra={"channel_pins": self.CHANNEL_LED_MAP,
                               "slider_pins": self.SLIDER_LED_MAP})
        except (ImportError, RuntimeError):
            logger.info("GPIO not available, LED state will be logged only")
            self._use_gpio = False

    def _setup(self):
        for pin in list(self.CHANNEL_LED_MAP.values()) + list(self.SLIDER_LED_MAP.values()):
            self.GPIO.setup(pin, self.GPIO.OUT)
            self.GPIO.output(pin, self.GPIO.LOW)

    def activate(self, channel: str):
        """Light up the LED for the given channel, turn off other channel LEDs."""
        self._active = channel
        if self._use_gpio:
            for ch, pin in self.CHANNEL_LED_MAP.items():
                self.GPIO.output(pin, self.GPIO.HIGH if ch == channel else self.GPIO.LOW)
        logger.debug("LED active channel: %s", channel)

    def set_callin(self, active: bool):
        """Toggle active channel LED to indicate call-in recording state."""
        if self._use_gpio and self._active:
            pin = self.CHANNEL_LED_MAP.get(self._active)
            if pin is not None:
                self.GPIO.output(pin, self.GPIO.HIGH if active else self.GPIO.LOW)
        logger.debug("LED call-in: %s", "ON" if active else "OFF")

    def set_slider_led(self, slider: str, on: bool):
        """Turn a slider indicator LED on or off ('tuning' or 'volume')."""
        pin = self.SLIDER_LED_MAP.get(slider)
        if pin is None:
            return
        if self._use_gpio:
            self.GPIO.output(pin, self.GPIO.HIGH if on else self.GPIO.LOW)
        logger.debug("LED slider %s: %s", slider, "ON" if on else "OFF")

    def blink_callin(self):
        """Indicate call-in processing state."""
        self.set_callin(True)

    def all_off(self):
        """Turn off all LEDs."""
        if self._use_gpio:
            for pin in list(self.CHANNEL_LED_MAP.values()) + list(self.SLIDER_LED_MAP.values()):
                self.GPIO.output(pin, self.GPIO.LOW)

    def cleanup(self):
        """Clean up GPIO."""
        if self._use_gpio:
            self.all_off()
