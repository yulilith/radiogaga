import time

import aiohttp

from log import get_logger, log_api_call

logger = get_logger(__name__)


async def get_sun_times(lat: float, lon: float) -> dict:
    """Get sunrise/sunset times from sunrise-sunset.org (free, no key)."""
    logger.debug(f"Fetching sun times for lat={lat}, lon={lon}")
    url = f"https://api.sunrise-sunset.org/json?lat={lat}&lng={lon}&formatted=0"

    start = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
        duration_ms = (time.monotonic() - start) * 1000

        results = data.get("results", {})
        sun_data = {
            "sunrise": results.get("sunrise", ""),
            "sunset": results.get("sunset", ""),
            "day_length": results.get("day_length", 0),
            "solar_noon": results.get("solar_noon", ""),
        }

        log_api_call(logger, "sunrise-sunset", "/json", status="ok",
                     duration_ms=duration_ms)
        logger.info(f"Astronomy: sunrise={sun_data['sunrise']}, sunset={sun_data['sunset']}",
                    extra={"lat": lat, "lon": lon})
        return sun_data
    except Exception as e:
        duration_ms = (time.monotonic() - start) * 1000
        log_api_call(logger, "sunrise-sunset", "/json", status="error",
                     duration_ms=duration_ms)
        logger.error(f"Astronomy fetch failed: {e}")
        return {"sunrise": "", "sunset": "", "day_length": 0, "solar_noon": ""}
