"""
Database module for img-gen — SQLite with projects, presets, assets, jobs, gallery.
"""
import os
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "img-gen.db"

_local = threading.local()


def get_db() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _local.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            active_preset_id TEXT DEFAULT '',
            instructions TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS presets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            image_provider TEXT DEFAULT '',
            image_url TEXT DEFAULT '',
            image_key TEXT DEFAULT '',
            image_model TEXT DEFAULT '',
            default_aspect_ratio TEXT DEFAULT '1:1',
            default_resolution TEXT DEFAULT '1024x1024',
            default_count INTEGER DEFAULT 1,
            default_seed TEXT DEFAULT '',
            default_negative_prompt TEXT DEFAULT '',
            default_quality TEXT DEFAULT '',
            default_style TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS assets (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            file_type TEXT DEFAULT '',
            size INTEGER DEFAULT 0,
            description TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            data BLOB DEFAULT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS generation_jobs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            preset_id TEXT DEFAULT '',
            prompt TEXT NOT NULL,
            negative_prompt TEXT DEFAULT '',
            assets_json TEXT DEFAULT '[]',
            parameters_json TEXT DEFAULT '{}',
            status TEXT DEFAULT 'queued',
            error TEXT DEFAULT '',
            image_model TEXT DEFAULT '',
            image_provider TEXT DEFAULT '',
            image_url TEXT DEFAULT '',
            result_json TEXT DEFAULT '',
            attempts INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS generated_images (
            id TEXT PRIMARY KEY,
            job_id TEXT DEFAULT '',
            project_id TEXT NOT NULL,
            preset_id TEXT DEFAULT '',
            prompt TEXT DEFAULT '',
            image_data TEXT DEFAULT '',
            image_url TEXT DEFAULT '',
            parameters_json TEXT DEFAULT '{}',
            image_model TEXT DEFAULT '',
            status TEXT DEFAULT 'completed',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_assets_project ON assets(project_id);
        CREATE INDEX IF NOT EXISTS idx_jobs_project ON generation_jobs(project_id);
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON generation_jobs(status);
        CREATE INDEX IF NOT EXISTS idx_images_project ON generated_images(project_id);
        CREATE INDEX IF NOT EXISTS idx_images_job ON generated_images(job_id);
    """)
    db.commit()
    _migrate(db)


def _migrate(db):
    """Add columns introduced after the initial schema."""
    def add(table, column, decl):
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in cols:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    add("generation_jobs", "attempts", "INTEGER DEFAULT 0")
    add("generation_jobs", "result_json", "TEXT DEFAULT ''")
    db.commit()


def dict_from_row(row):
    if row is None:
        return None
    return dict(row)


def dicts_from_rows(rows):
    return [dict(r) for r in rows]
