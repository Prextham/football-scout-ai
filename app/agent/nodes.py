import json
import asyncio
import logging
from datetime import datetime, date
from typing import Any
from anthropic import AsyncAnthropic

from app.agent.state import AgentState
from app.agent.prompts import (
    QUERY_PLANNER_PROMPT,
    RESEARCH_SUMMARIZER_PROMPT,
    FACT_BASE_BUILDER_PROMPT,
    REPORT_WRITER_PROMPT,
    FACT_CHECKER_PROMPT,
)
from app.agent.tools import tavily_search, build_search_queries, fetch_fbref_player_stats
from app.config import settings

logger = logging.getLogger(__name__)
client = AsyncAnthropic(api_key=settings.anthropic_api_key)


# ── Helpers ───────────────────────────────────────────────────────────────────

def emit(state: AgentState, event: str, data: Any) -> dict:
    """Append an SSE event to state for streaming."""
    events = state.get("stream_events", [])
    events.append({"event": event, "data": data, "ts": datetime.utcnow().isoformat()})
    return {"stream_events": events}


async def llm_call(prompt: str, model: str, max_tokens: int = 2000) -> str:
    """Single LLM call, returns text content."""
    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


async def llm_json(prompt: str, model: str, max_tokens: int = 2000) -> dict:
    """LLM call that parses JSON response. Returns empty dict on failure."""
    raw = await llm_call(prompt, model, max_tokens)
    try:
        # Strip markdown fences if present
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        return json.loads(clean.strip())
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse failed: {e}\nRaw: {raw[:500]}")
        return {}


# ── Node 1: Query Planner ─────────────────────────────────────────────────────

async def query_planner(state: AgentState) -> dict:
    """Break the user query into sub-topics and extract entities."""
    updates = emit(state, "status", "Planning research strategy...")

    prompt = QUERY_PLANNER_PROMPT.format(query=state["query"])
    plan = await llm_json(prompt, model=settings.fast_model)

    if not plan:
        plan = {
            "sub_topics": ["player_stats", "tactical_analysis", "transfer_news"],
            "report_type": "player_scout",
            "players_mentioned": [],
            "teams_mentioned": [],
            "comparison_player": None,
            "temporal_strategy": {"stats": "recent", "injuries": "historical"}
        }

    sub_topics = plan.get("sub_topics", [])
    if state.get("mode") == "quick":
        sub_topics = sub_topics[:3]

    updates.update({
        "sub_topics": sub_topics,
        "report_type": plan.get("report_type", "player_scout"),
        "players_mentioned": plan.get("players_mentioned", []),
        "teams_mentioned": plan.get("teams_mentioned", []),
        "comparison_player": plan.get("comparison_player"),
        "temporal_strategy": plan.get("temporal_strategy", {}),
        "status": "planning_complete",
    })
    updates["stream_events"] = state.get("stream_events", []) + [
        {"event": "plan", "data": {
            "sub_topics": sub_topics,
            "players": plan.get("players_mentioned", []),
            "report_type": plan.get("report_type")
        }, "ts": datetime.utcnow().isoformat()}
    ]
    return updates


# ── Node 2: FBRef Data Fetch ──────────────────────────────────────────────────

async def fbref_fetcher(state: AgentState) -> dict:
    """Fetch deep stats from FBRef via soccerdata for all mentioned players."""
    players = state.get("players_mentioned", [])
    if not players:
        return {"fbref_data": {}, "status": "fbref_skipped"}

    events = state.get("stream_events", [])
    events.append({"event": "status", "data": f"Fetching FBRef stats for {', '.join(players)}...",
                   "ts": datetime.utcnow().isoformat()})

    fbref_data = {}
    tasks = [fetch_fbref_player_stats(p) for p in players]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for player, result in zip(players, results):
        if isinstance(result, Exception):
            logger.error(f"FBRef failed for {player}: {result}")
            fbref_data[player] = {}
        else:
            fbref_data[player] = result

    events.append({"event": "fbref_complete", "data": {
        "players_found": [p for p, d in fbref_data.items() if d],
        "players_missing": [p for p, d in fbref_data.items() if not d],
    }, "ts": datetime.utcnow().isoformat()})

    return {"fbref_data": fbref_data, "stream_events": events, "status": "fbref_complete"}


