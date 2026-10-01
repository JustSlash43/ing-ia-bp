"""Consumo acumulado por sesión en SQLite (mismo archivo que los checkpoints de LangGraph).

Implementa los puertos GetSessionUsage / ChargeSession.
"""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS session_usage (
    session_id  TEXT PRIMARY KEY,
    analyst_id  TEXT NOT NULL,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    cost_usd    REAL NOT NULL DEFAULT 0,
    queries     INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL
)"""

EMPTY = {"tokens_used": 0, "cost_usd": 0.0, "queries": 0}


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    return conn


def get_usage(path: str, session_id: str) -> dict:
    with closing(connect(path)) as conn:
        row = conn.execute("SELECT tokens_used, cost_usd, queries FROM session_usage WHERE session_id = ?",
                           (session_id,)).fetchone()
    return dict(row) if row else dict(EMPTY)


def charge(path: str, session_id: str, analyst_id: str, tokens: int, cost: float) -> dict:
    with closing(connect(path)) as conn, conn:
        conn.execute(
            "INSERT INTO session_usage (session_id, analyst_id, tokens_used, cost_usd, queries, updated_at) "
            "VALUES (?, ?, ?, ?, 1, ?) ON CONFLICT(session_id) DO UPDATE SET "
            "tokens_used = tokens_used + excluded.tokens_used, cost_usd = cost_usd + excluded.cost_usd, "
            "queries = queries + 1, updated_at = excluded.updated_at",
            (session_id, analyst_id, tokens, cost, datetime.now(UTC).isoformat()),
        )
    return get_usage(path, session_id)
