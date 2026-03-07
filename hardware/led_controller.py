"""LED indicator controller for channel status and call-in state."""


class LEDController:
    """Manages LED indicators for active channel and call-in status."""

    LED_MAP = {
        "news": 12,
        "talkshow": 16,
        "sports": 20,
        "dj": 21,
        "callin": 4,
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
        except (ImportError, RuntimeError):
            self._use_gpio = False

    def _setup(self):
        for pin in self.LED_MAP.values():
            self.GPIO.setup(pin, self.GPIO.OUT)
            self.GPIO.output(pin, self.GPIO.LOW)

    def activate(self, channel: str):
        """Light up the LED for the given channel, turn off others."""
        self._active = channel
        if self._use_gpio:
            for ch, pin in self.LED_MAP.items():
                if ch == "callin":
                    continue  # Callin LED managed separately
                self.GPIO.output(pin, self.GPIO.HIGH if ch == channel else self.GPIO.LOW)
        else:
            print(f"[LED] Active: {channel}")

    def set_callin(self, active: bool):
        """Set the call-in LED state."""
        if self._use_gpio:
            self.GPIO.output(self.LED_MAP["callin"],
                             self.GPIO.HIGH if active else self.GPIO.LOW)
        else:
            print(f"[LED] Call-in: {'ON' if active else 'OFF'}")

    def blink_callin(self):
        """Blink call-in LED (processing state) - simplified for hackathon."""
        self.set_callin(True)

    def all_off(self):
        """Turn off all LEDs."""
        if self._use_gpio:
            for pin in self.LED_MAP.values():
                self.GPIO.output(pin, self.GPIO.LOW)

    def cleanup(self):
        """Clean up GPIO."""
        if self._use_gpio:
            self.all_off()
