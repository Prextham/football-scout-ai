# ⚽ Football Scout AI

A production-grade deep research agent that transforms football questions into verified scouting reports using real FBRef data, web search, and Claude AI.

---

## Architecture

```
[User Query]
     ↓
[Query Planner]          → breaks query into sub-topics, extracts players/teams
     ↓
[FBRef Fetcher]          → deep stats via soccerdata (xG, progressive passes, etc.)
     ↓
[Tavily Researcher]      → parallel web search across all sub-topics
     ↓
[Fact Base Builder]      → reconciles FBRef + web data into verified JSON
     ↓
[Comparison Engine]      → fetches comparison player data if detected
     ↓
[Report Writer]          → generates report using ONLY the Fact Base
     ↓
[Fact Checker]           → verifies every stat against Fact Base (max 2 loops)
     ↓
[Final Output]           → ships with audit banner if unverified
```

**Tech Stack:**
- **Agent:** LangGraph (Python)
- **LLM:** Claude Haiku (fast nodes) + Claude Sonnet (report writing)
- **Stats:** soccerdata + FBRef (xG, xAG, progressive passes, defensive actions)
- **Search:** Tavily API
- **Backend:** FastAPI + Server-Sent Events
- **Frontend:** Vanilla HTML/CSS/JS on GitHub Pages
- **Database:** SQLite (aiosqlite) for session persistence
- **Deploy:** Railway (backend) + GitHub Pages (frontend)

---

## Local Setup

### 1. Clone the repo
```bash
git clone https://github.com/yourusername/football-scout-ai
cd football-scout-ai
```

### 2. Install dependencies
```bash
pip install uv
uv pip install -e .
```

### 3. Configure environment
```bash
cp .env.example .env
# Edit .env and add your API keys:
# ANTHROPIC_API_KEY=your_key
# TAVILY_API_KEY=your_key
```

### 4. Run the backend
```bash
uvicorn app.main:app --reload --port 8000
```

### 5. Open the frontend
Open `docs/index.html` in your browser, or serve it:
```bash
cd docs && python -m http.server 3000
```

Update `API_BASE` in `docs/app.js` to `http://localhost:8000` for local dev.

---

## Deployment

### Backend → Railway

1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select your repo
4. Add environment variables in Railway dashboard:
   - `ANTHROPIC_API_KEY`
   - `TAVILY_API_KEY`
   - `CORS_ORIGINS` → `https://yourusername.github.io`
5. Railway auto-deploys on every push to `main`
6. Copy your Railway URL (e.g. `https://football-scout-ai.railway.app`)

### Frontend → GitHub Pages

1. Update `API_BASE` in `docs/app.js` with your Railway URL
2. Go to GitHub repo → Settings → Pages
3. Source: Deploy from branch → `main` → `/docs`
4. GitHub Actions will auto-deploy on every push to `docs/**`

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/research` | Start new research session |
| GET | `/research/{id}/stream` | SSE stream for live progress |
| GET | `/sessions` | List all past sessions |
| GET | `/sessions/{id}` | Get full session + report |
| DELETE | `/sessions/{id}` | Delete a session |
| GET | `/memory` | Get user research history |
| GET | `/health` | Health check |

### POST /research
```json
{
  "query": "Should Arsenal sign Florian Wirtz?",
  "mode": "deep"
}
```
Returns: `{ "session_id": "uuid", "status": "started" }`

### SSE Events
```
status          → progress message string
plan            → { sub_topics, players, report_type }
fbref_complete  → { players_found, players_missing }
topic_complete  → { topic, results_found, data_quality }
fact_base_ready → { conflicts, missing_fields, confidence }
comparison_ready→ { player }
report_drafted  → { length }
verification_result → { status, errors_found, attempt }
report          → { markdown, audit }
error           → { errors }
```

---

## Example Queries

- `Should Arsenal sign Florian Wirtz?`
- `Compare Salah vs Saka this season`
- `Is Pedri worth signing for Manchester City?`
- `Scouting report on Lamine Yamal`
- `Transfer analysis: should Chelsea sign Gavi?`

---

## Key Design Decisions

**Why soccerdata + FBRef over API-Football?**
FBRef provides advanced metrics (xG, xAG, progressive passes, defensive actions) that API-Football's free tier doesn't. These are the metrics real scouts use.

**Why the Fact Base pattern?**
Separating extraction from reasoning prevents hallucination. The report writer can only use numbers present in the Fact Base — verified, reconciled data.

**Why the verification loop?**
LLMs hallucinate numbers. The Fact Checker catches discrepancies before the report ships. Max 2 attempts — ships with an audit warning rather than hanging forever.

**Why SSE over WebSockets?**
Research takes 60-90 seconds. SSE is simpler, unidirectional, and auto-reconnects. WebSockets would be overkill for this use case.

**Why Railway over Render?**
No cold starts. Render free tier sleeps after 15 minutes — a 30-second cold start on top of 90-second research would kill the demo experience.
