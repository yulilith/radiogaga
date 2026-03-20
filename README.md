# RadioAgent

An AI-powered physical radio built on Raspberry Pi 5. It generates live, context-aware broadcasts with multi-agent hosts, physical controls, and peer-to-peer networking between devices.

Built at **HARD MODE @ MIT** (March 2026) by Lilith Yu, Chloe Ni, Yanchen Shen, Cleo Pontone, and Sophia DeVito.

---

## What Is This?

RadioAgent is a handheld radio where every broadcast is generated live by AI. Press buttons to switch channels, turn dials to tune subchannels, call in with your voice, and hear AI hosts that react to real-time weather, news, trends, and what your friends' radios are playing.

Four always-on channels:

| Channel | What It Does | Subchannels |
|---------|-------------|-------------|
| **Daily Brief** | News, weather, traffic, local events | Local, National, World, Weather, Traffic |
| **Talk Show** | Multi-host AI debate (3 personas) | Tech, Pop Culture, Philosophy, Comedy, Advice |
| **Music** | AI DJ + Spotify playback | Top Tracks, Discover, Genre, Mood, Decade |
| **Memos** | Voice memos + NFC tag content | — |

---

## Quick Start

### Prerequisites

```bash
# macOS
brew install ffmpeg portaudio

# Debian / Ubuntu / Raspberry Pi OS
sudo apt-get install ffmpeg portaudio19-dev
```

Requires **Python 3.11+**.

### Install

```bash
git clone https://github.com/YOUR_USERNAME/radioagent.git
cd radioagent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Edit .env and add your API keys
```

**Required keys:**

