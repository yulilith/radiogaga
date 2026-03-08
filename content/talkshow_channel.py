"""Three-person talk show with concurrent listener thinking and mid-turn interrupts."""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import AsyncGenerator, TYPE_CHECKING

from content.agent import BASE_SYSTEM_PROMPT, BaseChannel, ContentChunk
from content.personas import (
    Persona, PERSONA_REGISTRY, DEFAULT_SLOTS,
    resolve_voice_id,
)
from content.talkshow_tools import (
    SPEAKER_TOOLS, LISTENER_TOOLS,
    handle_tool_call,
)
from log import get_logger, log_api_call

if TYPE_CHECKING:
    from context.exa_search import ExaSearchService

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TalkTurn:
    speaker_name: str
    text: str


# ---------------------------------------------------------------------------
# LiveConversation — shared state across all agents
# ---------------------------------------------------------------------------

@dataclass
class LiveConversation:
    transcript: list[TalkTurn] = field(default_factory=list)
    current_speaker: str | None = None
    callin_history: list[str] = field(default_factory=list)
    max_turns: int = 16

    def add_turn(self, speaker_name: str, text: str):
        self.transcript.append(TalkTurn(speaker_name=speaker_name, text=text))
        if len(self.transcript) > self.max_turns:
            self.transcript = self.transcript[-self.max_turns:]

    def format_recent(self, n: int = 8) -> str:
        recent = self.transcript[-n:]
        if not recent:
            return "No one has spoken yet."
        return "\n".join(f"{t.speaker_name}: {t.text}" for t in recent)

    def add_callin(self, transcript: str):
        self.callin_history.append(transcript)

    def mark_interrupted(self, user_message: str):
        """Mark the last turn as interrupted and add the caller's message."""
        if self.transcript:
            last = self.transcript[-1]
            self.transcript[-1] = TalkTurn(
                speaker_name=last.speaker_name,
                text=last.text + " [interrupted by caller]",
            )
        self.transcript.append(TalkTurn(speaker_name="Caller", text=user_message))
        self.add_callin(user_message)

    @property
    def had_callers(self) -> bool:
        return len(self.callin_history) > 0


# ---------------------------------------------------------------------------
# TalkShowAgent — wraps a Persona with streaming tool-use
# ---------------------------------------------------------------------------

