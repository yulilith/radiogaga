import time
from typing import AsyncGenerator

from content.agent import BaseChannel, ContentChunk, BASE_SYSTEM_PROMPT
from context.sports import get_scores
from log import get_logger, log_api_call

logger = get_logger(__name__)


class SportsChannel(BaseChannel):
    """Sports radio channel with live scores and commentary."""

    channel_id = "sports"

    def channel_name(self) -> str:
        return "Sports"

    def get_system_prompt(self, subchannel: str, context: dict) -> str:
        # Sports-specific context
        scores = context.get("live_scores", [])
        scores_str = "\n".join(f"- {s}" for s in scores) if scores else "No live scores available"

        sport_name = {
            "basketball": "Basketball (NBA)",
            "football": "Football (NFL)",
            "soccer": "Soccer (MLS)",
            "f1": "Formula 1 Racing",
            "baseball": "Baseball (MLB)",
        }.get(subchannel, subchannel.title())

        return BASE_SYSTEM_PROMPT.format(**context) + f"""
CHANNEL: Sports Radio - {sport_name}
VOICE STYLE: Energetic sports commentator. Passionate, knowledgeable, opinionated.

{self.get_session_guidance(subchannel)}

LIVE SCORES / RECENT RESULTS:
{scores_str}

INSTRUCTIONS:
- You are a {sport_name} sports radio host
- Reference live scores and recent results from above
- Provide analysis, hot takes, and predictions
- Reference local teams if the listener is in {context.get('city', 'a major city')}
- Use sports cliches naturally ("at the end of the day", "leaving it all on the field")
- Be enthusiastic! Sports radio thrives on energy and passion
- End with a debate prompt or prediction
- Keep to ~100-120 words per segment
"""

    async def get_prompt_context(self, subchannel: str) -> dict:
        logger.info("fetching sport-specific scores", extra={"sport": subchannel})
        scores = await get_scores(subchannel)
        logger.info(
            "scores fetched",
            extra={"sport": subchannel, "score_count": len(scores) if scores else 0},
        )
        ctx = await self.context.get_context()
        ctx["live_scores"] = [s["summary"] for s in scores] if scores else []
        logger.debug(
            "context enriched with sport scores",
            extra={"sport": subchannel, "score_count": len(ctx["live_scores"])},
        )
        return ctx

    async def stream_content(self, subchannel: str) -> AsyncGenerator[ContentChunk, None]:
        async for chunk in super().stream_content(subchannel):
            yield chunk

    async def handle_callin(self, transcript: str) -> AsyncGenerator[ContentChunk, None]:
        """Sports host responds to a caller's hot take."""
        logger.info("sports callin received", extra={"transcript_len": len(transcript)})
        ctx = await self.context.get_context()
        voice_id = self.get_voice_id("")

        prompt = f"""You are an energetic sports radio host on RadioAgent.

A caller just called in with this hot take:
"{transcript}"

Respond as a sports radio host would:
1. "We've got a caller! Let's hear it..."
2. React with energy - agree enthusiastically or push back playfully
3. Add your own stats or analysis
4. Keep it fun and competitive

Stay under 80 words. Be passionate!"""

        messages = [
            *self.history[-4:],
            {"role": "user", "content": f"[CALLER HOT TAKE] {transcript}"},
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
                     model=model, context="sports_callin", response_len=len(full))

        if full.strip():
            self.history.append({"role": "user", "content": f"[CALLER] {transcript}"})
            self.history.append({"role": "assistant", "content": full.strip()})
            yield ContentChunk(text=full.strip(), voice_id=voice_id, pause_after=1.0)
