"""Row helpers and small utilities, over `app/db.py` (spec §16).

The schema now lives in `app/migrations.py` and the dialect handling in `app/db.py`.
This module keeps the helpers the rest of the app uses — `row`, `execute`, pagination,
JSON columns — so the services did not have to change when storage moved from a local
SQLite file to Postgres on Vercel.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from . import db as database
from .db import (close, execute, insert_returning_id, rows, row, scalar,  # noqa: F401
                 schema_version)


def utcnow() -> str:
    """An ISO-8601 UTC timestamp.

    Stored as text in SQLite and as TIMESTAMPTZ in Postgres; this string form is what
    the API returns and what the JSON payloads carry, so it stays stable across both.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:20]
    return f"{prefix}{raw}" if prefix else raw


def migrate() -> int:
    return database.migrate()


def init() -> int:
    return database.migrate()


def dialect() -> str:
    return database.dialect()


def health() -> Dict[str, Any]:
    return database.healthy()


def load_json(value: Any, fallback: Any) -> Any:
    """Parse a JSON column without ever raising.

    Unvalidated JSON must not be able to break a request (spec §16), so a corrupt
    column degrades to the fallback rather than a 500.
    """
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return fallback
    return parsed


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def paginate(limit: Any, offset: Any, max_limit: int = 200, default: int = 50) -> tuple:
    """Coerce pagination inputs, refusing nothing but never trusting them."""
    try:
        limit_i = int(limit)
    except (TypeError, ValueError):
        limit_i = default
    try:
        offset_i = int(offset)
    except (TypeError, ValueError):
        offset_i = 0
    if limit_i <= 0:
        limit_i = default
    return min(limit_i, max_limit), max(0, offset_i)


def log_event(job_id: str, from_status: str, to_status: str, detail: str = "") -> None:
    """Append to a job's history. Preserved for every transition (spec §19)."""
    execute(
        "INSERT INTO job_events(job_id, from_status, to_status, detail, created_at) "
        "VALUES(?,?,?,?,?)",
        (job_id, from_status, to_status, detail[:500], utcnow()),
    )