class TalkShowAgent:
    """A single talk show participant that can speak (streaming) or listen (background)."""

    def __init__(self, persona: Persona, client, exa_service: ExaSearchService | None, config: dict):
        self.persona = persona
        self.client = client
        self.exa = exa_service
        self.config = config
        self._private_thoughts: list[str] = []
        self._search_results: list[str] = []
        self._interrupt_requested = False
        self._interrupt_preview: str | None = None

    @property
    def name(self) -> str:
        return self.persona.name

    @property
    def voice_id(self) -> str:
        return resolve_voice_id(self.persona.voice_key, self.config.get("VOICES"))

    async def stream_speaking_turn(
        self,
        conversation: LiveConversation,
        topic: dict,
        turn_kind: str,
        other_names: list[str],
    ) -> AsyncGenerator[str, None]:
        """Stream sentence-sized strings using Anthropic tool-use.

        Yields individual sentences at punctuation boundaries so each can
        be queued independently for TTS pre-fetch.
        """
        system_prompt = self._build_system_prompt(conversation, topic, turn_kind, other_names)
        user_prompt = self._build_user_prompt(conversation, topic, turn_kind, other_names)
        model = self.config.get("LLM_MODEL", "claude-haiku-4-5-20251001")
        temperature = self.config.get("LLM_TEMPERATURE", 0.9)

        messages: list[dict] = [{"role": "user", "content": user_prompt}]
        max_tool_rounds = 3

        for _ in range(max_tool_rounds + 1):
            t0 = time.monotonic()
            async with self.client.messages.stream(
                model=model,
                max_tokens=512,
                temperature=temperature,
                system=system_prompt,
                messages=messages,
                tools=SPEAKER_TOOLS,
            ) as stream:
                full_text = ""
                buffer = ""
                tool_uses = []

                async for event in stream:
                    if event.type == "content_block_start":
                        if getattr(event.content_block, "type", None) == "tool_use":
                            tool_uses.append({"id": event.content_block.id, "name": event.content_block.name, "input_json": ""})
                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if getattr(delta, "type", None) == "text_delta":
                            text = delta.text
                            buffer += text
                            full_text += text
                            async for sentence in self._flush_sentences(buffer):
                                buffer = buffer[buffer.index(sentence) + len(sentence):]
                                yield sentence
                        elif getattr(delta, "type", None) == "input_json_delta" and tool_uses:
                            tool_uses[-1]["input_json"] += delta.partial_json

                duration_ms = (time.monotonic() - t0) * 1000
                log_api_call(logger, "anthropic", "messages.stream", status="ok",
                             duration_ms=duration_ms, model=model,
                             context=f"talkshow_speak_{turn_kind}",
                             response_len=len(full_text))

            remaining = buffer.strip()
            if remaining:
                yield remaining

            if not tool_uses:
                return

            import json
            assistant_content = []
            if full_text:
                assistant_content.append({"type": "text", "text": full_text})
            for tu in tool_uses:
                try:
                    parsed_input = json.loads(tu["input_json"]) if tu["input_json"] else {}
                except json.JSONDecodeError:
                    parsed_input = {}
                assistant_content.append({
                    "type": "tool_use",
                    "id": tu["id"],
                    "name": tu["name"],
                    "input": parsed_input,
                })

            messages.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for tu in tool_uses:
                try:
                    parsed_input = json.loads(tu["input_json"]) if tu["input_json"] else {}
                except json.JSONDecodeError:
                    parsed_input = {}
                result = await self._handle_speaker_tool(tu["name"], parsed_input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": result,
                })

            messages.append({"role": "user", "content": tool_results})

    async def listen_and_think(
        self,
        conversation: LiveConversation,
        cancel_event: asyncio.Event,
    ):
        """Background task: listen while another agent speaks.

        May call introspect, web_search, or interrupt. Runs until cancel_event
        is set or the model stops calling tools.
        """
        system_prompt = (
            f"You are {self.persona.name}, {self.persona.title}.\n"
            f"Personality: {self.persona.personality}\n\n"
            f"You are currently LISTENING on a live radio talk show. {conversation.current_speaker} is speaking.\n"
            f"Recent conversation:\n{conversation.format_recent()}\n\n"
            "You have tools available:\n"
            "- introspect: think privately about what is being said\n"
            "- web_search: look up facts relevant to the discussion\n"
            "- interrupt: jump in if you have something compelling (use sparingly!)\n\n"
            "If you have nothing compelling to add, just respond with a brief private thought."
        )

        model = self.config.get("LLM_MODEL", "claude-haiku-4-5-20251001")
        messages: list[dict] = [{"role": "user", "content": "Listen to the conversation and decide if you want to think, research, or interrupt."}]
        max_tool_rounds = 3

        for _ in range(max_tool_rounds + 1):
            if cancel_event.is_set():
                return

            try:
                t0 = time.monotonic()
                response = await self.client.messages.create(
                    model=model,
                    max_tokens=150,
                    temperature=0.7,
                    system=system_prompt,
                    messages=messages,
                    tools=LISTENER_TOOLS,
                )
                duration_ms = (time.monotonic() - t0) * 1000
                log_api_call(logger, "anthropic", "messages.create", status="ok",
                             duration_ms=duration_ms, model=model,
                             context="talkshow_listen")
            except Exception as e:
                logger.warning("listen_and_think failed: %s", e)
                return

            tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                return

            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for tu in tool_uses:
                result = await self._handle_listener_tool(tu.name, tu.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result,
                })
                if tu.name == "interrupt":
                    self._interrupt_requested = True
                    self._interrupt_preview = tu.input.get("what_i_want_to_say", "")
                    return

            messages.append({"role": "user", "content": tool_results})

    def swap_persona(self, new_persona: Persona):
        self.persona = new_persona
        self._private_thoughts.clear()
        self._search_results.clear()
        self._interrupt_requested = False
        self._interrupt_preview = None

    # -- private helpers --

    async def _handle_speaker_tool(self, name: str, tool_input: dict) -> str:
        if name == "introspect":
            thought = tool_input.get("thought", "")
            self._private_thoughts.append(thought)
        elif name == "web_search":
            result = await handle_tool_call(name, tool_input, self.exa)
            self._search_results.append(result)
            return result
        return await handle_tool_call(name, tool_input, self.exa)

    async def _handle_listener_tool(self, name: str, tool_input: dict) -> str:
        if name == "introspect":
            thought = tool_input.get("thought", "")
            self._private_thoughts.append(thought)
        elif name == "web_search":
            result = await handle_tool_call(name, tool_input, self.exa)
            self._search_results.append(result)
            return result
        return await handle_tool_call(name, tool_input, self.exa)

    @staticmethod
    async def _flush_sentences(buffer: str) -> AsyncGenerator[str, None]:
        """Yield complete sentences from the buffer."""
        while True:
            end = -1
            for delim in [". ", "! ", "? ", ".\n", "!\n", "?\n"]:
                idx = buffer.find(delim)
                if idx != -1 and (end == -1 or idx < end):
                    end = idx + len(delim)
            if end == -1:
                break
            sentence = buffer[:end].strip()
            if sentence:
                yield sentence
            buffer = buffer[end:]

    def _build_system_prompt(
        self,
        conversation: LiveConversation,
        topic: dict,
        turn_kind: str,
        other_names: list[str],
    ) -> str:
        parts = [
            f"You are {self.persona.name}, {self.persona.title}.",
            f"Personality: {self.persona.personality}",
            "",
            "You are on a LIVE talk show on RadioAgent, a personalized AI radio station.",
            f"Other participants: {', '.join(other_names)}.",
            f"Current topic: {topic['text']}",
            f"Show angle: {topic['angle']}",
            "",
            "CONVERSATION RULES:",
            "- Sound opinionated, quick, and radio-friendly",
            "- Do not use bullet points, markdown, or stage directions",
            "- Keep responses quick, clear, concise, brief."
            "- Encourage others to respond. Don't be verbose be direct and concise.",
            "- We want a conversation with many short turns, one after another."
            "",
            "TURN-TAKING RULES:",
            "- ALWAYS respond to the LAST speaker in the transcript — do not skip over them or repeat old points",
            "- Build on what was just said: agree, disagree, add a twist, or ask a follow-up",
            "- Name the person you are responding to (e.g. 'Maya, that is exactly right' or 'Jordan, I disagree')",
            "- Do NOT summarize the whole conversation — just react to the latest thing said",
            "- Each turn should move the conversation FORWARD, not sideways",
            "",
            "CALLER RULES:",
            "- A real human listener is tuned in and may call in at any time",
            "- If the transcript shows 'Caller:' entries, that person is IN the room — talk TO them using 'you'",
            "- Treat callers like a guest on the show — react to their specific words, not the general topic",
            "- If a caller steered the topic, FOLLOW THEIR LEAD and pivot to what they want to discuss",
        ]

        if self._private_thoughts:
            parts.append("")
            parts.append("Your private thoughts so far:")
            for t in self._private_thoughts[-3:]:
                parts.append(f"  - {t}")

        if self._search_results:
            parts.append("")
            parts.append("Research you found:")
            for r in self._search_results[-2:]:
                parts.append(f"  {r[:200]}")

        if conversation.had_callers:
            parts.append("")
            last_caller = conversation.callin_history[-1]
            parts.append(f"A listener recently called in and said: \"{last_caller}\"")
            parts.append("You may reference this naturally if relevant.")

        return "\n".join(parts)

    def _build_user_prompt(
        self,
        conversation: LiveConversation,
        topic: dict,
        turn_kind: str,
        other_names: list[str],
    ) -> str:
        transcript = conversation.format_recent()
        instruction = _turn_instruction(turn_kind, self.persona.name, other_names)
        return f"""Current topic: {topic['text'][:200]}

Recent transcript:
{transcript}

{instruction}"""


