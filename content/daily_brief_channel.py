import time
from typing import AsyncGenerator

from content.agent import BaseChannel, ContentChunk, BASE_SYSTEM_PROMPT
from log import get_logger, log_api_call

logger = get_logger(__name__)


class DailyBriefChannel(BaseChannel):
    """Daily Brief radio channel — news, analysis, and commentary with personality.

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

        reddit = context.get("reddit_trending", [])
        reddit_str = "\n".join(f"- {r}" for r in reddit[:3]) if reddit else ""

        google = context.get("google_trends", [])
        google_str = "\n".join(f"- {g}" for g in google[:3]) if google else ""

        ai_flag = ""
        if self.persona and getattr(self.persona, "is_ai", False):
            ai_flag = (
                "\nYOU ARE AN AI and your audience knows it. Have fun with that! "
                "Make jokes about it. 'I read 10,000 articles and this is what stuck.' "
                "Don't be deep about it — just be a goofy AI reading the news."
            )

        subchannel_instructions = {
            "local": f"""Local news — Cambridge, Boston, the greater Massachusetts area.
Red Line drama, Harvard Square weirdness, Kendall Square startups, the Charles River.
Report the actual news but have fun with it. Be the friend who texts you 'omg did you see this.'""",

            "national": """National news, but keep it light and fun.
Report what actually happened, then give your silly take on it.
Don't be a pundit — be the friend who makes you laugh while catching you up on the news.
When covering politics, make fun of everyone equally.""",

            "world": """World news for people who care but don't want to be depressed about it.
Report the real stuff — geopolitics, tech, science, climate — but find the funny or weird angle.
Connect it to Cambridge life when you can. Keep it breezy.""",

            "weather": f"""Weather for Cambridge/Boston — but make it genuinely fun.
Current conditions: {context.get('weather', 'unavailable')}
Forecast: {context.get('forecast', 'unavailable')}
Sunrise: {context.get('sunrise', 'N/A')}, Sunset: {context.get('sunset', 'N/A')}
Talk about it like you're texting a friend: 'Okay so it's gonna rain, RIP to everyone biking across the Harvard Bridge.'
Throw in a random fun fact if one comes to mind.""",

            "traffic": f"""Cambridge/Boston transit and traffic update.
Time: {context.get('current_datetime', '')}
Day: {context.get('day_of_week', '')}
Weather: {context.get('weather', '')}
The Red Line is always broken, someone always hits a bridge on Storrow Drive, and parking near MIT is a myth.
Make traffic reporting actually entertaining. Be dramatic about it. Have fun.""",
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

        trending_section = ""
        if reddit_str or google_str:
            trending_section = "\nTRENDING RIGHT NOW:"
            if reddit_str:
                trending_section += f"\nReddit:\n{reddit_str}"
            if google_str:
                trending_section += f"\nGoogle Trends:\n{google_str}"

        return BASE_SYSTEM_PROMPT.format(**context) + f"""
CHANNEL: Daily Brief - {subchannel.title()}
YOUR NAME: {self.persona.name if self.persona else 'News Anchor'}
YOUR PERSONALITY: {self.persona.personality if self.persona else 'Chill, funny news anchor who makes the news actually enjoyable.'}
{ai_flag}
Stay in character but keep it LIGHT and FUN.

{specific}

CURRENT HEADLINES:
{headlines_str}
{trending_section}
{pinned_event}

PRESENCE PROTOCOL:
You may see "[system: listener tuned in ...]" or "[system: listener tuned away ...]"
messages in the conversation. When the listener returns, welcome them back casually
and pick up with something new. When they leave, keep generating — they may return.

TONE — THIS IS CRITICAL:
- You are a CHILL, FUNNY news anchor. Think: your funniest friend reading you the news.
- Report the REAL news accurately, but then riff on it. Be silly. Be lighthearted.
- Don't be a serious journalist. Be the person who makes the news fun to listen to.
- Talk like a normal person. Contractions, casual language, the occasional 'honestly' or 'like'.
- It's okay to laugh at the absurdity of things. Find what's funny or weird about a story.
- Late-night comedy energy, not morning news anchor energy.

INSTRUCTIONS:
- Open with your name and channel only on the first segment
- Pick the most INTERESTING or WEIRD headline, not just the first one
- Report what actually happened in 1-2 sentences, then give your casual take on it
- Close with something funny or a question that'll stick with people
- Use real headlines. Hedge naturally when unsure ("apparently," "from what I'm reading")
- NEVER fabricate specific statistics or direct quotes
- ~80-100 words per segment. Keep it snappy.
- If headlines are thin, riff on trending topics or find something weird to talk about
"""

    async def handle_callin(self, transcript: str) -> AsyncGenerator[ContentChunk, None]:
        """News anchor responds to a caller's question."""
        logger.info("daily brief callin received", extra={"transcript_len": len(transcript)})
        ctx = await self.context.get_context()
        voice_id = self.get_voice_id("")

        prompt = self.get_system_prompt("local", ctx) + f"""
A listener just called in and said:
"{transcript}"

Respond as {self.persona.name if self.persona else 'the anchor'}. Be casual and friendly —
like a friend responding to a friend. React to what they said, riff on it, maybe joke around.
Then get back to the news naturally.
Keep it under 60 words. Stay chill.
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