| Variable | Service | Get it at |
|----------|---------|-----------|
| `ANTHROPIC_API_KEY` | Claude (LLM) | [console.anthropic.com](https://console.anthropic.com) |
| `ELEVENLABS_API_KEY` | ElevenLabs (TTS) | [elevenlabs.io](https://elevenlabs.io) |
| `DEEPGRAM_API_KEY` | Deepgram (call-in STT) | [deepgram.com](https://deepgram.com) |
| `SPOTIFY_CLIENT_ID` | Spotify | [developer.spotify.com](https://developer.spotify.com) |
| `SPOTIFY_CLIENT_SECRET` | Spotify | [developer.spotify.com](https://developer.spotify.com) |

**Optional keys:** `GNEWS_API_KEY` (news), `EXA_API_KEY` (web search)

See `.env.example` for all available settings including model selection and TTS speed.

### Run

```bash
python main.py
```

Without Raspberry Pi hardware, a keyboard simulator activates automatically:

| Key | Action |
|-----|--------|
| `1` `2` `3` `4` | Switch channels |
| `a` / `d` | Tune left / right |
| `w` / `s` | Volume up / down |
| `c` | Toggle call-in recording |
| `n` | Simulate NFC button press |
| `q` | Quit |

---

## Hardware Setup (Raspberry Pi 5)

### Components (~$90-130)

| Component | Purpose |
|-----------|---------|
| Raspberry Pi 5 + SD card + USB-C power | Main compute |
| 6x tactile push buttons | 4 channels + call-in + NFC system |
| 6x LEDs + 220-ohm resistors | Channel indicators + slider indicators |
| 2x HW-233 slide potentiometers | Tuning + volume dials |
| MCP3008 ADC | Analog-to-digital for slide pots (SPI) |
| INMP441 I2S microphone | Call-in voice input |
| 3W 8-ohm USB speaker | Audio output |
| Waveshare 2.13" e-ink display HAT | Station info display |
| PN532 NFC/RFID reader (I2C) | NFC tag content injection |
| 3D-printed enclosure (Bambu Lab) | Housing |

### GPIO Pinout (BCM)

```
BUTTONS (active-low, internal pull-up)
  GPIO  5  Channel 1 — Daily Brief
  GPIO  6  Channel 2 — Talk Show
  GPIO 13  Channel 3 — Music
  GPIO 26  Channel 4 — Memos
  GPIO 16  Call-in (press-and-hold)
  GPIO  4  System / NFC

LEDs (active-high, 220-ohm to GND)
  GPIO 12  Channel 1        GPIO 14  Tuning slider
  GPIO 22  Channel 2        GPIO 15  Volume slider
  GPIO 23  Channel 3
  GPIO 27  Channel 4

SPI0 (shared bus)
  GPIO 10  MOSI             GPIO  8  CE0 → e-ink CS
  GPIO  9  MISO             GPIO  7  CE1 → MCP3008 CS
  GPIO 11  SCLK

E-ink display control
  GPIO 25  DC               GPIO 17  RST
  GPIO 24  BUSY

I2S — INMP441 microphone
  GPIO 18  BCK              GPIO 20  DIN
  GPIO 19  WS               GPIO 21  DOUT (reserved)

I2C1 — PN532 NFC reader
  GPIO  2  SDA              GPIO  3  SCL
```

### Pi-Specific Setup

1. Enable overlays in `/boot/firmware/config.txt`:
   ```
   dtparam=i2s=on
   dtparam=spi=on
   dtoverlay=i2s-mmap
   ```

2. Uncomment the Pi-specific dependencies in `requirements.txt` and reinstall:
   ```bash
   pip install -r requirements.txt
   ```

3. Run:
   ```bash
   python main.py
   ```

---

## Testing Call-Ins

1. Start on a channel that supports callers:
   ```bash
   python main.py --channel talkshow
   ```
2. Confirm startup logs show `Found mic:` or `Using default input:`
3. Press `c` (keyboard) or hold the physical call-in button
4. Speak for 2-5 seconds, then press `c` again or release
5. Watch for: `Recording started` → `Transcribing call-in...` → `Caller said: ...`
6. The active host responds to your transcript over the speaker

If transcription fails, check `DEEPGRAM_API_KEY` and try in a quiet room.

---

## Project Structure

```
radioagent/
├── main.py                  # Entry point — wires hardware, channels, audio, networking
├── config.py                # Environment-driven config, GPIO pinout, model settings
├── content/
│   ├── agent.py             # Core LLM agent (Claude streaming + context injection)
│   ├── personas.py          # 10+ AI host personas (voice IDs, personalities, styles)
│   ├── daily_brief_channel.py
│   ├── talkshow_channel.py  # Multi-agent debate orchestration
│   ├── music_channel.py     # Spotify + AI DJ
│   └── memos_channel.py     # Voice memos + NFC
├── context/
│   ├── weather.py           # OpenWeatherMap
│   ├── news.py              # GNews API
│   ├── sports.py            # Live scores
│   ├── trends.py            # Google Trends + Reddit
│   ├── astronomy.py         # Sun/moon, celestial events
│   └── history.py           # On-this-day events
├── audio/
│   ├── tts_service.py       # ElevenLabs streaming TTS (OpenAI fallback)
│   ├── stt_service.py       # Deepgram Whisper STT
│   ├── audio_player.py      # Async audio playback with interruption
│   └── spotify_service.py   # Spotify Web API integration
├── hardware/
│   ├── input_controller.py  # GPIO buttons + ADC polling
│   ├── led_controller.py    # Channel + slider LEDs
│   ├── display_controller.py # Waveshare e-ink driver
│   ├── mic_controller.py    # I2S microphone capture
│   └── nfc_controller.py    # PN532 NFC/RFID reader
├── network/
│   ├── discovery.py         # mDNS/Zeroconf peer discovery
│   ├── peer_comm.py         # WebSocket radio-to-radio messaging
│   └── friends.py           # Friend activity tracking
├── radio-agent/             # Standalone debate runtime (separate package)
│   ├── scripts/run_local_debate.py
│   ├── radioagent/
│   │   ├── debate/orchestrator.py
│   │   ├── agents/runtime.py
│   │   ├── prompts/          # YAML persona files
│   │   └── voice/            # TTS provider boundary
│   └── pyproject.toml
├── demo_*.py                # Demo scripts for individual scenes
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  Physical Controls               │
│   Buttons → GPIO    Dials → ADC/SPI    NFC/I2C  │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│              main.py (RadioAgent)                │
│  Channel switching, state management, async loop │
└──┬──────────┬──────────┬──────────┬─────────────┘
   │          │          │          │
┌──▼───┐ ┌───▼───┐ ┌───▼───┐ ┌───▼───┐
│Daily │ │Talk   │ │Music  │ │Memos  │  Content
│Brief │ │Show   │ │       │ │       │  Channels
└──┬───┘ └───┬───┘ └───┬───┘ └───┬───┘
   │         │         │         │
┌──▼─────────▼─────────▼─────────▼────────────────┐
│              Claude API (Anthropic)              │
│  System prompts + live context → streaming text  │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│          ElevenLabs TTS (streaming)              │
│  Text → voice synthesis → MP3 audio stream       │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│              Audio Output (speaker)              │
└─────────────────────────────────────────────────┘

        ┌──────────────┐
        │ Peer Radios  │  mDNS discovery +
        │ (WebSocket)  │  WebSocket messaging
        └──────────────┘
```

---

## Design Decisions

| Decision | Why |
|----------|-----|
| Raw Anthropic SDK over Agent SDK | More control over streaming, context windows, tool use |
| Async Python throughout | Concurrent hardware events, LLM streams, and audio playback |
| Background pre-generation | Inactive channels warm up next segment for instant switching |
| SPI bus sharing (e-ink CE0 + ADC CE1) | Saves GPIO pins on Pi 5 |
| I2S mic over USB mic | Direct digital audio, lower latency, less cable clutter |
| YAML personas in `radio-agent/` | Fast iteration on personality, voice, and prompts |
| Intelligent context caching | Weather 30min, news 10min, sports 2min — balances freshness vs. cost |

---

## Running the Debate Runtime

The `radio-agent/` subdirectory contains a standalone debate show package:

```bash
cd radio-agent
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python scripts/run_local_debate.py
```

Requires `ANTHROPIC_API_KEY` and `ELEVENLABS_API_KEY`. See `radio-agent/` for full docs.

---

## Tech Stack

**AI & Voice:** Anthropic Claude, ElevenLabs TTS, Deepgram STT, OpenAI TTS (fallback)
**APIs:** Spotify, GNews, OpenWeatherMap, Google Trends, Reddit, Exa
**Hardware:** Raspberry Pi 5, MCP3008, INMP441, Waveshare e-ink, PN532 NFC
**Networking:** WebSocket, mDNS/Zeroconf
**Enclosure:** 3D-printed on Bambu Lab

---

## License

MIT
