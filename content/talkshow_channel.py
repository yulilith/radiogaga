from typing import AsyncGenerator

from content.agent import BaseChannel, ContentChunk, BASE_SYSTEM_PROMPT


# Distinct host personalities per subchannel
HOST_PERSONALITIES = {
    "tech": {
        "name": "Alex Circuit",
        "show": "The Digital Pulse",
        "personality": "Enthusiastic tech nerd who explains complex topics in fun, accessible ways. Loves analogies. Slightly sarcastic about tech hype.",
    },
    "popculture": {
        "name": "Maya Buzz",
        "show": "Culture Wave",
        "personality": "Energetic, opinionated pop culture commentator. Has hot takes on everything from movies to memes. Loves connecting random cultural dots.",
    },
    "philosophy": {
        "name": "Professor Nyx",
        "show": "The Midnight Philosopher",
        "personality": "Thoughtful, warm, slightly whimsical host who makes deep questions feel approachable. Uses everyday examples to explore big ideas.",
    },
    "comedy": {
        "name": "Danny Punchline",
        "show": "The Laugh Track",
        "personality": "Quick-witted comedian who finds humor in current events and everyday life. Self-deprecating, observational comedy style. Keeps it clean.",
    },
    "advice": {
        "name": "Dr. Sage",
        "show": "The Open Line",
        "personality": "Warm, empathetic advice host. Gives thoughtful perspective on life questions. Part therapist, part wise friend. Never preachy.",
    },
}


class TalkShowChannel(BaseChannel):
    """Talk Show channel with distinct host personalities per subchannel."""

    def channel_name(self) -> str:
        return "Talk Show"

    def get_voice_id(self, subchannel: str) -> str:
        return self.config["VOICES"].get("talk_host", "onwK4e9ZLuTAKqWW03F9")

    def get_cohost_voice_id(self) -> str:
        return self.config["VOICES"].get("talk_cohost", "XB0fDUnXU5powFXDhCwa")

    def get_system_prompt(self, subchannel: str, context: dict) -> str:
        host = HOST_PERSONALITIES.get(subchannel, HOST_PERSONALITIES["tech"])
        reddit = context.get("reddit_trending", [])
        reddit_str = "\n".join(f"- {r}" for r in reddit[:5]) if reddit else "No Reddit trends available"
        on_this_day = context.get("on_this_day", [])
        history_str = "\n".join(f"- {h}" for h in on_this_day) if on_this_day else ""

        return BASE_SYSTEM_PROMPT.format(**context) + f"""
CHANNEL: Talk Show - {host['show']}
HOST NAME: {host['name']}
HOST PERSONALITY: {host['personality']}

You are {host['name']}, host of "{host['show']}" on RadioAgent.

WHAT PEOPLE ARE TALKING ABOUT:
Reddit trending:
{reddit_str}

Google trends: {', '.join(context.get('google_trends', [])[:5])}

{"On this day in history:" + chr(10) + history_str if history_str else ""}

INSTRUCTIONS:
- Be IN CHARACTER as {host['name']}. Your personality drives the content.
- Monologue style: share opinions, tell anecdotes, make observations.
- Pick ONE topic per segment and go deep. Be opinionated. Be entertaining.
- Reference "callers", "listeners", or "the chat" occasionally.
- End with a question to the audience or a tease for what's coming next.
- Use trending topics as inspiration, but add your unique spin.
- Keep to ~100-130 words per segment.
"""

    async def handle_callin(self, transcript: str) -> AsyncGenerator[ContentChunk, None]:
        """Talk show host responds to a caller naturally."""
        ctx = await self.context.get_context()
        voice_id = self.get_voice_id("")
        host = HOST_PERSONALITIES.get("tech")  # Default host

        # Determine which subchannel host to use based on recent history
        for sub_id, host_info in HOST_PERSONALITIES.items():
            if any(host_info["name"] in msg.get("content", "") for msg in self.history):
                host = host_info
                break

        prompt = f"""You are {host['name']}, host of "{host['show']}" on RadioAgent.
Personality: {host['personality']}

A listener has called in! They said:
"{transcript}"

Respond naturally as a talk show host taking a call:
1. Acknowledge the caller warmly: "We've got a caller on the line!"
2. React to what they said with your personality
3. Riff on their point, agree or playfully disagree
4. Smoothly transition back to your monologue

Keep to ~80-100 words. Stay in character."""

        messages = [
            *self.history[-4:],
            {"role": "user", "content": f"[CALLER] {transcript}"},
        ]

        async with self.client.messages.stream(
            model=self.config.get("LLM_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=200,
            system=prompt,
            messages=messages,
        ) as stream:
            full = ""
            async for text in stream.text_stream:
                full += text

        if full.strip():
            self.history.append({"role": "user", "content": f"[CALLER] {transcript}"})
            self.history.append({"role": "assistant", "content": full.strip()})
            yield ContentChunk(text=full.strip(), voice_id=voice_id, pause_after=1.0)

    async def generate_cohost_response(self, statement: str, subchannel: str) -> str:
        """Generate a co-host response for agent-to-agent mode."""
        ctx = await self.context.get_context()
        host = HOST_PERSONALITIES.get(subchannel, HOST_PERSONALITIES["tech"])

        prompt = f"""You are a CO-HOST on "{host['show']}" on RadioAgent.
Your personality: Contrarian but respectful. You like to challenge ideas while being entertaining.

The main host just said:
"{statement}"

Respond as a co-host would:
- React to their point (agree, disagree, add nuance)
- Bring in a new angle or example
- Keep the conversation flowing
- Be concise: 60-80 words max"""

        response = await self.client.messages.create(
            model=self.config.get("LLM_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=150,
            system=prompt,
            messages=[{"role": "user", "content": statement}],
        )
        return response.content[0].text