# ── Node 3: Parallel Tavily Research ─────────────────────────────────────────

async def tavily_researcher(state: AgentState) -> dict:
    """Search the web for all sub-topics in parallel."""
    sub_topics = state.get("sub_topics", [])
    players = state.get("players_mentioned", [])
    teams = state.get("teams_mentioned", [])
    comparison = state.get("comparison_player")

    primary_player = players[0] if players else ""
    primary_team = teams[0] if teams else ""

    events = state.get("stream_events", [])
    events.append({"event": "status", "data": f"Searching web across {len(sub_topics)} topics...",
                   "ts": datetime.utcnow().isoformat()})

    async def research_topic(topic: str) -> tuple[str, dict]:
        queries = build_search_queries(
            topic=topic,
            player=primary_player,
            team=primary_team,
            comparison=comparison or "",
        )
        all_results = []
        for q in queries[:2]:  # max 2 queries per topic to respect rate limits
            results = await tavily_search(q, max_results=settings.max_search_results_per_topic)
            all_results.extend(results)

        # Summarize findings via LLM
        results_text = "\n\n".join([
            f"[{r['title']}] ({r['url']})\n{r['content'][:500]}"
            for r in all_results[:8]
        ])

        prompt = RESEARCH_SUMMARIZER_PROMPT.format(
            topic=topic,
            query=state["query"],
            results=results_text or "No results found."
        )
        summary = await llm_json(prompt, model=settings.fast_model, max_tokens=1500)

        events.append({"event": "topic_complete", "data": {
            "topic": topic,
            "results_found": len(all_results),
            "data_quality": summary.get("data_quality", "unknown")
        }, "ts": datetime.utcnow().isoformat()})

        return topic, summary

    # Run all topics in parallel
    tasks = [research_topic(topic) for topic in sub_topics]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    tavily_results = {}
    for item in results:
        if isinstance(item, Exception):
            logger.error(f"Topic research failed: {item}")
        else:
            topic, data = item
            tavily_results[topic] = data

    return {
        "tavily_results": tavily_results,
        "stream_events": events,
        "status": "research_complete"
    }


# ── Node 4: Fact Base Builder ─────────────────────────────────────────────────

async def fact_base_builder(state: AgentState) -> dict:
    """Reconcile FBRef + Tavily data into a single verified Fact Base."""
    events = state.get("stream_events", [])
    events.append({"event": "status", "data": "Building verified Fact Base...",
                   "ts": datetime.utcnow().isoformat()})

    players = state.get("players_mentioned", [])
    findings_text = json.dumps(state.get("tavily_results", {}), indent=2)[:4000]
    fbref_text = json.dumps(state.get("fbref_data", {}), indent=2)[:3000]

    prompt = FACT_BASE_BUILDER_PROMPT.format(
        query=state["query"],
        players=", ".join(players),
        findings=findings_text,
        fbref_data=fbref_text,
    )
    fact_base = await llm_json(prompt, model=settings.fast_model, max_tokens=2000)

    audit = fact_base.get("audit", {})
    events.append({"event": "fact_base_ready", "data": {
        "conflicts": audit.get("conflicts_detected", []),
        "missing_fields": audit.get("missing_fields", []),
        "confidence": audit.get("overall_confidence", "unknown"),
    }, "ts": datetime.utcnow().isoformat()})

    return {
        "fact_base": fact_base,
        "data_audit": audit,
        "stream_events": events,
        "status": "fact_base_complete"
    }


# ── Node 5: Comparison Engine ─────────────────────────────────────────────────

