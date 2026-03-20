## Inspiration

We love radio. There's something magical about turning a dial and landing on a voice mid-sentence — a host cracking a joke about the weather, debating the news, or playing the perfect song. But traditional radio is dying, and voice assistants feel transactional. We asked: what if AI could run a real radio station?

We also noticed something about how people connect now — it's all through screens, all remote, all frictionless. We wanted to build something where you have to be physically present to share. So we designed Radio Ga Ga around NFC tokens: small physical tags that represent your AI agent. To bring a friend's agent onto your radio, they have to hand you their token in person. You place it on the machine, and their agent appears on your broadcast — like a guest dropping by the studio. It's a deliberate choice: the most interesting interactions require showing up.

Radio Ga Ga is a local-first, multi-agent radio that feels alive. It has buttons you press and dials you turn to interact with AI agents powered by Claude living inside it. It generates live content through the persona of a radio host about your actual weather, your local news, and what your friends are up to.


## What it does

Radio Ga Ga is a handheld radio device with four always-on channels, each generating live audio content powered by Claude and ElevenLabs voice synthesis.

**Channels:**
- **Daily Brief** — Personalized news, weather, traffic, and local events. The host knows your city, the current forecast, trending topics, and what happened on this day in history. Rotate the tuning dial to switch between Local, National, World, Sports, and Culture subchannels.
- **Talk Show** — Three AI hosts debate and riff on topics in real time. Each host has a distinct personality, speaking style, and voice. Turn the dial to change the topic — and the entire cast swaps out.
- **Music** — An AI DJ introduces tracks with context-aware banter, powered by Spotify integration. The DJ reacts to the time of day, the weather, and what you've been listening to.
- **Memos** — Record voice memos with the built-in microphone, or tap an NFC tag to inject content into the broadcast.

**Physical controls bring it to life:**
- 4 channel buttons with LED indicators
- Tuning dial (5 subchannels per channel)
- Volume dial
- Call-in button — press and hold to speak live into the broadcast. Your voice is transcribed in real-time and the current host responds to you on-air.
- NFC reader for physical token interaction
- E-ink display showing the current channel, subchannel, and time

**Agent tokens and social features:**
Every user has a physical NFC token that represents their AI agent. To bring someone's agent onto your radio — as a talkshow guest, a co-host, or just to hear what they've been up to — they have to give you their token in person. You place it on the radio and their agent joins the broadcast live. This is intentional: we wanted the social layer to require real-world presence, not just a friend request.

Beyond tokens:
- Radios on the same network automatically discover each other via mDNS
- Hosts gossip about what your friends' radios are tuned to ("I hear Chloe's radio is on the Talk Show channel right now...")
- Call-ins can be forwarded between radios over WebSocket

## How we built it

**Hardware:**
- Raspberry Pi 5 as the brain, housed in a custom 3D-printed enclosure designed and printed on a Bambu Lab printer
- 6 GPIO buttons (4 channels + call-in + NFC/system) with matching LEDs
- 2 rotary encoders for tuning and volume
- MCP3008 ADC over SPI for analog potentiometer input
- Waveshare 2.13" e-ink display over SPI for low-power persistent UI
- USB microphone for direct digital audio capture (call-ins)
- PN532 NFC/RFID reader over I2C
- USB speaker for audio output

**Software architecture:**
The entire system is async Python built on asyncio, enabling concurrent hardware events, LLM generation, and audio playback. The codebase is modular: a hardware layer for GPIO/SPI/I2C drivers, content channels for each radio mode, a persona system with 10+ distinct AI hosts, real-time context providers (weather, news, sports, trends), audio services for TTS streaming and STT transcription, and a network layer for mDNS discovery and WebSocket peer communication.

