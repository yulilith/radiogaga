# RadioAgent

RadioAgent is a local-first AI radio built on a Raspberry Pi 5. It combines physical controls, AI-generated content channels, live context, peer networking, and voice call-ins into a device that feels like a real radio — not a chatbot.

The repo has two complementary tracks:

- A hardware-first radio prototype at the repo root with physical controls, channel switching, live context, peer radios, and voice call-ins
- A cleaner packaged runtime in `radio-agent/` focused on a real-time two-host debate show with local orchestration, configurable personas, caller interruption, and swappable TTS

## Hardware

**Platform:** Raspberry Pi 5 (Linux, Python, GPIO, audio out, WiFi built-in)

### Components (~$90-130 total)

| Component | Purpose |
|-----------|---------|
| Raspberry Pi 5 + SD card + USB-C power | Main compute |
| 6x tactile push buttons | 4 channel buttons + call-in + NFC system |
| 6x LEDs with 220-ohm resistors | 4 channel indicators + 2 slider indicators |
| 2x HW-233 slide potentiometers | Tuning dial + volume dial |
| MCP3008 ADC | Analog-to-digital for slide pots (SPI) |
| INMP441 I2S microphone | Call-in voice input |
| 3W 8ohm speaker (JST PH2.0) + USB audio adapter | Audio output |
| Waveshare 2.13" e-ink display HAT | Station info display |
| PN532 NFC/RFID reader (I2C) | Read NFC tags for content injection |
| Breadboard + jumper wires | Prototyping |
| 3D-printed enclosure | Housing |

### GPIO Pinout (BCM mode, Raspberry Pi 5)

```
┌─────────────────────────────────────────────────────────┐
│  BUTTONS (6x, active-low, internal pull-up)             │
│    GPIO  5  (pin 29)  Channel 1 — Daily Brief           │
│    GPIO  6  (pin 31)  Channel 2 — Talk Show             │
│    GPIO 13  (pin 33)  Channel 3 — Music                 │
│    GPIO 26  (pin 37)  Channel 4 — Memos                 │
│    GPIO 16  (pin 36)  Call-in (press-and-hold to talk)   │
│    GPIO  4  (pin  7)  System / NFC (process tag)         │
│                                                         │
│  LEDs (6x, active-high, 220 ohm resistor to GND)        │
│    GPIO 12  (pin 32)  Channel 1 LED — Daily Brief       │
│    GPIO 22  (pin 15)  Channel 2 LED — Talk Show         │
│    GPIO 23  (pin 16)  Channel 3 LED — Music             │
│    GPIO 27  (pin 13)  Channel 4 LED — Memos             │
│    GPIO 14  (pin  8)  Tuning slider LED                 │
│    GPIO 15  (pin 10)  Volume slider LED                 │
│                                                         │
│  SPI0 — shared by e-ink display (CE0) + MCP3008 (CE1)   │
│    GPIO 10  (pin 19)  MOSI                              │
│    GPIO  9  (pin 21)  MISO                              │
│    GPIO 11  (pin 23)  SCLK                              │
│    GPIO  8  (pin 24)  CE0 → e-ink CS                    │
│    GPIO  7  (pin 26)  CE1 → MCP3008 CS                  │
│                                                         │
│  E-ink display control                                  │
│    GPIO 25  (pin 22)  DC                                │
│    GPIO 17  (pin 11)  RST                               │
│    GPIO 24  (pin 18)  BUSY                              │
│                                                         │
│  I2S — INMP441 microphone                               │
│    GPIO 18  (pin 12)  BCK  (bit clock)                  │
│    GPIO 19  (pin 35)  WS   (word select / LRCK)         │
│    GPIO 20  (pin 38)  DIN  (data in from mic)           │
│    GPIO 21  (pin 40)  DOUT (reserved: future I2S amp)   │
│                                                         │
│  I2C1 — PN532 NFC reader                               │
│    GPIO  2  (pin  3)  SDA                               │
│    GPIO  3  (pin  5)  SCL                               │
│                                                         │
│  Power                                                  │
│    3.3V (pin 1, 17)   VCC for INMP441, PN532            │
│    5V   (pin 2, 4)    VCC for MCP3008, e-ink HAT        │
│    GND  (pins 6,9,14,20,25,30,34,39)                   │
└─────────────────────────────────────────────────────────┘
```

### Content Channels

| Button | Channel | Tuning Dial (subchannels) |
|--------|---------|--------------------------|
| 1 | Daily Brief (News & Weather) | Local → National → World → Weather → Traffic |
| 2 | Talk Show | Tech Talk → Pop Culture → Philosophy → Comedy → Advice |
| 3 | Music | My Top Tracks → Discover → Genre Radio → Mood/Vibe → Decade |
| 4 | Memos | (no subchannels — voice memo record/playback) |

### Controls

**4 channel buttons:** Press to switch between content channels. Active channel LED lights up.

**Tuning dial (slide pot):** Position maps 0-100 across subchannels within the active channel. Sliding between stations plays brief static SFX for analog radio feel.

**Volume dial (slide pot):** Controls speaker volume 0-100.

**Call-in button:** Press-and-hold to speak into the INMP441 mic. Audio is transcribed via Whisper, then the current channel's host responds to the caller as part of the show. LED glows while mic is active. Works on Talk Show channel.

**NFC/System button:** Press to read an NFC tag via the PN532 reader. Tag content (NDEF text) is saved to the Memos channel and announced via TTS.

**E-ink display:** Shows current channel name, subchannel, time, and volume level. Updates on channel/subchannel changes.

## Repo Layout

### Repo Root — Hardware Prototype

