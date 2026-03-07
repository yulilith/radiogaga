import asyncio


async def get_reddit_trending(client_id: str, client_secret: str,
                               user_agent: str, limit: int = 10) -> list[str]:
    """Get trending posts from Reddit via PRAW."""
    if not client_id or not client_secret:
        return []

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

        return await asyncio.to_thread(_fetch)
    except Exception as e:
        print(f"[Context] Reddit error: {e}")
        return []


async def get_google_trends(region: str = "united_states") -> list[str]:
    """Get trending Google searches via pytrends (unofficial)."""
    try:
        from pytrends.request import TrendReq

        def _fetch():
            pytrends = TrendReq(hl="en-US")
            trending = pytrends.trending_searches(pn=region)
            return trending[0].tolist()[:10]

        return await asyncio.to_thread(_fetch)
    except Exception as e:
        print(f"[Context] Google Trends error: {e}")
        return []