**AI and APIs:**
- Anthropic Claude — the core intelligence. Every channel uses Claude for content generation with carefully crafted system prompts that inject live context. The Talk Show channel runs 3 concurrent Claude agents, each embodying a different persona, taking turns speaking and naturally handing off conversation.
- ElevenLabs TTS — 10+ distinct voice IDs for streaming voice synthesis. Each persona maps to a unique voice with tuned stability and style settings.
- Deepgram — real-time speech-to-text for call-in transcription.
- Spotify Web API — track metadata and playback control for the Music channel.
- GNews, OpenWeatherMap, Google Trends, Reddit API — live context feeds cached intelligently (weather: 30min, news: 10min, sports: 2min).

**The multi-agent Talk Show:**
The Talk Show is the technical centerpiece. Three Claude agents run concurrently, each with a distinct persona, a shared conversation transcript so they can react to each other, tool-use capabilities for sound effects and music interludes, and natural turn-taking where each agent decides who speaks next. When you press the call-in button, the active audio is immediately cancelled, your voice is transcribed via Deepgram, and the current host responds to you live on-air.

## Challenges we ran into

- **Latency management** — Chaining Claude generation + ElevenLabs TTS streaming + audio playback creates noticeable delay. We mitigated this with background pre-generation: inactive channels warm up their next segment in the background, so channel switches feel near-instant.
- **Multi-agent coordination** — Getting three Claude agents to have a natural-sounding conversation without talking over each other or getting stuck in loops required careful prompt engineering and a shared transcript state machine.
- **Hardware on a deadline** — Wiring GPIO buttons, SPI displays, I2S microphones, and NFC readers on a Pi 5 while simultaneously building the software stack in 48 hours was intense. The 3D-printed enclosure went through multiple iterations to fit everything.
- **Hardware integration gaps** — Honestly, a lot of the hardware integration is still incomplete or untested. The I2S microphone, NFC reader, and e-ink display all have drivers written but haven't been fully validated end-to-end on the Pi. We ended up hard-coding many of our demo sequences — pre-generating TTS audio and scripting the scenes rather than running everything live. The software architecture is real and the pieces work individually, but the fully integrated "turn it on and everything just works" experience isn't there yet.
- **Context window management** — Each channel accumulates conversation history. We had to implement smart truncation and summarization to keep Claude's context window fresh without losing continuity.
- **Call-in interruption** — Cleanly cancelling in-progress TTS audio, splicing a caller's transcribed voice into the transcript, and resuming generation required careful async cancellation patterns.

## Accomplishments that we're proud of

- **It actually feels like radio.** The static crackle on channel switch, the tuning dial, the e-ink display — it doesn't feel like a tech demo. It feels like a device you'd want on your nightstand.
- **The Talk Show hosts sound alive.** Three distinct AI personalities debating, interrupting, and riffing on each other creates something that genuinely sounds like a podcast — not a chatbot.
- **The enclosure came out great.** Our 3D-printed enclosure fit everything on the first assembled print — buttons, dials, display, speaker, and Pi 5 all snug. It looks and feels like a real product, not a breadboard in a box.

## What we learned

- **Physical interfaces change everything.** The same AI content feels completely different when you access it through a dial and a button versus a screen. Embodiment matters.
- **Multi-agent conversation is hard but rewarding.** Getting agents to naturally hand off turns, reference each other's points, and maintain distinct personalities pushed us to think deeply about prompt architecture.
- **Context injection is the secret sauce.** The difference between a generic AI radio host and one that knows it's 42 degrees and raining in Cambridge right now is enormous. Real-time data makes AI feel present.
- **Async Python is powerful but unforgiving.** Managing concurrent hardware events, LLM streams, and audio playback taught us a lot about asyncio's cancellation model and task lifecycle.

## What's next for Radio Ga Ga: Agentic Radio

- I2S audio output via MAX98357A amplifier for better sound quality without USB
- Exa API integration for deep web research — hosts can pull in detailed articles on topics mid-conversation
- Persistent memory — the radio remembers your preferences, your name, your favorite topics across sessions
- More channels — a Storytelling channel (serialized AI fiction), a Language Learning channel, a Meditation channel
- Enclosure v2 — refined 3D-printed design with integrated speaker grille and antenna aesthetic
- Open-source release — we want anyone with a Raspberry Pi to be able to build their own Radio Ga Ga
