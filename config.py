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
    "SPOTIFY_PLAYBACK_MODE": os.getenv("SPOTIFY_PLAYBACK_MODE", "pi"),  # "pi" (librespot) or "mac" (desktop app)
    "GNEWS_API_KEY": os.getenv("GNEWS_API_KEY"),
    "REDDIT_CLIENT_ID": os.getenv("REDDIT_CLIENT_ID"),
    "REDDIT_CLIENT_SECRET": os.getenv("REDDIT_CLIENT_SECRET"),
    "REDDIT_USER_AGENT": os.getenv("REDDIT_USER_AGENT", "RadioAgent/1.0"),

    # LLM Settings
    "LLM_MODEL": "claude-sonnet-4-20250514",
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
        "news_anchor": "pNInz6obpgDQGcFmaJgB",       # Adam
        "field_reporter": "EXAVITQu4vr4xnSDxMaL",     # Bella
        "talk_host": "onwK4e9ZLuTAKqWW03F9",          # Daniel
        "talk_cohost": "XB0fDUnXU5powFXDhCwa",        # Charlotte
        "sports_commentator": "TX3LPaxmHKxFdv7VOQHJ",  # Liam
        "dj": "iP95p4xoKVk53GoZ742B",                 # Chris
    },

    # GPIO Pin Assignments (BCM mode) - only used on Raspberry Pi
    "PINS": {
        "tuning_clk": 17,
        "tuning_dt": 27,
        "tuning_sw": 22,
        "volume_clk": 23,
        "volume_dt": 24,
        "volume_sw": 25,
        "btn_news": 5,
        "btn_talkshow": 6,
        "btn_sports": 13,
        "btn_dj": 19,
        "btn_callin": 26,
        "led_news": 12,
        "led_talkshow": 16,
        "led_sports": 20,
        "led_dj": 21,
        "led_callin": 4,
    },

    # Agent-to-Agent Networking
    "AGENT_PORT": 8765,
    "MDNS_SERVICE_TYPE": "_radioagent._tcp.local.",

    # Content Settings
    "SEGMENT_WORD_LIMIT": 150,
    "HISTORY_WINDOW": 8,
    "CALLIN_MAX_SECONDS": 15,
}
