from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HeardSnippet:
    channel: str
    subchannel: str
    text: str


@dataclass(frozen=True, slots=True)
class SwitchEvent:
    from_channel: str
    from_subchannel: str
    to_channel: str
    to_subchannel: str


class SessionMemory:
    """Tracks what the listener actually heard during this app session."""

    def __init__(
        self,
        per_channel_limit: int = 6,
        global_limit: int = 10,
        switch_limit: int = 8,
    ):
        self._per_channel_limit = per_channel_limit
        self._global_limit = global_limit
        self._switch_limit = switch_limit
        self._per_channel: dict[tuple[str, str], deque[HeardSnippet]] = defaultdict(
            lambda: deque(maxlen=self._per_channel_limit)
        )
        self._global: deque[HeardSnippet] = deque(maxlen=self._global_limit)
        self._switches: deque[SwitchEvent] = deque(maxlen=self._switch_limit)

    def commit_heard(self, channel: str, subchannel: str, text: str):
        cleaned = " ".join(text.split())
        if not cleaned:
            return

        key = (channel, subchannel)
        snippets = self._per_channel[key]
        if snippets and snippets[-1].text == cleaned:
            return

        snippet = HeardSnippet(channel=channel, subchannel=subchannel, text=cleaned)
        snippets.append(snippet)
        self._global.append(snippet)

    def record_switch(
        self,
        from_channel: str,
        from_subchannel: str,
        to_channel: str,
        to_subchannel: str,
    ):
        if (
            from_channel == to_channel
            and from_subchannel == to_subchannel
        ):
            return

        self._switches.append(
            SwitchEvent(
                from_channel=from_channel,
                from_subchannel=from_subchannel,
                to_channel=to_channel,
                to_subchannel=to_subchannel,
            )
        )

    def recent_channel_items(self, channel: str, subchannel: str) -> list[str]:
        return [item.text for item in self._per_channel.get((channel, subchannel), ())]

    def recent_global_items(
        self,
        *,
        exclude_channel: str | None = None,
        exclude_subchannel: str | None = None,
    ) -> list[str]:
        items = []
        for item in self._global:
            if (
                exclude_channel is not None
                and item.channel == exclude_channel
                and item.subchannel == exclude_subchannel
            ):
                continue
            items.append(item.text)
        return items

    def recent_switches(self) -> list[SwitchEvent]:
        return list(self._switches)

    def build_prompt(self, channel: str, subchannel: str) -> str:
        lines = [
            "Use the listener session memory below to keep continuity and avoid repetition.",
        ]

        channel_recent = self.recent_channel_items(channel, subchannel)
        if channel_recent:
            joined = self._join_recent(channel_recent, limit=2)
            lines.append(
                f"- This station already aired recently: {joined}"
            )

        global_recent = self.recent_global_items(
            exclude_channel=channel,
            exclude_subchannel=subchannel,
        )
        if global_recent:
            joined = self._join_recent(global_recent, limit=2)
            lines.append(
                f"- The listener just heard on other stations: {joined}"
            )

        if self._switches:
            latest = self._switches[-1]
            lines.append(
                f"- Most recent dial move: {latest.from_channel}/{latest.from_subchannel} -> "
                f"{latest.to_channel}/{latest.to_subchannel}"
            )

        lines.append(
            "- Continue naturally instead of restarting the same opener or repeating the same topic."
        )
        lines.append(
            "- If you reference another station's topic, do it as a quick handoff instead of a full re-introduction."
        )
        return "\n".join(lines)

    @staticmethod
    def _join_recent(items: list[str], limit: int) -> str:
        trimmed = items[-limit:]
        return " | ".join(trimmed)
