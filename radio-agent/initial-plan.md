# RadioAgent: AI-Powered Radio Hackathon Plan

## Context

Build a physical radio device powered by LLM agents for a 24-hour hackathon. The radio generates personalized, context-aware audio content in real time. Users interact via physical buttons for content types, a tuning dial for subchannel selection, and a volume dial. A dedicated call-in button lets users speak into a mic and participate in live talk shows.

Agent-to-agent interaction is a core feature: nearby radios discover each other and co-host content.

## Hardware

### Platform

- Raspberry Pi 4B (Linux, Python, GPIO, audio out, Wi-Fi built in)

### Components

Estimated total: `~$90-130`

- Raspberry Pi 4B + SD card + USB-C power
- 5x tactile push buttons (4 channel buttons + 1 call-in button)
- 5x LEDs with 220-ohm resistors (4 channel indicators + 1 call-in / mic-active indicator)
- 2x KY-040 rotary encoders (1 tuning dial, 1 volume dial)
- USB microphone
- Speaker + USB audio adapter (simplest path); I2S MAX98357A amp as an upgrade
- Breadboard + jumper wires
- Cardboard or 3D-printed enclosure

### GPIO Pins (BCM)

| Control | Pins |
| --- | --- |
| Tuning Dial | `CLK=GPIO17`, `DT=GPIO27`, `SW=GPIO22` |
| Volume Dial | `CLK=GPIO23`, `DT=GPIO24`, `SW=GPIO25` |
| Buttons | `News=GPIO5`, `Talk=GPIO6`, `Sports=GPIO13`, `DJ=GPIO19`, `CallIn=GPIO26` |
| LEDs | `News=GPIO12`, `Talk=GPIO16`, `Sports=GPIO20`, `DJ=GPIO21`, `CallIn=GPIO4` |

## Content Channels

| Button | Channel | Dial Tuning (subchannel examples) |
| --- | --- | --- |
| 1 | News & Weather | Local -> National -> World -> Weather -> Traffic |
| 2 | Talk Show | Tech Talk -> Pop Culture -> Philosophy -> Comedy -> Advice |
| 3 | Sports | Basketball -> Football -> Soccer -> F1 -> Baseball |
| 4 | DJ & Music (Spotify) | My Top Tracks -> Discover -> Genre Radio -> Mood/Vibe -> Decade |
| 5 (Call-In) | User Mic Input | Press and hold to talk into the current show |

- Tuning dial: Rotary encoder position (`0-100`) maps to subchannels within the active channel. Tuning between stations plays brief static SFX for an analog radio feel.
- Volume dial: Second rotary encoder controls speaker volume (`0-100`). The push-button on the volume dial toggles mute.
- Call-in button: Press and hold to speak into the USB mic. Audio is transcribed with Whisper, then the talk show agent responds to the caller as part of the show. This works on Talk Show and Sports channels. The LED glows while the mic is active.
- Cut order if behind schedule: Sports first, then DJ/Music. News + Talk Show + Call-In + A2A still make a compelling demo.

## Software Architecture

