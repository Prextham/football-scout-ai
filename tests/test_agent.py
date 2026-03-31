"""
Basic smoke tests for the Football Scout AI.
Run: pytest tests/ -v
"""
import pytest
import asyncio
import json


# ── Tools tests ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tavily_search_returns_results():
    """Tavily should return at least some results for a real query."""
    from app.agent.tools import tavily_search
    results = await tavily_search("Florian Wirtz stats 2025/26", max_results=3)
    assert isinstance(results, list)
    # Even if API fails, should return empty list not raise
    for r in results:
        assert "title" in r
        assert "content" in r


@pytest.mark.asyncio
async def test_fbref_fetch_graceful_failure():
    """FBRef fetch should return empty dict on failure, not raise."""
    from app.agent.tools import fetch_fbref_player_stats
    # Use a clearly non-existent player
    result = await fetch_fbref_player_stats("XXXXNONEXISTENT9999")
    assert isinstance(result, dict)


def test_build_search_queries():
    """Query builder should format templates correctly."""
    from app.agent.tools import build_search_queries
    queries = build_search_queries(
        topic="player_stats",
        player="Florian Wirtz",
        season="2025/26",
        year="2026",
    )
    assert len(queries) > 0
    assert all("Florian Wirtz" in q for q in queries)


# ── Graph tests ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_query_planner_returns_plan():
    """Query planner should return valid sub-topics for a real football query."""
    from app.agent.nodes import query_planner
    from app.agent.state import AgentState

    state: AgentState = {
        "query": "Should Arsenal sign Florian Wirtz?",
        "session_id": "test-123",
        "mode": "quick",
        "sub_topics": [],
        "report_type": "",
        "temporal_strategy": {},
        "players_mentioned": [],
        "teams_mentioned": [],
        "fbref_data": {},
        "tavily_results": {},
        "raw_findings": {},
        "fact_base": {},
        "data_audit": {},
        "comparison_player": None,
        "comparison_data": None,
        "report_draft": "",
        "final_report": "",
        "verification_status": "",
        "verification_errors": [],
        "verification_attempts": 0,
        "status": "started",
        "errors": [],
        "tokens_used": 0,
        "cost_estimate": 0.0,
        "stream_events": [],
    }

    result = await query_planner(state)
    assert "sub_topics" in result
    assert len(result["sub_topics"]) > 0
    assert "players_mentioned" in result


def test_verification_routing():
    """should_retry_verification should route correctly."""
    from app.agent.graph import should_retry_verification

    # Approved → finalize
    state_approved = {"verification_status": "APPROVED", "verification_attempts": 1}
    assert should_retry_verification(state_approved) == "finalize"

    # Rejected, first attempt → retry
    state_rejected_first = {"verification_status": "REJECTED", "verification_attempts": 1}
    assert should_retry_verification(state_rejected_first) == "retry_report"

    # Rejected, max attempts → finalize with warning
    state_rejected_max = {"verification_status": "REJECTED", "verification_attempts": 2}
    assert should_retry_verification(state_rejected_max) == "finalize"


# ── DB tests ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_lifecycle():
    """Session should be created, updated, and retrieved correctly."""
    import os
    import tempfile
    from app import config

    # Use temp DB for tests
    with tempfile.TemporaryDirectory() as tmpdir:
        config.settings.database_path = os.path.join(tmpdir, "test.db")

        from app.db.database import init_db, create_session, get_session, complete_session

        await init_db()
        await create_session("test-session-1", "Test query", "deep")

        session = await get_session("test-session-1")
        assert session is not None
        assert session["query"] == "Test query"
        assert session["status"] == "planning"

        await complete_session("test-session-1", "Final report text", "approved", {"confidence": "high"})
        session = await get_session("test-session-1")
        assert session["status"] == "complete"
        assert session["final_report"] == "Final report text"
