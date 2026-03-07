import time

import aiohttp

from log import get_logger, log_api_call

logger = get_logger(__name__)


async def get_headlines(api_key: str, country: str = "us", max_results: int = 10) -> list[str]:
    """Get top headlines via GNews.io API."""
    if not api_key:
        logger.warning("No news API key configured, skipping headlines")
        return ["No news API key configured"]

    # Log the URL without the API key for security
    logger.debug(f"Fetching headlines from gnews.io: category=general, country={country}, max={max_results}")
    start = time.monotonic()
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
                headlines = [a.get("title", "") for a in articles if a.get("title")]
        duration_ms = (time.monotonic() - start) * 1000
        log_api_call(logger, "gnews", "/v4/top-headlines", status="ok",
                     duration_ms=duration_ms, count=len(headlines))
        logger.info(f"News: fetched {len(headlines)} headlines",
                    extra={"country": country})
        return headlines
    except Exception as e:
        duration_ms = (time.monotonic() - start) * 1000
        log_api_call(logger, "gnews", "/v4/top-headlines", status="error",
                     duration_ms=duration_ms)
        logger.error(f"News fetch failed: {e}")
        return []


async def get_category_headlines(api_key: str, category: str,
                                  country: str = "us", max_results: int = 5) -> list[str]:
    """Get headlines for a specific category (business, sports, technology, etc.)."""
    if not api_key:
        logger.warning(f"No news API key configured, skipping {category} headlines")
        return []

    logger.debug(f"Fetching {category} headlines from gnews.io: country={country}, max={max_results}")
    start = time.monotonic()
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
                headlines = [a.get("title", "") for a in articles if a.get("title")]
        duration_ms = (time.monotonic() - start) * 1000
        log_api_call(logger, "gnews", f"/v4/top-headlines/{category}", status="ok",
                     duration_ms=duration_ms, count=len(headlines))
        logger.info(f"Category news: fetched {len(headlines)} {category} headlines",
                    extra={"category": category, "country": country})
        return headlines
    except Exception as e:
        duration_ms = (time.monotonic() - start) * 1000
        log_api_call(logger, "gnews", f"/v4/top-headlines/{category}", status="error",
                     duration_ms=duration_ms)
        logger.error(f"Category news fetch failed for {category}: {e}")
        return []
