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
# Credentials are environment variables now, so a developer's shell can change what the
# app sees. The suite must not depend on that: any <PREFIX>_API_KEY / _API_URL / _MODEL
# from the ambient environment is cleared, and tests that need one set it explicitly with
# monkeypatch. Without this a stray export turns a passing run into an intermittent one.
for _name in [k for k in os.environ
              if k.endswith(("_API_KEY", "_API_URL", "_MODEL"))
              and k.split("_")[0] not in ("RELAYAI", "HERMES", "COMPOSIO")]:
    os.environ.pop(_name, None)

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
def mock_connection():
    """A mock provider with its two demo models, seeded straight into the registry.

    Credentials are environment-only now, so there is no API call that creates a
    connection and no way to POST a model. The demo provider is not in the 59-model
    catalogue either (it is not a real Higgsfield model), so this writes the rows
    directly — which is what `services.sync` does for a real model, minus the env read.
    """
    from app import store
    from app.services import connections as connection_service

    provider = store.row(
        "SELECT * FROM connections WHERE owner_id='local' AND base_url='mock://offline'")
    if not provider:
        provider_id = store.new_id("prov_")
        now = store.utcnow()
        store.execute(
            "INSERT INTO connections (id, owner_id, workspace_id, name, kind, base_url, "
            "secret_encrypted, key_hint, status, last_error, uses_platform_credentials, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,'','','env','',1,?,?)",
            (provider_id, "local", "default", "Demo", "mock", "mock://offline", now, now))
        provider = store.row("SELECT * FROM connections WHERE id=?", (provider_id,))

    models = {}
    for model_id, modality, capabilities in (
            ("demo-image-1", "image", ["image.generate"]),
            ("demo-video-1", "video", ["video.generate", "video.async"])):
        existing = store.row(
            "SELECT * FROM models WHERE owner_id='local' AND connection_id=? AND model_id=?",
            (provider["id"], model_id))
        if existing:
            store.execute("UPDATE models SET enabled=1 WHERE id=?", (existing["id"],))
            model_pk = existing["id"]
        else:
            model_pk = store.new_id("mdl_")
            now = store.utcnow()
            store.execute(
                "INSERT INTO models (id, connection_id, owner_id, model_id, display_name, "
                "modality, enabled, is_default, metadata_json, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,1,0,?,?,?)",
                (model_pk, provider["id"], "local", model_id, model_id, modality,
                 store.dumps({"source": "demo"}), now, now))
        if model_id == "demo-image-1":
            # The demo adapter produces N images when asked, so the parameter is real
            # for this model — capability gating refuses anything it does not document.
            store.execute("DELETE FROM model_parameters WHERE model_pk=?", (model_pk,))
            store.execute(
                "INSERT INTO model_parameters (model_pk, name, type, default_value, "
                "minimum, maximum, required, choices_json) VALUES (?,?,?,?,?,?,?,?)",
                (model_pk, "count", "integer", "1", "1", "4", 0, store.dumps([])))

        # Applied whether the row was created now or reused: a stale row from an earlier
        # run would otherwise keep an out-of-date capability set.
        store.execute("DELETE FROM model_capabilities WHERE model_pk=?", (model_pk,))
        for capability in capabilities:
            store.execute(
                "INSERT INTO model_capabilities (model_pk, capability) VALUES (?,?) "
                "ON CONFLICT (model_pk, capability) DO NOTHING", (model_pk, capability))
        models[model_id] = connection_service.public_model(
            store.row("SELECT * FROM models WHERE id=?", (model_pk,)))

    return {"connection_id": provider["id"], "models": models,
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
