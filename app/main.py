import uuid
import json
import asyncio
import logging
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.agent.graph import scout_graph
from app.agent.state import AgentState
from app.db.database import (
    init_db, create_session, update_session,
    complete_session, fail_session,
    get_session, list_sessions, save_memory, get_user_memory
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Startup / Shutdown ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database initialized")
    yield


app = FastAPI(
    title="Football Scout AI",
    description="Deep research agent for football scouting reports",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response Models ─────────────────────────────────────────────────

class ResearchRequest(BaseModel):
    query: str
    mode: str = "deep"  # "deep" | "quick"


class FollowUpRequest(BaseModel):
    question: str


# ── Active sessions tracker (in-memory) ──────────────────────────────────────

_active_sessions: dict[str, AgentState] = {}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.post("/research")
async def start_research(req: ResearchRequest):
    """Start a new research session. Returns session_id immediately."""
    if len(_active_sessions) >= settings.max_concurrent_sessions:
        raise HTTPException(status_code=429, detail="Too many concurrent sessions. Try again later.")

    session_id = str(uuid.uuid4())
    await create_session(session_id, req.query, req.mode)

    # Initialize state
    initial_state: AgentState = {
        "query": req.query,
        "session_id": session_id,
        "mode": req.mode,
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

    _active_sessions[session_id] = initial_state

    # Run agent in background
    asyncio.create_task(_run_agent(session_id, initial_state))

    return {"session_id": session_id, "status": "started"}


async def _run_agent(session_id: str, initial_state: AgentState):
    """Run the LangGraph agent and update session state."""
    try:
        final_state = await scout_graph.ainvoke(initial_state)
        _active_sessions[session_id] = final_state

        await complete_session(
            session_id,
            final_state.get("final_report", ""),
            final_state.get("verification_status", ""),
            final_state.get("data_audit", {}),
        )
        await save_memory(
            final_state.get("players_mentioned", []),
            final_state.get("teams_mentioned", []),
            initial_state["query"],
        )
    except Exception as e:
        logger.error(f"Agent failed for session {session_id}: {e}")
        errors = [str(e)]
        if session_id in _active_sessions:
            _active_sessions[session_id]["errors"] = errors
            _active_sessions[session_id]["status"] = "error"
        await fail_session(session_id, errors)


@app.get("/research/{session_id}/stream")
async def stream_research(session_id: str):
    """
    SSE endpoint. Streams events as the agent progresses.
    Frontend connects here after POST /research.
    """
    if session_id not in _active_sessions:
        # Try loading from DB (session recovery)
        session = await get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        # Already completed — stream the final report immediately
        async def stream_completed():
            report = session.get("final_report", "")
            yield f"data: {json.dumps({'event': 'complete', 'data': {'report': report}})}\n\n"
        return StreamingResponse(stream_completed(), media_type="text/event-stream")

    async def event_generator():
        seen_events = 0
        max_wait = 180  # 3 min timeout
        waited = 0

        while waited < max_wait:
            state = _active_sessions.get(session_id)
            if not state:
                break

            events = state.get("stream_events", [])

            # Push new events
            while seen_events < len(events):
                event = events[seen_events]
                payload = json.dumps({"event": event["event"], "data": event["data"]})
                yield f"data: {payload}\n\n"
                seen_events += 1

            # Check if done
            current_status = state.get("status", "")
            if current_status in ("complete", "error"):
                # Send final report in one chunk
                if current_status == "complete":
                    report = state.get("final_report", "")
                    audit = state.get("data_audit", {})
                    yield f"data: {json.dumps({'event': 'report', 'data': {'markdown': report, 'audit': audit}})}\n\n"
                else:
                    errors = state.get("errors", [])
                    yield f"data: {json.dumps({'event': 'error', 'data': {'errors': errors}})}\n\n"

                # Clean up
                _active_sessions.pop(session_id, None)
                break

            await asyncio.sleep(0.5)
            waited += 0.5

        if waited >= max_wait:
            yield f"data: {json.dumps({'event': 'error', 'data': {'errors': ['Research timed out']}})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        }
    )


@app.get("/sessions")
async def get_sessions():
    """List all past research sessions."""
    sessions = await list_sessions()
    return {"sessions": sessions}


@app.get("/sessions/{session_id}")
async def get_session_detail(session_id: str):
    """Get full session including report."""
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session."""
    from app.db.database import get_db
    async with await get_db() as db:
        await db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        await db.commit()
    _active_sessions.pop(session_id, None)
    return {"deleted": session_id}


@app.get("/memory")
async def get_memory():
    """Get user's research history summary."""
    return await get_user_memory()
