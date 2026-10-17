# Prompt Studio

A model-agnostic AI creative studio: bring your own API URL, key and model id, and the
app discovers what that model can do before it offers you a button for it.

Image and video generation run as background jobs with persisted state; outputs are
stored as real files and organised into projects. Every capability in the UI comes from
what the provider actually reported, so nothing is a button that fails when clicked.

## Why this shape

This is a **BYOK/BYOM** platform. There is no shared provider key: each connection holds
its own credential, encrypted at rest, and the server never returns it. That drives four
decisions worth knowing up front:

| Decision | Why |
|---|---|
| Keys are encrypted with `APP_ENCRYPTION_KEY`, never logged, never serialised | A credential the server can hand back is a credential it can leak |
| Base URLs are SSRF-checked, and the **resolved IP is pinned** for the connection | Check-then-connect is a time-of-check/time-of-use hole; validating a hostname and then re-resolving it is not a guard |
| A model's capabilities come from the provider's listing, not from client JSON | A capability is an authorization fact, so it must not be user-supplied |
| A cost is stored only when a published rate is known, else `cost_source=unknown` | An invented price in a usage record is something people make spending decisions on |

## Quick start

```bash
# 1. Configure
cp .env.example .env
python -m app.cli init-key        # paste the output into APP_ENCRYPTION_KEY

# 2. Run
python -m app.web.server          # or: python run_studio.py
# → http://127.0.0.1:8765
```

No web framework, no ORM, no build step. The server is `http.server` from the standard
library, the database is SQLite, and the front end is one HTML file. The only third-party
packages are the ones already used for media work:

```
cryptography    encrypting stored credentials
pillow          reading uploaded images (real dimensions, not claimed ones)
numpy           image inspection helper
```

`python -m app.cli check` reports the resolved configuration, the schema version and
whether anything is unsafe for production — without starting a server.

## The flow

1. **Connections** — base URL, API key, optional name. The key is encrypted before it is
   stored and only ever appears masked (`sk-a…7890`).
2. **Test** — a real request to `/models`. An untested connection stays `untested`; it is
   never marked good because the URL parsed.
3. **Register models** — pick from what the provider lists; each one stores the
   capabilities and parameters it was discovered with.
4. **Studios** — Image and Video list only models whose capabilities match, and only offer
   the parameters that model documents. A model without `video.image_to_video` gets no
   starting-frame control.
5. **Generate** — `POST /api/generations/images` returns `202` with a job; a worker pool
   runs it; `GET /api/jobs/{id}` reports real state and progress.
6. **Store** — outputs are downloaded and saved under `uploads/`, with dimensions read
   from the bytes, and linked to the job and project.

## What is implemented

| Area | Status |
|---|---|
| Connections: create, update, rotate key, delete, test | done |
| Model registry: discover, register, enable/disable, capabilities, parameters | done |
| Image generation | done |
| Video generation, including asynchronous polling and cancel | done |
| Background jobs: queue, worker pool, retries, cancellation, crash recovery | done |
| Projects, asset library, uploads with signature validation | done |
| Presets, saved prompts, characters, usage records, job history | done |
| Creative assistant | done, but requires a registered chat model (see below) |
| Editing / inpaint / upscale / extend | **not implemented** — refused with an explanation rather than faked |
| Authentication and multi-user workspaces | **not implemented** — single-owner local deployment |

## Provider adapters

| Adapter | Covers |
|---|---|
| `openai-compatible` | Any OpenAI-shaped endpoint: `/models`, `/images/generations`, `/chat/completions`, and the `videos/generations` shape several gateways expose |
| `openai`, `custom` | Aliases for the above |
| `mock` | Offline demo. Renders a placeholder image stamped **DEMO IMAGE — NOT A REAL GENERATION**, reports no cost, and is never used as a fallback for a failing real provider |

Capability discovery is deliberately conservative. If the provider's `/models` reply says
nothing about a model, it is registered with `image.generate` only — the one capability
every one of these endpoints has by definition — rather than a guess that would produce a
button that fails.

## API

Full specification: [`docs/openapi.yaml`](docs/openapi.yaml). Every response is either
`{"data": ...}` or `{"error": {"code", "message", "hint?"}}`, and the error codes are a
closed set in `app/errors.py`.

```
GET    /api/health
GET    /api/capabilities

GET    /api/connections                  POST   /api/connections
GET    /api/connections/{id}             PATCH  /api/connections/{id}
DELETE /api/connections/{id}
POST   /api/connections/{id}/test
POST   /api/connections/{id}/rotate-key
GET    /api/connections/{id}/models      POST   /api/connections/{id}/models

GET    /api/models[?modality=&capability=]
GET    /api/models/{pk}                  PATCH  /api/models/{pk}
DELETE /api/models/{pk}
GET    /api/models/{pk}/capabilities
GET    /api/models/{pk}/parameters

POST   /api/generations/images
POST   /api/generations/videos
GET    /api/jobs                         GET    /api/jobs/{id}
POST   /api/jobs/{id}/cancel             POST   /api/jobs/{id}/retry

GET    /api/projects                     POST   /api/projects
GET    /api/projects/{id}                PATCH  /api/projects/{id}
DELETE /api/projects/{id}
GET    /api/assets                       POST   /api/assets/upload
GET    /api/assets/{id}                  GET    /api/assets/{id}/raw
DELETE /api/assets/{id}

GET    /api/presets                      POST   /api/presets
PATCH  /api/presets/{id}                 DELETE /api/presets/{id}
GET    /api/prompts                      POST   /api/prompts
PATCH  /api/prompts/{id}                 DELETE /api/prompts/{id}
GET    /api/characters                   POST   /api/characters
PATCH  /api/characters/{id}              DELETE /api/characters/{id}

GET    /api/analytics?days=30
GET    /api/assistant                    POST   /api/assistant
```

## Tests

```bash
python -m pytest tests/ -q
```

The suite covers what the spec calls critical: credential encryption and redaction, SSRF
refusal (including IPv4-mapped IPv6 and the cloud metadata address), capability gating,
job-state transitions, duplicate submission, retry and cancellation, asset ownership, and
the error vocabulary. Provider behaviour is exercised against an in-process fake, and the
test names say so — a mock test is never presented as a real generation.

## Configuration

See `.env.example`. The two values that matter:

- **`APP_ENCRYPTION_KEY`** — required in production. Without it, development runs with a
  throwaway key and says so, so the failure is obvious locally instead of quietly
  persisting credentials that cannot be read back.
- **`ALLOW_PRIVATE_UPSTREAM`** — development only. Setting it to `1` lets you connect to a
  model server on localhost, and disables the SSRF guard. `app.cli check` warns when
  production has it set.

## Known limits

Documented honestly in [`docs/limits.md`](docs/limits.md): what is not built, what is
approximated, and which claims this app deliberately refuses to make.