```text
radioagent/
├── main.py                  # Entry point, async event loop
├── config.py                # API keys, pin assignments, constants
├── hardware/
│   ├── input_controller.py  # Buttons + both rotary encoders GPIO handling
│   ├── led_controller.py    # LED state management
│   └── mic_controller.py    # USB mic recording (call-in feature)
├── audio/
│   ├── tts_service.py       # ElevenLabs streaming TTS wrapper
│   ├── stt_service.py       # Speech-to-text (Whisper via local or API)
│   ├── audio_player.py      # PyAudio buffer + playback thread + volume control
│   ├── spotify_service.py   # Spotipy wrapper: auth, playback, recommendations
│   └── music_manager.py     # Fallback: royalty-free clip selection (no Spotify)
├── content/
│   ├── agent.py             # BaseChannel ABC, ContentChunk, LLM streaming
│   ├── channels.py          # Channel registry + subchannel definitions
│   ├── news_channel.py      # News-specific prompts + generation
│   ├── talkshow_channel.py  # Talk show prompts + generation + call-in handling
│   ├── sports_channel.py    # Sports prompts + generation
│   └── dj_channel.py        # DJ banter + music interleaving
├── context/
│   ├── context_provider.py  # Aggregates all context sources into one dict
│   ├── weather.py           # Open-Meteo API
│   ├── news.py              # GNews.io API
│   ├── sports.py            # ESPN hidden API
│   ├── trends.py            # Reddit (praw) + Google Trends (pytrends)
│   ├── history.py           # Wikipedia "On This Day" feed
│   ├── location.py          # ip-api.com geolocation
│   └── astronomy.py         # Sunrise-Sunset.org
├── network/
│   ├── discovery.py         # mDNS via python-zeroconf (agent-to-agent)
│   └── peer_comm.py         # WebSocket server/client for agent communication
└── assets/
    ├── music/               # Royalty-free clips per genre (15-30s each)
    ├── jingles/             # Station IDs (TTS pre-generated)
    └── sfx/                 # Tuning static, channel switch sounds
```

## Core Data Flow

1. Button press or dial turn.
2. `InputController` emits an event.
3. Main controller cancels current generation and plays static SFX.
4. `Channel.stream_content(subchannel)` asks Claude to stream text in sentence chunks.
5. TTS converts each chunk to streaming audio bytes.
6. `AudioPlayer` queues and plays audio through the speaker at the current volume.
7. The loop generates the next segment when the buffer runs low.

### Call-In Flow

1. User holds the Call-In button and the mic records audio.
2. User releases the button and audio is sent to Whisper STT.
3. Transcript is injected into the talk show agent as `caller says: ...`.
4. The agent responds in character as the host reacting to a caller.
5. Response audio is synthesized with TTS and played through the speaker.

### Key Abstraction: `BaseChannel`

Each channel implements `async stream_content(subchannel) -> AsyncGenerator[ContentChunk]`.

A `ContentChunk` contains:

- `text`
- `voice_id`
- `pause_after`
- optional `music_path`

Each channel maintains a sliding window of 5-8 previous segments as conversation history to reduce repetition.

Channels that support call-in, such as Talk Show and Sports, also implement `async handle_callin(transcript: str) -> AsyncGenerator[ContentChunk]`.

## APIs and Services

### Core APIs

| Service | Purpose | Cost | Auth | Python Library |
| --- | --- | --- | --- | --- |
| Anthropic Claude Haiku 4.5 | Content generation (LLM) | `~$0.10-0.50 total` | API key | `anthropic` |
| ElevenLabs Flash v2.5 | Text-to-speech (streaming) | Free tier (`10k chars/mo`) | API key | `elevenlabs` |
| OpenAI Whisper API | Speech-to-text (call-in) | `~$0.006/min` | API key | `openai` |
| Spotify Web API | DJ mode: music playback, user taste, recommendations | Free (Premium required) | OAuth | `spotipy` |

### Context APIs

| Service | Purpose | Cost | Auth | Python Library |
| --- | --- | --- | --- | --- |
| Open-Meteo | Weather: current + forecast | Free, unlimited | None | `requests` |
| GNews.io | News headlines from 60K+ sources, 22 languages | Free (`100 req/day`) | API key | `requests` |
| ip-api.com | Geolocation from IP | Free (`45 req/min`) | None | `requests` |
| ESPN hidden API | Live sports scores, schedules, standings | Free, unlimited* | None | `requests` |
| Reddit API | Trending discussions, community pulse | Free (`100 req/min`) | OAuth | `praw` |
| Wikipedia Feed API | "On this day" historical events | Free, unlimited | Bearer token | `requests` |
| Sunrise-Sunset.org | Sunrise and sunset times for atmosphere | Free, unlimited | None | `requests` |
| Alpha Vantage | Stock market and crypto prices | Free (`25 req/day`) | API key | `alpha_vantage` |
| Google Trends | Trending search queries | Free* (unofficial) | None | `pytrends` |

## Agent-to-Agent Design

### Overview

