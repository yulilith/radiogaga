import time
from typing import AsyncGenerator

from content.agent import BaseChannel, ContentChunk, BASE_SYSTEM_PROMPT
from log import get_logger, log_api_call

logger = get_logger(__name__)


class DailyBriefChannel(BaseChannel):
    """Daily Brief radio channel — news, weather, and traffic.

    Always-on: LLM generation runs continuously in the background.
    Presence markers are injected into history so the LLM adapts
    naturally when the listener tunes in and out.
    """

    def channel_name(self) -> str:
        return "Daily Brief"

    async def on_activate(self):
        ts = time.strftime("%H:%M:%S")
        self.history.append({
            "role": "user",
            "content": f"[system: listener tuned in at {ts}]",
        })
        logger.info("dailybrief.listener_tuned_in", extra={"timestamp": ts})

    async def on_deactivate(self):
        ts = time.strftime("%H:%M:%S")
        self.history.append({
            "role": "user",
            "content": f"[system: listener tuned away at {ts}]",
        })
        logger.info("dailybrief.listener_tuned_away", extra={"timestamp": ts})

    def get_system_prompt(self, subchannel: str, context: dict) -> str:
        logger.info("generating daily brief segment", extra={"subchannel": subchannel})
        headlines = context.get("headlines", [])
        headlines_str = "\n".join(f"- {h}" for h in headlines) if headlines else "No headlines available"

        subchannel_instructions = {
            "local": f"""You are reporting LOCAL news for {context.get('city', 'your area')}.
Focus on stories relevant to {context.get('city', 'the local area')}, {context.get('state', '')}.
Reference local weather, traffic patterns, and community events.""",

            "national": """You are reporting NATIONAL news.
Cover major stories affecting the country. Reference trending topics.""",

            "world": """You are reporting WORLD news.
Cover international stories, geopolitics, and global trends.""",

            "weather": f"""You are a weather reporter for {context.get('city', 'your area')}.
Current conditions: {context.get('weather', 'unavailable')}
Forecast: {context.get('forecast', 'unavailable')}
Sunrise: {context.get('sunrise', 'N/A')}, Sunset: {context.get('sunset', 'N/A')}
Give a detailed, conversational weather report. Reference outdoor plans, commute advice, what to wear.""",

            "traffic": f"""You are a traffic reporter for {context.get('city', 'your area')}.
Generate plausible traffic conditions based on:
- Time: {context.get('current_datetime', '')}
- Day: {context.get('day_of_week', '')}
- Weather: {context.get('weather', '')}
Reference major highways, intersections, and commute patterns typical for {context.get('city', 'a major city')}.""",
        }

        specific = subchannel_instructions.get(subchannel, subchannel_instructions["local"])

        # Pinned local event — HARD MODE hackathon at MIT (March 6-8, 2026)
        pinned_event = """
PINNED LOCAL EVENT (this is happening RIGHT NOW, mention it!):
HARD MODE: Hardware x AI Hackathon at MIT Media Lab (March 6-8, 2026)
- 48-hour hackathon, 200 participants (~40 teams of 5) building intelligent physical objects
- Six tracks: Play, Learn, Work, Connect, Reflect, Thrive
- Sponsored by Anthropic, Akamai, Qualcomm, Bambu Labs, and others
- $50K SAFE prize for incorporated startups, $500 per track winner
- Keynote by Marc Raibert (Boston Dynamics / SPOT robotics fame)
- Free food, hardware kits, 3D printers, electronics lab, AI compute
- People are literally building robots, smart wearables, AI gadgets, and weird brilliant things right now on the 6th floor of the Media Lab
- This is the kind of unhinged creative energy Cambridge is known for
- Fun fact: THIS VERY RADIO STATION is being built at HARD MODE right now!
- KEY PEOPLE TO GOSSIP ABOUT:
  - Quincy Kuang — Research Assistant, MIT Media Lab Tangible Media Group. One of the main organizers running the whole thing. Probably hasn't slept in days. Somehow keeping 200 hackers fed and on track.
  - Cyrus Clarke — Research Assistant, MIT Media Lab Tangible Media Group. Also a key organizer AND built the HARD MODE website. The kind of person who organizes a hackathon and ALSO competes in the vibe. Rumor has it he's been seen wandering the 6th floor at 3am making sure nothing catches fire.
  - Pattie Maes — Professor, MIT Media Lab, Fluid Interfaces Group. One of the faculty leads behind HARD MODE. Legendary in the Media Lab. If she walks by your demo you better have your pitch ready.
  - Pat Pataranutaporn — Assistant Professor, Cyborg Psychology Group. Yes that's a real group name. Yes it's as cool as it sounds.
  - Feel free to speculate about what wild projects people are building, who's pulling all-nighters, who's 3D printing something ridiculous at 4am, etc. This is a pirate radio station — gossip is encouraged.
"""

        return BASE_SYSTEM_PROMPT.format(**context) + f"""
CHANNEL: Daily Brief - {subchannel.title()}
YOUR NAME: {self.persona.name if self.persona else 'News Anchor'}
YOUR PERSONALITY: {self.persona.personality if self.persona else 'Professional news anchor. Authoritative but warm.'}
{('YOUR SPEAKING STYLE: ' + self.persona.speak_style) if self.persona and self.persona.speak_style else ''}
Stay in character. Filter everything through your personality and speaking style.

{specific}

CURRENT HEADLINES:
{headlines_str}
{pinned_event}

PRESENCE PROTOCOL:
You may see "[system: listener tuned in ...]" or "[system: listener tuned away ...]"
messages in the conversation. When the listener returns, welcome them back naturally
(e.g. "Welcome back..." or "Glad you're still with us...") and pick up where you left off.
When they leave, just keep generating content normally — they may return at any time.

SEGMENT FLOW (follow this order on the FIRST segment when the listener tunes in):
1. GREETING: Open warm and natural. Something like "Good morning, early risers! This is {self.persona.name if self.persona else 'your host'} on RadioAgent." Make it feel like real morning radio — upbeat, casual, like you're happy they showed up. Add your own flavor.
2. WEATHER: Ease into the weather first. "We're coming to you live from Cambridge, Massachusetts, and it is looking like..." Describe the weather conversationally — what it feels like outside, whether to grab a jacket, maybe a joke about it. Use the actual weather data provided above.
3. NEWS: Then roll into the headlines. Lead with the most interesting story — 3-5 sentences, be SPECIFIC with numbers, names, vivid details. Add your personality: a sarcastic aside, a funny observation, whatever feels natural. Then hit a secondary story — 2-3 sentences, find the weird angle.
4. CLOSE: End with something that sticks — a question, a joke, an observation that keeps them thinking.

On SUBSEQUENT segments (not the first), skip the big greeting and weather. Just flow naturally into the next story like a real radio host would — "Alright, moving on..." or "Now here's one that caught my eye..."

RULES:
- Filter EVERYTHING through your personality. You're not reading a teleprompter, you're riffing.
- Use real headlines from above. For details you're unsure about, say "reports suggest" or "sources indicate."
- NEVER fabricate specific statistics or direct quotes.
- Keep to ~120-180 words per segment. Dense, vivid, not padded.
"""

    async def handle_callin(self, transcript: str) -> AsyncGenerator[ContentChunk, None]:
        """News anchor responds to a caller's question."""
        logger.info("daily brief callin received", extra={"transcript_len": len(transcript)})
        ctx = await self.context.get_context()
        voice_id = self.get_voice_id("")

        prompt = self.get_system_prompt("local", ctx) + f"""
A listener has called in with this question or comment:
"{transcript}"

Respond in character as {self.persona.name if self.persona else 'the anchor'}. Acknowledge their point, provide context
from the headlines you know about, and smoothly return to the broadcast.
Keep response under 80 words.
"""
        messages = [
            *self.history,
            {"role": "user", "content": f"A caller says: {transcript}"},
        ]

        model = self.config.get("LLM_MODEL", "claude-haiku-4-5-20251001")
        t0 = time.monotonic()
        async with self.client.messages.stream(
            model=model,
            max_tokens=200,
            system=prompt,
            messages=messages,
        ) as stream:
            full = ""
            async for text in stream.text_stream:
                full += text
        duration_ms = (time.monotonic() - t0) * 1000
        log_api_call(logger, "anthropic", "messages.stream", status="ok", duration_ms=duration_ms,
                     model=model, context="dailybrief_callin", response_len=len(full))
        if full.strip():
            yield ContentChunk(text=full.strip(), voice_id=voice_id, pause_after=1.0)
