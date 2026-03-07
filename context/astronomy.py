import aiohttp


async def get_sun_times(lat: float, lon: float) -> dict:
    """Get sunrise/sunset times from sunrise-sunset.org (free, no key)."""
    url = f"https://api.sunrise-sunset.org/json?lat={lat}&lng={lon}&formatted=0"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()

        results = data.get("results", {})
        return {
            "sunrise": results.get("sunrise", ""),
            "sunset": results.get("sunset", ""),
            "day_length": results.get("day_length", 0),
            "solar_noon": results.get("solar_noon", ""),
        }
    except Exception as e:
        print(f"[Context] Astronomy error: {e}")
        return {"sunrise": "", "sunset": "", "day_length": 0, "solar_noon": ""}
