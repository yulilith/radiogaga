"""Channel registry and subchannel definitions."""

from log import get_logger

logger = get_logger(__name__)

CHANNELS = {
    "dailybrief": {
        "name": "Daily Brief",
        "btn_pin": 5,
        "led_pin": 12,
        "subchannels": [
            {"id": "local", "name": "Local News", "dial_min": 0, "dial_max": 20},
            {"id": "national", "name": "National News", "dial_min": 21, "dial_max": 40},
            {"id": "world", "name": "World News", "dial_min": 41, "dial_max": 60},
            {"id": "weather", "name": "Weather", "dial_min": 61, "dial_max": 80},
            {"id": "traffic", "name": "Traffic", "dial_min": 81, "dial_max": 100},
        ],
    },
    "talkshow": {
        "name": "Talk Show",
        "btn_pin": 6,
        "led_pin": 22,
        "subchannels": [
            {"id": "tech", "name": "Tech Debate", "dial_min": 0, "dial_max": 20},
            {"id": "popculture", "name": "Culture Debate", "dial_min": 21, "dial_max": 40},
            {"id": "philosophy", "name": "Philosophy Debate", "dial_min": 41, "dial_max": 60},
            {"id": "comedy", "name": "Comedy Debate", "dial_min": 61, "dial_max": 80},
            {"id": "advice", "name": "Life Advice Debate", "dial_min": 81, "dial_max": 100},
        ],
    },
    "music": {
        "name": "Music",
        "btn_pin": 13,
        "led_pin": 23,
        "subchannels": [
            {"id": "top_tracks", "name": "My Top Tracks", "dial_min": 0, "dial_max": 20},
            {"id": "discover", "name": "Discover", "dial_min": 21, "dial_max": 40},
            {"id": "genre", "name": "Genre Radio", "dial_min": 41, "dial_max": 60},
            {"id": "mood", "name": "Mood / Vibe", "dial_min": 61, "dial_max": 80},
            {"id": "decade", "name": "Decade", "dial_min": 81, "dial_max": 100},
        ],
    },
    "memos": {
        "name": "Memos",
        "btn_pin": 26,
        "led_pin": 27,
        "subchannels": [],
    },
}


def resolve_subchannel(channel_id: str, dial_position: int) -> str:
    """Map a dial position (0-100) to a subchannel ID."""
    channel = CHANNELS.get(channel_id)
    if not channel:
        return ""
    if not channel["subchannels"]:
        return ""
    for sub in channel["subchannels"]:
        if sub["dial_min"] <= dial_position <= sub["dial_max"]:
            logger.debug("subchannel resolved", extra={"channel": channel_id, "dial_position": dial_position, "subchannel": sub["id"]})
            return sub["id"]
    default = channel["subchannels"][0]["id"]
    logger.debug("subchannel defaulted", extra={"channel": channel_id, "dial_position": dial_position, "subchannel": default})
    return default


def get_subchannel_name(channel_id: str, subchannel_id: str) -> str:
    """Get human-readable subchannel name."""
    channel = CHANNELS.get(channel_id)
    if not channel:
        return subchannel_id
    for sub in channel["subchannels"]:
        if sub["id"] == subchannel_id:
            return sub["name"]
    return subchannel_id
