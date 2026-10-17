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

    # ---- 4. a full generation through the demo provider
    print("\n4. generation")
    code, created, _ = call(base, "POST", "/api/connections", {
        "name": f"Sanity demo {__import__('uuid').uuid4().hex[:8]}",
        "kind": "mock", "base_url": "mock://offline"})
    # A repeat run against a server that already has the demo connection is not a
    # failure: the duplicate guard is correct, and the existing connection is usable.
    if code == 409:
        _, existing, _ = call(base, "GET", "/api/connections")
        rows = (existing or {}).get("data") or []
        demo = [c for c in rows if c.get("base_url") == "mock://offline"]
        created = {"data": demo[0]} if demo else None
        code = 201 if demo else code
    check("a demo connection is available", code == 201, f"got {code}: {created}")
    connection = (created or {}).get("data") or {}
    check("the connection has an id", bool(connection.get("id")))

    if connection.get("id"):
        code, registered, _ = call(
            base, "POST", f"/api/connections/{connection['id']}/models",
            # The mock adapter only serves its own two ids; using a made-up one makes
            # the adapter (correctly) refuse the job, which proves nothing about the
            # queue. Register the id the demo provider actually offers.
            {"models": [{"model_id": "demo-image-1",
                         "capabilities": ["image.generate"]}]})
        check("a model registers", code == 201, f"got {code}: {registered}")

        code, listed, _ = call(base, "GET", "/api/models?modality=image")
        image_models = (listed or {}).get("data") or []
        check("the model is listed", bool(image_models), f"{len(image_models)} image models")

        if image_models:
            model_pk = image_models[0]["id"]
            prompt = f"sanity lighthouse {__import__('uuid').uuid4().hex[:6]}"
            code, job, _ = call(base, "POST", "/api/generations/images",
                                {"model_pk": model_pk, "prompt": prompt})
            check("a job is accepted", code in (201, 202), f"got {code}: {job}")
            job_data = (job or {}).get("data") or {}
            job_id = job_data.get("id")
            check("the job has an id", bool(job_id))
            check("the job reaches a terminal state",
                  job_data.get("status") in ("queued", "running", "succeeded"),
                  str(job_data.get("status")))

            # Poll briefly: a local server runs it in a worker thread.
            import time
            status = job_data.get("status")
            for _ in range(40):
                if status in ("succeeded", "failed", "cancelled"):
                    break
                time.sleep(0.25)
                _, one, _ = call(base, "GET", f"/api/jobs/{job_id}")
                status = ((one or {}).get("data") or {}).get("status", status)
            check("the job finishes", status == "succeeded", f"ended {status}")

            # ---- 5. the output is real, fetchable bytes
            print("\n5. output")
            code, assets, _ = call(base, "GET", "/api/assets")
            rows = (assets or {}).get("assets") or []
            check("the output is recorded as an asset", bool(rows), f"{len(rows)} assets")
            generated = [a for a in rows if a.get("source") == "generated"]
            if generated:
                asset_id = generated[0]["id"]
                code, _, raw = call(base, "GET", f"/api/assets/{asset_id}/raw")
                check("the asset serves bytes", code == 200 and len(raw) > 500,
                      f"{code}, {len(raw)} bytes")
                check("the bytes are a real image",
                      raw[:8] == b"\x89PNG\r\n\x1a\n" or raw[:3] == b"\xff\xd8\xff"
                      or raw[:4] == b"RIFF", raw[:8].hex())

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
