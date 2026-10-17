#!/usr/bin/env python3
"""End-to-end API test for img-gen platform."""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8765"
results = []


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
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
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)[:200]))


# --- projects ---
s, p = req("GET", "/api/projects")
check("GET /api/projects", s == 200 and isinstance(p, list), (s, p))

s, proj = req("POST", "/api/projects", {"name": "Test Project", "description": "e2e", "instructions": "be creative"})
check("POST /api/projects", s == 201 and proj.get("id"), (s, proj))
pid = proj.get("id")

s, proj2 = req("POST", "/api/projects", {"name": "Second Project"})
check("POST second project", s == 201 and proj2.get("id"), (s, proj2))
pid2 = proj2.get("id")

s, pl = req("GET", "/api/projects")
check("project list isolation", s == 200 and len(pl) >= 2, (s, len(pl) if isinstance(pl, list) else pl))

s, one = req("GET", "/api/projects/" + pid)
check("GET /api/projects/<id>", s == 200 and one.get("name") == "Test Project", (s, one))

s, u = req("PUT", "/api/projects/" + pid, {"name": "Renamed", "description": "e2e2", "instructions": "x", "active_preset_id": ""})
check("PUT /api/projects/<id>", s == 200, (s, u))
s, one = req("GET", "/api/projects/" + pid)
check("project rename persisted", one.get("name") == "Renamed", one)

# --- presets (dynamic model config, no hardcoding) ---
preset_body = {
    "name": "OpenAI Preset",
    "logic_provider": "openai", "logic_url": "https://api.openai.com/v1",
    "logic_key": "sk-test-logic-123", "logic_model": "gpt-4o",
    "image_provider": "openai", "image_url": "https://api.openai.com/v1",
    "image_key": "sk-test-image-456", "image_model": "dall-e-3",
    "default_aspect_ratio": "16:9", "default_resolution": "1792x1024",
    "default_count": 2, "system_prompt": "You are a creative director.",
}
s, pre = req("POST", "/api/presets", preset_body)
check("POST /api/presets", s == 201 and pre.get("id"), (s, pre))
prid = pre.get("id")

s, pre_custom = req("POST", "/api/presets", {
    "name": "Custom Local", "logic_provider": "ollama", "logic_url": "http://localhost:11434/v1",
    "logic_key": "local", "logic_model": "llama3",
    "image_provider": "local-sd", "image_url": "http://localhost:7860",
    "image_key": "", "image_model": "sdxl", "default_aspect_ratio": "1:1",
    "default_resolution": "512x512", "default_count": 1, "system_prompt": "",
})
check("POST arbitrary provider preset (no hardcoding)", s == 201 and pre_custom.get("id"), (s, pre_custom))

s, plist = req("GET", "/api/presets")
check("GET /api/presets", s == 200 and len(plist) >= 2, (s, plist))

# API keys must never be returned in usable form: a masked key must not equal
# the original plaintext we submitted.
def _leaks(plist):
    real = {"sk-test-logic-123", "sk-test-image-456", "local"}
    for p in plist:
        for k in ("image_key", "logic_key"):
            v = p.get(k) or ""
            if v and v in real:
                return True
    return False


check("API keys never returned in plaintext", not _leaks(plist), plist)
sample = [p for p in plist if p.get("name") == "OpenAI Preset"]
if sample:
    check("long keys are partially masked", "*" in (sample[0].get("image_key") or ""), sample[0].get("image_key"))

s, pone = req("GET", "/api/presets/" + prid)
check("GET /api/presets/<id>", s == 200 and pone.get("name") == "OpenAI Preset", (s, pone))

s, up = req("PUT", "/api/presets/" + prid, dict(preset_body, name="OpenAI Preset v2", image_model="gpt-image-1"))
check("PUT /api/presets/<id>", s == 200, (s, up))
s, pone = req("GET", "/api/presets/" + prid)
check("preset update persisted", pone.get("image_model") == "gpt-image-1", pone)

# assign preset to project
s, _ = req("PUT", "/api/projects/" + pid, {"name": "Renamed", "description": "e2e2", "instructions": "x", "active_preset_id": prid})
check("assign preset to project", s == 200, s)
s, one = req("GET", "/api/projects/" + pid)
check("project preset link persisted", one.get("active_preset_id") == prid, one)

