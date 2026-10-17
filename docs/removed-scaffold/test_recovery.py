#!/usr/bin/env python3
"""Verifies crash recovery: a job left 'running' by a dead server is requeued
and completed by the worker of a freshly started server."""
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = "http://127.0.0.1:8765"
FLAGS = 0x00000008 if sys.platform == "win32" else 0

ok = True


def check(name, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)[:200]))


def start_server():
    env = dict(os.environ, IMGGEN_ALLOW_PRIVATE_UPSTREAM="1")
    return subprocess.Popen([sys.executable, str(ROOT / "src" / "server.py")], cwd=ROOT, env=env,
                            creationflags=FLAGS, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_mock():
    env = dict(os.environ, IMGGEN_ALLOW_PRIVATE_UPSTREAM="1")
    return subprocess.Popen([sys.executable, str(ROOT / "test_mock_provider.py")], cwd=ROOT, env=env,
                            creationflags=FLAGS, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_up(url, seconds=30):
    end = time.time() + seconds
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(r, timeout=10).read() or "{}")
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or "{}")


# ---- bring up a server, seed data, inject a stuck job --------------------
srv = start_server()
wait_up(API + "/api/health")
mock = start_mock()
wait_up("http://127.0.0.1:8790/v1")

proj = req("POST", "/api/projects", {"name": "Recovery Test"})
preset = req("POST", "/api/presets", {
    "name": "Recovery Preset",
    "image_provider": "mock", "image_url": "http://127.0.0.1:8790/v1",
    "image_key": "k", "image_model": "mock-image",
    "logic_provider": "mock", "logic_url": "http://127.0.0.1:8790/v1",
    "logic_key": "k", "logic_model": "mock-logic"})
req("PUT", "/api/projects/" + proj["id"], {"name": "Recovery Test", "description": "",
    "instructions": "", "active_preset_id": preset["id"]})

db = sqlite3.connect(ROOT / "data" / "img-gen.db")
jid = str(uuid.uuid4())
db.execute(
    "INSERT INTO generation_jobs (id, project_id, preset_id, prompt, parameters_json, status, image_model) "
    "VALUES (?,?,?,?,?,?,?)",
    (jid, proj["id"], preset["id"], "recovery test",
     json.dumps({"aspectRatio": "1:1", "count": 1, "size": "1024x1024"}), "running", "mock-image"))
db.commit()
db.close()
print("seeded stuck running job", jid[:8])

# ---- simulate a crash: hard-kill, then restart ---------------------------
srv.kill()
mock.kill()
time.sleep(2)

srv2 = start_server()
check("server restarted", wait_up(API + "/api/health"))
mock2 = start_mock()
check("mock provider restarted", wait_up("http://127.0.0.1:8790/v1"))

# ---- worker should have requeued and completed the job -------------------
completed = False
job = None
end = time.time() + 30
while time.time() < end:
    try:
        jobs = req("GET", "/api/jobs/" + proj["id"])
    except Exception:
        time.sleep(1)
        continue
    job = next((j for j in jobs if j["id"] == jid), None)
    if job and job["status"] == "completed":
        completed = True
        break
    time.sleep(1)

check("stuck running job recovered and completed", completed, job)
gal = req("GET", "/api/gallery/" + proj["id"])
check("recovered job produced its image", isinstance(gal, list) and len(gal) == 1, len(gal) if isinstance(gal, list) else gal)

req("DELETE", "/api/projects/" + proj["id"])
req("DELETE", "/api/presets/" + preset["id"])
srv2.kill()
mock2.kill()
print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
