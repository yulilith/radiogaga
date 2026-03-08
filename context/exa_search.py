"""Exa web search service for talk show agent tool use."""

import aiohttp

from log import get_logger

logger = get_logger(__name__)


class ExaSearchService:
    """Async wrapper around the Exa search API."""

    BASE_URL = "https://api.exa.ai"

    def __init__(self, api_key: str | None):
        self.api_key = api_key

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, num_results: int = 3) -> list[dict]:
        """Search the web via Exa. Returns list of {title, url, snippet}.

        Non-fatal: returns empty list on error or missing key.
        """
        if not self.api_key:
            logger.debug("exa_search skipped — no API key")
            return []

        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"{self.BASE_URL}/search",
                    headers={
                        "x-api-key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": query,
                        "numResults": num_results,
                        "type": "auto",
                        "text": True,
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                if resp.status != 200:
                    logger.warning("exa_search HTTP %d for query: %s", resp.status, query[:60])
                    return []

                data = await resp.json()
                results = []
                for item in data.get("results", []):
                    text = item.get("text", "")
                    snippet = " ".join(text.split()[:100]) if text else ""
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": snippet,
                    })
                logger.info("exa_search ok", extra={"query": query[:60], "results": len(results)})
                return results
        except Exception as e:
            logger.warning("exa_search failed: %s", e)
            return []