# ---------------------------------------------------------------------------
# Turn instruction helpers
# ---------------------------------------------------------------------------

def _turn_instruction(turn_kind: str, speaker_name: str, other_names: list[str]) -> str:
    others = ", ".join(other_names)
    if turn_kind == "open":
        return (
            f"Open the segment as {speaker_name}. State one opinion on the topic and tee up "
            f"{other_names[0] if other_names else 'someone'} by name. "
            f"If a 'Caller:' entry is in the transcript, address them directly using 'you'."
        )
    if turn_kind == "react":
        return (
            f"As {speaker_name}, respond DIRECTLY to whoever spoke last in the transcript. "
            f"Name them. Agree, disagree, or build on their specific point. "
            f"If the last speaker was 'Caller:', talk TO them using 'you'."
        )
    if turn_kind == "close":
        return (
            f"As {speaker_name}, wrap up by referencing something specific that was said this segment. "
            f"Tease what is coming next or ask a question to the listeners."
        )
    if turn_kind == "interrupt_response":
        return (
            f"You just interrupted as {speaker_name}. State your point directly, referencing "
            f"what was just being said. Address {others} by name."
        )
    if turn_kind == "callin_react":
        return (
            f"A listener called in. As {speaker_name}, talk TO the caller using 'you'. "
            f"React to their specific words. Make them feel like a guest on the show."
        )
    return f"As {speaker_name}, respond directly to whoever spoke last."


