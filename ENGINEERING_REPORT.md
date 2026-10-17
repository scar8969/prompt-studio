# img-gen — Engineering Report

**Date:** 2026-09-06
**Scope:** Full audit, repair, refactor and completion of the AI image generation platform at `C:\Users\priya\Desktop\img-gen`, per the 10-phase master strategy.

---

## 1. Problems Found (original prototype)

The starting codebase was a ~178-line `src/server.py` (static file server + thin proxy) and a single monolithic `index.html` (~634 lines, all CSS/JS inline). All state lived in `localStorage`. Specific problems:

| # | Problem | Root cause |
|---|---------|-----------|
| 1 | No persistence — projects/conversations/gallery lost on reload | No database; everything in `localStorage` |
| 2 | API keys stored client-side in `localStorage` | No server-side secret store |
| 3 | `/api/logic` endpoint existed but was **never called** — the "two-AI-system" design was unwired | Frontend had no chat pipeline |
| 4 | Providers/models hard-coded in UI markup | No preset/config abstraction |
| 5 | No job system — synchronous single-request generation only | No queue/worker architecture |
| 6 | Stale dead files (`BUGS.md.old`, `PLAN.md.old`, later `server.py.bak`) shipped alongside | No cleanup discipline |
| 7 | Masked keys could be round-tripped back into the DB on preset edit, silently corrupting real keys | UI prefilled masked value into editable input |
| 8 | `GET /api/projects/<id>/assets` shadowed by `GET /api/projects/<id>` — asset list unreachable | Routing-order bug (prefix match before sub-resource match) |
| 9 | `updated_at` was set to SQL `NULL` on every update, losing timestamps | `updates["updated_at"] = None` bound as parameter instead of SQL expression |
| 10 | Gallery showed blank cards for b64 images | Server stripped `image_data` without substituting `/api/gallery-image/<id>` URL |
| 11 | `test-model` crashed the connection on malformed URLs (e.g. `"not-a-url"`) | `urllib.request.Request()` constructed before any validation; ValueError escaped the handler |
| 12 | Every modal "cancel" button threw `hideModal is not defined` | `hideModal` was module-scoped (ES module) but referenced by inline `onclick` |
| 13 | A failed conversation creation permanently wedged the UI (`STATE.loading` stuck true) | try/catch started after the unguarded `await` |
| 14 | All server errors were swallowed — failed mutations showed success toasts | `API()` never checked `r.ok`; `r.json()` always resolves |

## 2. Security Findings (phase 9, agent-audited) & Fixes

| # | Severity | Finding | Fix |
|---|----------|---------|-----|
| 1 | **CRITICAL** | `GET //data/img-gen.db` served the SQLite DB with **plaintext API keys** — `//` prefix bypassed the `startswith("/data/")` block-list before `lstrip("/")` normalized it (verified by the audit agent executing the code path) | Rewrote `sanitize_static_path` as an **allow-list**: normalize first, deny sensitive dirs, and require a known static extension (`.db`, `.bak` are unservable). Verified: all bypass paths → 403 |
| 2 | **HIGH** | `Access-Control-Allow-Origin: *` let any website read data, delete everything, and use `/api/test-model` as a readable SSRF oracle | CORS now restricted to `localhost:5173`/`127.0.0.1:5173` (Vite proxy requests are same-origin anyway) |
| 3 | MEDIUM | `urlopen` followed redirects: a 302 from a malicious provider re-ran **no** SSRF validation and forwarded `Authorization: Bearer <key>` to the redirect target | Installed a `NoRedirect` opener — redirects are never followed |
| 4 | MEDIUM | String-based private-host checks bypassed by alternate encodings (`http://2130706433/`, `http://0x7f000001/`, `http://127.1/`) | `validate_upstream_url` now resolves the hostname via `socket.getaddrinfo` and checks the real IP with `ipaddress` (loopback/link-local/private/reserved/multicast all rejected). 16/16 guard unit-tests pass |
| 5 | MEDIUM | `mask_key("abcdefgh")` (8 chars) returned the full secret | `len(key) <= 8` → fully masked |
| 6 | MEDIUM | Provider-controlled `image_url` was interpolated unescaped into `src="${src}"` and `onclick="showLightbox('${src}')"` → stored XSS | Gallery routes every image through the local origin-safe `/api/gallery-image/<id>` endpoint; actions migrated to `data-` attributes + delegated event listeners; prompts escaped with `title` attr |
| 7 | LOW | Open redirect: `/api/gallery-image/<id>` 302'd to arbitrary provider schemes | Redirect constrained to http/https |
| 8 | LOW | Negative `Content-Length` → `rfile.read(-1)` blocks a worker thread | Reject `length < 0` |
| 9 | LOW | Uploaded SVGs served inline same-origin (`image/svg+xml`) — script execution vector | SVG served as `application/octet-stream` + `Content-Disposition: attachment` + `nosniff` |
| 10 | LOW | No prompt length caps (`/api/generate` unbounded; batch 50 × unbounded) | All prompts capped at 4000 chars |
| 11 | MEDIUM | Preset edit round-tripped **masked** keys back into the DB, corrupting real credentials | Key inputs no longer prefilled (mask shown as placeholder); empty field = keep existing (frontend deletes key from PUT body; server skips empty key fields on update) |

