import aiohttp


async def get_location() -> dict:
    """Get location from IP via ip-api.com (free, no key)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://ip-api.com/json/", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return {
                    "city": data.get("city", "Unknown"),
                    "region": data.get("regionName", ""),
                    "country": data.get("country", ""),
                    "lat": data.get("lat", 0),
                    "lon": data.get("lon", 0),
                    "timezone": data.get("timezone", "UTC"),
                }
    except Exception as e:
        print(f"[Context] Location error: {e}")
        return {"city": "Unknown", "region": "", "country": "", "lat": 0, "lon": 0, "timezone": "UTC"}