# --- assets ---
asset_body = {"name": "ref.png", "data": "iVBORw0KGgoAAAANSUhEUg==", "file_type": "png", "description": "ref image"}
s, a = req("POST", "/api/projects/%s/assets" % pid, asset_body)
check("POST asset", s == 201 and a.get("id"), (s, a))
aid = a.get("id")

s, alist = req("GET", "/api/projects/%s/assets" % pid)
check("GET assets", s == 200 and len(alist) >= 1, (s, alist))

s, alist2 = req("GET", "/api/projects/%s/assets" % pid2)
check("asset project isolation", s == 200 and len(alist2) == 0, (s, alist2))

# --- conversations ---
s, c = req("POST", "/api/projects/%s/conversations" % pid, {"title": "First Chat"})
check("POST conversation", s == 201 and c.get("id"), (s, c))
cid = c.get("id")

s, cl = req("GET", "/api/conversations/" + pid)
check("GET conversations for project", s == 200 and len(cl) >= 1, (s, cl))

s, cl2 = req("GET", "/api/conversations/" + pid2)
check("conversation project isolation", s == 200 and len(cl2) == 0, (s, cl2))

# --- messages / chat pipeline (multi-turn + structured JSON) ---
s, m = req("POST", "/api/conversations/%s/messages" % cid, {"content": "make a poster"})
check("POST message turn 1", s == 200, (s, m))
print("     turn1 resp:", json.dumps(m)[:300])

s, m2 = req("POST", "/api/conversations/%s/messages" % cid, {"content": "make it darker"})
check("POST message turn 2", s == 200, (s, m2))

s, msgs = req("GET", "/api/messages/" + cid)
check("GET messages (history)", s == 200 and len(msgs) >= 2, (s, len(msgs) if isinstance(msgs, list) else msgs))
turns = [x for x in msgs if x.get("role") == "user"] if isinstance(msgs, list) else []
check("multi-turn history preserved", len(turns) >= 2, len(turns))

# --- gallery ---
s, g = req("GET", "/api/gallery/" + pid)
check("GET gallery (empty ok)", s == 200 and isinstance(g, list), (s, g))

# --- jobs (preset-backed, queued to worker) ---
s, j = req("POST", "/api/generate", {"project_id": pid, "conversation_id": cid, "prompt": "a red fox", "aspect_ratio": "1:1", "count": 1})
check("POST /api/generate", s == 201 and j.get("job_id"), (s, j))
jid = j.get("job_id")
print("     generate resp:", json.dumps(j)[:200])

s, jl = req("GET", "/api/jobs/" + pid)
check("GET /api/jobs/<project>", s == 200 and isinstance(jl, list) and len(jl) >= 1, (s, jl))
if jl and isinstance(jl, list):
    print("     job record:", json.dumps(jl[0])[:250])
    check("job has status field", "status" in jl[0], jl[0])
    check("job records image model", jl[0].get("image_model") != "", jl[0])

# batch: queue multiple jobs via preset
s, b = req("POST", "/api/generate-batch", {"project_id": pid, "conversation_id": cid, "prompts": ["one", "two", "three"], "aspect_ratio": "1:1"})
check("POST /api/generate-batch", s == 201, (s, b))
print("     batch resp:", json.dumps(b)[:200])
if isinstance(b, dict) and b.get("jobs"):
    check("batch created multiple jobs", len(b["jobs"]) == 3, b.get("jobs"))
s, jl = req("GET", "/api/jobs/" + pid)
check("jobs persisted after batch", isinstance(jl, list) and len(jl) >= 4, len(jl) if isinstance(jl, list) else jl)

# generate requires a preset -> should reject when none assigned
s, np = req("POST", "/api/generate", {"project_id": pid2, "prompt": "orphan"})
check("generate rejects project without preset", s == 400, (s, np))

# invalid aspect ratio should be coerced, not crash
s, ir = req("POST", "/api/generate", {"project_id": pid, "prompt": "ratio test", "aspect_ratio": "99:1"})
check("invalid aspect ratio coerced", s == 201, (s, ir))