**Confirmed non-issues:** SQL injection (all queries parameterized; f-string SQL uses only hard-coded whitelisted column names), no ReDoS, no unvalidated outbound call sites, no secrets in logs, no stack traces in HTTP responses.

## 3. Architecture Changes

```
Before:  index.html (monolith) ──> server.py (static + /api/proxy passthrough)

After:   index.html ─┐
         src/app.js ─┴─> HTTP router (regex-routed verbs)
                           ├─ Services: ChatService, job runner/worker
                           ├─ ProviderAdapter (OpenAICompatibleAdapter + registry)
                           ├─ validate_plan()  (structured JSON validation)
                           ├─ validate_upstream_url()  (SSRF guard)
                           └─ db.py: SQLite (WAL) — projects, presets, assets,
                              conversations, messages, generation_jobs, generated_images
```

- **Provider adapter layer:** all upstream calls go through a registry-based `ProviderAdapter`. Providers/models are **data** (preset rows) — nothing is hard-coded; any OpenAI-compatible endpoint (OpenAI, Ollama, LocalAI, vLLM, Groq, OpenRouter…) works by filling in a preset form.
- **Generation pipeline:** chat → logic model (`response_format: json_object`) → `validate_plan()` (drop unsafe fields, clamp ratios/counts, cap 8 gens) → `generation_jobs` rows (`queued`) → background worker thread → provider → `generated_images` → gallery.
- **Job lifecycle:** `queued → running → completed | failed` with `attempts` counter and max-3 retry, plus `cancelled` via cancel endpoints. `POST /api/jobs/<id>/retry` refuses to double-queue active jobs (409).
- **Router:** regex-based with most-specific-first ordering; sub-resources (`/api/projects/<id>/assets`) can no longer be shadowed by the parent route.
- **Key handling:** keys live server-side only; every client-facing response masks them; the generation endpoints take `project_id` and resolve credentials from the project's preset — keys never cross the wire in generation requests.

## 4. Features Implemented

- **Projects:** CRUD, per-project instructions injected into the logic model, asset/gallery counts, cascade delete.
- **Presets (first-class):** full CRUD with separate logic/image provider/URL/key/model, defaults (ratio, resolution, count, seed, negative prompt, quality, style), system prompt, JSON schema field, test buttons.
- **Assets:** upload (b64, type-validated, 10 MB cap), list, delete, byte-serving endpoint, project isolation verified.
- **Chat:** multi-turn conversations, full message history replay to the model, project instructions + asset context injected as system messages, structured JSON plans parsed and validated, graceful error messages stored as assistant turns.
- **Generation jobs:** queue, background worker, cancel/retry, status/errors visible via API, batch queueing (up to 50 prompts).
- **Gallery:** project-scoped, lightbox, download, delete, per-image model badges; b64 images served via cacheable local endpoint; job completion surfaced by frontend polling.
- **API testing:** `/api/test-model` validates any endpoint/model combination for both `image` and `logic` types with proper error reporting.
- **UX:** toast system (aria-live), status bar with live job progress, Esc closes modal/lightbox, modal autofocus, placeholder hint for Ctrl+Enter, focus-within reveal of gallery actions, badge ellipsis, race-free project switching (selection token), prompt preserved on send failure.

