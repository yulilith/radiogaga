# RadioGaga

RadioGaga is a local-first AI radio project with two complementary tracks in the same repo:

- a hardware-first radio prototype at the repo root that mixes physical controls, channel switching, live context, peer radios, and voice call-ins
- a cleaner packaged runtime in `radio-agent/` focused on a real-time two-host debate show with local orchestration, configurable personas, caller interruption, and swappable TTS

The short version: this repo explores what an AI-native radio can feel like when it behaves more like a live show than a chatbot.

## Repo Layout

### `radio-agent/`

This is the most modular and easiest-to-run version of the project.

- `radio-agent/scripts/run_local_debate.py`: launches the local debate stack, starts the WebSocket hub, spawns the two host runtimes, and optionally accepts live text injection
- `radio-agent/radioagent/transport/ws_hub.py`: central localhost WebSocket hub that owns session routing, caller injection, and turn dispatch
- `radio-agent/radioagent/debate/orchestrator.py`: in-memory debate state machine and turn scheduling
- `radio-agent/radioagent/agents/runtime.py`: per-host runtime that generates a turn, synthesizes audio, plays it locally, and sends the result back to the hub
- `radio-agent/radioagent/prompts/`: YAML persona files for the hosts, including voice IDs and system prompts
- `radio-agent/radioagent/voice/`: TTS provider boundary, currently ElevenLabs-first with a mock provider for testing
- `radio-agent/radioagent/audio/player.py`: local playback with interruption support
- `radio-agent/radioagent/observability/`: structured session logs and event recording

### Repo Root Prototype

The repo root is the broader "AI radio device" prototype.

- `main.py`: top-level controller that wires together hardware input, channel logic, audio, context, and peer networking
- `content/`: channel-specific behavior like talk, news, sports, and DJ flows
- `context/`: live context collection and caching
- `hardware/`: GPIO and physical input abstractions
- `audio/`: TTS, STT, playback, Spotify, and music management
- `network/`: peer discovery and radio-to-radio communication
- `config.py`: environment-driven config for the prototype stack

## What The Radio Agent Does

In the packaged `radio-agent/` runtime, two AI hosts run as separate local processes and take turns discussing a topic. A local WebSocket hub coordinates the show, tracks history, and accepts caller input. Each host has its own editable YAML persona and voice, generates a short stance-heavy response, runs TTS, and plays the result locally.

The caller path is intentionally simple right now: the user injects plain text into the show, and the active host can be interrupted so the show pivots quickly to the caller instead of waiting for the current monologue to finish.

## Design Decisions

### 1. Keep everything local first

The current debate stack runs entirely on localhost with a central WebSocket hub. That makes it easy to debug, test, and run repeatedly without introducing extra infrastructure.

Why:

- easier to reason about than a distributed first version
- simpler for hackathon iteration
- keeps state transitions visible and observable

### 2. Separate host runtimes from the hub

Each host runs in its own process and talks to the hub over typed socket messages.

Why:

- closer to how independent radio personalities should behave
- failures stay more isolated
- easier to swap prompts, voices, and backends per host later

### 3. Make personas editable outside the code

Host personas live in YAML prompt files under `radio-agent/radioagent/prompts/`.

Why:

- fast testing between runs
- easy to tune voice IDs and host style without touching orchestration code
- supports sharper host differentiation

### 4. Prefer short turns and strong opinions

We deliberately pushed the hosts away from neutral assistant behavior. The current prompts bias them toward short, punchy, stance-heavy turns and radio-friendly handoffs.

Why:

- long neutral answers do not sound like live radio
- more turns feels more conversational
- stronger priors create a more entertaining debate dynamic

### 5. Swappable TTS boundary, ElevenLabs first

The debate runtime uses a small provider boundary in `radio-agent/radioagent/voice/` so TTS can be swapped without rewriting the rest of the system.

Why:

- ElevenLabs is a strong default for fast voice prototyping
- mock TTS keeps tests and local validation cheap
- future providers can slot in at the boundary

### 6. Default to raw Anthropic API for generation

The debate runtime keeps support for the Claude Agent SDK, but the default backend is the raw Anthropic Python SDK.

Why:

- it proved more reliable in live runs for this project
- the direct messages API is simpler to validate and debug
- keeping the Agent SDK optional preserves flexibility without making it the critical path

### 7. Use explicit preflight checks

Startup validates the configured LLM and TTS providers before the show begins.

Why:

- API failures are easier to understand up front than mid-session
- reduces "silent failure" during live iteration
- shortens feedback loops when keys or models are wrong

### 8. Interrupt audio, not just text flow

Caller injection now interrupts the active host instead of merely queuing the next response after playback ends.

Why:

- live radio needs actual interruption behavior
- cutting the local playback process creates a much more believable show rhythm
- rerouting the next turn immediately makes caller input feel first-class

### 9. Keep logs structured and session-oriented

The packaged runtime records structured session events under `.radioagent/logs/`.

Why:

- replaying and debugging session flow matters more than pretty console output
- machine-readable logs help both humans and coding agents inspect behavior quickly

### 10. Keep the repo split between product direction and focused runtime

The repo root still contains the bigger radio-device vision, while `radio-agent/` is the tighter runtime that is easiest to run and evolve quickly.

Why:

- the prototype proves the broader system ambition
- the packaged runtime keeps the most useful core loop isolated
- both layers are valuable, but they solve different iteration problems

## Current Debate Host Direction

The current debate runtime is intentionally character-driven:

- `Alex` is a louder, Southern, plain-spoken American radio host
- `Blair` is a more controlled but still highly opinionated co-host

Both are configured to:

- keep responses short
- avoid neutral hedging
- react directly to callers and each other
- keep the show moving through frequent turns

## Running The Packaged Debate Runtime

From `radio-agent/`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python scripts/run_local_debate.py
```

Useful variants:

```bash
python scripts/run_local_debate.py --topic "Is Northwestern the best engineering school?"
python scripts/run_local_debate.py --max-turns 4 --no-cli
python -m radioagent.interface.cli_injector --uri ws://127.0.0.1:8765 --text "Caller says you're both wrong."
```

Key environment variables:

- `ANTHROPIC_API_KEY`
- `ELEVENLABS_API_KEY`
- `RADIO_AGENT_BACKEND`
- `RADIO_ANTHROPIC_MODEL`
- `RADIO_ELEVENLABS_MODEL`
- `RADIO_ELEVENLABS_SPEED`
- `RADIO_DEBATE_TOPIC`

## Running The Hardware-First Prototype

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
| `SPOTIFY_CLIENT_ID` | Spotify app client ID |
| `SPOTIFY_CLIENT_SECRET` | Spotify app client secret |
| `SPOTIFY_REDIRECT_URI` | Spotify OAuth redirect (default `http://127.0.0.1:8888/callback`) |

Optional variables:

| Variable | Description |
|---|---|
| `GNEWS_API_KEY` | GNews.io key for news context |
| `RADIO_ANTHROPIC_MODEL` | Claude model to use (default `claude-haiku-4-5-20251001`) |
| `RADIO_ELEVENLABS_MODEL` | ElevenLabs model (default `eleven_flash_v2_5`) |
| `RADIO_ELEVENLABS_SPEED` | TTS playback speed (default `1.2`) |

### Run

```bash
python main.py
```

This version is broader than the packaged runtime and is aimed at the full radio-device experience: channels, context, audio, physical inputs, peer radios, and voice call-ins.

## Why This Repo Exists

The core bet behind RadioGaga is that "AI radio" should feel alive: strong hosts, fast turn-taking, real caller interruption, configurable voices, and a system architecture that is simple enough to keep changing quickly.
