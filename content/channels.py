"""Channel registry and subchannel definitions."""

CHANNELS = {
    "news": {
        "name": "News & Weather",
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
        "led_pin": 16,
        "subchannels": [
            {"id": "tech", "name": "Tech Talk", "dial_min": 0, "dial_max": 20},
            {"id": "popculture", "name": "Pop Culture", "dial_min": 21, "dial_max": 40},
            {"id": "philosophy", "name": "Philosophy", "dial_min": 41, "dial_max": 60},
            {"id": "comedy", "name": "Comedy", "dial_min": 61, "dial_max": 80},
            {"id": "advice", "name": "Advice Column", "dial_min": 81, "dial_max": 100},
        ],
    },
    "sports": {
        "name": "Sports",
        "btn_pin": 13,
        "led_pin": 20,
        "subchannels": [
            {"id": "basketball", "name": "Basketball", "dial_min": 0, "dial_max": 20},
            {"id": "football", "name": "Football", "dial_min": 21, "dial_max": 40},
            {"id": "soccer", "name": "Soccer", "dial_min": 41, "dial_max": 60},
            {"id": "f1", "name": "Formula 1", "dial_min": 61, "dial_max": 80},
            {"id": "baseball", "name": "Baseball", "dial_min": 81, "dial_max": 100},
        ],
    },
    "dj": {
        "name": "DJ & Music",
        "btn_pin": 19,
        "led_pin": 21,
        "subchannels": [
            {"id": "top_tracks", "name": "My Top Tracks", "dial_min": 0, "dial_max": 20},
            {"id": "discover", "name": "Discover", "dial_min": 21, "dial_max": 40},
            {"id": "genre", "name": "Genre Radio", "dial_min": 41, "dial_max": 60},
            {"id": "mood", "name": "Mood / Vibe", "dial_min": 61, "dial_max": 80},
            {"id": "decade", "name": "Decade", "dial_min": 81, "dial_max": 100},
        ],
    },
}


def resolve_subchannel(channel_id: str, dial_position: int) -> str:
    """Map a dial position (0-100) to a subchannel ID."""
    channel = CHANNELS.get(channel_id)
    if not channel:
        return ""
    for sub in channel["subchannels"]:
        if sub["dial_min"] <= dial_position <= sub["dial_max"]:
            return sub["id"]
    return channel["subchannels"][0]["id"]


def get_subchannel_name(channel_id: str, subchannel_id: str) -> str:
    """Get human-readable subchannel name."""
    channel = CHANNELS.get(channel_id)
    if not channel:
        return subchannel_id
    for sub in channel["subchannels"]:
        if sub["id"] == subchannel_id:
            return sub["name"]
    return subchannel_id
