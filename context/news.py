import aiohttp


async def get_headlines(api_key: str, country: str = "us", max_results: int = 10) -> list[str]:
    """Get top headlines via GNews.io API."""
    if not api_key:
        return ["No news API key configured"]

    url = (
        f"https://gnews.io/api/v4/top-headlines?"
        f"category=general&lang=en&country={country}"
        f"&max={max_results}&apikey={api_key}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                articles = data.get("articles", [])
                return [a.get("title", "") for a in articles if a.get("title")]
    except Exception as e:
        print(f"[Context] News error: {e}")
        return []


async def get_category_headlines(api_key: str, category: str,
                                  country: str = "us", max_results: int = 5) -> list[str]:
    """Get headlines for a specific category (business, sports, technology, etc.)."""
    if not api_key:
        return []

    url = (
        f"https://gnews.io/api/v4/top-headlines?"
        f"category={category}&lang=en&country={country}"
        f"&max={max_results}&apikey={api_key}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                articles = data.get("articles", [])
                return [a.get("title", "") for a in articles if a.get("title")]
    except Exception as e:
        print(f"[Context] Category news error: {e}")
        return []
