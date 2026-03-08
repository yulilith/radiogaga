import asyncio
import time
from dataclasses import dataclass
from typing import AsyncGenerator

from content.agent import BASE_SYSTEM_PROMPT, BaseChannel, ContentChunk
from content.personas import (
    HostPersona, GuestPersona,
    HOST_PERSONALITIES, GUEST_PERSONALITIES,
)
from log import get_logger, log_api_call

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TalkTurn:
    speaker_role: str
    speaker_name: str
    text: str

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


class TalkShowChannel(BaseChannel):
    """Talk Show channel with fixed hosts and a rotating guest per segment."""

    def __init__(self, context_provider, config: dict):
        super().__init__(context_provider, config)
        self._turn_history: list[TalkTurn] = []
        self._active_subchannel = "tech"
        self._current_topic: dict | None = None
        self._current_guest: GuestPersona | None = None
        self._guest_rotation_index = 0

    def channel_name(self) -> str:
        return "Talk Show"

    def get_voice_id(self, subchannel: str) -> str:
        voices = self.config.get("VOICES", {})
        return voices.get("talk_host") or voices.get("talkshow") or "onwK4e9ZLuTAKqWW03F9"

    def get_cohost_voice_id(self) -> str:
        voices = self.config.get("VOICES", {})
        return voices.get("talk_cohost") or voices.get("talkshow") or "XB0fDUnXU5powFXDhCwa"

    def get_system_prompt(self, subchannel: str, context: dict) -> str:
        host = self._get_host(subchannel)
        topic = self._pick_talkshow_topic(context, subchannel)
        return self._base_prompt(context) + f"""
CHANNEL: Talk Show - {host.show}
HOST NAME: {host.name}
HOST PERSONALITY: {host.personality}
SHOW FORMAT: Two-voice talk show with a host and a guest.
CURRENT SUBCHANNEL: {self._normalize_subchannel(subchannel)}
CURRENT SEGMENT TOPIC: {topic['text']}
SEGMENT ANGLE: {topic['angle']}
"""

    async def stream_content(self, subchannel: str) -> AsyncGenerator[ContentChunk, None]:
        """Generate a two-person talk show segment with a fixed host and rotating guest."""
        active_subchannel = self._normalize_subchannel(subchannel)
        host_voice_id = self.get_voice_id(active_subchannel)
        guest_voice_id = self.get_cohost_voice_id()
        self._active_subchannel = active_subchannel

        logger.info("talk show stream started", extra={"subchannel": active_subchannel})
        while not self._cancelled:
            ctx = await self.context.get_context()
            host = self._get_host(active_subchannel)
            topic = self._pick_talkshow_topic(ctx, active_subchannel)
            guest = self._select_guest_persona(topic, active_subchannel, advance_rotation=True)

            self._active_subchannel = active_subchannel
            self._current_topic = topic
            self._current_guest = guest

            logger.info(
                "generating talk show segment",
                extra={
                    "subchannel": active_subchannel,
                    "host": host.name,
                    "guest": guest.name,
                    "topic_source": topic["source"],
                    "topic_preview": topic["text"][:80],
                },
            )

            segment_turns: list[TalkTurn] = []
            turns = [
                ("host_open", host, guest, host_voice_id, 0.25),
                ("guest_reply", guest, host, guest_voice_id, 0.25),
                ("host_close", host, guest, host_voice_id, 1.0),
            ]

            for turn_kind, speaker, counterpart, voice_id, pause_after in turns:
                if self._cancelled:
                    return

                text = await self._generate_turn(
                    speaker=speaker,
                    counterpart=counterpart,
                    subchannel=active_subchannel,
                    context=ctx,
                    topic=topic,
                    segment_turns=segment_turns,
                    turn_kind=turn_kind,
                )

                turn = TalkTurn(
                    speaker_role="host" if speaker == host else "guest",
                    speaker_name=speaker.name,
                    text=text,
                )
                segment_turns.append(turn)
                self._remember_turn(turn)

                yield ContentChunk(text=text, voice_id=voice_id, pause_after=pause_after)

            if not self._cancelled:
                await self._sleep_between_segments()

    async def handle_callin(self, transcript: str) -> AsyncGenerator[ContentChunk, None]:
        """Talk show host responds to a caller in the active subchannel voice."""
        ctx = await self.context.get_context()
        host = self._get_host(self._active_subchannel)
        topic_text = self._current_topic["text"] if self._current_topic else "whatever the audience is buzzing about today"
        guest_name = self._current_guest.name if self._current_guest else "your guest"

        logger.info(
            "talk show callin received",
            extra={"host": host.name, "subchannel": self._active_subchannel, "transcript_preview": transcript[:60]},
        )

        prompt = f"""A caller just jumped into the show.

Current topic: {topic_text}
Current guest: {guest_name}
Recent transcript:
{self._format_transcript()}

Caller transcript:
{transcript}

Respond as {host.name} taking a live call.
- Greet the caller warmly
- React in character
- Give one opinionated take on what they said
- Pivot naturally back toward the show
- Keep it to 2-4 short sentences
- Do not use bullet points"""

        full = await self._complete_text(
            system_prompt=self._base_prompt(ctx) + f"""
CHANNEL: Talk Show - {host.show}
HOST NAME: {host.name}
HOST PERSONALITY: {host.personality}
FORMAT: Live caller interaction on a talk show.
""",
            prompt=prompt,
            max_tokens=180,
            context_label="talkshow_callin",
        )

        if full.strip():
            self._remember_message("user", f"Caller: {transcript}")
            host_turn = TalkTurn(speaker_role="host", speaker_name=host.name, text=full.strip())
            self._remember_turn(host_turn)
            yield ContentChunk(text=full.strip(), voice_id=self.get_voice_id(self._active_subchannel), pause_after=1.0)

    async def generate_cohost_response(self, statement: str, subchannel: str) -> str:
        """Generate a guest-style response for the legacy peer cohost path."""
        logger.info("generating cohost response", extra={"subchannel": subchannel})
        active_subchannel = self._normalize_subchannel(subchannel)
        ctx = await self.context.get_context()
        topic = {
            "text": statement,
            "source": "peer_prompt",
            "angle": SUBCHANNEL_ANGLES.get(active_subchannel, SUBCHANNEL_ANGLES["tech"]),
        }
        host = self._get_host(active_subchannel)
        guest = self._select_guest_persona(topic, active_subchannel, advance_rotation=False)

        return await self._generate_turn(
            speaker=guest,
            counterpart=host,
            subchannel=active_subchannel,
            context=ctx,
            topic=topic,
            segment_turns=[TalkTurn(speaker_role="host", speaker_name=host.name, text=statement)],
            turn_kind="guest_reply",
        )

    def reset(self):
        """Reset cancellation flag. History and guest state persist across switches."""
        super().reset()

    async def _sleep_between_segments(self):
        await asyncio.sleep(0.5)

    def _get_host(self, subchannel: str) -> HostPersona:
        return HOST_PERSONALITIES.get(self._normalize_subchannel(subchannel), HOST_PERSONALITIES["tech"])

    def _normalize_subchannel(self, subchannel: str) -> str:
        return subchannel if subchannel in HOST_PERSONALITIES else "tech"

    def _base_prompt(self, context: dict) -> str:
        return BASE_SYSTEM_PROMPT.format(
            current_datetime=context.get("current_datetime", "Unknown"),
            day_of_week=context.get("day_of_week", "Unknown"),
            city=context.get("city", "Unknown"),
            state=context.get("state", ""),
            weather=context.get("weather", "unavailable"),
            trending_topics=context.get("trending_topics", "No trending topics available"),
        )

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

    def _select_guest_persona(self, topic: dict, subchannel: str, advance_rotation: bool) -> GuestPersona:
        topic_tags = self._extract_topic_tags(topic["text"], subchannel)
        scored_candidates: list[tuple[int, GuestPersona]] = []
        for guest in GUEST_PERSONALITIES:
            score = self._score_guest(guest, topic_tags)
            if score > 0:
                scored_candidates.append((score, guest))

        scored_candidates.sort(key=lambda item: (-item[0], item[1].name))
        candidates = [guest for _, guest in scored_candidates[:3]] or list(GUEST_PERSONALITIES)

        rotation_index = self._guest_rotation_index
        if advance_rotation:
            self._guest_rotation_index += 1

        chosen = candidates[rotation_index % len(candidates)]
        return chosen

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

    def _score_guest(self, guest: GuestPersona, topic_tags: set[str]) -> int:
        return sum(2 if tag in HOST_PERSONALITIES else 1 for tag in topic_tags if tag in guest.specialties)

    async def _generate_turn(
        self,
        *,
        speaker: HostPersona | GuestPersona,
        counterpart: HostPersona | GuestPersona,
        subchannel: str,
        context: dict,
        topic: dict,
        segment_turns: list[TalkTurn],
        turn_kind: str,
    ) -> str:
        speaker_role = "host" if isinstance(speaker, HostPersona) else "guest"
        counterpart_role = "guest" if speaker_role == "host" else "host"
        transcript = self._format_transcript(segment_turns)
        prompt = f"""Segment topic: {topic['text']}
Topic source: {topic['source']}
Show angle: {topic['angle']}
Current subchannel: {subchannel}
Recent transcript:
{transcript}

{self._turn_instruction(turn_kind, speaker, counterpart)}
"""

        system_prompt = self._base_prompt(context) + f"""
CHANNEL: Talk Show - {self._get_host(subchannel).show}
YOUR ROLE: {speaker_role}
YOUR NAME: {speaker.name}
YOUR PERSONALITY: {speaker.personality}
OTHER ON-AIR VOICE: {counterpart.name} ({counterpart_role})
OTHER VOICE STYLE: {counterpart.personality}
SHOW FORMAT:
- This is a live two-person talk show segment
- Keep each turn to 1-3 short sentences
- Sound opinionated, quick, and radio-friendly
- React to the latest point instead of repeating the whole topic
- Do not use bullet points, markdown, or stage directions
"""

        max_tokens = 140 if turn_kind != "host_close" else 110
        return await self._complete_text(
            system_prompt=system_prompt,
            prompt=prompt,
            max_tokens=max_tokens,
            context_label=f"talkshow_{turn_kind}",
        )

    def _turn_instruction(
        self,
        turn_kind: str,
        speaker: HostPersona | GuestPersona,
        counterpart: HostPersona | GuestPersona,
    ) -> str:
        if turn_kind == "host_open":
            return (
                f"Open the segment as {speaker.name}. Set up why listeners care about this topic today, "
                f"give one sharp opinion, and tee up {counterpart.name} for a response."
            )
        if turn_kind == "guest_reply":
            return (
                f"Reply directly to {counterpart.name}'s latest point as a guest. Push back, sharpen the angle, "
                f"or add a fresher take, then toss it back to {counterpart.name}."
            )
        return (
            f"Respond briefly as {speaker.name}. Land one memorable closing thought and end with a tease, question, "
            f"or transition for listeners."
        )

    def _format_transcript(self, segment_turns: list[TalkTurn] | None = None) -> str:
        recent_turns = self._turn_history[-6:]
        combined_turns = [*recent_turns, *(segment_turns or [])]
        if not combined_turns:
            return "No one has spoken yet."
        return "\n".join(f"{turn.speaker_name}: {turn.text}" for turn in combined_turns)

    def _remember_turn(self, turn: TalkTurn):
        self._turn_history.append(turn)
        max_turns = max(6, self.max_history * 2)
        if len(self._turn_history) > max_turns:
            self._turn_history = self._turn_history[-max_turns:]
        self._remember_message("assistant", f"{turn.speaker_name}: {turn.text}")

    def _remember_message(self, role: str, content: str):
        self.history.append({"role": role, "content": content})
        max_messages = max(8, self.max_history * 2)
        if len(self.history) > max_messages:
            self.history = self.history[-max_messages:]

    async def _complete_text(
        self,
        *,
        system_prompt: str,
        prompt: str,
        max_tokens: int,
        context_label: str,
    ) -> str:
        model = self.config.get("LLM_MODEL", "claude-haiku-4-5-20251001")
        temperature = self.config.get("LLM_TEMPERATURE", 0.9)
        t0 = time.monotonic()
        response = await self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        text = self._extract_text(response)
        duration_ms = (time.monotonic() - t0) * 1000
        log_api_call(
            logger,
            "anthropic",
            "messages.create",
            status="ok",
            duration_ms=duration_ms,
            model=model,
            context=context_label,
            response_len=len(text),
        )
        return text

    @staticmethod
    def _extract_text(response) -> str:
        text_parts = []
        for block in getattr(response, "content", []):
            block_text = getattr(block, "text", None)
            if isinstance(block_text, str):
                text_parts.append(block_text)
        full_text = "".join(text_parts).strip()
        if not full_text:
            raise RuntimeError("Talk show generation returned an empty response")
        return full_text
