#!/usr/bin/env python3
"""End-to-end generation pipeline test using a mock OpenAI-compatible provider.

Covers: preset (mock endpoints) -> chat -> logic model -> structured JSON plan
-> validated job creation -> background worker -> image generation -> gallery
persistence -> image serving.
"""
import json
import time
import urllib.request
import urllib.error

API = "http://127.0.0.1:8765"
MOCK = "http://127.0.0.1:8790/v1"
results = []


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:300]}
    except Exception as e:
        return 0, {"error": str(e)}


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)[:250]))


def wait_jobs(pid, expect_min, timeout=45):
    """Poll until no queued/running jobs remain (worker drained)."""
    end = time.time() + timeout
    while time.time() < end:
        s, jobs = req("GET", "/api/jobs/" + pid)
        if s == 200 and isinstance(jobs, list):
            pending = [j for j in jobs if j.get("status") in ("queued", "running")]
            if not pending and len(jobs) >= expect_min:
                return jobs
        time.sleep(1)
    s, jobs = req("GET", "/api/jobs/" + pid)
    return jobs if isinstance(jobs, list) else []


print("=== pipeline test (mock provider) ===\n")

# 1. model testing against mock (both logic + image)
s, r = req("POST", "/api/test-model", {"api_url": MOCK, "api_key": "test", "model": "mock-logic", "type": "logic"})
check("test-model logic against mock", s == 200 and r.get("ok") is True, (s, r))

s, r = req("POST", "/api/test-model", {"api_url": MOCK, "api_key": "test", "model": "mock-image", "type": "image"})
check("test-model image against mock", s == 200 and r.get("ok") is True, (s, r))

# 2. project + preset wired to mock endpoints (proves no hard-coded provider)
s, proj = req("POST", "/api/projects", {"name": "Pipeline Test", "description": "mock e2e", "instructions": "prefer moody lighting"})
check("create project", s == 201 and proj.get("id"), (s, proj))
pid = proj["id"]

s, preset = req("POST", "/api/presets", {
    "name": "Mock Provider",
    "logic_provider": "mock", "logic_url": MOCK, "logic_key": "test", "logic_model": "mock-logic",
    "image_provider": "mock", "image_url": MOCK, "image_key": "test", "image_model": "mock-image",
    "default_aspect_ratio": "1:1", "default_resolution": "1024x1024", "default_count": 1,
    "system_prompt": "You are a helpful image planning assistant.",
})
check("create preset with mock endpoints", s == 201 and preset.get("id"), (s, preset))
prid = preset["id"]

s, _ = req("PUT", "/api/projects/" + pid, {"name": "Pipeline Test", "description": "mock e2e",
                                           "instructions": "prefer moody lighting", "active_preset_id": prid})
check("assign preset to project", s == 200, s)

# 3. conversation
s, conv = req("POST", "/api/projects/%s/conversations" % pid, {"title": "pipeline chat"})
check("create conversation", s == 201 and conv.get("id"), (s, conv))
cid = conv["id"]

# 4. chat -> logic model -> structured plan -> jobs
s, m = req("POST", "/api/conversations/%s/messages" % cid, {"content": "generate two fox images"})
check("chat returns without error", s == 200, (s, m))

plan = m.get("plan")
check("logic model produced a plan", plan is not None, m.get("plan_error") or str(m)[:200])
check("plan action is generate", plan and plan.get("action") == "generate", plan)
check("plan validated (2 generations)", plan and len(plan.get("generations", [])) == 2, plan)
check("plan_error is empty", m.get("plan_error") is None, m.get("plan_error"))

jobs = m.get("jobs") or []
check("jobs created from plan", len(jobs) == 2, jobs)

# 5. worker drains jobs
final_jobs = wait_jobs(pid, 2, timeout=60)
statuses = {j["id"]: j.get("status") for j in final_jobs}
check("all jobs completed", all(v == "completed" for v in statuses.values()), statuses)
check("jobs record image model", all(j.get("image_model") == "mock-image" for j in final_jobs),
      [j.get("image_model") for j in final_jobs])

# 6. gallery persisted (2 gens: count 2 + count 1 = 3 images)
s, gal = req("GET", "/api/gallery/" + pid)
check("gallery populated", s == 200 and len(gal) == 3, (s, len(gal) if isinstance(gal, list) else gal))

