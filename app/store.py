"""Database, schema and migrations (spec §16).

SQLite with stdlib only. The schema is created by numbered migrations so an existing
database is upgraded in place rather than requiring a reset — `MIGRATIONS` is append
only, and `schema_version` records what has been applied.

Design notes that the spec calls out:

* Credentials live in `connections.secret_encrypted` and are **never** selected into a
  response payload. `public_connection()` is the only serialiser.
* A model's configuration is not an authorization mechanism — capability checks read
  the `model_capabilities` table, which the server controls, never client JSON.
* Large media is not stored in ordinary rows. Bytes go to `uploads/` and the row keeps
  a path plus the metadata needed to serve it safely.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import DB_PATH, ensure_dirs

_local = threading.local()

MIGRATIONS: List[str] = [
    # 1 — foundation: connections, models, capabilities, parameters, projects
    """
    CREATE TABLE connections (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL DEFAULT 'local',
        workspace_id TEXT NOT NULL DEFAULT 'default',
        name TEXT NOT NULL,
        base_url TEXT NOT NULL,
        secret_encrypted TEXT NOT NULL DEFAULT '',
        key_hint TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT 'openai-compatible',
        status TEXT NOT NULL DEFAULT 'untested',
        last_tested_at TEXT,
        last_error TEXT NOT NULL DEFAULT '',
        uses_platform_credentials INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE models (
        id TEXT PRIMARY KEY,
        connection_id TEXT NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
        owner_id TEXT NOT NULL DEFAULT 'local',
        model_id TEXT NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
        modality TEXT NOT NULL DEFAULT 'image',
        enabled INTEGER NOT NULL DEFAULT 1,
        is_default INTEGER NOT NULL DEFAULT 0,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (connection_id, model_id)
    );

    CREATE TABLE model_capabilities (
        model_pk TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
        capability TEXT NOT NULL,
        PRIMARY KEY (model_pk, capability)
    );

    CREATE TABLE model_parameters (
        model_pk TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        default_value TEXT,
        minimum REAL,
        maximum REAL,
        choices_json TEXT NOT NULL DEFAULT '[]',
        required INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (model_pk, name)
    );

    CREATE TABLE projects (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL DEFAULT 'local',
        workspace_id TEXT NOT NULL DEFAULT 'default',
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        settings_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE INDEX idx_connections_owner ON connections(owner_id);
    CREATE INDEX idx_models_connection ON models(connection_id);
    CREATE INDEX idx_models_modality ON models(modality, enabled);
    CREATE INDEX idx_projects_owner ON projects(owner_id);
    """,
    # 2 — media, jobs, outputs
    """
    CREATE TABLE assets (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL DEFAULT 'local',
        project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
        name TEXT NOT NULL,
        media_type TEXT NOT NULL,
        mime TEXT NOT NULL DEFAULT '',
        bytes INTEGER NOT NULL DEFAULT 0,
        width INTEGER,
        height INTEGER,
        duration_ms INTEGER,
        path TEXT NOT NULL DEFAULT '',
        sha256 TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'upload',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    );

    CREATE TABLE generation_jobs (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL DEFAULT 'local',
        project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
        connection_id TEXT REFERENCES connections(id) ON DELETE SET NULL,
        model_pk TEXT REFERENCES models(id) ON DELETE SET NULL,
        kind TEXT NOT NULL,
        prompt TEXT NOT NULL DEFAULT '',
        negative_prompt TEXT NOT NULL DEFAULT '',
        params_json TEXT NOT NULL DEFAULT '{}',
        reference_asset_ids TEXT NOT NULL DEFAULT '[]',
        request_fingerprint TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'queued',
        progress INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        error_code TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        provider_job_id TEXT NOT NULL DEFAULT '',
        submitted_at TEXT,
        started_at TEXT,
        finished_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE outputs (
        id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
        owner_id TEXT NOT NULL DEFAULT 'local',
        project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
        asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
        remote_url TEXT NOT NULL DEFAULT '',
        mime TEXT NOT NULL DEFAULT '',
        width INTEGER,
        height INTEGER,
        duration_ms INTEGER,
        position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE INDEX idx_assets_owner ON assets(owner_id, created_at DESC);
    CREATE INDEX idx_assets_project ON assets(project_id);
    CREATE INDEX idx_jobs_status ON generation_jobs(status, created_at);
    CREATE INDEX idx_jobs_owner ON generation_jobs(owner_id, created_at DESC);
    CREATE INDEX idx_jobs_fingerprint ON generation_jobs(request_fingerprint, created_at DESC);
    CREATE INDEX idx_outputs_job ON outputs(job_id, position);
    """,
    # 3 — creative workspace: presets, saved prompts, characters, usage, job events
    """
    CREATE TABLE presets (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL DEFAULT 'local',
        workspace_id TEXT NOT NULL DEFAULT 'default',
        name TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT '',
        modality TEXT NOT NULL DEFAULT 'image',
        configuration_json TEXT NOT NULL DEFAULT '{}',
        visibility TEXT NOT NULL DEFAULT 'private',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE saved_prompts (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL DEFAULT 'local',
        workspace_id TEXT NOT NULL DEFAULT 'default',
        title TEXT NOT NULL,
        prompt TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        favorite INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE characters (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL DEFAULT 'local',
        workspace_id TEXT NOT NULL DEFAULT 'default',
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE character_references (
        id TEXT PRIMARY KEY,
        character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        role TEXT NOT NULL DEFAULT 'face',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    );

    CREATE TABLE usage_records (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL DEFAULT 'local',
        workspace_id TEXT NOT NULL DEFAULT 'default',
        generation_job_id TEXT REFERENCES generation_jobs(id) ON DELETE SET NULL,
        provider TEXT NOT NULL DEFAULT '',
        model_id TEXT NOT NULL DEFAULT '',
        usage_type TEXT NOT NULL DEFAULT 'generation',
        quantity REAL NOT NULL DEFAULT 1,
        estimated_cost REAL,
        currency TEXT NOT NULL DEFAULT 'USD',
        cost_source TEXT NOT NULL DEFAULT 'unknown',
        created_at TEXT NOT NULL
    );

    CREATE TABLE job_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
        from_status TEXT NOT NULL DEFAULT '',
        to_status TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE INDEX idx_presets_owner ON presets(owner_id, modality);
    CREATE INDEX idx_saved_prompts_owner ON saved_prompts(owner_id, updated_at DESC);
    CREATE INDEX idx_characters_owner ON characters(owner_id);
    CREATE INDEX idx_usage_job ON usage_records(generation_job_id);
    CREATE INDEX idx_usage_owner ON usage_records(owner_id, created_at DESC);
    CREATE INDEX idx_job_events_job ON job_events(job_id, id);
    """,
]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:20]
    return f"{prefix}{raw}" if prefix else raw


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    """A connection for the current thread, created on first use."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        ensure_dirs()
        conn = sqlite3.connect(str(path or DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return conn


def db() -> sqlite3.Connection:
    return connect()


def schema_version(conn: Optional[sqlite3.Connection] = None) -> int:
    conn = conn or db()
    conn.execute("CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    return int(row["value"]) if row else 0


def migrate(conn: Optional[sqlite3.Connection] = None) -> int:
    """Apply every migration not yet recorded. Returns the resulting version."""
    conn = conn or db()
    current = schema_version(conn)
    for index, script in enumerate(MIGRATIONS, start=1):
        if index <= current:
            continue
        conn.executescript(script)
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(index),),
        )
        conn.commit()
        current = index
    return current


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------

def rows(sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    return [dict(r) for r in db().execute(sql, tuple(params)).fetchall()]


def row(sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    found = db().execute(sql, tuple(params)).fetchone()
    return dict(found) if found else None


def scalar(sql: str, params: Iterable[Any] = ()) -> Any:
    found = db().execute(sql, tuple(params)).fetchone()
    return found[0] if found else None


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    conn = db()
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur


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


def init() -> int:
    return migrate()
