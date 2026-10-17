# Limits and what is deliberately not claimed

This file exists because a spec that lists twenty deliverables invites a README that
describes all twenty whether or not they work. Here is what is real, what is partial, and
what this app refuses to pretend.

## Not implemented

| Area | What happens if you try it |
|---|---|
| **Editing an existing image** (inpaint, replace, restyle) | `POST /api/generations/images` with a reference image is refused with `unsupported_parameter` and an explanation that the multipart `/images/edits` shape is not implemented. It is not silently ignored. |
| **Image variations, upscale, video extend/transform** | The capability vocabulary includes them so a model can declare them, and no adapter implements them. A job that asks for one fails with `unsupported_modality`. |
| **Authentication, users, workspaces** | Every row carries `owner_id`/`workspace_id` and every query filters on them, but the owner is the constant `"local"`. This is a single-owner local application. It is **not** safe to expose to more than one person: there is no login, and isolation between users is untested because there are no users. |
| **Backups** | Nothing runs automatically. The database is `data/studio.db` and media is `uploads/`; copy both. |
| **Rate limiting** | Not implemented. A public deployment would need it at the reverse proxy. |
| **Transcoding** | Video is stored as delivered. There is no transcode step, so playback depends on what the provider returned. |

## Approximated, and how

- **Video metadata.** Duration and dimensions are read with `ffprobe` *if it is on the
  PATH*. When it is not, `metadata.probed` is `false` and the fields stay empty. The UI says
  unknown rather than showing a number that was never measured.
- **Cost.** `estimate_cost` returns `None` for every adapter currently shipped, because no
  published per-image rate is available to this codebase. Usage records therefore say
  `cost_source: unknown`, and `/api/analytics` sums only the records that have a real cost
  while reporting `cost_known_for` alongside — so a partial total cannot be read as a
  complete one.
- **Capability discovery** parses name patterns (`video`, `i2v`, `edit`) from a provider's
  model listing. That is a heuristic about naming, not a documented contract. A model whose
  name says nothing useful is registered with `image.generate` only. If a provider documents
  capabilities properly, `POST /api/connections/{id}/models` accepts them explicitly.
- **The demo adapter** renders a placeholder with a visible banner and reports no cost. It
  is selected only by an explicit `kind: "mock"` connection, never as a fallback.

## Deliberate refusals

These are cases where doing the convenient thing would be dishonest:

1. **No fabricated outputs.** A job that did not produce a stored file is `failed`. The
   `outputs` table is only written after bytes exist on disk.
2. **No fabricated prices.** See above. `estimated_cost: null` is preferred to a guess.
3. **No invented capabilities.** A capability is not granted because a model name suggests
   it, and never taken from the request body.
4. **No silent parameter dropping.** A parameter the model does not document is a `400`
   naming it, with the supported list attached.
5. **An untested connection is not "ok".** `status` starts `untested` and only becomes
   `ok` after a real `/models` call succeeds.
6. **No plaintext credentials.** Not in responses, not in logs (the request logger records
   the request line only, never headers), and not in the database once a connection is
   deleted.

## The old code is still in the repository

`src/server.py` and `src/db.py` are the previous single-file application. They are left in
place rather than deleted, and **nothing in this project imports them**:
`run_studio.py` → `app.web.server`. They also contain a working, independent SSRF
implementation that was useful as a reference.

Relatedly, `docs/README.aetheria-fabricated.md.bak` and the previous `.env.example` /
`package.json` described a Node + Express + MongoDB + React application with Socket.IO,
Replicate and JWT auth. None of that existed in this repository — there was a partial
scaffold of it under `server/` and `src/pages/` that nothing ran. That description has
been replaced by this README, and the scaffold is unreferenced. Deleting it is safe; it is
kept only because removing files the user may not expect to disappear is worse than
leaving a note about them.

## Security notes worth reading

- **SSRF** is blocked in two steps: `check_url` for shape, then `resolve_pinned_ip` whose
  result is the address the client connects to (IP pinning with SNI and Host preserved).
  Redirects are refused, because a 302 to `169.254.169.254` would otherwise defeat both.
- **`ALLOW_PRIVATE_UPSTREAM=1` disables this.** It exists so a local model server can be
  connected during development, and `app.cli check` warns when production has it set.
- **Uploads** are validated by signature, not by the client's declared MIME type, and the
  bytes are decoded before a file is kept. SVG is not accepted, so no uploaded file is ever
  served as active content.
- **The API sends `Cache-Control: no-store`** on every response and does not log headers.