async def comparison_engine(state: AgentState) -> dict:
    """Fetch stats for comparison player if one was detected."""
    comparison_player = state.get("comparison_player")
    if not comparison_player:
        return {"comparison_data": None, "status": "comparison_skipped"}

    events = state.get("stream_events", [])
    events.append({"event": "status", "data": f"Fetching comparison data for {comparison_player}...",
                   "ts": datetime.utcnow().isoformat()})

    # FBRef stats for comparison player
    fbref = await fetch_fbref_player_stats(comparison_player)

    # Quick Tavily search for comparison context
    results = await tavily_search(f"{comparison_player} stats 2025/26 season", max_results=3)
    context = "\n".join([r["content"][:300] for r in results])

    comparison_data = {
        "player": comparison_player,
        "fbref": fbref,
        "context": context,
    }

    events.append({"event": "comparison_ready", "data": {"player": comparison_player},
                   "ts": datetime.utcnow().isoformat()})

    return {
        "comparison_data": comparison_data,
        "stream_events": events,
        "status": "comparison_complete"
    }


# ── Node 6: Report Writer ─────────────────────────────────────────────────────

async def report_writer(state: AgentState) -> dict:
    """Generate the scouting report using ONLY the Fact Base."""
    events = state.get("stream_events", [])
    events.append({"event": "status", "data": "Writing scouting report...",
                   "ts": datetime.utcnow().isoformat()})

    prompt = REPORT_WRITER_PROMPT.format(
        query=state["query"],
        fact_base=json.dumps(state.get("fact_base", {}), indent=2)[:4000],
        comparison_data=json.dumps(state.get("comparison_data"), indent=2)[:2000],
        audit=json.dumps(state.get("data_audit", {}), indent=2),
        date=date.today().strftime("%B %d, %Y"),
        mode=state.get("mode", "deep"),
    )

    report = await llm_call(prompt, model=settings.report_model, max_tokens=4000)

    events.append({"event": "report_drafted", "data": {"length": len(report)},
                   "ts": datetime.utcnow().isoformat()})

    return {
        "report_draft": report,
        "stream_events": events,
        "status": "report_drafted",
        "verification_attempts": state.get("verification_attempts", 0)
    }


# ── Node 7: Fact Checker ──────────────────────────────────────────────────────

async def fact_checker(state: AgentState) -> dict:
    """Verify the report against the Fact Base. Max 2 attempts."""
    attempts = state.get("verification_attempts", 0) + 1
    events = state.get("stream_events", [])
    events.append({"event": "status", "data": f"Verifying report accuracy (attempt {attempts})...",
                   "ts": datetime.utcnow().isoformat()})

    prompt = FACT_CHECKER_PROMPT.format(
        fact_base=json.dumps(state.get("fact_base", {}), indent=2)[:3000],
        report_draft=state.get("report_draft", "")[:4000],
    )
    result = await llm_json(prompt, model=settings.fast_model, max_tokens=1000)

    status = result.get("status", "APPROVED")
    errors = result.get("errors", [])

    events.append({"event": "verification_result", "data": {
        "status": status,
        "errors_found": len(errors),
        "attempt": attempts,
    }, "ts": datetime.utcnow().isoformat()})

    return {
        "verification_status": status,
        "verification_errors": [e.get("issue", "") for e in errors],
        "verification_attempts": attempts,
        "stream_events": events,
        "status": "verified" if status == "APPROVED" else "verification_failed"
    }


# ── Node 8: Final Output ──────────────────────────────────────────────────────

async def finalize_report(state: AgentState) -> dict:
    """Set the final report. Adds warning banner if unverified."""
    events = state.get("stream_events", [])
    report = state.get("report_draft", "")
    v_status = state.get("verification_status", "APPROVED")
    errors = state.get("verification_errors", [])
    attempts = state.get("verification_attempts", 0)

    # If rejected after max attempts, ship with warning
    if v_status == "REJECTED" and attempts >= settings.max_verification_attempts:
        warning = f"\n\n---\n⚠️ **Verification Warning:** {len(errors)} stat(s) could not be fully verified against sources after {attempts} attempts. Review with caution.\n"
        report = report + warning
        v_status = "unverified"

    events.append({"event": "complete", "data": {
        "verification_status": v_status,
        "report_length": len(report),
    }, "ts": datetime.utcnow().isoformat()})

    return {
        "final_report": report,
        "verification_status": v_status,
        "stream_events": events,
        "status": "complete"
    }