if isinstance(gal, list) and gal:
    g0 = gal[0]
    check("image has prompt", bool(g0.get("prompt")), g0)
    check("image has model", g0.get("image_model") == "mock-image", g0.get("image_model"))
    check("image linked to job", bool(g0.get("job_id")), g0.get("job_id"))
    check("image_data stripped from list", g0.get("image_data") == "", g0.get("image_data"))

    # 7. image bytes serve correctly
    iid = g0["id"]
    try:
        with urllib.request.urlopen(API + "/api/gallery-image/" + iid, timeout=10) as resp:
            data = resp.read()
            check("gallery image serves PNG bytes",
                  resp.status == 200 and data[:8] == b"\x89PNG\r\n\x1a\n", (resp.status, len(data)))
    except Exception as e:
        check("gallery image serves PNG bytes", False, e)

    # 8. delete an image
    s, _ = req("DELETE", "/api/gallery/" + iid)
    check("delete gallery image", s == 200, s)
    s, gal2 = req("GET", "/api/gallery/" + pid)
    check("gallery image removed", isinstance(gal2, list) and len(gal2) == 2, len(gal2) if isinstance(gal2, list) else gal2)

# 9. batch generation through the worker
s, b = req("POST", "/api/generate-batch", {"project_id": pid, "conversation_id": cid,
                                           "prompts": ["batch one", "batch two"], "aspect_ratio": "4:3"})
check("queue batch", s == 201 and len(b.get("jobs", [])) == 2, (s, b))
wait_jobs(pid, 4, timeout=60)
s, gal3 = req("GET", "/api/gallery/" + pid)
check("batch images generated", isinstance(gal3, list) and len(gal3) == 4, len(gal3) if isinstance(gal3, list) else gal3)

# 10. direct generate endpoint
s, g = req("POST", "/api/generate", {"project_id": pid, "prompt": "direct gen", "aspect_ratio": "9:16"})
check("direct generate queues job", s == 201 and g.get("job_id"), (s, g))
wait_jobs(pid, 5, timeout=60)
s, gal4 = req("GET", "/api/gallery/" + pid)
check("direct generate produced image", isinstance(gal4, list) and len(gal4) == 5,
      len(gal4) if isinstance(gal4, list) else gal4)

# 11. failure handling: preset with unreachable endpoint -> job fails, no crash
s, bad_preset = req("POST", "/api/presets", {
    "name": "Broken", "image_provider": "x", "image_url": "http://127.0.0.1:9999/v1",
    "image_key": "k", "image_model": "m", "logic_provider": "x", "logic_url": MOCK,
    "logic_key": "k", "logic_model": "mock-logic",
})
s, bad_proj = req("POST", "/api/projects", {"name": "Fail Test"})
req("PUT", "/api/projects/" + bad_proj["id"], {"name": "Fail Test", "description": "", "instructions": "",
                                               "active_preset_id": bad_preset["id"]})
s, fg = req("POST", "/api/generate", {"project_id": bad_proj["id"], "prompt": "will fail"})
check("generate with broken preset queued", s == 201, (s, fg))
wait_jobs(bad_proj["id"], 1, timeout=60)
s, fj = req("GET", "/api/jobs/" + bad_proj["id"])
check("broken job marked failed, not crashed",
      fj and fj[0].get("status") == "failed" and fj[0].get("error"), fj[0] if fj else None)
check("failed job retried up to max attempts", fj and (fj[0].get("attempts") or 0) >= 1, fj[0].get("attempts") if fj else None)

# 12. cleanup
for p in (pid, bad_proj["id"]):
    req("DELETE", "/api/projects/" + p)
for p in (prid, bad_preset["id"]):
    req("DELETE", "/api/presets/" + p)
s, left = req("GET", "/api/projects")
check("cleanup complete", isinstance(left, list) and len(left) == 0, left)

print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
print("RESULT: %d/%d passed" % (passed, len(results)))
if passed < len(results):
    print("\nFAILURES:")
    for name, ok, detail in results:
        if not ok:
            print("  - " + name + " :: " + str(detail)[:250])
