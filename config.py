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
    "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
    "SPOTIFY_CLIENT_ID": os.getenv("SPOTIFY_CLIENT_ID"),
    "SPOTIFY_CLIENT_SECRET": os.getenv("SPOTIFY_CLIENT_SECRET"),
    "SPOTIFY_REDIRECT_URI": os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
    "GNEWS_API_KEY": os.getenv("GNEWS_API_KEY"),
    "REDDIT_CLIENT_ID": os.getenv("REDDIT_CLIENT_ID"),
    "REDDIT_CLIENT_SECRET": os.getenv("REDDIT_CLIENT_SECRET"),
    "REDDIT_USER_AGENT": os.getenv("REDDIT_USER_AGENT", "RadioAgent/1.0"),

    # LLM Settings
    "LLM_MODEL": "claude-haiku-4-5-20251001",
    "LLM_MAX_TOKENS": 300,
    "LLM_TEMPERATURE": 0.85,

    # TTS Settings
    "TTS_MODEL": "eleven_flash_v2_5",
    "TTS_OUTPUT_FORMAT": "mp3_22050_32",
    "TTS_LATENCY_OPTIMIZATION": 3,

    # Audio Settings
    "SAMPLE_RATE": 22050,
    "AUDIO_CHANNELS": 1,
    "BUFFER_SIZE": 1024,

    # Voice IDs (ElevenLabs pre-made voices)
    "VOICES": {
        "news_anchor": "pNInz6obpgDQGcFmaJgB",       # Adam — Daily Brief
        "field_reporter": "EXAVITQu4vr4xnSDxMaL",     # Bella — Weather/Traffic
        "talk_host": "onwK4e9ZLuTAKqWW03F9",          # Daniel — Talk Show
        "talk_cohost": "XB0fDUnXU5powFXDhCwa",        # Charlotte — Talk co-host
        "dj": "iP95p4xoKVk53GoZ742B",                 # Chris — Music channel
        "memo_host": "pNInz6obpgDQGcFmaJgB",          # Adam — Memos readback
    },

    # ─────────────────────────────────────────────────────────────
    # GPIO Pin Assignments (BCM mode) — Raspberry Pi 5
    #
    # Pin budget (40-pin header):
    #   SPI0  (e-ink + MCP3008 ADC): GPIO 7,8,9,10,11 + 17,24,25
    #   I2S   (INMP441 mic):         GPIO 18,19,20  (21 reserved for amp)
    #   I2C1  (PN532 NFC):           GPIO 2,3
    #   Buttons (6):                 GPIO 4,5,6,13,16,26
    #   LEDs   (6):                  GPIO 12,14,15,22,23,27
    # ─────────────────────────────────────────────────────────────
    "PINS": {
        # 4 channel push buttons (active-low, internal pull-up)
        "btn_dailybrief": 5,        # pin 29
        "btn_talkshow": 6,          # pin 31
        "btn_music": 13,            # pin 33
        "btn_memos": 26,            # pin 37

        # 2 large push buttons
        "btn_callin": 16,           # pin 36 — press-and-hold to record
        "btn_nfc": 4,               # pin  7 — press to integrate NFC tag

        # 4 channel indicator LEDs (active-high, 220 ohm resistor)
        "led_dailybrief": 12,       # pin 32
        "led_talkshow": 22,         # pin 15
        "led_music": 23,            # pin 16
        "led_memos": 27,            # pin 13

        # 2 slider indicator LEDs
        "led_tuning": 14,           # pin  8  (TX — safe if UART console disabled)
        "led_volume": 15,           # pin 10  (RX — safe if UART console disabled)

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
