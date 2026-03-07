import asyncio
import time

from log import get_logger, log_api_call

logger = get_logger(__name__)


async def get_reddit_trending(client_id: str, client_secret: str,
                               user_agent: str, limit: int = 10) -> list[str]:
    """Get trending posts from Reddit via PRAW."""
    if not client_id or not client_secret:
        logger.warning("Reddit credentials not configured, skipping trending posts")
        return []

    logger.debug(f"Fetching Reddit trending posts (limit={limit})")
    start = time.monotonic()
    try:
        import praw

        reddit = await asyncio.to_thread(
            praw.Reddit,
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
        )

        def _fetch():
            posts = []
            for post in reddit.subreddit("all").hot(limit=limit):
                posts.append(f"r/{post.subreddit}: {post.title}")
            return posts

        posts = await asyncio.to_thread(_fetch)
        duration_ms = (time.monotonic() - start) * 1000
        log_api_call(logger, "reddit", "r/all/hot", status="ok",
                     duration_ms=duration_ms, count=len(posts))
        logger.info(f"Reddit: fetched {len(posts)} trending posts")
        return posts
    except Exception as e:
        duration_ms = (time.monotonic() - start) * 1000
        log_api_call(logger, "reddit", "r/all/hot", status="error",
                     duration_ms=duration_ms)
        logger.error(f"Reddit trending fetch failed: {e}")
        return []


async def get_google_trends(region: str = "united_states") -> list[str]:
    """Get trending Google searches via pytrends (unofficial)."""
    logger.debug(f"Fetching Google Trends for region={region}")
    start = time.monotonic()
    try:
        from pytrends.request import TrendReq

        def _fetch():
            pytrends = TrendReq(hl="en-US")
            trending = pytrends.trending_searches(pn=region)
            return trending[0].tolist()[:10]

        trends = await asyncio.to_thread(_fetch)
        duration_ms = (time.monotonic() - start) * 1000
        log_api_call(logger, "google-trends", "/trending_searches", status="ok",
                     duration_ms=duration_ms, count=len(trends))
        logger.info(f"Google Trends: fetched {len(trends)} trending searches",
                    extra={"region": region})
        return trends
    except Exception as e:
        duration_ms = (time.monotonic() - start) * 1000
        log_api_call(logger, "google-trends", "/trending_searches", status="error",
                     duration_ms=duration_ms)
        logger.error(f"Google Trends fetch failed: {e}")
        return []