- Discovery: mDNS via `python-zeroconf`. Each RadioAgent advertises `_radioagent._tcp.local.` on the local Wi-Fi network with metadata: `agent_id`, current channel, and user interests.
- Proximity: Same Wi-Fi network. This maps naturally to "people in the same room/event." Agents auto-discover each other when powered on.
- Communication: WebSocket on port `8765`. Once discovered, agents establish persistent connections.

### Interaction Modes

- Co-Host Talk Show: When two agents are both on Talk Show, they generate a live conversation. Agent A states an opinion, sends it to Agent B via WebSocket, and Agent B generates a counterpoint. Both radios play the full exchange with different voices.
- Shared News Desk: Two agents on News split stories like co-anchors. Agent A covers story 1 and Agent B covers story 2.
- Cross-Radio Call-In: When a human uses the call-in button, their transcript can be broadcast to a nearby agent's show. The other radio's host agent responds to the remote caller.
- Human connection angle: The agents act as mediators, not replacements. They create shared listening moments that nearby humans can react to together.

### Protocol Messages

| Message | Payload | Notes |
| --- | --- | --- |
| `hello` | `{agent_id, interests, current_channel}` | Initial handshake |
| `goodbye` | `{agent_id}` | Disconnect event |
| `cohost_prompt` | `{statement, topic, channel}` | "I just said this, your turn" |
| `cohost_response` | `{response, voice_id}` | "Here's my reply" |
| `callin_forward` | `{transcript, caller_agent_id}` | Forward a human caller to a peer |
| `channel_sync` | `{channel, subchannel}` | Notify peers of channel changes |

## DJ and Music Channel

### How It Works

The DJ agent curates music from the user's Spotify account, plays songs via Spotify Connect, and talks between tracks with contextual DJ banter.

### Setup

Install `librespot` on the Pi so it becomes a Spotify Connect device. The `spotipy` library controls playback.

### Auth

Use OAuth Authorization Code Flow. The user authenticates once via browser, and the refresh token is stored for long-running use. Spotify Premium is required.

### Dial Tuning

| Dial Position | Subchannel | Source |
| --- | --- | --- |
| `0-20` | My Top Tracks | User's top tracks + recently played |
| `21-40` | Discover | Spotify recommendations seeded from user taste |
| `41-60` | Genre Radio | User picks genre, agent curates within it |
| `61-80` | Mood/Vibe | Agent picks songs matching time-of-day mood |
| `81-100` | Decade | Agent curates by era (`80s`, `90s`, `2000s`, etc.) |

### DJ Agent Flow

1. Analyze the user's Spotify data, including top tracks, genres, and audio features.
2. Use Claude to plan a set based on time of day, weather, and current trends.
3. Queue the first song via Spotify API so it plays through the Pi speaker via `librespot`.
4. While the song plays, generate DJ banter about the track, artist, and thematic connection to the next song.
5. As the song nears its end, speak the banter via TTS.
6. Queue the next song and repeat.

### DJ Banter Examples

- "That was Radiohead with Everything In Its Right Place. Perfect for a Friday night wind-down. Speaking of winding down, let's keep this mellow vibe going with..."
- "You've been listening to a lot of indie rock lately. Let me throw in something from your Discover Weekly that I think you'll dig..."
- "It's almost sunset here in San Francisco, 6:12 PM. Time to shift the mood. Here's something a bit warmer..."

### Audio Mixing Challenge

- Simple and recommended for the hackathon: Pause Spotify, play DJ banter via TTS, then resume Spotify. This avoids audio mixing complexity.
- Advanced: Use PulseAudio to mix both streams so TTS can sit over the music.

Use Spotify's `currently_playing` endpoint to track `progress_ms` and `duration_ms` so banter starts near the end of a song.

### Transitions and Fallback

- No audio-level remixing. The DJ banter naturally fills the gap between songs.
- If Spotify Premium is unavailable, use preloaded royalty-free music clips from Free Music Archive or Incompetech.

### Implementation Files

