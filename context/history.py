import time

import aiohttp
from datetime import datetime

from log import get_logger, log_api_call

logger = get_logger(__name__)


async def get_on_this_day(limit: int = 5) -> list[str]:
    """Get 'On This Day' historical events from Wikipedia REST API."""
    now = datetime.now()
    month = f"{now.month:02d}"
    day = f"{now.day:02d}"

    # Use en.wikipedia.org REST API directly (more reliable than api.wikimedia.org)
    url = f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{month}/{day}"
    logger.debug(f"Fetching history events for {month}/{day} from Wikipedia")
    headers = {
        "User-Agent": "RadioAgent/1.0 (hackathon project; contact@example.com)",
        "Accept": "application/json",
    }

    start = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    duration_ms = (time.monotonic() - start) * 1000
                    log_api_call(logger, "wikipedia", f"/onthisday/events/{month}/{day}",
                                 status=f"http_{resp.status}", duration_ms=duration_ms)
                    logger.warning(f"History API returned HTTP {resp.status}")
                    return []
                data = await resp.json()
        duration_ms = (time.monotonic() - start) * 1000

        events = data.get("events", [])
        results = []
        for event in events[:limit]:
            year = event.get("year", "?")
            text = event.get("text", "")
            if text:
                results.append(f"{year}: {text}")

        log_api_call(logger, "wikipedia", f"/onthisday/events/{month}/{day}",
                     status="ok", duration_ms=duration_ms, count=len(results))
        logger.info(f"History: fetched {len(results)} events for {month}/{day}")
        return results
    except Exception as e:
        duration_ms = (time.monotonic() - start) * 1000
        log_api_call(logger, "wikipedia", f"/onthisday/events/{month}/{day}",
                     status="error", duration_ms=duration_ms)
        logger.error(f"History fetch failed: {e}")
        return []
