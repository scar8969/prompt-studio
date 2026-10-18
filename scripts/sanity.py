"""A sanity pass over a running server: does it actually work, end to end?

Not a unit test. This starts nothing and asserts nothing about internals — it talks to a
live process over HTTP the way a client does, and prints what it found. Run it after any
change you intend to deploy:

    python scripts/sanity.py                     # against http://127.0.0.1:8765
    python scripts/sanity.py --base http://...   # against a deployment

Exit code is the number of failures, so `python scripts/sanity.py && deploy` is safe.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

PASS, FAIL = "PASS", "FAIL"
failures = 0


def call(base: str, method: str, path: str, body=None, timeout: float = 30.0):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            try:
                return response.status, json.loads(raw), raw
            except ValueError:
                return response.status, None, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw), raw
        except ValueError:
            return exc.code, None, raw
    except Exception as exc:                                    # noqa: BLE001
        return 0, {"transport_error": str(exc)}, b""


def check(label: str, condition, detail: str = "") -> None:
    global failures
    if condition:
        print(f"  {PASS}  {label}")
    else:
        failures += 1
        print(f"  {FAIL}  {label}" + (f"  — {detail}" if detail else ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    base = args.base

    print(f"\nPrompt Studio sanity — {base}\n")

    # ---- 1. the service answers at all
    print("1. service")
    code, health, _ = call(base, "GET", "/api/health")
    check("health responds 200", code == 200, f"got {code}")
    if not health:
        print("\n  cannot continue without a health response\n")
        return max(1, failures)
    check("health reports a status", health.get("status") in ("ok", "degraded"),
          str(health.get("status")))
    check("health lists adapters", bool(health.get("adapters")))
    counts = health.get("counts") or {}

    # ---- 2. the page and its assets are served
    print("\n2. the page")
    code, _, raw = call(base, "GET", "/")
    check("page responds 200", code == 200, f"got {code}")
    check("page is HTML", b"<html" in raw.lower() or b"<!doctype" in raw.lower())
    check("page is the studio", b"Prompt Studio" in raw)
    for asset in ("/studio.css", "/studio.js"):
        code, _, raw = call(base, "GET", asset)
        check(f"{asset} serves bytes", code == 200 and len(raw) > 1000,
              f"{code}, {len(raw)} bytes")
    # A query string must not break the page.
    code, _, _ = call(base, "GET", "/?sanity=1")
    check("page survives a query string", code == 200, f"got {code}")

    # ---- 3. the catalogue: 58 models, honest about configuration
    print("\n3. the model catalogue")
    code, catalog, _ = call(base, "GET", "/api/catalog")
    check("catalog responds 200", code == 200, f"got {code}")
    models = (catalog or {}).get("data") or []
    check("catalog lists 59 models", len(models) == 59, f"got {len(models)}")
    summary = (catalog or {}).get("summary") or {}
    by_modality = summary.get("by_modality") or {}
    check("24 image models", by_modality.get("image") == 24, str(by_modality))
    check("25 video models", by_modality.get("video") == 25, str(by_modality))
    check("5 3D models", by_modality.get("3d") == 5, str(by_modality))
    check("5 audio models", by_modality.get("audio") == 5, str(by_modality))
    with_ids = [m for m in models if m.get("job_set_type")]
    check("every model carries a job_set_type", len(with_ids) == len(models))
    # An unconfigured model must say so rather than pretend.
    for model in models:
        if not model.get("configured"):
            check(f"{model['label']} names its missing variable",
                  bool(model.get("missing")), "no `missing` list")
            break
    check("no model invents a key", all("api_key" not in m for m in models))

    # ---- 4. the model credential path
    print()
    print("4. configured models")
    code, providers, _ = call(base, "GET", "/api/providers")
    check("providers endpoint responds", code == 200, f"got {code}")
    code, listed, _ = call(base, "GET", "/api/models")
    check("models endpoint responds", code == 200, f"got {code}")
    configured = (listed or {}).get("data") or []

    # A model exists only because the environment configured it. Nothing configured is a
    # legitimate state, not a failure: the deployment simply cannot generate yet.
    if not configured:
        print("  NOTE  no models are configured here — set <PREFIX>_API_KEY, "
              "<PREFIX>_MODEL and <PREFIX>_API_URL to enable one")
    for model in configured:
        check(f"{model['display_name']} has an endpoint", bool(model.get("api_url")),
              "no api_url recorded")
        # The built-in demo provider is not a catalogue model, so it has no env prefix
        # by design; every real model must name the variables that configure it.
        if model.get("provider") != "Demo" and model["provider"] != "mock":
            check(f"{model['display_name']} names its env prefix",
                  bool(model.get("env_prefix")), "no env_prefix")
    check("no response exposes a credential", "api_key" not in str(listed).lower())

    # ---- 5. the removed BYOK endpoints stay removed
    print()
    print("5. BYOK is gone")
    for method, path in (("GET", "/api/connections"), ("POST", "/api/connections"),
                         ("DELETE", "/api/connections/x")):
        check(f"{method} {path} is not a route",
              call(base, method, path, {} if method != "DELETE" else None)[0]
              in (404, 405))

    # ---- 6. the error vocabulary (a bad request is answered, not fatal)
    print("\n6. errors are answered, not raised")
    code, body, _ = call(base, "GET", "/api/jobs/does_not_exist")
    check("an unknown job is a 404", code == 404, f"got {code}")
    check("the 404 carries an error code",
          bool(((body or {}).get("error") or {}).get("code")), str(body)[:120])
    code, body, _ = call(base, "POST", "/api/connections", "not-an-object")
    check("a malformed body is a 4xx", 400 <= code < 500, f"got {code}")
    code, body, _ = call(base, "POST", "/api/generations/images", {})
    check("a missing field is a 400", code == 400, f"got {code}")
    check("still alive after bad input", call(base, "GET", "/api/health")[0] == 200)

    # ---- 7. source is not downloadable
    print("\n7. nothing private is served")
    for path in ("/app/config.py", "/app/catalog.py", "/data/studio.db",
                 "/requirements.txt", "/.env"):
        code, _, _ = call(base, "GET", path)
        check(f"{path} is not served", code in (403, 404), f"got {code}")

    total = 0
    print()
    if failures:
        print(f"  {failures} check(s) failed\n")
    else:
        print("  all checks passed\n")
    return min(failures, 120)


if __name__ == "__main__":
    sys.exit(main())
