"""Tracks status updates from connected peer RadioAgents (friends)."""

import time
from dataclasses import dataclass, field

from log import get_logger

logger = get_logger(__name__)

STALE_THRESHOLD_S = 300  # drop updates older than 5 minutes


@dataclass
class FriendStatus:
    agent_id: str
    agent_name: str
    channel: str
    subchannel: str
    activity: str
    timestamp: float = field(default_factory=time.time)
    announced: bool = False


class FriendsTracker:
    """Collects status updates from peers and formats them for broadcast."""

    def __init__(self):
        self._friends: dict[str, FriendStatus] = {}

    def update(self, agent_id: str, agent_name: str, channel: str,
               subchannel: str, activity: str):
        is_new = agent_id not in self._friends
        self._friends[agent_id] = FriendStatus(
            agent_id=agent_id,
            agent_name=agent_name,
            channel=channel,
            subchannel=subchannel,
            activity=activity,
        )
        logger.info("friend status updated",
                     extra={"agent_id": agent_id, "name": agent_name,
                            "channel": channel, "activity": activity[:60]})
        return is_new

    def remove(self, agent_id: str):
        self._friends.pop(agent_id, None)

    def get_unannounced(self) -> list[FriendStatus]:
        """Return friend updates that haven't been announced yet, mark them."""
        self._prune_stale()
        result = [f for f in self._friends.values() if not f.announced]
        for f in result:
            f.announced = True
        return result

    def get_all_active(self) -> list[FriendStatus]:
        self._prune_stale()
        return list(self._friends.values())

    def build_broadcast_context(self) -> str:
        """Build a text block for injection into the LLM system prompt."""
        self._prune_stale()
        if not self._friends:
            return ""

        lines = ["CONNECTED FRIENDS (other RadioAgent users on your network):"]
        for f in self._friends.values():
            age = int(time.time() - f.timestamp)
            lines.append(
                f"- {f.agent_name} (agent {f.agent_id}): "
                f"listening to {f.channel}/{f.subchannel}, "
                f"activity: {f.activity} ({age}s ago)"
            )
        lines.append(
            "\nMention your connected friends naturally — what they're up to, "
            "what they're listening to. Deliver it like a community bulletin: "
            "warm, gossipy, funny. Keep each friend mention to 1-2 sentences."
        )
        return "\n".join(lines)

    def _prune_stale(self):
        now = time.time()
        stale = [k for k, v in self._friends.items()
                 if now - v.timestamp > STALE_THRESHOLD_S]
        for k in stale:
            del self._friends[k]
            logger.debug("pruned stale friend", extra={"agent_id": k})
