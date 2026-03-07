import time

import aiohttp

from log import get_logger, log_api_call

logger = get_logger(__name__)


async def get_location() -> dict:
    """Get location from IP via ip-api.com (free, no key)."""
    logger.debug("Fetching location from ip-api.com")
    start = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://ip-api.com/json/", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                result = {
                    "city": data.get("city", "Unknown"),
                    "region": data.get("regionName", ""),
                    "country": data.get("country", ""),
                    "lat": data.get("lat", 0),
                    "lon": data.get("lon", 0),
                    "timezone": data.get("timezone", "UTC"),
                }
        duration_ms = (time.monotonic() - start) * 1000
        log_api_call(logger, "ip-api", "/json", status="ok", duration_ms=duration_ms)
        logger.info(f"Location: {result['city']}, {result['region']}",
                    extra={"city": result["city"], "region": result["region"],
                           "lat": result["lat"], "lon": result["lon"]})
        return result
    except Exception as e:
        duration_ms = (time.monotonic() - start) * 1000
        log_api_call(logger, "ip-api", "/json", status="error", duration_ms=duration_ms)
        logger.error(f"Location fetch failed: {e}")
        return {"city": "Unknown", "region": "", "country": "", "lat": 0, "lon": 0, "timezone": "UTC"}