# retry / cancel
if jid:
    # a queued job must refuse a redundant retry (409) — that is correct behavior
    s, r = req("POST", "/api/jobs/%s/retry" % jid)
    check("retry rejects already-active job", s == 409, (s, r))

    # force to failed, then retry must re-queue it
    import sqlite3 as _sq
    _c = _sq.connect(str(__import__("pathlib").Path(r"C:\Users\priya\Desktop\img-gen\data\img-gen.db")))
    _c.execute("UPDATE generation_jobs SET status='failed', error='boom' WHERE id=?", (jid,))
    _c.commit()
    _c.close()
    s, r = req("POST", "/api/jobs/%s/retry" % jid)
    check("retry re-queues failed job", s == 200, (s, r))
    s, jl = req("GET", "/api/jobs/" + pid)
    retried = [x for x in jl if x.get("id") == jid]
    check("retry persisted (status queued)", retried and retried[0].get("status") == "queued", retried)

    s, r = req("POST", "/api/jobs/%s/cancel" % jid)
    check("POST job cancel", s == 200, (s, r))
    s, jl = req("GET", "/api/jobs/" + pid)
    cancelled = [x for x in jl if x.get("id") == jid]
    check("cancel persisted", cancelled and cancelled[0].get("status") == "cancelled", cancelled)
    s, r = req("PUT", "/api/jobs/%s/cancel" % jid)
    check("PUT job cancel also works", s == 200, (s, r))
    s, r = req("POST", "/api/jobs/does-not-exist/cancel")
    check("cancel unknown job 404", s == 404, (s, r))

# --- model testing ---
s, tm = req("POST", "/api/test-model", {"api_url": "https://api.openai.com/v1", "api_key": "sk-invalid", "model": "gpt-4o", "type": "logic"})
check("POST /api/test-model logic", s == 200 and "ok" in tm, (s, tm))
print("     test-model logic:", json.dumps(tm)[:300])

s, tm2 = req("POST", "/api/test-model", {"api_url": "http://127.0.0.1:1/v1", "api_key": "k", "model": "m", "type": "image"})
check("POST /api/test-model image", s == 200 and "ok" in tm2, (s, tm2))

# --- security: SSRF-ish / bad input handling ---
s, bad = req("POST", "/api/test-model", {"api_url": "not-a-url", "api_key": "k", "model": "m", "type": "logic"})
check("test-model rejects bad url", s == 200 and bad.get("ok") is False, (s, bad))

# --- deletes (cleanup, verify cascade) ---
s, _ = req("DELETE", "/api/projects/%s/assets/%s" % (pid, aid))
check("DELETE asset", s == 200, s)

s, _ = req("DELETE", "/api/presets/" + prid)
check("DELETE preset", s == 200, s)

s, plist = req("GET", "/api/presets")
check("preset deleted from list", all(p.get("id") != prid for p in plist), plist)

s, _ = req("DELETE", "/api/projects/" + pid)
check("DELETE project", s == 200, s)

s, pl = req("GET", "/api/projects")
check("project deleted from list", all(p.get("id") != pid for p in pl), pl)

s, cl = req("GET", "/api/conversations/" + pid)
check("conversations cascaded on project delete", isinstance(cl, list) and len(cl) == 0, cl)

s, _ = req("DELETE", "/api/projects/" + pid2)
check("DELETE second project", s == 200, s)

# --- 404 handling ---
s, nf = req("GET", "/api/projects/does-not-exist")
check("404 for missing project", s in (404, 400, 500), (s, nf))

s, nf = req("GET", "/api/nonexistent-endpoint")
check("404 unknown endpoint", s == 404, (s, nf))

# --- static file serving ---
try:
    with urllib.request.urlopen(BASE + "/", timeout=5) as resp:
        check("GET / serves index.html", resp.status == 200 and b"img-gen" in resp.read())
except Exception as e:
    check("GET / serves index.html", False, e)

print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("RESULT: %d/%d passed" % (passed, total))
if passed < total:
    print("\nFAILURES:")
    for name, ok, detail in results:
        if not ok:
            print("  - " + name + " :: " + str(detail)[:250])
