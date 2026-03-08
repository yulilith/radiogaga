import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root (same directory as this file)
_env_path = Path(__file__).parent / ".env"
load_dotenv(_env_path, override=True)

CONFIG = {
    # API Keys
    "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY"),
    "ELEVENLABS_API_KEY": os.getenv("ELEVENLABS_API_KEY"),
    "DEEPGRAM_API_KEY": os.getenv("DEEPGRAM_API_KEY"),
    "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
    "SPOTIFY_CLIENT_ID": os.getenv("SPOTIFY_CLIENT_ID"),
    "SPOTIFY_CLIENT_SECRET": os.getenv("SPOTIFY_CLIENT_SECRET"),
    "SPOTIFY_REDIRECT_URI": os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
    "SPOTIFY_PLAYBACK_MODE": os.getenv("SPOTIFY_PLAYBACK_MODE", "pi"),  # "pi" (librespot) or "mac" (desktop app)
    "GNEWS_API_KEY": os.getenv("GNEWS_API_KEY"),
    "REDDIT_CLIENT_ID": os.getenv("REDDIT_CLIENT_ID"),
    "REDDIT_CLIENT_SECRET": os.getenv("REDDIT_CLIENT_SECRET"),
    "REDDIT_USER_AGENT": os.getenv("REDDIT_USER_AGENT", "RadioAgent/1.0"),
    "EXA_API_KEY": os.getenv("EXA_API_KEY"),
    "DEBUG_LLM_WITHOUT_VOICE": os.getenv("DEBUG_LLM_WITHOUT_VOICE", "false").lower() == "true",

    # LLM Settings
    "LLM_MODEL": "claude-opus-4-6",
    "LLM_MAX_TOKENS": 300,
    "LLM_TEMPERATURE": 0.85,

    # TTS Settings
    "TTS_MODEL": "eleven_v3",
    "TTS_OUTPUT_FORMAT": "mp3_22050_32",
    "TTS_SPEED": float(os.getenv("RADIO_ELEVENLABS_SPEED", "1.1")),
    "TTS_LATENCY_OPTIMIZATION": 3,

    # STT Settings
    "DEEPGRAM_MODEL": os.getenv("RADIO_DEEPGRAM_MODEL", "nova-3"),

    # Audio Settings
    "SAMPLE_RATE": 22050,
    "AUDIO_CHANNELS": 1,
    "BUFFER_SIZE": 1024,
    "RADIO_FILTER_STRENGTH": float(os.getenv("RADIO_FILTER_STRENGTH", "0.7")),

    # Voice IDs — one per persona, sourced from content/personas.py
    "VOICES": {
        "dj":                 "iP95p4xoKVk53GoZ742B",   # Chris — DJ Spark
        "wacky_gymbro":       "IKne3meq5aSn9XLyUdCD",   # Charlie — Brax Ironclad
        "wacky_conspiracy":   "y0SYydk17lMbUIUvSf3N",   # AK British Posh — Dr. Elena
        "wacky_grandpa":      "xKhbyU7E3bC6T89Kn26c",   # Adam Spencer — Hiroshi
        "wacky_theater":      "pFZP5JQG7iQjIQuC4Bku",   # Lily (generic, unused)
        "kid_lily":           "wGcFBfKz5yUQqhqr0mVy",   # Maria Moody — Lily the kid
        "wacky_techbro":      "N2lVS1w4EtoT3dr4eOWO",   # Callum — Jax Wirecutter
        "wacky_grandma":      "cgSgspJ2msm6clMCkdW9",   # Jessica — Peggy Butterworth
        "wacky_weather":      "SOYHLrjzK2X1ezoPC6cr",   # Harry — Captain Rick Stormborn
        "wacky_alien":        "SAz9YHcvj6GT2YYXdXww",   # River — Zephyr-7
    },

    # ─────────────────────────────────────────────────────────────
    # GPIO Pin Assignments (BCM mode) — Raspberry Pi 5
    #
    # Pin budget (40-pin header):
    #   SPI0  (e-ink + MCP3008 ADC): GPIO 7,8,9,10,11 + 17,24,25
    #   I2S   (INMP441 mic):         GPIO 18,19,20
    #   I2C1  (PN532 NFC):           GPIO 2,3
    #   Rotary encoders:             GPIO 14,15 (tuning) + GPIO 23,21 (volume)
    #   Encoder buttons:             GPIO 4 (callin) + GPIO 3 (nfc)
    #   Channel buttons (4):         GPIO 5,6,13,26
    #   LEDs (4 channel):            GPIO 12,16,22,27
    # ─────────────────────────────────────────────────────────────
    "PINS": {
        # 4 channel push buttons (active-low, internal pull-up)
        "btn_dailybrief": 5,        # pin 29
        "btn_talkshow": 6,          # pin 31
        "btn_music": 13,            # pin 33
        "btn_memos": 26,            # pin 37

        # Rotary encoder push buttons
        "btn_callin": 4,            # pin  7 — tuning encoder push, press-and-hold to record
        "btn_nfc": 3,               # pin  5 — volume encoder push, press to integrate NFC tag

        # Rotary encoder rotation pins
        "enc_tuning_clk": 14,       # pin  8 — tuning encoder CLK
        "enc_tuning_dt": 15,        # pin 10 — tuning encoder DT
        "enc_volume_clk": 23,       # pin 16 — volume encoder CLK
        "enc_volume_dt": 21,        # pin 40 — volume encoder DT

        # 4 channel indicator LEDs (active-high, 220 ohm resistor)
        "led_dailybrief": 12,       # pin 32
        "led_talkshow": 22,         # pin 15
        "led_music": 16,            # pin 36 (moved from GPIO 23 — conflicts with volume encoder)
        "led_memos": 27,            # pin 13

        # ── E-ink display (Waveshare 2.13" HAT, SPI0 CE0) ──
        "epd_cs": 8,                # pin 24  SPI0 CE0
        "epd_dc": 25,               # pin 22
        "epd_rst": 17,              # pin 11
        "epd_busy": 24,             # pin 18
        # DIN/CLK shared on SPI0 bus (GPIO 10 MOSI, GPIO 11 SCLK)

        # ── MCP3008 ADC (SPI0 CE1, for slide pots) ──
        "adc_cs": 7,                # pin 26  SPI0 CE1
        # MOSI/MISO/SCLK shared on SPI0 bus (GPIO 10, 9, 11)
    },

    # ─── ADC — MCP3008 via SPI0 for HW-233 slide potentiometers ───
    "ADC": {
        "spi_bus": 0,
        "spi_device": 1,           # CE1 = GPIO 7
        "tuning_channel": 0,       # MCP3008 CH0 — tuning dial
        "volume_channel": 1,       # MCP3008 CH1 — volume dial
        "poll_interval_ms": 50,    # How often to read potentiometers
        "deadzone": 2,             # Ignore changes smaller than this (0-100 scale)
    },

    # ─── I2S Microphone — INMP441 ───
    # Directly wired to hardware I2S pins (enable via dtoverlay):
    #   SCK  = GPIO 18  (pin 12)  I2S bit clock
    #   WS   = GPIO 19  (pin 35)  I2S word select / LRCK
    #   SD   = GPIO 20  (pin 38)  I2S data in
    #   L/R  → GND (left channel)
    #   VDD  → 3.3 V
    "MIC": {
        "type": "i2s",             # "i2s" for INMP441, "usb" for USB mic fallback
        "sample_rate": 16000,
        "channels": 1,
        "chunk_size": 1024,
    },

    # ─── NFC — PN532 via I2C1 ───
    #   SDA = GPIO 2  (pin 3)
    #   SCL = GPIO 3  (pin 5)
    #   NOTE: GPIO 3 is shared with volume encoder button (btn_nfc).
    #   NFC read is triggered by the same button press, so the I2C
    #   transaction happens after the button callback fires.
    "NFC": {
        "interface": "i2c",
        "i2c_bus": 1,              # /dev/i2c-1
    },

    # ─── E-ink Display — Waveshare 2.13" V4 ───
    "DISPLAY": {
        "type": "waveshare_2in13_V4",
        "width": 250,
        "height": 122,
    },

    # ─── Speaker — 3W 8ohm via USB audio adapter ───
    # No GPIO needed; audio output via ALSA / PyAudio over USB.
    # Future upgrade: MAX98357A I2S amp on GPIO 21 (DOUT, pin 40)

    # Agent-to-Agent Networking
    "AGENT_PORT": 8765,
    "MDNS_SERVICE_TYPE": "_radioagent._tcp.local.",

    # Content Settings
    "SEGMENT_WORD_LIMIT": 150,
    "HISTORY_WINDOW": 8,
    "CALLIN_MAX_SECONDS": 15,
}