- `main.py`: top-level controller wiring hardware, channels, audio, context, networking
- `config.py`: environment-driven config with full GPIO pinout
- `content/`: channel implementations — daily brief, talk show, music, memos
- `context/`: live context collection (weather, news, sports, trends, history)
- `hardware/`: GPIO buttons, LED control, I2S mic, NFC reader, e-ink display, ADC polling
- `audio/`: TTS (ElevenLabs), STT (Whisper), playback, Spotify, music manager
- `network/`: peer discovery (mDNS) and radio-to-radio communication (WebSocket)

### `radio-agent/` — Packaged Debate Runtime

- `radio-agent/scripts/run_local_debate.py`: launches the local debate stack
- `radio-agent/radioagent/transport/ws_hub.py`: central WebSocket hub
- `radio-agent/radioagent/debate/orchestrator.py`: debate state machine
- `radio-agent/radioagent/agents/runtime.py`: per-host agent runtime
- `radio-agent/radioagent/prompts/`: YAML persona files (voice IDs + system prompts)
- `radio-agent/radioagent/voice/`: TTS provider boundary (ElevenLabs + mock)
- `radio-agent/radioagent/audio/player.py`: local playback with interruption
- `radio-agent/radioagent/observability/`: structured session logs

## Design Decisions

1. **Local-first WebSocket hub** — All coordination on localhost for easy debugging
2. **Separate host runtimes** — Each agent in its own process for isolation
3. **Editable YAML personas** — Prompts outside code for fast iteration
4. **Short, stance-heavy turns** — Audio-friendly, conversational rhythm
5. **Swappable TTS boundary** — ElevenLabs primary, OpenAI fallback
6. **Raw Anthropic SDK** — More reliable than Agent SDK for this project
7. **Explicit preflight checks** — Validate APIs before show starts
8. **Audio interruption** — Caller input cuts live playback immediately
9. **Structured session logs** — Machine-readable event recording
10. **MCP3008 ADC for analog pots** — Pi has no analog GPIO; SPI ADC reads HW-233 sliders
11. **I2S microphone (INMP441)** — Direct digital audio, no USB adapter needed for mic
12. **SPI bus sharing** — E-ink display on CE0, ADC on CE1, same SPI0 bus
13. **NFC via I2C** — PN532 on I2C1 for tag reading, separate from SPI devices
14. **E-ink for status** — Low-power persistent display, partial refresh for quick updates

## Running The Hardware Prototype

### Prerequisites

Install system-level dependencies first:

```bash
# macOS
brew install ffmpeg portaudio

# Debian / Ubuntu
sudo apt-get install ffmpeg portaudio19-dev
```

Requires **Python 3.11+**. If you are on Python 3.13 or later, `audioop-lts` is included in `requirements.txt` to replace the removed stdlib `audioop` module that `pydub` depends on.

### Setup

```bash
cd radiogaga
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Environment Variables

Copy the example and fill in your keys:

```bash
cp .env.example .env
```

Required variables:

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key for LLM generation |
| `ELEVENLABS_API_KEY` | ElevenLabs API key for TTS |
| `DEEPGRAM_API_KEY` | Deepgram API key for call-in transcription |
| `SPOTIFY_CLIENT_ID` | Spotify app client ID |
| `SPOTIFY_CLIENT_SECRET` | Spotify app client secret |
| `SPOTIFY_REDIRECT_URI` | Spotify OAuth redirect (default `http://127.0.0.1:8888/callback`) |

Optional variables:

| Variable | Description |
|---|---|
| `GNEWS_API_KEY` | GNews.io key for news context |
| `RADIO_ANTHROPIC_MODEL` | Claude model to use (default `claude-haiku-4-5-20251001`) |
| `RADIO_DEEPGRAM_MODEL` | Deepgram STT model for call-ins (default `nova-3`) |
| `RADIO_ELEVENLABS_MODEL` | ElevenLabs model (default `eleven_flash_v2_5`) |
| `RADIO_ELEVENLABS_SPEED` | TTS playback speed (default `1.2`) |

### Run

```bash
python main.py
```

On Raspberry Pi 5, also enable I2S and SPI overlays in `/boot/firmware/config.txt`:

<<<<<<< HEAD
```
dtparam=i2s=on
dtparam=spi=on
dtoverlay=i2s-mmap
```
=======
### Testing Call-In

For local testing, start on a channel that supports callers:

```bash
python main.py --channel talkshow
```

Then:

1. Confirm startup logs show a detected mic such as `Found mic:` or `Using default input:`.
2. Press `c` to start recording if you are using keyboard controls, or hold the physical call-in button on the device.
3. Speak for 2 to 5 seconds, then press `c` again or release the button to stop.
4. Watch for the log sequence `Recording started`, `Recorded ...`, `Transcribing call-in...`, and `Caller said: ...`.
5. Verify the active host responds to your transcript over the speaker.

If transcription fails, double-check `DEEPGRAM_API_KEY`, make sure the mic is the selected input device, and try again in a quiet room with the mic closer to your mouth.

## Why This Repo Exists
>>>>>>> 0cae437859bd4eff91de44367d0ea3f2c2b31e9b

Then uncomment the Pi-specific dependencies in `requirements.txt` and install them.

Without Pi hardware, the keyboard simulator activates automatically:
- `1-4` = channel buttons
- `a/d` = tune left/right
- `w/s` = volume up/down
- `c` = toggle call-in recording
- `n` = simulate NFC button press
- `q` = quit

## Running The Debate Runtime

From `radio-agent/`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python scripts/run_local_debate.py
```

Key environment variables: `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `RADIO_AGENT_BACKEND`, `RADIO_ANTHROPIC_MODEL`, `RADIO_ELEVENLABS_MODEL`.
