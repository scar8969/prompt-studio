"""Shared fixtures: an isolated database and a live server per test session."""

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Configure before importing the app: config reads the environment at import time.
_TMP = tempfile.mkdtemp(prefix="studio-test-")
os.environ.setdefault("DATA_DIR", str(Path(_TMP) / "data"))
os.environ.setdefault("UPLOAD_DIR", str(Path(_TMP) / "uploads"))
os.environ.setdefault("APP_ENCRYPTION_KEY", "test-key-not-a-secret")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("JOB_WORKERS", "2")

#: Point this at a scratch Postgres to run the same suite on the production engine:
#:     TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5433/promptstudio pytest
#: It is wiped at session start, so it must be a throwaway database. This is how the
#: psycopg2 row-mapping bug was caught — SQLite passed while every Postgres endpoint
#: returned 500.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
if TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL


@pytest.fixture(scope="session", autouse=True)
def _schema():
    from app import store

    if TEST_DATABASE_URL:
        _wipe_postgres()
    store.init()
    yield


def _wipe_postgres() -> None:
    """Drop every table in the scratch database so each session starts empty.

    Without this, a previous run's rows are still there, and the duplicate guards and
    "the list is empty" assertions fail for a reason that has nothing to do with the code.
    """
    from app import db

    conn = db.db()
    cursor = conn.cursor()
    cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    tables = [row[0] for row in cursor.fetchall()]
    if tables:
        names = ", ".join(f'"{table}"' for table in tables)
        cursor.execute(f"DROP TABLE IF EXISTS {names} CASCADE")
        conn.commit()
    cursor.close()


@pytest.fixture(scope="session")
def dialect():
    """Which engine this run is using — so a test can assert dialect-specific behaviour."""
    from app import db

    return db.dialect()


@pytest.fixture(scope="session")
def server():
    """A real server on an ephemeral port, so tests exercise the deployed shape."""
    from app.web.server import build_server

    # build_server, not a bare ThreadingHTTPServer: it also starts the job workers, and a
    # server whose queue has no workers accepts jobs that never run.
    httpd = build_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


class Client:
    """Tiny HTTP client that returns (status, body) instead of raising."""

    def __init__(self, base):
        self.base = base

    def call(self, method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                try:
                    return resp.status, json.loads(raw.decode() or "{}")
                except ValueError:
                    return resp.status, {"raw": raw}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw.decode() or "{}")
            except ValueError:
                return exc.code, {"raw": raw}

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, payload=None):
        return self.call("POST", path, payload or {})

    def patch(self, path, payload=None):
        return self.call("PATCH", path, payload or {})

    def delete(self, path):
        return self.call("DELETE", path)


@pytest.fixture()
def client(server):
    return Client(server)


@pytest.fixture()
def mock_connection(client):
    """A fresh mock connection with its two models registered.

    A mock connection's base URL is a fixed constant, and the API (correctly) refuses a
    second connection to the same URL — so this test's connection is given a unique
    name and the duplicate is deleted first, keeping each test independent without
    weakening the guard being tested.
    """
    import uuid

    # Reuse any existing mock connection, or make one.
    code, listing = client.get("/api/connections")
    existing = [c for c in listing.get("data", []) if c["kind"] == "mock"]
    if existing:
        conn_id = existing[0]["id"]
        code, body = client.post(f"/api/connections/{conn_id}/models",
                                 {"models": ["demo-image-1", "demo-video-1"]})
        assert code == 201, body
    else:
        code, body = client.post("/api/connections", {
            "name": f"Test mock {uuid.uuid4().hex[:6]}", "kind": "mock",
            "base_url": "mock://offline"})
        assert code == 201, body
        conn_id = body["data"]["id"]
        code, body = client.post(f"/api/connections/{conn_id}/models",
                                 {"models": ["demo-image-1", "demo-video-1"]})
        assert code == 201, body

    # The registry is the source of truth for registered models.
    code, body = client.get(f"/api/models?include_disabled=true")
    models = {m["model_id"]: m for m in body.get("data", [])
              if m["connection_id"] == conn_id}
    if not models:
        code, body = client.post(f"/api/connections/{conn_id}/models",
                                 {"models": ["demo-image-1", "demo-video-1"]})
        assert code == 201, body
        models = {m["model_id"]: m for m in body["data"]}
    return {"connection_id": conn_id, "models": models,
            "image_pk": models["demo-image-1"]["id"],
            "video_pk": models["demo-video-1"]["id"]}


@pytest.fixture()
def wait_for_job(client):
    """Poll a job until it reaches a terminal state."""
    import time

    def _wait(job_id, timeout=30):
        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            code, body = client.get(f"/api/jobs/{job_id}")
            last = body.get("data", {})
            if last.get("status") in ("succeeded", "failed", "cancelled"):
                return last
            time.sleep(0.2)
        return last

    return _wait
