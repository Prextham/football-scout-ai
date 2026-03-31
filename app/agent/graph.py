from langgraph.graph import StateGraph, END
from app.agent.state import AgentState
from app.agent.nodes import (
    query_planner,
    fbref_fetcher,
    tavily_researcher,
    fact_base_builder,
    comparison_engine,
    report_writer,
    fact_checker,
    finalize_report,
)
from app.config import settings


def should_retry_verification(state: AgentState) -> str:
    """
    Routing function after fact_checker:
    - APPROVED → finalize
    - REJECTED + attempts < max → loop back to report_writer
    - REJECTED + attempts >= max → finalize anyway (with warning)
    """
    v_status = state.get("verification_status", "APPROVED")
    attempts = state.get("verification_attempts", 0)

    if v_status == "APPROVED":
        return "finalize"
    elif attempts < settings.max_verification_attempts:
        return "retry_report"
    else:
        return "finalize"  # ship with warning banner


def build_graph() -> StateGraph:
    """
    Construct the Football Scout agent graph.

    Flow:
    query_planner
         ↓
    fbref_fetcher  (parallel-ish with tavily via asyncio inside nodes)
         ↓
    tavily_researcher
         ↓
    fact_base_builder
         ↓
    comparison_engine
         ↓
    report_writer  ←──────────────────┐
         ↓                            │ (if REJECTED & attempts < max)
    fact_checker ──────────────────────┘
         ↓ (APPROVED or max attempts)
    finalize_report
         ↓
        END
    """
    graph = StateGraph(AgentState)

    # Register all nodes
    graph.add_node("query_planner", query_planner)
    graph.add_node("fbref_fetcher", fbref_fetcher)
    graph.add_node("tavily_researcher", tavily_researcher)
    graph.add_node("fact_base_builder", fact_base_builder)
    graph.add_node("comparison_engine", comparison_engine)
    graph.add_node("report_writer", report_writer)
    graph.add_node("fact_checker", fact_checker)
    graph.add_node("finalize_report", finalize_report)

    # Linear edges
    graph.set_entry_point("query_planner")
    graph.add_edge("query_planner", "fbref_fetcher")
    graph.add_edge("fbref_fetcher", "tavily_researcher")
    graph.add_edge("tavily_researcher", "fact_base_builder")
    graph.add_edge("fact_base_builder", "comparison_engine")
    graph.add_edge("comparison_engine", "report_writer")
    graph.add_edge("report_writer", "fact_checker")

    # Conditional edge: verification loop or finalize
    graph.add_conditional_edges(
        "fact_checker",
        should_retry_verification,
        {
            "retry_report": "report_writer",
            "finalize": "finalize_report",
        }
    )

    graph.add_edge("finalize_report", END)

    return graph.compile()


# Singleton compiled graph
scout_graph = build_graph()
