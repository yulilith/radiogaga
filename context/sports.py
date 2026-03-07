import aiohttp

# ESPN API endpoints (undocumented but publicly accessible)
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"

SPORT_MAP = {
    "basketball": ("basketball", "nba"),
    "football": ("football", "nfl"),
    "soccer": ("soccer", "usa.1"),  # MLS
    "f1": ("racing", "f1"),
    "baseball": ("baseball", "mlb"),
}


async def get_scores(sport: str = "basketball") -> list[dict]:
    """Get live/recent scores from ESPN hidden API."""
    sport_path, league = SPORT_MAP.get(sport, ("basketball", "nba"))
    url = f"{ESPN_BASE}/{sport_path}/{league}/scoreboard"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()

        events = data.get("events", [])
        scores = []
        for event in events[:5]:
            name = event.get("name", "")
            status = event.get("status", {}).get("type", {}).get("shortDetail", "")
            competitors = event.get("competitions", [{}])[0].get("competitors", [])
            if len(competitors) >= 2:
                home = competitors[0]
                away = competitors[1]
                score_line = (
                    f"{away['team']['abbreviation']} {away.get('score', '?')} "
                    f"@ {home['team']['abbreviation']} {home.get('score', '?')} "
                    f"({status})"
                )
            else:
                score_line = f"{name} ({status})"
            scores.append({"summary": score_line, "name": name, "status": status})
        return scores
    except Exception as e:
        print(f"[Context] Sports error: {e}")
        return []


async def get_standings(sport: str = "basketball") -> list[str]:
    """Get current standings for a sport."""
    sport_path, league = SPORT_MAP.get(sport, ("basketball", "nba"))
    url = f"{ESPN_BASE}/{sport_path}/{league}/standings"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()

        standings = []
        for group in data.get("children", [])[:2]:
            conf = group.get("name", "")
            for entry in group.get("standings", {}).get("entries", [])[:3]:
                team = entry.get("team", {}).get("displayName", "?")
                stats = {s["name"]: s["displayValue"] for s in entry.get("stats", [])}
                record = stats.get("overall", "?")
                standings.append(f"{team} ({record}) - {conf}")
        return standings
    except Exception as e:
        print(f"[Context] Standings error: {e}")
        return []