## 5. Files Changed

| File | Status | Notes |
|------|--------|-------|
| `src/server.py` | rewritten (~1260 lines) | full REST API, provider adapters, job worker, SSRF guard, static allow-list |
| `src/db.py` | rewritten (~168 lines) | SQLite schema + idempotent column migration |
| `index.html` | rewritten (~160 lines) | modular layout, a11y additions |
| `src/app.js` | new (~693 lines) | SPA: state, API client, CRUD, chat, gallery, polling |
| `test_e2e.py` | new | 57-check API suite |
| `test_pipeline.py` | new | 30-check pipeline suite (mock provider) |
| `test_ui.js`, `test_ui_gallery.js` | new | headless Chrome (CDP) UI suites |
| `test_mock_provider.py` | new | OpenAI-compatible mock (chat + images) |
| `src/server.py.bak` | deleted | was exfiltratable via finding #1 |
| `vite.config.js`, `package.json`, `run.bat` | unchanged | worked as-is |

## 6. Database Changes

SQLite at `data/img-gen.db` (WAL mode). Tables: `projects`, `presets`, `assets`, `conversations`, `messages`, `generation_jobs`, `generated_images` — with FKs (`ON DELETE CASCADE`, verified), indexes on project/status columns, and an idempotent migration step (`_migrate`) that adds `generation_jobs.attempts` / `result_json` to pre-existing DBs. API keys are stored in `presets` server-side; the DB file is the single secret store.

## 7. Tests Performed (all passing)

Single entry point: **`python run_tests.py`** (adds `--fast` to skip browser suites). Starts/stops the backend and mock provider itself and exits non-zero on any failure, so it works as a CI gate. Exit code 0 = all suites green.

| Suite | Result | Covers |
|-------|--------|--------|
| `test_e2e.py` | **57/57** | CRUD, isolation, validation, masking, cascade deletes, 404s, static serving |
| `test_pipeline.py` | **30/30** | chat → plan → jobs → worker → gallery, batch, retry/cancel, failure paths |
| `test_recovery.py` | **4/4** | crash recovery: stuck `running` job requeued and completed after restart |
| `test_ui.js` | **15/15** | real-browser flow: project/preset creation, chat, badges, gallery |
| `test_ui_gallery.js` | **10/10** | gallery cards, image loading, lightbox, delete, model badges |
| `test_ui_conversations.js` | **10/10** | conversation list, switching, create/delete via UI |
| guard unit tests | **16/16** | SSRF bypass vectors, key masking |
| `node --check src/app.js` | pass | frontend syntax |

Also verified manually via curl/PowerShell: DB-exfiltration paths return 403; CORS no longer `*`; Vite proxy serves app + API (200s).

## 8. Remaining Limitations

1. **Job worker is single-threaded and in-process** — jobs run sequentially. *Resolved:* restart recovery is implemented (`recover_stale_jobs()` requeues or fails jobs stuck in `running`, verified by `test_recovery.py`); remaining gap is only horizontal scale (multiple workers), irrelevant for a local tool.
2. **No authentication** — by design for a local single-user tool; the CORS restriction is the main mitigation. Not suitable for multi-user/network deployment without adding auth.
3. **DNS rebinding** — *resolved:* outbound connections are pinned to the validated IP (`resolve_validated_ip` + custom `_PinnedHTTPSConnection`/`_PinnedHTTPConnection` openers, SNI/Host preserved), closing the validate-then-connect race.
4. **Asset size limits** — 10 MB cap. *Resolved (gallery perf):* `renderGallery` skips the innerHTML rebuild when the image-id signature is unchanged, so polling no longer recreates `<img>` elements or resets scroll.
5. **Conversation UI** — *resolved:* the sidebar now lists conversations with switching, "+ new", and delete (verified by `test_ui_conversations.js`).
6. **Provider coverage** — the adapter registry (`register_adapter`) is documented as the extension point; unknown providers fall back to the OpenAI-compatible dialect. Additional dialects are opt-in, not built in.
7. **CI** — *resolved:* `run_tests.py` is a single CI-ready gate (combined summary, non-zero exit on failure). Wiring it to a hosted CI system remains, as the project has no remote repository.
