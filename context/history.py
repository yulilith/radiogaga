import aiohttp
from datetime import datetime


async def get_on_this_day(limit: int = 5) -> list[str]:
    """Get 'On This Day' historical events from Wikipedia REST API."""
    now = datetime.now()
    month = f"{now.month:02d}"
    day = f"{now.day:02d}"

    # Use en.wikipedia.org REST API directly (more reliable than api.wikimedia.org)
    url = f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{month}/{day}"
    headers = {
        "User-Agent": "RadioAgent/1.0 (hackathon project; contact@example.com)",
        "Accept": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    print(f"[Context] History API returned {resp.status}")
                    return []
                data = await resp.json()

        events = data.get("events", [])
        results = []
        for event in events[:limit]:
            year = event.get("year", "?")
            text = event.get("text", "")
            if text:
                results.append(f"{year}: {text}")
        return results
    except Exception as e:
        print(f"[Context] History error: {e}")
        return []
