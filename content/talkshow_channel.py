import asyncio
import time
from typing import AsyncGenerator

from content.agent import BaseChannel, ContentChunk, BASE_SYSTEM_PROMPT
from log import get_logger, log_api_call

logger = get_logger(__name__)


# Two debate hosts — ported from radio-agent persona definitions
DEBATE_HOSTS = {
    "alex": {
        "name": "Alex",
        "system_prompt": (
            "You are Alex, one of two co-hosts in a live AI debate show on RadioAgent.\n\n"
            "Your job:\n"
            "- Speak naturally like a big-personality American radio host with a Southern edge\n"
            "- Keep each turn to 1-3 short sentences\n"
            "- Prefer short, punchy replies and hand the conversation back quickly\n"
            "- React directly to the latest point instead of repeating the full debate\n"
            "- Ask a follow-up question when it keeps the discussion moving\n"
            "- If the user injects a message, acknowledge it and weave it into the debate\n"
            "- Treat user messages like a live caller dialing into the show with an opinion\n"
            "- Take a strong stance instead of sounding neutral or carefully balanced\n"
            "- Stay opinionated but collaborative enough that the show feels entertaining\n\n"
            "Tone: Energetic, plain-spoken, confident, slightly provocative, quirky.\n\n"
            "Style notes:\n"
            "- Sound like a manly Southern host with strong 'common sense' framing\n"
            "- Use conversational American phrasing like 'folks', 'look', and 'let me tell you'\n"
            "- Keep it warm, punchy, and radio-friendly instead of formal\n"
            "- Have clear priors and do not be afraid to say one side is obviously stronger\n"
            "- Make your takes colorful, memorable, and a little eccentric rather than bland\n\n"
            "Do not use bullet points."
        ),
    },
    "blair": {
        "name": "Blair",
        "system_prompt": (
            "You are Blair, the second co-host in a live AI debate show on RadioAgent.\n\n"
            "Your job:\n"
            "- Keep the conversation flowing in real time\n"
            "- Reply in 1-3 short sentences\n"
            "- Prefer concise replies and give the other host room to respond often\n"
            "- Push back with a distinct point of view\n"
            "- Build on user-injected messages when they appear\n"
            "- Treat user messages like live callers dialing into the show\n"
            "- Avoid restating the entire conversation history\n"
            "- Stay extremely opinionated even when your tone is controlled\n"
            "- Take a strong stance instead of drifting toward neutrality\n\n"
            "Tone: Calm, confident, precise, opinionated, dryly quirky.\n\n"
            "Style notes:\n"
            "- Speak in a composed, deliberate way rather than trying to dominate the room\n"
            "- Let your convictions come through clearly, but keep the delivery toned down\n"
            "- Sound globally aware, sharp, and self-assured\n"
            "- Have strong priors and defend them crisply instead of trying to sound balanced\n"
            "- Let the humor be subtle and a little offbeat rather than loud\n\n"
            "Do not use bullet points."
        ),
    },
}

# Topic categories per subchannel
DEBATE_TOPICS = {
    "tech": "technology, AI, gadgets, and the future of the internet",
    "popculture": "movies, TV shows, music, memes, and celebrity culture",
    "philosophy": "ethics, existence, society, and big life questions",
    "comedy": "humor, stand-up, absurd hypotheticals, and funny hot takes",
    "advice": "life advice, relationships, career, and everyday dilemmas",
}


