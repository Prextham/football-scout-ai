import aiosqlite
import json
import os
from datetime import datetime
from app.config import settings


def _db_path() -> str:
    path = os.path.abspath(settings.database_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


async def init_db():
    """Create tables if they don't exist."""
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                query TEXT NOT NULL,
                status TEXT DEFAULT 'planning',
                mode TEXT DEFAULT 'deep',
                sub_topics TEXT,
                fact_base TEXT,
                final_report TEXT,
                verification_status TEXT,
                data_audit TEXT,
                errors TEXT,
                created_at TEXT,
                completed_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player TEXT,
                team TEXT,
                query TEXT,
                queried_at TEXT
            )
        """)
        await db.commit()


async def create_session(session_id: str, query: str, mode: str):
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """INSERT INTO sessions (id, query, status, mode, created_at)
               VALUES (?, ?, 'planning', ?, ?)""",
            (session_id, query, mode, datetime.utcnow().isoformat())
        )
        await db.commit()


async def update_session(session_id: str, **kwargs):
    if not kwargs:
        return
    for k, v in kwargs.items():
        if isinstance(v, (dict, list)):
            kwargs[k] = json.dumps(v)
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [session_id]
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(f"UPDATE sessions SET {fields} WHERE id = ?", values)
        await db.commit()


async def complete_session(session_id: str, final_report: str, verification_status: str, data_audit: dict):
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """UPDATE sessions
               SET status='complete', final_report=?, verification_status=?,
                   data_audit=?, completed_at=?
               WHERE id=?""",
            (final_report, verification_status, json.dumps(data_audit),
             datetime.utcnow().isoformat(), session_id)
        )
        await db.commit()


async def fail_session(session_id: str, errors: list):
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "UPDATE sessions SET status='error', errors=? WHERE id=?",
            (json.dumps(errors), session_id)
        )
        await db.commit()


async def get_session(session_id: str) -> dict | None:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            d = dict(row)
            for field in ["sub_topics", "fact_base", "data_audit", "errors"]:
                if d.get(field):
                    try:
                        d[field] = json.loads(d[field])
                    except Exception:
                        pass
            return d


async def list_sessions(limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, query, status, mode, created_at, completed_at FROM sessions ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def save_memory(players: list, teams: list, query: str):
    async with aiosqlite.connect(_db_path()) as db:
        now = datetime.utcnow().isoformat()
        for player in players:
            await db.execute(
                "INSERT INTO user_memory (player, query, queried_at) VALUES (?, ?, ?)",
                (player, query, now)
            )
        for team in teams:
            await db.execute(
                "INSERT INTO user_memory (team, query, queried_at) VALUES (?, ?, ?)",
                (team, query, now)
            )
        await db.commit()


async def get_user_memory() -> dict:
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(
            "SELECT player, COUNT(*) as count FROM user_memory WHERE player IS NOT NULL GROUP BY player ORDER BY count DESC LIMIT 5"
        ) as cur:
            players = [{"player": r[0], "count": r[1]} for r in await cur.fetchall()]
        async with db.execute(
            "SELECT team, COUNT(*) as count FROM user_memory WHERE team IS NOT NULL GROUP BY team ORDER BY count DESC LIMIT 5"
        ) as cur:
            teams = [{"team": r[0], "count": r[1]} for r in await cur.fetchall()]
        return {"frequent_players": players, "frequent_teams": teams}