- `audio/spotify_service.py`: Spotipy wrapper for auth, playback control, recommendations, and audio features
- `content/dj_channel.py`: DJ agent for set planning, banter generation, and the song-banter-song loop

## Call-In Feature Design

### Hardware

USB microphone connected to the Pi. Call-In button on `GPIO26` with LED indicator on `GPIO4`.

### Interaction

1. User presses and holds the Call-In button. LED turns on and recording starts.
2. User speaks a question or comment for up to 15 seconds.
3. User releases the button. Recording stops and the LED blinks while processing.
4. Audio is sent to Whisper STT and converted into transcript text.
5. Transcript is injected into the current channel's agent as a caller message.
6. The agent responds in character and the audio plays through the speaker. LED turns off.

### Channel Support

- Talk Show: natural fit
- Sports: caller hot takes
- News: listener questions
- DJ: ignores call-ins mid-set

### With Agent-to-Agent

If two radios are connected, a call-in on Radio A can be forwarded to Radio B's show via the `callin_forward` WebSocket message. Radio B's host agent responds to the remote caller.

### Implementation

- `hardware/mic_controller.py` handles recording via PyAudio input streams
- `audio/stt_service.py` wraps Whisper
- `content/talkshow_channel.py` includes prompt instructions for handling caller input naturally

## 24-Hour Timeline

### Phase 0: Setup (`Hours 0-2`)

- Flash Pi OS and install system dependencies: `portaudio19-dev`, `ffmpeg`, `librespot`
- Create a Python virtual environment and install all packages
- Sign up for APIs: Anthropic, ElevenLabs, OpenAI (Whisper), Spotify, GNews, Reddit
- Assemble the breadboard with all 5 buttons, 5 LEDs, 2 encoders, USB mic, and USB speaker
- Test GPIO, test USB mic, and verify `librespot` plays Spotify audio
- Test APIs independently: Claude streams text, TTS returns audio, Whisper transcribes, Spotify plays a track

### Phase 1: Core Pipeline + A2A Foundation (`Hours 2-8`)

- Build `hardware/input_controller.py` for 5 buttons + both encoders
- Build `audio/audio_player.py` with a buffer queue and volume control
- Build `audio/tts_service.py` for ElevenLabs streaming
- Build context APIs: `context/weather.py`, `context/news.py`, `context/location.py`, `context/context_provider.py`
- Milestone (`Hour 4`): Button press -> Claude -> TTS -> speaker playback end to end
- Build `content/news_channel.py` using real weather + news context
- Build `content/talkshow_channel.py`
- Build `network/discovery.py` and `network/peer_comm.py`
- Milestone (`Hour 8`): Two channels + dial tuning + volume control + agent discovery

### Phase 2: A2A + Call-In + Spotify DJ (`Hours 8-16`)

- Build co-host talk show mode over WebSocket
- Build `hardware/mic_controller.py` and `audio/stt_service.py` for call-in recording and transcription
- Integrate call-in into the talk show channel
- Build `audio/spotify_service.py` for OAuth, playback control, recommendations, and audio features
- Build `content/dj_channel.py`
- Build `content/sports_channel.py` using ESPN live scores context
- Add more context APIs: `context/sports.py`, `context/trends.py`, `context/history.py`, `context/astronomy.py`
- Add tuning static and crossfade behavior between subchannels
- Add cross-radio call-in forwarding
- Milestone (`Hour 16`): All 4 channels + Spotify DJ + call-in + two radios co-hosting

### Phase 3: Polish (`Hours 16-22`)

- Tune prompts so content feels natural, non-repetitive, and contextually rich
- Build the hardware enclosure so it looks and feels like a radio
- Improve buffer management to handle latency and avoid silence gaps
- Improve DJ transitions and time-of-day mood awareness
- Pre-generate station ID jingles with TTS
- Handle edge cases such as agent disconnects, Spotify auth expiry, mic errors, and API failures
- Milestone (`Hour 20`): Stable, polished, demo-ready experience

### Phase 4: Demo Prep (`Hours 22-24`)

