# Prompt Studio

A model-agnostic AI creative studio: bring your own API URL, key and model id, and the
app discovers what that model can do before it offers you a button for it.

Image and video generation run as background jobs with persisted state; outputs are
stored as real files and organised into projects. Every capability in the UI comes from
what the provider actually reported, so nothing is a button that fails when clicked.

## Why this shape

**No BYOK. Credentials are environment variables.** There is no screen for entering a
provider key, no encrypted credential column, and no endpoint that accepts one. A model
exists because the deployment's environment configures it, with three variables:

```bash
NANO_BANANA_2_API_KEY=...                        # the credential
NANO_BANANA_2_MODEL=gemini-3-pro-image-preview   # the model name to send
NANO_BANANA_2_API_URL=https://.../v1beta         # the endpoint
```

`<PREFIX>` is the model's job-set id uppercased. All 59 models are listed in
`.env.example` with their three variables; `python -m app.cli catalog` prints what is
configured and what is missing. On Vercel this is Project → Settings → Environment
Variables, which means a key is rotated where it is issued and never transits the app.

That drives four decisions worth knowing up front:

| Decision | Why |
|---|---|
| A credential is read from the environment at call time, never stored | Nothing to leak, nothing to decrypt, and rotation takes effect on the next request |
| Base URLs are SSRF-checked, and the **resolved IP is pinned** | The URL now arrives from the environment, so the guard moved with it — check-then-connect is a time-of-check/time-of-use hole |
| A model's capabilities come from its catalogue row, not from request JSON | A capability is an authorization fact, so it must not be user-supplied |
| A cost is stored only when a published rate is known, else `cost_source=unknown` | An invented price in a usage record is something people make spending decisions on |

## Quick start

```bash
# 1. Configure — fill in the three variables for each model you want
cp .env.example .env
#    <PREFIX>_API_KEY / _MODEL / _API_URL. Nothing else is required to run.

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

1. **Models** — appear because the environment configures them: a key, a model name and
   an API URL per model. There is no form and no stored credential; the Models view is
   read-only and shows which are configured.
2. **Studios** — Image and Video list only models whose capabilities match, and only offer
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

GET    /api/providers                    POST   /api/sync
GET    /api/catalog

GET    /api/catalog                      every model Higgsfield offers (59)
POST   /api/catalog/seed                 register the configured ones
GET    /api/cron/tick                    the queue driver (guarded by CRON_SECRET)

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

The deployment shape is tested too, not just described: `tests/test_deploy.py` asserts
that every function in `api/` is covered by a `vercel.json` pattern, that the cron entry
points at a route that exists, and that the deny rules for `/app/*`, `/data/*`,
`/uploads/*` and `.env*` are present.

## The model catalogue

`app/catalog.py` lists **every model Higgsfield offers** — 24 image, 25 video, 5 3D and
5 audio — with the job-set id their CLI actually accepts (`nano_banana_2`,
`gpt_image_2_5`, `seedance_2_5`), the parameters each documents, and the capabilities
derived from those parameters.

It is generated, not typed:

```bash
python scripts/gen_catalog.py          # refresh from Higgsfield's published model list
python scripts/gen_env_example.py      # ...and rebuild the per-model env section
python -m app.cli catalog              # what is configured, and what is missing
```

`tests/test_catalog.py` runs both `--check` modes, so a model added upstream fails the
suite instead of quietly going missing.

Higgsfield publishes no REST API key; their supported programmatic path is the official
CLI (`higgsfield auth login`). The catalogue therefore records the real job-set id rather
than inventing an endpoint. A model with no credential is `configured: false`, names the
variable that would fix it, and is hidden from the studios.

## Configuration

See `.env.example`. The two values that matter:

- **`APP_ENCRYPTION_KEY`** — required in production. Without it, development runs with a
  throwaway key and says so, so the failure is obvious locally instead of quietly
  persisting credentials that cannot be read back.
- **`ALLOW_PRIVATE_UPSTREAM`** — development only. Setting it to `1` lets you connect to a
  model server on localhost, and disables the SSRF guard. `app.cli check` warns when
  production has it set.

## Deploy to Vercel

The app runs unchanged on Vercel: three Python functions (`api/page.py` for the page,
`api/index.py` for the whole API, `api/cron/tick.py` for the queue) and no build step.

```bash
vercel link            # project: whatever you name it
vercel pull --environment production
# per model: <PREFIX>_API_KEY, <PREFIX>_MODEL, <PREFIX>_API_URL
vercel env add NANO_BANANA_2_API_KEY
vercel env add DATABASE_URL            # Postgres (Neon, Supabase, Vercel Postgres)
vercel env add BLOB_READ_WRITE_TOKEN   # Storage → Blob, then the connection string
vercel env add CRON_SECRET             # any long random string
vercel deploy --prod
```

Then `curl https://<deployment>/api/health`. It answers `"status": "ok"` when the
deployment is sound and `"degraded"` with a `problems` list when it is not — a missing
Postgres URL or Blob token is reported there rather than discovered on the first upload.

### What a serverless platform changes, and what it forced

The spec assumes background workers and a local disk. Neither exists on Vercel, so three
things are different — all of them configured by environment, never guessed:

- **The queue is driven, not threaded.** `app/services/runner.py` claims a job with a
  lease (`lease_token`, `lease_until`) and runs it. Submitting a job drives it inline for
  up to `INLINE_BUDGET_SECONDS` (20) before returning the `202`; `GET /api/cron/tick`
  is the backstop that picks up anything left. Two invocations cannot run the same job,
  and a dead invocation's lease expires instead of blocking the queue forever.
- **Storage is Blob, not disk.** `app/storage.py` picks a backend from configuration.
  With no `BLOB_READ_WRITE_TOKEN` on Vercel the app reports `degraded` rather than
  writing files that vanish when the function sleeps.
- **The database is Postgres.** With no `DATABASE_URL` on Vercel it reports `degraded`,
  because SQLite on an ephemeral filesystem loses every job and credential.

### Two deployment problems the first real build exposed

Both were found by running `vercel build` locally, not on the dashboard:

1. **A stale `package.json` hijacked the build.** A leftover React/Vite scaffold from an
   earlier draft made Vercel run `npm run build` for an app that has no Node code. It
   moved to `docs/removed-scaffold/`. `vercel build` then detected Python and built the
   three functions.
2. **The whole repo was published as static.** `data/`, `uploads/`, `tests/`, logs and
   `app/` were all downloadable. `.vercelignore` trims the upload, and `vercel.json` adds
   explicit `404` rules for `/app/*`, `/data/*`, `/uploads/*`, `/tests/*`, `/scripts/*`
   and `.env*` so a future routing change cannot re-expose them.

### Verify a deployment

Deployment Protection turns every URL into a 302 to SSO, so `curl` on the alias proves
nothing. Use the authenticated path:

```bash
vercel curl "/api/health" --deployment <immutable-deployment-url>
vercel curl "/" --deployment <immutable-deployment-url> | head -c 200
```

## Known limits

Documented honestly in [`docs/limits.md`](docs/limits.md): what is not built, what is
approximated, and which claims this app deliberately refuses to make.
