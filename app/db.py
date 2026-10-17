"""Database access, for both SQLite locally and Postgres on Vercel.

The app is written against this module, not against `sqlite3`, so the same code runs
in both places. Two things make that honest rather than approximate:

* **Dialect handling is explicit.** SQLite spells placeholders `?` and Postgres `%s`;
  every call goes through `_sql()` which rewrites one to the other. Without this a
  query that works locally fails in production with a syntax error nobody tested for.
* **`RETURNING` and `AUTOINCREMENT` differ.** Where they did, the query is written for
  both and the test suite runs against SQLite while the Postgres path is exercised by
  a contract test that asserts the rewritten SQL.

Why not keep SQLite on Vercel: the filesystem is per-instance and ephemeral, so a
write on one invocation is gone by the next — every job and asset would vanish. That is
the whole reason this module exists.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any, Dict, Iterable, List, Optional

from .config import DB_PATH, ensure_dirs

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
IS_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

_local = threading.local()

MIGRATION_FILES = "schema"


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------

def _pg_connect():
    """A Postgres connection. Imported lazily so local runs never need the driver."""
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    conn.autocommit = False
    return conn


def connect():
    """A connection for the current thread. Local SQLite, or Postgres when configured."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn
    if IS_POSTGRES:
        conn = _pg_connect()
    else:
        ensure_dirs()
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
    _local.conn = conn
    return conn


def db():
    return connect()


def close() -> None:
    """Drop this thread's connection. Important on serverless, where a frozen process
    holding an open connection is how a pool runs out."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


# ---------------------------------------------------------------------------
# SQL translation
# ---------------------------------------------------------------------------

def _sql(query: str) -> str:
    """`?` placeholders -> `%s` for Postgres.

    Literal question marks inside strings would be mangled by a blind replace, so the
    rewrite skips quoted text. The queries in this app never contain one, and this
    guard means a future one cannot silently corrupt a query.
    """
    if not IS_POSTGRES:
        return query
    out = []
    in_string = False
    for char in query:
        if char == "'":
            in_string = not in_string
            out.append(char)
        elif char == "?" and not in_string:
            out.append("%s")
        else:
            out.append(char)
    return "".join(out)


def _execute(cursor, query: str, params: Iterable[Any] = ()):
    if IS_POSTGRES:
        import psycopg2.extras

        cursor.execute(_sql(query), tuple(params))
        if cursor.description:
            return [dict(row) for row in cursor.fetchall()]
        # Asking for a dict cursor up front is not needed: `.description` is None for
        # a statement that returns no rows, and that is exactly the distinction used.
        return []
    cursor.execute(query, tuple(params))
    if cursor.description:
        return [dict(r) for r in cursor.fetchall()]
    return []


def rows(query: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    conn = db()
    cursor = conn.cursor()
    try:
        return _execute(cursor, query, params)
    finally:
        cursor.close()


def row(query: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    found = rows(query, params)
    return found[0] if found else None


def scalar(query: str, params: Iterable[Any] = ()) -> Any:
    found = rows(query, params)
    if not found:
        return None
    return next(iter(found[0].values()))


def execute(query: str, params: Iterable[Any] = ()) -> None:
    conn = db()
    cursor = conn.cursor()
    try:
        cursor.execute(_sql(query), tuple(params))
        conn.commit()
    finally:
        cursor.close()


def insert_returning_id(query: str, params: Iterable[Any] = ()) -> Any:
    """Insert and return the new id, for a table with an auto-increment key.

    SQLite exposes `lastrowid`; Postgres needs `RETURNING id`. Both are handled here so
    no caller has to care.
    """
    conn = db()
    cursor = conn.cursor()
    try:
        if IS_POSTGRES:
            cursor.execute(_sql(query) + " RETURNING id", tuple(params))
            new_id = cursor.fetchone()[0]
        else:
            cursor.execute(query, tuple(params))
            new_id = cursor.lastrowid
        conn.commit()
        return new_id
    finally:
        cursor.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def schema_version() -> int:
    conn = db()
    cursor = conn.cursor()
    try:
        if IS_POSTGRES:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            cursor.execute("SELECT value FROM schema_meta WHERE key='schema_version'")
            found = cursor.fetchone()
            conn.commit()
            return int(found[0]) if found else 0
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        found = cursor.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        conn.commit()
        return int(found["value"]) if found else 0
    finally:
        cursor.close()


def migrate() -> int:
    """Apply every migration not yet recorded.

    The DDL is written once in Postgres-compatible SQL and adapted for SQLite, because
    the two differ in ways that matter (`SERIAL` vs `INTEGER PRIMARY KEY`,
    `TIMESTAMPTZ` vs `TEXT`). `_adapt` is the single place that difference lives.
    """
    from . import migrations

    conn = db()
    current = schema_version()
    for index, script in enumerate(migrations.SCRIPTS, start=1):
        if index <= current:
            continue
        for statement in _split(script):
            conn.cursor().execute(statement)
        conn.commit()
        execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(index),))
        current = index
    return current


def _split(script: str) -> List[str]:
    """Split a migration into statements, then adapt each for the dialect."""
    statements = []
    for chunk in script.split(";\n"):
        cleaned = chunk.strip().rstrip(";").strip()
        if cleaned and not cleaned.startswith("--"):
            statements.append(_adapt(cleaned))
    return statements


def _adapt(statement: str) -> str:
    """Turn portable DDL into dialect-specific DDL."""
    if IS_POSTGRES:
        return statement.replace("{{SERIAL}}", "SERIAL")
    statement = statement.replace("{{SERIAL}}", "INTEGER")
    # SQLite has no TIMESTAMPTZ and no timezone-aware default; ISO strings are stored.
    statement = statement.replace("TIMESTAMPTZ", "TEXT")
    statement = statement.replace("DEFAULT NOW()", "DEFAULT (datetime('now'))")
    return statement


def init() -> int:
    return migrate()


def dialect() -> str:
    return "postgres" if IS_POSTGRES else "sqlite"


def healthy() -> Dict[str, Any]:
    """A cheap round trip, used by the health endpoint on a serverless platform."""
    try:
        return {"ok": True, "dialect": dialect(), "schema_version": schema_version()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "dialect": dialect(), "error": type(exc).__name__}