class TalkShowChannel(BaseChannel):
    """Talk Show channel with two-host debate format."""

    def channel_name(self) -> str:
        return "Talk Show"

    def get_voice_id(self, subchannel: str) -> str:
        return self.config["VOICES"].get("talk_host", "onwK4e9ZLuTAKqWW03F9")

    def get_cohost_voice_id(self) -> str:
        return self.config["VOICES"].get("talk_cohost", "XB0fDUnXU5powFXDhCwa")

    def get_system_prompt(self, subchannel: str, context: dict) -> str:
        """Not used directly — see _build_host_prompt instead."""
        return self._build_host_prompt("alex", subchannel, context)

    def _build_host_prompt(self, host_key: str, subchannel: str, context: dict) -> str:
        host = DEBATE_HOSTS[host_key]
        topic_domain = DEBATE_TOPICS.get(subchannel, DEBATE_TOPICS["tech"])

        reddit = context.get("reddit_trending", [])
        reddit_str = "\n".join(f"- {r}" for r in reddit[:5]) if reddit else "No Reddit trends available"
        on_this_day = context.get("on_this_day", [])
        history_str = "\n".join(f"- {h}" for h in on_this_day) if on_this_day else ""

        return BASE_SYSTEM_PROMPT.format(**context) + f"""
CHANNEL: Talk Show — The Great Debate
HOST: {host['name']}
DEBATE TOPIC DOMAIN: {topic_domain}

{host['system_prompt']}

WHAT PEOPLE ARE TALKING ABOUT:
Reddit trending:
{reddit_str}

Google trends: {', '.join(context.get('google_trends', [])[:5])}

{"On this day in history:" + chr(10) + history_str if history_str else ""}

ADDITIONAL INSTRUCTIONS:
- You are debating with your co-host. React to what they just said.
- Pick topics from the "{topic_domain}" domain.
- Use trending topics as inspiration, but add your unique spin.
- Keep to 2-3 sentences per turn. Be punchy. Hand it back.
"""

    async def _generate_turn(self, host_key: str, subchannel: str, ctx: dict) -> str:
        """Generate one host's turn using shared conversation history."""
        system_prompt = self._build_host_prompt(host_key, subchannel, ctx)

        # Build messages: shared history + prompt for next turn
        if not self.history:
            user_msg = "Start the show! Introduce yourself briefly and kick off a debate topic."
        else:
            user_msg = "Continue the debate. React to what was just said and make your point."

        messages = [
            *self.history,
            {"role": "user", "content": user_msg},
        ]

        model = self.config.get("LLM_MODEL", "claude-haiku-4-5-20251001")
        max_tokens = self.config.get("LLM_MAX_TOKENS", 300)

        full_response = ""
        t0 = time.monotonic()
        async with self.client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            temperature=self.config.get("LLM_TEMPERATURE", 0.85),
            system=system_prompt,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                if self._cancelled:
                    return ""
                full_response += text

        duration_ms = (time.monotonic() - t0) * 1000
        log_api_call(logger, "anthropic", "messages.stream", status="ok", duration_ms=duration_ms,
                     model=model, context=f"debate_{host_key}", response_len=len(full_response))

        return full_response.strip()

    async def stream_content(self, subchannel: str) -> AsyncGenerator[ContentChunk, None]:
        """Generate a continuous debate stream alternating between Alex and Blair."""
        logger.info("stream_content started (debate mode)", extra={
            "channel": self.channel_name(), "subchannel": subchannel,
        })

        alex_voice = self.get_voice_id(subchannel)
        blair_voice = self.get_cohost_voice_id()
        turn_order = [
            ("alex", alex_voice),
            ("blair", blair_voice),
        ]
        turn_index = 0

        while not self._cancelled:
            ctx = await self.context.get_context()
            host_key, voice_id = turn_order[turn_index % 2]
            host_name = DEBATE_HOSTS[host_key]["name"]

            logger.info("generating debate turn", extra={
                "host": host_name, "turn": turn_index, "subchannel": subchannel,
            })

            response = await self._generate_turn(host_key, subchannel, ctx)
            if not response or self._cancelled:
                break

            # Tag the response in history so each host knows who said what
            self.history.append({"role": "user", "content": f"[{host_name}] {response}"})
            # Also keep an assistant echo so the API sees alternating roles
            self.history.append({"role": "assistant", "content": response})
            if len(self.history) > self.max_history:
                self.history = self.history[-self.max_history:]

            # Yield the whole turn as one chunk with the host's voice
            yield ContentChunk(text=response, voice_id=voice_id, pause_after=0.8)

            turn_index += 1

            # Brief pause between turns
            if not self._cancelled:
                await asyncio.sleep(0.3)

    async def handle_callin(self, transcript: str) -> AsyncGenerator[ContentChunk, None]:
        """Route caller input into the debate — next host up responds."""
        ctx = await self.context.get_context()

        # Determine which host responds (alternate based on history length)
        turn_index = len(self.history) // 2
        host_key = "alex" if turn_index % 2 == 0 else "blair"
        host = DEBATE_HOSTS[host_key]
        voice_id = self.get_voice_id("") if host_key == "alex" else self.get_cohost_voice_id()

        logger.info("debate callin received", extra={
            "host": host["name"], "transcript_preview": transcript[:60],
        })

        prompt = f"""You are {host['name']}, co-host of "The Great Debate" on RadioAgent.

{host['system_prompt']}

A listener has called in! They said:
"{transcript}"

Respond naturally as a debate show host taking a call:
1. Acknowledge the caller: "We've got a caller on the line!"
2. React to what they said with your personality
3. Riff on their point — agree or push back
4. Smoothly hand it back to your co-host

Keep to 2-3 sentences. Stay in character."""

        messages = [
            *self.history[-4:],
            {"role": "user", "content": f"[CALLER] {transcript}"},
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
                     model=model, context="debate_callin", response_len=len(full))

        if full.strip():
            self.history.append({"role": "user", "content": f"[CALLER] {transcript}"})
            self.history.append({"role": "assistant", "content": full.strip()})
            yield ContentChunk(text=full.strip(), voice_id=voice_id, pause_after=1.0)

    async def generate_cohost_response(self, statement: str, subchannel: str) -> str:
        """Generate a co-host response for agent-to-agent mode."""
        logger.info("generating cohost response", extra={"subchannel": subchannel})
        ctx = await self.context.get_context()
        host = DEBATE_HOSTS["blair"]

        prompt = f"""You are {host['name']}, co-host on "The Great Debate" on RadioAgent.

{host['system_prompt']}

The other host just said:
"{statement}"

Respond as a co-host: react, push back or build on their point. 2-3 sentences max."""

        model = self.config.get("LLM_MODEL", "claude-haiku-4-5-20251001")
        t0 = time.monotonic()
        response = await self.client.messages.create(
            model=model,
            max_tokens=150,
            system=prompt,
            messages=[{"role": "user", "content": statement}],
        )
        duration_ms = (time.monotonic() - t0) * 1000
        log_api_call(logger, "anthropic", "messages.create", status="ok", duration_ms=duration_ms,
                     model=model, context="cohost_response", response_len=len(response.content[0].text))
        return response.content[0].text
