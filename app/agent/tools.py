import asyncio
import logging
from typing import Optional
from tavily import TavilyClient
from app.config import settings

logger = logging.getLogger(__name__)

# ── Tavily Search ─────────────────────────────────────────────────────────────

_tavily_client: Optional[TavilyClient] = None


def get_tavily_client() -> TavilyClient:
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = TavilyClient(api_key=settings.tavily_api_key)
    return _tavily_client


async def tavily_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web using Tavily. Returns list of {title, url, content}."""
    try:
        client = get_tavily_client()
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.search(
                query=query,
                max_results=max_results,
                search_depth="advanced",
                include_answer=True,
            )
        )
        results = []
        if response.get("answer"):
            results.append({
                "title": "AI Summary",
                "url": "tavily_answer",
                "content": response["answer"]
            })
        for r in response.get("results", []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", "")
            })
        return results
    except Exception as e:
        logger.error(f"Tavily search failed for '{query}': {e}")
        return []


SEARCH_TEMPLATES = {
    "player_stats": [
        "{player} goals assists stats {season}",
        "{player} xG xA progressive passes {season}",
    ],
    "tactical_analysis": [
        "{player} tactical analysis style of play position",
        "{player} heatmap role system",
    ],
    "injury_history": [
        "{player} injury history record availability",
        "{player} fitness concerns medical",
    ],
    "transfer_news": [
        "{player} transfer news {year} fee",
        "{player} contract expiry release clause interest",
    ],
    "market_value": [
        "{player} transfermarkt market value {year}",
        "{player} transfer fee worth how much {year}",
        "{player} contract until salary wage {year}",
    ],
    "team_fit": [
        "{player} {team} tactical fit how would he play",
        "{player} signing analysis {team}",
    ],
    "comparison": [
        "{player} vs {comparison} stats comparison {season}",
        "{player} {comparison} who is better analysis",
    ],
}


def build_search_queries(
    topic: str,
    player: str,
    season: str = "2025/26",
    year: str = "2026",
    team: str = "",
    comparison: str = "",
) -> list[str]:
    """Build search queries for a given topic and player."""
    templates = SEARCH_TEMPLATES.get(topic, ["{player} {topic} football"])
    queries = []
    for t in templates:
        q = t.format(
            player=player,
            topic=topic,
            season=season,
            year=year,
            team=team,
            comparison=comparison,
        )
        queries.append(q)
    return queries


# ── Stats fetcher via Tavily (FBRef blocked 403) ──────────────────────────────

async def fetch_fbref_player_stats(player_name: str, season: str = "2425") -> dict:
    """
    FBRef direct scraping returns 403 Forbidden.
    Use targeted Tavily searches hitting fbref, sofascore, whoscored instead.
    """
    logger.info(f"Using Tavily stats fallback for {player_name}")

    queries = [
        f"{player_name} fbref stats 2025/26 goals xG progressive passes",
        f"{player_name} sofascore whoscored stats season 2025/26",
        f"{player_name} detailed statistics goals assists minutes played 2025/26",
    ]

    all_results = []
    for q in queries:
        results = await tavily_search(q, max_results=4)
        all_results.extend(results)

    if not all_results:
        return {}

    return {
        "source": "tavily_stats_fallback",
        "player": player_name,
        "raw_results": [
            {"title": r["title"], "content": r["content"][:600]}
            for r in all_results[:8]
        ]
    }