- Feature freeze at hour 22
- Pre-generate backup audio in case APIs fail during the demo
- Ensure the Spotify auth token is fresh and pre-warm all API connections
- Practice the demo script 2-3 times
- Prepare royalty-free music clips as a fallback if Spotify fails

## Demo Script (`4 minutes`)

1. Intro (`20s`): "This is RadioAgent, a physical radio powered by AI agents. Everything you hear is generated live."
2. News (`30s`): Press News. The agent delivers real news for this city at this time. Turn the dial to Weather for the local forecast.
3. Talk Show (`30s`): Press Talk Show. A different voice riffs on what is trending on Reddit and Google right now.
4. Call-In (`40s`): Press and hold Call-In. Speak a question. Release. The host responds live. "You just called into an AI talk show."
5. DJ Mode (`40s`): Press DJ. The agent says, "Let's check what you've been listening to..." pulls from the user's Spotify, plays a real song, then explains the pick and what comes next.
6. Agent-to-Agent (`45s`): Bring over a second radio. Both are on Talk Show. Two agents discover each other and co-host with different voices and opinions in real time.
7. Cross-Radio Call-In (`20s`): Call in on Radio A. Radio B's host responds to the caller from the other radio.
8. Close (`10s`): "Context-aware, personalized, social AI radio."

Backup: show live Claude, TTS, and Spotify calls in the terminal as visual support. Keep pre-generated audio clips ready if APIs fail.

## Risk Mitigations

| Risk | Mitigation |
| --- | --- |
| TTS quota exhaustion | Monitor usage, fallback to OpenAI TTS, pre-generate backup audio |
| Network latency leading to silence | Buffer 2-3 segments ahead, play a jingle during buffer underrun |
| Wi-Fi drops at demo | Use a phone hotspot backup, add offline cached content mode |
| PyAudio install issues | Fallback to `mpg123` subprocess or `aplay` for playback |
| Whisper API latency | Use `faster-whisper` locally with a tiny model as fallback |
| A2A discovery fails at venue | Test on a hotspot beforehand, add a force-connect-by-IP fallback |
| USB mic not detected | Test early, bring a `3.5 mm` mic + USB adapter backup |
| Spotify Premium required | Use a team member's Premium account, fallback to royalty-free music clips |
| Spotify OAuth expires | Store refresh token, support re-auth flow, pre-warm before demo |
| `librespot` install fails | Fallback to phone playback connected to the Pi speaker via Bluetooth or aux |

## Dependencies

### `requirements.txt`

```txt
# Core
anthropic>=0.40.0
elevenlabs>=1.0.0
openai>=1.12.0
spotipy>=2.16.0
PyAudio>=0.2.14
pydub>=0.25.1

# Hardware
RPi.GPIO>=0.7.1
gpiozero>=2.0

# Networking
aiohttp>=3.9.0
python-zeroconf>=0.131.0
websockets>=12.0

# Context APIs
praw>=7.7.0
pytrends>=4.9.0
# alpha_vantage>=2.3.0

# Utilities
python-dotenv>=1.0.0
# faster-whisper>=1.0.0
```

### System Packages

- `sudo apt install python3-pip python3-venv portaudio19-dev libmpg123-dev ffmpeg`
- Spotify Connect on Pi: install `librespot`

## Verification

- Unit test GPIO: run `test_gpio.py`, press all 5 buttons, turn both dials, and verify events print to the console
- Unit test audio pipeline: run `test_audio.py`, send hardcoded text to TTS, and verify speaker playback at the current volume
- Unit test mic: run `test_mic.py`, record 3 seconds of audio, transcribe with Whisper, and print the transcript
- Integration test: run `main.py`, switch channels, tune the dial, adjust volume, and verify audio works
- Call-in test: hold the call-in button, speak, release, and verify the agent responds to the caller
- Agent-to-agent test: run two instances or two Pis on the same Wi-Fi, then verify mDNS discovery, co-host talk show behavior, and cross-radio call-in
- Demo rehearsal: run the full 3-4 minute demo script, time it, and keep backup audio ready

