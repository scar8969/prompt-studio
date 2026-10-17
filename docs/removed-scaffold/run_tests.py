#!/usr/bin/env python3
"""Run every img-gen test suite and print a combined pass/fail summary.

Usage:
    python run_tests.py            # full suite (API + pipeline + browser UI)
    python run_tests.py --fast     # skip headless-browser suites

Exit code is 0 only when every suite passes, so it works as a CI gate.
Starts/stops the backend and mock provider itself; the Vite dev server is
reused if already listening, otherwise it is started for the browser suites.
"""
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

API = "http://127.0.0.1:8765"
MOCK = "http://127.0.0.1:8790"
VITE = "http://127.0.0.1:5173"

SUITES_API = ["test_e2e.py", "test_pipeline.py", "test_recovery.py"]
SUITES_UI = ["test_ui.cjs", "test_ui_gallery.cjs", "test_ui_conversations.cjs"]


def ping(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def wait_ping(url, seconds=30):
    end = time.time() + seconds
    while time.time() < end:
        if ping(url):
            return True
        time.sleep(0.5)
    return False


def spawn(args, cwd=ROOT):
    flags = 0x00000008  # DETACHED_PROCESS on Windows
    creationflags = flags if os.name == "nt" else 0
    env = dict(os.environ, IMGGEN_ALLOW_PRIVATE_UPSTREAM="1")
    return subprocess.Popen(args, cwd=cwd, env=env, creationflags=creationflags,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_backends():
    procs = []
    if not ping(API + "/api/health"):
        print("starting backend...")
        procs.append(spawn([PY, os.path.join(ROOT, "src", "server.py")]))
        if not wait_ping(API + "/api/health"):
            print("FATAL: backend did not come up")
            sys.exit(2)
    if not ping(MOCK + "/v1"):
        print("starting mock provider...")
        procs.append(spawn([PY, os.path.join(ROOT, "test_mock_provider.py")]))
        wait_ping(MOCK + "/v1")
    return procs


def start_vite():
    if ping(VITE):
        return None
    print("starting vite dev server...")
    npm = subprocess.run(["npm", "run", "dev"], cwd=ROOT, shell=True,
                         creationflags=0x00000008 if os.name == "nt" else 0)
    if not wait_ping(VITE, 60):
        print("FATAL: vite did not come up")
        return None
    return npm


def run(name, cmd):
    print(f"\n--- {name} ---")
    p = subprocess.run(cmd, cwd=ROOT)
    return p.returncode == 0


def main():
    fast = "--fast" in sys.argv
    started = start_backends()
    # UI suites target the backend-served shell (http://127.0.0.1:8765/ui.html),
    # so the Vite dev server is not required for tests.
    vite_proc = None

    results = []
    try:
        for s in SUITES_API:
            results.append((s, run(s, [PY, os.path.join(ROOT, s)])))
        if not fast:
            for s in SUITES_UI:
                results.append((s, run(s, ["node", os.path.join(ROOT, s)])))
    finally:
        for p in started:
            try:
                p.terminate()
            except Exception:
                pass

    print("\n" + "=" * 60)
    print("SUMMARY")
    all_ok = True
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        all_ok = all_ok and ok
    print("=" * 60)
    print("ALL SUITES PASS" if all_ok else "SOME SUITES FAILED")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
