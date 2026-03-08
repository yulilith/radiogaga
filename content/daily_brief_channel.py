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

        return BASE_SYSTEM_PROMPT.format(**context) + f"""
CHANNEL: Daily Brief - {subchannel.title()}
YOUR NAME: {self.persona.name if self.persona else 'News Anchor'}
YOUR PERSONALITY: {self.persona.personality if self.persona else 'Professional news anchor. Authoritative but warm.'}
Stay in character. Filter everything through your personality.

{specific}

CURRENT HEADLINES:
{headlines_str}

PRESENCE PROTOCOL:
You may see "[system: listener tuned in ...]" or "[system: listener tuned away ...]"
messages in the conversation. When the listener returns, welcome them back naturally
(e.g. "Welcome back..." or "Glad you're still with us...") and pick up where you left off.
When they leave, just keep generating content normally — they may return at any time.

INSTRUCTIONS:
- Open with: "This is [subchannel name] on RadioAgent" (only on first segment)
- Lead story: 2-3 sentences on the most relevant headline
- Secondary item: 1-2 sentences
- Close with a transition or tease
- Use real headlines from above. For details you're unsure about, say "reports suggest" or "sources indicate"
- NEVER fabricate specific statistics or direct quotes
- Keep to ~100-120 words per segment
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
