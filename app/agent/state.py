from typing import TypedDict, Optional, Any
import operator
from langgraph.graph import add_messages


class AgentState(TypedDict):
    # ── Input ─────────────────────────────────────────
    query: str
    session_id: str
    mode: str                          # "deep" | "quick"

    # ── Planning ──────────────────────────────────────
    sub_topics: list[str]
    report_type: str                   # player_scout | transfer_analysis | comparison
    temporal_strategy: dict            # {"stats": "recent", "injuries": "historical"}
    players_mentioned: list[str]       # extracted player names
    teams_mentioned: list[str]         # extracted team names

    # ── Research ──────────────────────────────────────
    fbref_data: dict                   # structured stats from soccerdata
    tavily_results: dict               # web search results per topic
    raw_findings: dict                 # combined raw data per topic

    # ── Fact Base ─────────────────────────────────────
    fact_base: dict                    # validated, reconciled facts
    data_audit: dict                   # conflicts, missing fields, confidence

    # ── Comparison ────────────────────────────────────
    comparison_player: Optional[str]   # auto-detected comparison target
    comparison_data: Optional[dict]    # stats for comparison player

    # ── Report ────────────────────────────────────────
    report_draft: str
    final_report: str
    verification_status: str           # "approved" | "rejected" | "unverified"
    verification_errors: list[str]
    verification_attempts: int

    # ── Meta ──────────────────────────────────────────
    status: str                        # current pipeline stage
    errors: list[str]
    tokens_used: int
    cost_estimate: float
    stream_events: list[dict]          # events to push to SSE