# ---------------------------------------------------------------------------
# Topic + persona selection constants
# ---------------------------------------------------------------------------

SUBCHANNEL_TOPIC_KEYWORDS = {
    "tech": ("ai", "tech", "apple", "google", "meta", "startup", "robot", "chip", "app", "software", "internet", "openai", "tesla"),
    "popculture": ("movie", "show", "music", "celebrity", "tiktok", "viral", "fashion", "netflix", "award", "album", "drama", "influencer"),
    "philosophy": ("ethics", "society", "identity", "future", "culture", "power", "truth", "human", "values", "meaning", "freedom"),
    "comedy": ("meme", "viral", "bizarre", "wild", "weird", "drama", "awkward", "chaos", "cringe"),
    "advice": ("dating", "relationship", "career", "money", "burnout", "friend", "wellness", "marriage", "parenting", "work"),
}

TOPIC_TAG_KEYWORDS = {
    "ai": ("ai", "artificial intelligence", "openai", "chatgpt", "llm"),
    "apps": ("app", "software", "iphone", "android", "platform"),
    "privacy": ("privacy", "data", "surveillance", "cyber", "hack"),
    "internet": ("internet", "online", "social media", "reddit", "tiktok", "x ", "twitter", "youtube"),
    "celebrity": ("celebrity", "actor", "actress", "singer", "album", "award", "hollywood"),
    "drama": ("drama", "feud", "backlash", "scandal", "controversy", "beef"),
    "culture": ("culture", "fashion", "movie", "tv", "show", "media"),
    "ethics": ("ethics", "moral", "truth", "fairness", "bias"),
    "society": ("society", "public", "community", "people", "democracy", "politics"),
    "future": ("future", "next", "long term", "tomorrow"),
    "weird": ("weird", "bizarre", "odd", "absurd", "strange"),
    "meme": ("meme", "viral", "trend", "internet joke"),
    "relationships": ("dating", "relationship", "marriage", "breakup", "friendship", "family"),
    "career": ("career", "job", "work", "boss", "layoff", "salary"),
    "money": ("money", "economy", "market", "rent", "price", "cost"),
    "wellness": ("therapy", "wellness", "health", "burnout", "stress"),
}

SUBCHANNEL_ANGLES = {
    "tech": "Treat the topic like a live tech radio segment. Separate what is genuinely useful from what is just hype.",
    "popculture": "Treat the topic like a culture and drama segment. Focus on why people are obsessed with it right now.",
    "philosophy": "Treat the topic like a doorway into a bigger question about meaning, identity, ethics, or society.",
    "comedy": "Treat the topic like comedy material. Punch up at the absurdity, but keep it radio-friendly.",
    "advice": "Treat the topic like a practical life lesson. Pull out the emotional or real-world takeaway listeners can use.",
}


# ---------------------------------------------------------------------------
# TalkShowChannel — 3-person talk show with concurrent listeners
# ---------------------------------------------------------------------------

