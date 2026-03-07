import time

import aiohttp

from log import get_logger, log_api_call

logger = get_logger(__name__)

# WMO Weather interpretation codes
WMO_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


async def get_weather(lat: float, lon: float) -> dict:
    """Get current weather + forecast via Open-Meteo (free, no key)."""
    logger.debug(f"Fetching weather for lat={lat}, lon={lon}")
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}"
        f"&current_weather=true"
        f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode"
        f"&timezone=auto"
        f"&forecast_days=3"
    )
    start = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
        duration_ms = (time.monotonic() - start) * 1000

        cw = data.get("current_weather", {})
        temp_c = cw.get("temperature", 20)
        temp_f = temp_c * 9 / 5 + 32
        wind_kmh = cw.get("windspeed", 0)
        code = cw.get("weathercode", 0)
        condition = WMO_CODES.get(code, "unknown")

        # Daily forecast
        daily = data.get("daily", {})
        forecast_lines = []
        dates = daily.get("time", [])
        highs = daily.get("temperature_2m_max", [])
        lows = daily.get("temperature_2m_min", [])
        precip = daily.get("precipitation_probability_max", [])
        codes = daily.get("weathercode", [])

        for i in range(min(3, len(dates))):
            hi_f = highs[i] * 9 / 5 + 32 if i < len(highs) else 0
            lo_f = lows[i] * 9 / 5 + 32 if i < len(lows) else 0
            p = precip[i] if i < len(precip) else 0
            c = WMO_CODES.get(codes[i], "unknown") if i < len(codes) else "unknown"
            forecast_lines.append(
                f"{dates[i]}: {c}, high {hi_f:.0f}F / low {lo_f:.0f}F, {p}% chance of rain"
            )

        log_api_call(logger, "open-meteo", "/v1/forecast", status="ok",
                     duration_ms=duration_ms)
        logger.info(f"Weather: {temp_f:.0f}°F, {condition}",
                    extra={"lat": lat, "lon": lon})

        return {
            "current": f"{temp_f:.0f}°F ({temp_c:.0f}°C), {condition}, wind {wind_kmh} km/h",
            "forecast": "; ".join(forecast_lines),
        }
    except Exception as e:
        duration_ms = (time.monotonic() - start) * 1000
        log_api_call(logger, "open-meteo", "/v1/forecast", status="error",
                     duration_ms=duration_ms)
        logger.error(f"Weather fetch failed: {e}")
        return {"current": "weather unavailable", "forecast": ""}
