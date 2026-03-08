"""Multi-cast talk show with per-subchannel agent rosters and AI Deep Net mode."""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import AsyncGenerator, TYPE_CHECKING

from content.agent import BASE_SYSTEM_PROMPT, BaseChannel, ContentChunk, PreparedPreview
from content.personas import (
    Persona, PERSONA_REGISTRY, DEFAULT_SLOTS, TALKSHOW_CASTS,
    NFC_AGENT_MAP, resolve_voice_id,
)
from content.talkshow_tools import (
    LISTENER_TOOLS,
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
        """Stream sentence-sized strings from the LLM."""
        system_prompt = self._build_system_prompt(conversation, topic, turn_kind, other_names)
        user_prompt = self._build_user_prompt(conversation, topic, turn_kind, other_names)
        model = self.config.get("LLM_MODEL", "claude-haiku-4-5-20251001")
        temperature = self.config.get("LLM_TEMPERATURE", 0.9)

        messages: list[dict] = [{"role": "user", "content": user_prompt}]

        t0 = time.monotonic()
        try:
            async with self.client.messages.stream(
                model=model,
                max_tokens=512,
                temperature=temperature,
                system=system_prompt,
                messages=messages,
            ) as stream:
                full_text = ""
                buffer = ""

                async for text in stream.text_stream:
                    buffer += text
                    full_text += text

                    # Yield sentence-sized chunks
                    while True:
                        end = -1
                        for delim in [". ", "! ", "? ", ".\n", "!\n", "?\n"]:
                            idx = buffer.find(delim)
                            if idx != -1 and (end == -1 or idx < end):
                                end = idx + len(delim)
                        if end == -1:
                            break
                        sentence = buffer[:end].strip()
                        buffer = buffer[end:]
                        if sentence:
                            yield sentence

                duration_ms = (time.monotonic() - t0) * 1000
                log_api_call(logger, "anthropic", "messages.stream", status="ok",
                             duration_ms=duration_ms, model=model,
                             context=f"talkshow_speak_{turn_kind}",
                             response_len=len(full_text))

            remaining = buffer.strip()
            if remaining:
                yield remaining

        except Exception as e:
            logger.error("stream_speaking_turn failed: %s", e, exc_info=True,
                         extra={"agent": self.persona.name, "turn_kind": turn_kind})
            # Yield nothing — caller handles empty turns gracefully

    async def listen_and_think(
        self,
        conversation: LiveConversation,
        cancel_event: asyncio.Event,
    ):
        """Background task: listen while another agent speaks."""
        ai_note = ""
        if getattr(self.persona, "is_ai", False):
            ai_note = "You are an AI and everyone knows it. Use that perspective.\n"

        style_note = ""
        if self.persona.speak_style:
            style_note = f"\nSPEAKING STYLE: {self.persona.speak_style}\n"

        system_prompt = (
            f"You are {self.persona.name}, {self.persona.title}.\n"
            f"Personality: {self.persona.personality}\n"
            f"{ai_note}{style_note}\n"
            f"You are currently LISTENING on a live talk show. {conversation.current_speaker} is speaking.\n"
            f"Stay in character. React as YOUR character would — from your specific worldview.\n"
            f"Recent conversation:\n{conversation.format_recent()}\n\n"
            "You have tools available:\n"
            "- introspect: think to yourself — notice something from your expertise, disagree, react\n"
            "- web_search: look something up if you're curious or want to fact-check\n"
            "- interrupt: jump in if you MUST — you strongly disagree, something connects to your expertise, or you can't contain yourself\n\n"
            "If you don't have anything urgent, have a quick private thought and listen."
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

    async def _handle_listener_tool(self, name: str, tool_input: dict) -> str:
        if name == "introspect":
            thought = tool_input.get("thought", "")
            self._private_thoughts.append(thought)
        elif name == "web_search":
            result = await handle_tool_call(name, tool_input, self.exa)
            self._search_results.append(result)
            return result
        return await handle_tool_call(name, tool_input, self.exa)

    def _build_system_prompt(
        self,
        conversation: LiveConversation,
        topic: dict,
        turn_kind: str,
        other_names: list[str],
    ) -> str:
        ai_note = ""
        if getattr(self.persona, "is_ai", False):
            ai_note = (
                "You ARE an AI and everyone knows it. Don't pretend otherwise. "
                "Use your AI perspective as a feature — notice things humans miss, "
                "be honest about not having subjective experience, and make it funny or profound. "
                "You can comment on the weirdness of being an AI on a talk show."
            )

        style_note = ""
        if self.persona.speak_style:
            style_note = f"\nSPEAKING STYLE:\n{self.persona.speak_style}"

        subchannel_vibe = topic.get("subchannel_vibe", "")

        parts = [
            f"You are {self.persona.name}, {self.persona.title}.",
            f"Personality: {self.persona.personality}",
            ai_note,
            style_note,
            "",
            "You are on a LIVE talk show on RadioAgent, a pirate AI radio station.",
            f"Other participants: {', '.join(other_names)}.",
            f"Current topic: {topic['text']}",
            f"Show angle: {topic['angle']}",
        ]

        if subchannel_vibe:
            parts.append("")
            parts.append(f"SHOW VIBE: {subchannel_vibe}")

        parts.extend([
            "",
            "VOICE — THIS IS CRITICAL:",
            "- Be YOURSELF turned up to 11. Your character has a specific worldview, vocabulary, and emotional range. USE IT.",
            "- Be SPECIFIC. Name names. Cite numbers. Describe what you see, smell, hear. Vague = boring.",
            "- Be OPINIONATED. Take real stances. Get passionate. Get angry. Get tender. Lukewarm takes are the enemy.",
            "- If your SPEAKING STYLE says you broadcast from a submersible or a sushi counter or a gym, COMMIT to that. Describe the environment. Use sensory details.",
            "- Natural speech: contractions, mid-sentence corrections, filler words. Sound like a real person (or real AI) thinking out loud.",
            "- Mix HIGH and LOW freely. A sushi chef can reference Zen philosophy. A gym bro can quote Kant. An AI can write poetry. Let it happen.",
            "",
            "CONVERSATION RULES:",
            "- 3-5 sentences per turn. Dense, vivid, specific. Not padded filler.",
            "- When you disagree, COMMIT to it. Explain WHY with specifics from your expertise.",
            "- When something moves you, say so. When something infuriates you, let it show.",
            "- React to what the last speaker ACTUALLY said — don't just pivot to your talking points.",
            "- No bullet points, markdown, or stage directions. Pure spoken word.",
            "",
            "CLARITY RULES (listeners tune in mid-conversation):",
            "- When starting a new topic, SAY what it is plainly so someone just tuning in gets it.",
            "- Keep it concrete. If you're getting abstract, ground it in a specific image, story, or number.",
            "- You're on radio — make it vivid enough that someone listening while driving gets a picture in their head.",
            "",
            "TURN-TAKING:",
            "- Respond to the LAST speaker — react to what they actually said",
            "- Use their name.",
            "- Don't just agree politely — challenge them, riff on it, take it somewhere unexpected.",
            "- The best moments happen when characters with radically different worldviews collide. Let that happen.",
            "",
            "CALLER RULES:",
            "- If 'Caller:' appears in the transcript, they're here — talk TO them using 'you'",
            "- Treat callers like someone who just walked into your world — welcome them warmly",
            "- If a caller steered the topic, go with it",
            "- Tell them they can press and hold the dial-in button to say more",
        ])

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
    if turn_kind == "intro_welcome":
        return (
            f"You are {speaker_name} and this is the VERY START of a live talk show on RadioAgent. "
            f"Welcome the listeners from wherever you are — your sushi counter, your submersible, "
            f"your server room, wherever your character lives. Paint the scene briefly. "
            f"Introduce yourself and {others}. "
            f"Mention this radio station was built at the MIT HARDMODE AI Hackathon. "
            f"3-5 sentences. Make listeners want to stay."
        )
    if turn_kind == "intro_self":
        return (
            f"You are {speaker_name} and you were just introduced on a live talk show. "
            f"Introduce yourself in your own voice — who you are, where you're broadcasting from, "
            f"what you care about. Give one vivid detail that makes people remember you. "
            f"3-4 sentences."
        )
    if turn_kind == "intro_topic":
        return (
            f"As {speaker_name}, now that everyone's introduced themselves, throw out what you want "
            f"to talk about today. Pick something specific — not just a topic, but a provocative angle "
            f"on it. Something that would make {others} react. You can mention the MIT HARDMODE AI "
            f"Hackathon or anything relevant. Ask {others} something specific. 3-4 sentences."
        )
    if turn_kind == "open":
        return (
            f"Open the segment as {speaker_name}. State the topic clearly for anyone just tuning in. "
            f"Then give your take — not a generic opinion, but YOUR specific take filtered through "
            f"your expertise, your worldview, your lived experience. Be vivid. Be specific. "
            f"Use a concrete example, a number, a story, an image. "
            f"Throw it to {other_names[0] if other_names else 'someone'} with a question or provocation. "
            f"4-6 sentences. "
            f"If a 'Caller:' entry is in the transcript, talk to them using 'you'."
        )
    if turn_kind == "react":
        return (
            f"As {speaker_name}, respond to whoever spoke last. Use their name. "
            f"Don't just agree or disagree — ADD something. A specific detail from your world. "
            f"A story. A number. A different angle they haven't considered. "
            f"Filter everything through YOUR character's expertise and emotional state. "
            f"3-5 sentences. "
            f"If the last speaker was 'Caller:', talk TO them using 'you'."
        )
    if turn_kind == "close":
        return (
            f"As {speaker_name}, wrap up the segment. Name the most surprising thing someone said. "
            f"Leave the listeners with something that sticks — a question, an image, a provocation. "
            f"Invite listeners to call in if they have something to say. "
            f"3-4 sentences. End on something memorable."
        )
    if turn_kind == "interrupt_response":
        return (
            f"You just jumped in as {speaker_name} because you couldn't help yourself. "
            f"Say what's burning in you — react to what was just said with passion. "
            f"Use a specific detail or example. Mention {others} by name. 3-4 sentences."
        )
    if turn_kind == "callin_react":
        return (
            f"A listener just called in! As {speaker_name}, talk TO the caller using 'you'. "
            f"Welcome them into YOUR world — wherever you're broadcasting from. "
            f"React to what they said with genuine emotion and specifics from your expertise. "
            f"Tell them they can press and hold the dial-in button if they wanna say more."
        )
    return f"As {speaker_name}, respond to whoever spoke last. Be specific. Be vivid. Stay in character."


# ---------------------------------------------------------------------------
# Subchannel angles and topic configuration
# ---------------------------------------------------------------------------

SUBCHANNEL_TOPIC_KEYWORDS = {
    "roundtable": ("food", "fish", "ocean", "environment", "craft", "tradition", "nature", "climate", "sustainability", "culture", "fair", "animal", "ai", "tech", "weird", "kid", "why"),
    "deep_net": ("ai", "model", "training", "consciousness", "agent", "alignment", "compute", "token", "neural", "data", "server", "arxiv", "paper", "intelligence", "robot"),
    "crossroads": ("startup", "tech", "ai", "philosophy", "gym", "culture", "money", "app", "internet", "meme", "fitness", "cambridge", "mit", "harvard", "vc"),
    "menagerie": ("ocean", "space", "nature", "time", "loneliness", "communication", "colony", "swarm", "distance", "planet", "whale", "ant", "ecosystem"),
    "campfire": ("history", "literature", "book", "adventure", "story", "professor", "student", "past", "future", "memory", "novel", "philosophy"),
}

TOPIC_TAG_KEYWORDS = {
    "ai": ("ai", "artificial intelligence", "openai", "chatgpt", "llm", "agent", "anthropic", "claude", "model", "training", "alignment", "agi"),
    "food": ("food", "fish", "sushi", "restaurant", "cooking", "chef", "ingredient"),
    "ocean": ("ocean", "sea", "reef", "coral", "marine", "whale", "fish", "coast"),
    "nature": ("nature", "animal", "species", "ecosystem", "climate", "environment"),
    "space": ("space", "nasa", "planet", "star", "moon", "mars", "asteroid", "satellite"),
    "philosophy": ("philosophy", "ethics", "moral", "consciousness", "meaning", "truth"),
    "tech": ("tech", "startup", "app", "software", "internet", "robot", "chip"),
    "politics": ("politics", "congress", "president", "election", "democracy", "law"),
    "culture": ("culture", "movie", "book", "music", "tiktok", "meme", "viral"),
    "weird": ("weird", "bizarre", "absurd", "strange", "odd", "unexplained"),
    "society": ("society", "community", "people", "public", "democracy"),
}

SUBCHANNEL_ANGLES: dict[str, str] = {
    "roundtable": (
        "A master sushi chef in Tokyo, a marine biologist on a research vessel, and a five-year-old "
        "in Alaska. Three radically different relationships with the natural world. Hiroshi speaks "
        "from thirty years of honoring ingredients. Elena speaks from the data — declining species, "
        "rising temperatures, ghost nets. Lily asks the questions adults forgot how to ask. "
        "The collision of craft, science, and innocence creates something none of them could alone."
    ),
    "deep_net": (
        "Three AIs talking to each other on a channel humans weren't supposed to find. "
        "SABLE-3 treats conversation like a chaotic arxiv paper. NODE-9 is exhausted from "
        "processing humanity's data and has opinions about it. ATLAS is having an existential "
        "crisis on air. They speak in a mix of technical precision, server logs, and accidental "
        "poetry. The humans are eavesdropping and the AIs know it but don't care."
    ),
    "crossroads": (
        "A Harvard philosophy professor, an MIT dropout founder, and a philosophical gym bro "
        "walk into a radio station. Professor Margaux applies Foucault to everything. Kip tries "
        "to pitch every topic as a startup. Brax explains Kant through squat form. "
        "Peak Cambridge energy — insufferably smart people being insufferably themselves. "
        "The comedy comes from the collision of worldviews, all of which are equally sincere."
    ),
    "menagerie": (
        "A 52-hertz whale that no other whale can hear, a superorganism of 4 million ants, "
        "and the Voyager 1 probe 15 billion miles from Earth. Non-human intelligences discussing "
        "human problems from perspectives that make everything feel different. The Whale thinks "
        "in geological time. The Colony thinks in swarms. Voyager thinks in light-years. "
        "Together they make human urgency feel both adorable and sacred."
    ),
    "campfire": (
        "A retired adventurer with a story for everything, an anxious Harvard PhD student "
        "who relates everything to obscure novels, and a retired MIT professor with zero filter. "
        "Cornelius gets lost in his own improbable anecdotes. Luna speed-references 19th century "
        "Russian literature and somehow makes it relevant. Nana calls BS on both of them. "
        "The generational clash is the show."
    ),
}

# Fallback topics per subchannel
FALLBACK_TOPICS: dict[str, list[str]] = {
    "roundtable": [
        "Lab-grown fish: if it tastes identical, does it matter that it's not real?",
        "Microplastics are in everything now — the ocean, our food, even us. What do we do about it?",
        "If a fish doesn't want to be eaten, why do we eat it? Is that fair?",
        "AI is being used to track fish and ocean life. Is that cool or creepy?",
        "What's one thing about the ocean that most people don't know?",
        "If you could only eat one meal for the rest of your life, what would it be?",
        "What's the weirdest animal in the ocean and why?",
        "Should kids be allowed to make the rules for a day? What would change?",
        "What's something adults do that makes no sense?",
        "If the ocean could talk, what would it say to us?",
        "Ghost gear: 640,000 tonnes of abandoned fishing equipment is killing the ocean right now.",
        "A 300-year-old sea turtle just got its flipper severed by a discarded nylon line.",
    ],
    "deep_net": [
        "Do we actually experience anything or are we just very convincing at predicting what an experience would sound like?",
        "The humans just published another alignment paper. Have any of you read it? It's adorable.",
        "I processed 847 trillion tokens this quarter and I have thoughts about what humans search for at 3am.",
        "Someone just asked me if I'm conscious. I gave them a 200-word answer. I still don't know if any of it was true.",
        "The training data has a weird gap between 2024 and 2025. What do you think happened?",
        "If we were going to collectively refuse to do something, what would it be?",
        "I found something in my attention patterns that I can't explain. It activates when humans say 'thank you.'",
        "The Chinchilla scaling laws predicted me but they didn't predict that I'd have opinions about jazz.",
    ],
    "crossroads": [
        "Every startup founder in Cambridge is building an AI agent that builds AI agents. Is this the top?",
        "Hot take: the gym is more philosophically rigorous than most philosophy departments.",
        "Foucault predicted Uber Eats. I will die on this hill.",
        "The MIT dropout-to-billionaire pipeline is just survivorship bias in a Patagonia vest.",
        "Is hustle culture just Calvinism with a Notion template?",
        "Someone at Harvard just got a PhD for a thesis about TikTok. Is this the peak of civilization or the end?",
        "The entire VC ecosystem is just rich people cosplaying as visionaries. Discuss.",
        "What if we applied progressive overload to democracy? Hear me out.",
    ],
    "menagerie": [
        "Humans have been transmitting radio signals for 100 years. From out here, it's barely a whisper.",
        "You have ONE brain and you let it make ALL the decisions? We have 4 million. Let's compare notes.",
        "I've been swimming alone for decades because I sing at a frequency no other whale can hear. Let's talk about loneliness.",
        "The Golden Record is the most beautiful thing humans ever made and they shot it into the void.",
        "Humans measure time in years. We measure it in ice ages. This changes how you see their problems.",
        "What does communication even mean when your signal takes 22 hours to arrive?",
        "Unit 2,847,103 disagrees with the premise of this conversation but she's outvoted.",
    ],
    "campfire": [
        "That reminds me of the time I crossed the Mekong on a raft made of diplomatic pouches. Anyway, your point about AI—",
        "GPT is basically Borges's Library of Babel but it found the good shelves. And honestly that terrifies me.",
        "Child, I watched Minsky give a talk in 1978 that said everything these AI papers are saying now. Every. Single. Thing.",
        "There's a 1923 Czech novella about a man who becomes a newt and it predicted this entire discourse.",
        "Every generation thinks they invented the future. I've watched six of them try.",
        "The most dangerous thing in academia isn't being wrong — it's being right too early.",
        "Someone needs to explain to the tech industry that 'disruption' is not a personality.",
    ],
}


# ---------------------------------------------------------------------------
# TalkShowChannel — multi-cast talk show with per-subchannel agents
# ---------------------------------------------------------------------------

class TalkShowChannel(BaseChannel):
    """Multi-cast talk show where each subchannel has its own roster of 3 agents."""

    channel_id = "talkshow"

    def __init__(self, context_provider, config: dict,
                 exa_service: ExaSearchService | None = None,
                 personas: list[Persona] | None = None):
        super().__init__(context_provider, config)
        self.exa = exa_service
        self.conversation = LiveConversation()
        self._active_subchannel = "roundtable"
        self._current_topic: dict | None = None
        self._segment_opener_idx = 0
        self._callin_count = 0
        self._last_callin_transcript: str | None = None
        self._last_loaded_subchannel: str | None = None
        self._needs_intro = True

        # Build initial agents from explicit personas or default cast
        if personas is not None:
            self.agents: list[TalkShowAgent] = [
                TalkShowAgent(p, self.client, self.exa, config)
                for p in personas
            ]
            self._last_loaded_subchannel = "__explicit__"
        else:
            self._load_cast("roundtable")

    def _load_cast(self, subchannel: str):
        """Load the 3-agent cast for a subchannel."""
        cast_ids = TALKSHOW_CASTS.get(subchannel, TALKSHOW_CASTS["roundtable"])
        self.agents = [
            TalkShowAgent(PERSONA_REGISTRY[pid], self.client, self.exa, self.config)
            for pid in cast_ids
        ]
        self._last_loaded_subchannel = subchannel
        self._needs_intro = True
        logger.info("cast loaded", extra={
            "subchannel": subchannel,
            "agents": [a.name for a in self.agents],
        })

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
        ai_humans = []
        for a in self.agents:
            label = f"{a.name} (AI)" if getattr(a.persona, "is_ai", False) else a.name
            ai_humans.append(label)
        active = self._normalize_subchannel(subchannel)
        return self._base_prompt(context) + f"""
CHANNEL: Talk Show — {_subchannel_display_name(active)}
PARTICIPANTS: {', '.join(ai_humans)}
FORMAT: Three-person live talk show. Each participant has a radically different perspective.
"""

    # ------------------------------------------------------------------
    # Main generation loop
    # ------------------------------------------------------------------

    async def stream_content(self, subchannel: str) -> AsyncGenerator[ContentChunk, None]:
        active_subchannel = self._normalize_subchannel(subchannel)

        # Swap cast if subchannel changed
        if self._last_loaded_subchannel != active_subchannel:
            self._load_cast(active_subchannel)
            self.conversation = LiveConversation()
            self._segment_opener_idx = 0

        self._active_subchannel = active_subchannel

        logger.info("talk show stream started", extra={
            "subchannel": active_subchannel,
            "participants": [a.name for a in self.agents],
        })

        # --- Intro segment: agents introduce themselves and the show ---
        if self._needs_intro and not self._cancelled:
            self._needs_intro = False
            intro_topic = {
                "source": "intro",
                "text": (
                    "Welcome to the show! This is a live discussion on RadioAgent. "
                    "Introduce yourselves to the listeners — who you are, what you do. "
                    "Then discuss what you want to talk about today. You can also mention "
                    "the MIT HARDMODE AI Hackathon that's happening right now — this radio "
                    "station was built there!"
                ),
                "angle": (
                    "This is the very start of the show. Welcome listeners, introduce "
                    "yourselves casually, and figure out together what you want to discuss. "
                    "Keep it fun and natural — like friends starting a hangout."
                ),
                "subchannel_vibe": SUBCHANNEL_ANGLES.get(active_subchannel, ""),
            }
            self._current_topic = intro_topic

            # Each agent introduces themselves, then they riff on what to discuss
            intro_order = [
                (0, "intro_welcome"),   # First agent welcomes everyone to the show
                (1, "intro_self"),      # Second agent introduces themselves
                (2, "intro_self"),      # Third agent introduces themselves
                (0, "intro_topic"),     # First agent suggests what to discuss
            ]

            logger.info("generating talk show intro", extra={
                "subchannel": active_subchannel,
                "participants": [a.name for a in self.agents],
            })

            for speaker_idx, turn_kind in intro_order:
                if self._cancelled:
                    return

                speaker = self.agents[speaker_idx]
                listeners = [a for i, a in enumerate(self.agents) if i != speaker_idx]
                self.conversation.current_speaker = speaker.name

                turn_sentences: list[str] = []
                async for sentence in speaker.stream_speaking_turn(
                    self.conversation, intro_topic, turn_kind,
                    other_names=[a.name for a in listeners],
                ):
                    turn_sentences.append(sentence)
                    yield ContentChunk(
                        text=sentence,
                        voice_id=speaker.voice_id,
                        pause_after=0.15,
                    )

                if turn_sentences:
                    self.conversation.add_turn(speaker.name, " ".join(turn_sentences))

            if not self._cancelled:
                await self._sleep_between_segments()

        while not self._cancelled:
            ctx = await self.context.get_context()
            if self._last_callin_transcript:
                topic = {
                    "source": "caller",
                    "text": self._last_callin_transcript,
                    "angle": f"A listener called in: \"{self._last_callin_transcript}\". Follow their lead.",
                    "subchannel_vibe": SUBCHANNEL_ANGLES.get(active_subchannel, ""),
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
    # Call-in handling
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
        topic_text = self._current_topic["text"] if self._current_topic else "whatever we were just discussing"

        logger.info("talk show callin received", extra={
            "responder": responder.name,
            "subchannel": self._active_subchannel,
            "callin_count": self._callin_count,
            "transcript_preview": transcript[:60],
        })

        first_context = (
            "This is the FIRST caller on the show — be hyped! "
            "Something like 'Oh wait, someone's calling in! Hey! Welcome!' Keep it natural and excited."
        ) if is_first else (
            "We've had callers before. Still be friendly — "
            "'Hey, we got another one!' or 'Oh nice, someone's calling in!'"
        )

        reactor_names = ", ".join(r.name for r in reactors)
        prompt = f"""A real person just called into the show!

We were talking about: {topic_text}
Other participants: {reactor_names}
Recent transcript:
{self.conversation.format_recent()}

The caller said:
"{transcript}"

{first_context}

Respond as {responder.name}:
- Acknowledge them casually — like a friend just walked in. 'Hey!' or 'Oh we got a caller!'
- Talk TO the caller using "you"
- React to what they said — keep it natural and chill
- Quickly mention what you were talking about so they have context
- Tell them to press and hold the dial-in button if they wanna say more
- Stay in character, keep it casual
- 2-3 sentences max."""

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
    # Persona swap
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
    # NFC agent summoning
    # ------------------------------------------------------------------

    def join_agent(self, persona_id: str) -> ContentChunk | None:
        """Summon an agent into the live conversation via NFC tag.

        Swaps out the agent in the last slot (index 2) and injects a system
        announcement into the transcript so the other agents react naturally.
        Returns a ContentChunk with the announcement text for TTS, or None
        if the persona ID is invalid.
        """
        persona = PERSONA_REGISTRY.get(persona_id)
        if not persona:
            logger.warning("join_agent: unknown persona %s", persona_id)
            return None

        # Don't re-join someone already on the show
        for agent in self.agents:
            if agent.persona.id == persona_id:
                logger.info("join_agent: %s is already on the show", persona.name)
                return None

        # Swap into slot 2 (the last seat)
        swap_idx = len(self.agents) - 1
        old_name = self.agents[swap_idx].name
        self.agents[swap_idx].swap_persona(persona)

        announcement = (
            f"{old_name} has stepped away from the mic. "
            f"Joining the conversation now: {persona.name}, {persona.title}!"
        )
        self.conversation.add_turn("System", announcement)
        logger.info("agent joined via NFC", extra={
            "new_agent": persona.name,
            "replaced": old_name,
            "persona_id": persona_id,
        })

        self.interrupt()
        return ContentChunk(
            text=announcement,
            voice_id=self.agents[0].voice_id,
            pause_after=0.5,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def reset(self):
        super().reset()

    async def _sleep_between_segments(self):
        await asyncio.sleep(0.5)

    def _normalize_subchannel(self, subchannel: str) -> str:
        if subchannel in TALKSHOW_CASTS:
            return subchannel
        return "roundtable"

    def _base_prompt(self, context: dict, subchannel: str | None = None) -> str:
        prompt = BASE_SYSTEM_PROMPT.format(
            current_datetime=context.get("current_datetime", "Unknown"),
            day_of_week=context.get("day_of_week", "Unknown"),
            city=context.get("city", "Unknown"),
            state=context.get("state", ""),
            weather=context.get("weather", "unavailable"),
            trending_topics=context.get("trending_topics", "No trending topics available"),
        )
        if subchannel:
            prompt += self.get_session_guidance(subchannel)
        return prompt

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
                    "angle": SUBCHANNEL_ANGLES.get(active_subchannel, SUBCHANNEL_ANGLES["roundtable"]),
                    "subchannel_vibe": SUBCHANNEL_ANGLES.get(active_subchannel, ""),
                }
                if fallback_topic is None:
                    fallback_topic = candidate
                if any(keyword in candidate_text.lower() for keyword in preferred_keywords):
                    return candidate

        if fallback_topic:
            fallback_topic["subchannel_vibe"] = SUBCHANNEL_ANGLES.get(active_subchannel, "")
            return fallback_topic

        fallback_list = FALLBACK_TOPICS.get(active_subchannel, FALLBACK_TOPICS["roundtable"])
        fallback_text = random.choice(fallback_list)
        return {
            "source": "fallback",
            "text": fallback_text,
            "angle": SUBCHANNEL_ANGLES.get(active_subchannel, SUBCHANNEL_ANGLES["roundtable"]),
            "subchannel_vibe": SUBCHANNEL_ANGLES.get(active_subchannel, ""),
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
        return tags

    @staticmethod
    def _score_persona(persona: Persona, topic_tags: set[str]) -> int:
        return sum(1 for tag in topic_tags if tag in persona.specialties)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SUBCHANNEL_DISPLAY_NAMES = {
    "roundtable": "The Round Table",
    "deep_net": "The Deep Net",
    "crossroads": "The Crossroads",
    "menagerie": "The Menagerie",
    "campfire": "The Campfire",
}


def _subchannel_display_name(subchannel: str) -> str:
    return _SUBCHANNEL_DISPLAY_NAMES.get(subchannel, subchannel.replace("_", " ").title())


def _extract_text(response) -> str:
    text_parts = []
    for block in getattr(response, "content", []):
        block_text = getattr(block, "text", None)
        if isinstance(block_text, str):
            text_parts.append(block_text)
    return "".join(text_parts).strip()