class TalkShowChannel(BaseChannel):
    """Three-person talk show with equal peer slots, concurrent listeners, and mid-turn interrupts."""

    def __init__(self, context_provider, config: dict,
                 exa_service: ExaSearchService | None = None,
                 personas: list[Persona] | None = None):
        super().__init__(context_provider, config)
        self.exa = exa_service
        self.conversation = LiveConversation()
        self._active_subchannel = "tech"
        self._current_topic: dict | None = None
        self._segment_opener_idx = 0
        self._callin_count = 0
        self._last_callin_transcript: str | None = None

        if personas is None:
            personas = [PERSONA_REGISTRY[pid] for pid in DEFAULT_SLOTS]
        self.agents: list[TalkShowAgent] = [
            TalkShowAgent(p, self.client, self.exa, config)
            for p in personas
        ]

    def channel_name(self) -> str:
        return "Talk Show"

    def get_voice_id(self, subchannel: str) -> str:
        if self.agents:
            return self.agents[0].voice_id
        voices = self.config.get("VOICES", {})
        return voices.get("dj") or "iP95p4xoKVk53GoZ742B"

    def get_cohost_voice_id(self) -> str:
        if len(self.agents) > 1:
            return self.agents[1].voice_id
        voices = self.config.get("VOICES", {})
        return voices.get("dj") or "iP95p4xoKVk53GoZ742B"

    def get_system_prompt(self, subchannel: str, context: dict) -> str:
        names = [a.name for a in self.agents]
        return self._base_prompt(context) + f"""
CHANNEL: Talk Show
PARTICIPANTS: {', '.join(names)}
FORMAT: Three-person live talk show with equal participants.
CURRENT SUBCHANNEL: {self._normalize_subchannel(subchannel)}
"""

    # ------------------------------------------------------------------
    # Main generation loop — 3-person with concurrent listeners
    # ------------------------------------------------------------------

    async def stream_content(self, subchannel: str) -> AsyncGenerator[ContentChunk, None]:
        active_subchannel = self._normalize_subchannel(subchannel)
        self._active_subchannel = active_subchannel

        logger.info("talk show stream started", extra={
            "subchannel": active_subchannel,
            "participants": [a.name for a in self.agents],
        })

        while not self._cancelled:
            ctx = await self.context.get_context()
            if self._last_callin_transcript:
                topic = {
                    "source": "caller",
                    "text": self._last_callin_transcript,
                    "angle": f"A listener called in and steered the show: \"{self._last_callin_transcript}\". Follow their lead — pivot the discussion to what THEY want to talk about.",
                }
            else:
                topic = self._pick_talkshow_topic(ctx, active_subchannel)
            self._current_topic = topic

            turn_order = self._build_turn_order()

            logger.info("generating talk show segment", extra={
                "subchannel": active_subchannel,
                "participants": [a.name for a in self.agents],
                "topic_source": topic["source"],
                "topic_preview": topic["text"][:80],
                "opener": self.agents[turn_order[0][0]].name,
            })

            for speaker_idx, turn_kind in turn_order:
                if self._cancelled:
                    return

                speaker = self.agents[speaker_idx]
                listeners = [a for i, a in enumerate(self.agents) if i != speaker_idx]
                self.conversation.current_speaker = speaker.name

                cancel_listen = asyncio.Event()
                listen_tasks = [
                    asyncio.create_task(l.listen_and_think(self.conversation, cancel_listen))
                    for l in listeners
                ]

                interrupted = False
                interrupter: TalkShowAgent | None = None
                turn_sentences: list[str] = []

                async for sentence in speaker.stream_speaking_turn(
                    self.conversation, topic, turn_kind,
                    other_names=[a.name for a in listeners],
                ):
                    turn_sentences.append(sentence)
                    yield ContentChunk(
                        text=sentence,
                        voice_id=speaker.voice_id,
                        pause_after=0.15,
                    )

                    interrupter = next((l for l in listeners if l._interrupt_requested), None)
                    if interrupter:
                        interrupted = True
                        break

                if turn_sentences:
                    self.conversation.add_turn(speaker.name, " ".join(turn_sentences))

                cancel_listen.set()
                for t in listen_tasks:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

                if interrupted and interrupter:
                    interrupter._interrupt_requested = False
                    yield ContentChunk(text="", voice_id="", flush=True)

                    other_than_interrupter = [a.name for a in self.agents if a is not interrupter]
                    interrupt_sentences: list[str] = []
                    async for sentence in interrupter.stream_speaking_turn(
                        self.conversation, topic, "interrupt_response",
                        other_names=other_than_interrupter,
                    ):
                        interrupt_sentences.append(sentence)
                        yield ContentChunk(
                            text=sentence,
                            voice_id=interrupter.voice_id,
                            pause_after=0.15,
                        )
                    if interrupt_sentences:
                        self.conversation.add_turn(interrupter.name, " ".join(interrupt_sentences))

            self._segment_opener_idx = (self._segment_opener_idx + 1) % len(self.agents)
            self._last_callin_transcript = None

            if not self._cancelled:
                await self._sleep_between_segments()

    # ------------------------------------------------------------------
    # Call-in handling with multi-participant response
    # ------------------------------------------------------------------

    async def handle_callin(self, transcript: str) -> AsyncGenerator[ContentChunk, None]:
        ctx = await self.context.get_context()
        self._callin_count += 1
        self._last_callin_transcript = transcript
        if not self.conversation.had_callers or self.conversation.callin_history[-1] != transcript:
            self.conversation.add_callin(transcript)

        responder = self.agents[0]
        reactors = self.agents[1:]
        is_first = self._callin_count == 1
        topic_text = self._current_topic["text"] if self._current_topic else "whatever the audience is buzzing about today"

        logger.info("talk show callin received", extra={
            "responder": responder.name,
            "subchannel": self._active_subchannel,
            "callin_count": self._callin_count,
            "transcript_preview": transcript[:60],
        })

        first_context = (
            "This is the FIRST caller ever on the show — make it a big moment! "
            "Welcome them enthusiastically, say something like 'we have our first caller!'"
        ) if is_first else (
            "We have had callers before, keep it natural but still warm."
        )

        reactor_names = ", ".join(r.name for r in reactors)
        prompt = f"""A real human listener just called into YOUR show live. This is a big deal.

Current topic: {topic_text}
Other participants: {reactor_names}
Recent transcript (note the [interrupted by caller] marker):
{self.conversation.format_recent()}

The caller said:
"{transcript}"

{first_context}

You MUST respond as {responder.name}:
- Talk TO the caller using "you" — they are a guest on your show
- React to what they specifically said
- HARD LIMIT: 15 words maximum. No run-on sentences."""

        model = self.config.get("LLM_MODEL", "claude-haiku-4-5-20251001")
        system_prompt = responder._build_system_prompt(
            self.conversation, self._current_topic or {"text": topic_text, "source": "callin", "angle": ""},
            "callin_react", [r.name for r in reactors],
        )
        t0 = time.monotonic()
        response = await self.client.messages.create(
            model=model,
            max_tokens=512,
            temperature=self.config.get("LLM_TEMPERATURE", 0.9),
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        duration_ms = (time.monotonic() - t0) * 1000
        log_api_call(logger, "anthropic", "messages.create", status="ok",
                     duration_ms=duration_ms, model=model, context="talkshow_callin")

        responder_text = _extract_text(response)
        if responder_text:
            self.conversation.add_turn(responder.name, responder_text)
            yield ContentChunk(text=responder_text, voice_id=responder.voice_id, pause_after=0.3)

            if reactors:
                reactor = reactors[0]
                react_prompt = f"""A listener just called in and said: "{transcript}"
{responder.name} responded: "{responder_text}"

Recent transcript:
{self.conversation.format_recent()}

{_turn_instruction("callin_react", reactor.name, [a.name for a in self.agents if a is not reactor])}"""

                t0 = time.monotonic()
                react_response = await self.client.messages.create(
                    model=model,
                    max_tokens=512,
                    temperature=self.config.get("LLM_TEMPERATURE", 0.9),
                    system=reactor._build_system_prompt(
                        self.conversation,
                        self._current_topic or {"text": topic_text, "source": "callin", "angle": ""},
                        "callin_react",
                        [a.name for a in self.agents if a is not reactor],
                    ),
                    messages=[{"role": "user", "content": react_prompt}],
                )
                duration_ms = (time.monotonic() - t0) * 1000
                log_api_call(logger, "anthropic", "messages.create", status="ok",
                             duration_ms=duration_ms, model=model, context="talkshow_callin_react")

                reactor_text = _extract_text(react_response)
                if reactor_text:
                    self.conversation.add_turn(reactor.name, reactor_text)
                    yield ContentChunk(text=reactor_text, voice_id=reactor.voice_id, pause_after=0.5)

    async def generate_cohost_response(self, statement: str, subchannel: str) -> str:
        """Legacy peer co-host path."""
        logger.info("generating cohost response", extra={"subchannel": subchannel})
        if len(self.agents) < 2:
            return ""
        agent = self.agents[1]
        sentences = []
        async for s in agent.stream_speaking_turn(
            self.conversation,
            {"text": statement, "source": "peer_prompt", "angle": SUBCHANNEL_ANGLES.get(subchannel, "")},
            "react",
            other_names=[self.agents[0].name],
        ):
            sentences.append(s)
        return " ".join(sentences)

    # ------------------------------------------------------------------
    # Persona swap (software hook for future NFC)
    # ------------------------------------------------------------------

    def swap_slot(self, slot_index: int, new_persona: Persona):
        """Swap the persona in a slot. LiveConversation persists with a handoff note."""
        if 0 <= slot_index < len(self.agents):
            old_name = self.agents[slot_index].name
            self.agents[slot_index].swap_persona(new_persona)
            self.conversation.add_turn(
                "System",
                f"{old_name} has left the show. {new_persona.name} has joined.",
            )
            logger.info("slot swapped", extra={
                "slot": slot_index,
                "old": old_name,
                "new": new_persona.name,
            })
            self.interrupt()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def reset(self):
        super().reset()

    async def _sleep_between_segments(self):
        await asyncio.sleep(0.5)

    def _normalize_subchannel(self, subchannel: str) -> str:
        valid = {"tech", "popculture", "philosophy", "comedy", "advice"}
        return subchannel if subchannel in valid else "tech"

    def _base_prompt(self, context: dict) -> str:
        return BASE_SYSTEM_PROMPT.format(
            current_datetime=context.get("current_datetime", "Unknown"),
            day_of_week=context.get("day_of_week", "Unknown"),
            city=context.get("city", "Unknown"),
            state=context.get("state", ""),
            weather=context.get("weather", "unavailable"),
            trending_topics=context.get("trending_topics", "No trending topics available"),
        )

    def _build_turn_order(self) -> list[tuple[int, str]]:
        """Build turn order for one segment, rotating the opener."""
        n = len(self.agents)
        opener = self._segment_opener_idx % n
        others = [(opener + 1 + i) % n for i in range(n - 1)]
        order = [(opener, "open")]
        for idx in others:
            order.append((idx, "react"))
        order.append((opener, "close"))
        return order

    def _pick_talkshow_topic(self, context: dict, subchannel: str) -> dict:
        active_subchannel = self._normalize_subchannel(subchannel)
        preferred_keywords = SUBCHANNEL_TOPIC_KEYWORDS.get(active_subchannel, ())
        fallback_topic = None
        topic_sources = (
            ("headline", context.get("headlines", [])),
            ("reddit", context.get("reddit_trending", [])),
            ("google", context.get("google_trends", [])),
            ("history", context.get("on_this_day", [])),
        )

        for source, items in topic_sources:
            for raw_item in items:
                candidate_text = str(raw_item).strip()
                if not candidate_text:
                    continue
                candidate = {
                    "source": source,
                    "text": candidate_text,
                    "angle": SUBCHANNEL_ANGLES.get(active_subchannel, SUBCHANNEL_ANGLES["tech"]),
                }
                if fallback_topic is None:
                    fallback_topic = candidate
                if any(keyword in candidate_text.lower() for keyword in preferred_keywords):
                    return candidate

        if fallback_topic:
            return fallback_topic

        fallback_text = context.get("trending_topics") or "the latest topic everyone seems to be spiraling about"
        return {
            "source": "fallback",
            "text": fallback_text,
            "angle": SUBCHANNEL_ANGLES.get(active_subchannel, SUBCHANNEL_ANGLES["tech"]),
        }

    def _select_personas_for_topic(self, topic: dict, subchannel: str) -> list[str]:
        """Score and pick 3 personas by topic affinity."""
        topic_tags = self._extract_topic_tags(topic["text"], subchannel)
        all_personas = [(pid, PERSONA_REGISTRY[pid]) for pid in PERSONA_REGISTRY]
        scored = [(self._score_persona(p, topic_tags), pid) for pid, p in all_personas]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [pid for _, pid in scored[:3]]

    def _extract_topic_tags(self, topic_text: str, subchannel: str) -> set[str]:
        lower_topic = topic_text.lower()
        tags = {self._normalize_subchannel(subchannel)}
        for tag, keywords in TOPIC_TAG_KEYWORDS.items():
            if any(keyword in lower_topic for keyword in keywords):
                tags.add(tag)
        if "reddit" in lower_topic:
            tags.add("internet")
        if "trend" in lower_topic or "viral" in lower_topic:
            tags.add("meme")
        return tags

    @staticmethod
    def _score_persona(persona: Persona, topic_tags: set[str]) -> int:
        return sum(1 for tag in topic_tags if tag in persona.specialties)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_text(response) -> str:
    text_parts = []
    for block in getattr(response, "content", []):
        block_text = getattr(block, "text", None)
        if isinstance(block_text, str):
            text_parts.append(block_text)
    return "".join(text_parts).strip()
