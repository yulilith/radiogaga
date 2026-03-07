import asyncio
import time
from datetime import datetime

from log import get_logger

from context.location import get_location
from context.weather import get_weather
from context.news import get_headlines
from context.sports import get_scores
from context.trends import get_reddit_trending, get_google_trends
from context.history import get_on_this_day
from context.astronomy import get_sun_times

logger = get_logger(__name__)


class ContextProvider:
    """Aggregates all context sources into a single dict for LLM prompts.
    Caches results to avoid hammering APIs."""

    def __init__(self, config: dict):
        self.config = config
        self._cache: dict[str, tuple[float, any]] = {}
        self._cache_ttl = {
            "location": 86400,    # 24 hours
            "weather": 1800,      # 30 min
            "news": 600,          # 10 min
            "sports": 120,        # 2 min (live scores)
            "reddit": 600,        # 10 min
            "google_trends": 600, # 10 min
            "history": 86400,     # 24 hours (same all day)
            "astronomy": 86400,   # 24 hours
        }

    def _get_cached(self, key: str):
        if key in self._cache:
            ts, data = self._cache[key]
            ttl = self._cache_ttl.get(key, 300)
            if time.time() - ts < ttl:
                return data
        return None

    def _set_cached(self, key: str, data):
        self._cache[key] = (time.time(), data)

    async def get_context(self) -> dict:
        """Return complete context dict for prompt formatting."""
        fetch_start = time.monotonic()
        logger.debug("Starting full context fetch")

        # Always fresh: time
        now = datetime.now()
        time_context = {
            "current_datetime": now.strftime("%A, %B %d, %Y at %I:%M %p"),
            "day_of_week": now.strftime("%A"),
            "time_of_day": self._time_of_day(now),
            "hour": now.hour,
        }

        # Fetch all context sources concurrently (with caching)
        location, weather, news, sports, reddit, google, history, sun = (
            await asyncio.gather(
                self._cached_fetch("location", self._fetch_location),
                self._cached_fetch("weather", self._fetch_weather),
                self._cached_fetch("news", self._fetch_news),
                self._cached_fetch("sports", self._fetch_sports),
                self._cached_fetch("reddit", self._fetch_reddit),
                self._cached_fetch("google_trends", self._fetch_google_trends),
                self._cached_fetch("history", self._fetch_history),
                self._cached_fetch("astronomy", self._fetch_astronomy),
                return_exceptions=True,
            )
        )

        # Handle exceptions gracefully
        def safe(val, default, source_name: str = "unknown"):
            if isinstance(val, Exception):
                logger.warning(f"Context source '{source_name}' failed: {val}")
                return default
            return val

        location = safe(location, {"city": "Unknown", "region": ""}, "location")
        weather = safe(weather, {"current": "unavailable", "forecast": ""}, "weather")
        news = safe(news, [], "news")
        sports = safe(sports, [], "sports")
        reddit = safe(reddit, [], "reddit")
        google = safe(google, [], "google_trends")
        history = safe(history, [], "history")
        sun = safe(sun, {"sunrise": "", "sunset": ""}, "astronomy")

        # Log which sources were successfully fetched
        sources_fetched = []
        if location.get("city") != "Unknown":
            sources_fetched.append("location")
        if weather.get("current") != "unavailable":
            sources_fetched.append("weather")
        if news:
            sources_fetched.append(f"news({len(news)})")
        if sports:
            sources_fetched.append(f"sports({len(sports)})")
        if reddit:
            sources_fetched.append(f"reddit({len(reddit)})")
        if google:
            sources_fetched.append(f"google_trends({len(google)})")
        if history:
            sources_fetched.append(f"history({len(history)})")
        if sun.get("sunrise"):
            sources_fetched.append("astronomy")

        fetch_duration_ms = (time.monotonic() - fetch_start) * 1000
        logger.info(f"Context fetch complete in {fetch_duration_ms:.0f}ms: {', '.join(sources_fetched)}",
                    extra={"duration_ms": f"{fetch_duration_ms:.0f}",
                           "sources_count": len(sources_fetched)})

        return {
            **time_context,
            "city": location.get("city", "Unknown"),
            "state": location.get("region", ""),
            "country": location.get("country", ""),
            "weather": weather.get("current", "unavailable"),
            "forecast": weather.get("forecast", ""),
            "headlines": news[:5],
            "headlines_full": news,
            "live_scores": [s["summary"] for s in sports] if isinstance(sports, list) and sports and isinstance(sports[0], dict) else sports,
            "reddit_trending": reddit[:5],
            "google_trends": google[:5],
            "on_this_day": history[:3],
            "sunrise": sun.get("sunrise", ""),
            "sunset": sun.get("sunset", ""),
            "trending_topics": ", ".join((news[:3] if news else []) + (google[:2] if google else [])),
        }

    async def _cached_fetch(self, key: str, fetcher):
        cached = self._get_cached(key)
        if cached is not None:
            logger.debug(f"Cache hit for '{key}'")
            return cached
        logger.debug(f"Cache miss for '{key}', fetching fresh data")
        data = await fetcher()
        self._set_cached(key, data)
        return data

    async def _fetch_location(self):
        return await get_location()

    async def _fetch_weather(self):
        loc = self._get_cached("location")
        if not loc:
            loc = await get_location()
            self._set_cached("location", loc)
        return await get_weather(loc.get("lat", 0), loc.get("lon", 0))

    async def _fetch_news(self):
        return await get_headlines(self.config.get("GNEWS_API_KEY", ""))

    async def _fetch_sports(self):
        return await get_scores("basketball")  # Default sport

    async def _fetch_reddit(self):
        return await get_reddit_trending(
            self.config.get("REDDIT_CLIENT_ID", ""),
            self.config.get("REDDIT_CLIENT_SECRET", ""),
            self.config.get("REDDIT_USER_AGENT", "RadioAgent/1.0"),
        )

    async def _fetch_google_trends(self):
        return await get_google_trends()

    async def _fetch_history(self):
        return await get_on_this_day()

    async def _fetch_astronomy(self):
        loc = self._get_cached("location")
        if not loc:
            loc = await get_location()
            self._set_cached("location", loc)
        return await get_sun_times(loc.get("lat", 0), loc.get("lon", 0))

    async def get_sports_context(self, sport: str = "basketball") -> list:
        """Get scores for a specific sport (called by sports channel)."""
        return await get_scores(sport)

    @staticmethod
    def _time_of_day(now: datetime) -> str:
        hour = now.hour
        if 5 <= hour < 12:
            return "morning"
        elif 12 <= hour < 17:
            return "afternoon"
        elif 17 <= hour < 21:
            return "evening"
        else:
            return "late night"
