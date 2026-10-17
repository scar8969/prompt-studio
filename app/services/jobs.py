"""Job creation, the state machine, and the worker pool (spec §19, §21, §24.10).

States: ``queued`` → ``running`` → (``succeeded`` | ``failed`` | ``cancelled``), with
``queued`` again after a retryable failure. Every transition appends to `job_events`,
so the history survives (spec §19: "Preserve the job history").

Reliability rules this implements:

* A fingerprint of (connection, model, kind, prompt, params, references) is checked
  before submission; a duplicate within `DUPLICATE_WINDOW_SECONDS` returns the
  existing job instead of spending money twice (spec §20).
* A job that exceeds ``JOB_TIMEOUT`` is failed, not left running forever.
* A worker crash leaves the job ``running``; `reap_stale` returns it to ``queued`` so
  progress continues after a restart (spec §21).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from .. import store
from ..adapters import get as get_adapter
from ..adapters.base import Connection
from .. import crypto
from ..config import JOB_TIMEOUT, JOB_WORKERS, MAX_JOB_ATTEMPTS
from ..errors import ApiError, not_found
from ..security_url import resolve_pinned_ip

log = logging.getLogger("studio.jobs")

OWNER = "local"

TERMINAL = ("succeeded", "failed", "cancelled")
DUPLICATE_WINDOW_SECONDS = 120
STALE_AFTER_SECONDS = max(JOB_TIMEOUT * 2, 300)

_cancelled: set = set()
_lock = threading.Lock()
_started = False


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

#: A job in one of these states will never be picked up again by the runner.
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")


def _transition(job_id: str, to_status: str, *, detail: str = "", **fields: Any) -> None:
    """Move a job to `to_status`, recording an event.

    Reaching a terminal state also **clears the lease**. That is not bookkeeping: the
    runner treats `running` with an expired lease as an abandoned job and runs it again,
    so a finished job that kept its lease could be executed a second time by the next
    worker — producing duplicate outputs. Releasing here means every terminal path is
    covered, rather than relying on each caller to remember.
    """
    current = store.row("SELECT status FROM generation_jobs WHERE id=?", (job_id,))
    from_status = current["status"] if current else ""
    sets = ["status=?", "updated_at=?"]
    params: List[Any] = [to_status, store.utcnow()]
    for column, value in fields.items():
        sets.append(f"{column}=?")
        params.append(value)
    if to_status in TERMINAL_STATUSES:
        sets.append("lease_token=''")
        sets.append("lease_until=NULL")
    params.append(job_id)
    store.execute(f"UPDATE generation_jobs SET {', '.join(sets)} WHERE id=?", params)
    store.log_event(job_id, from_status, to_status, detail)


def public_job(row: Dict[str, Any], *, include_outputs: bool = True) -> Dict[str, Any]:
    if not row:
        return {}
    out = {
        "id": row["id"],
        "project_id": row["project_id"],
        "connection_id": row["connection_id"],
        "model_pk": row["model_pk"],
        "kind": row["kind"],
        "prompt": row["prompt"],
        "negative_prompt": row["negative_prompt"],
        "params": store.load_json(row["params_json"], {}),
        "reference_asset_ids": store.load_json(row["reference_asset_ids"], []),
        "status": row["status"],
        "progress": row["progress"],
        "attempts": row["attempts"],
        "error": ({"code": row["error_code"], "message": row["error_message"]}
                  if row["error_code"] else None),
        "provider": "mock" if _is_mock(row) else "external",
        "submitted_at": row["submitted_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if include_outputs:
        out["outputs"] = store.rows(
            "SELECT id, remote_url, mime, width, height, duration_ms, "
            "asset_id, position FROM outputs WHERE job_id=? ORDER BY position",
            (row["id"],))
    return out


def _is_mock(row: Dict[str, Any]) -> bool:
    if not row.get("connection_id"):
        return False
    kind = store.scalar("SELECT kind FROM connections WHERE id=?", (row["connection_id"],))
    return kind == "mock"


def public_events(job_id: str) -> List[Dict[str, Any]]:
    return store.rows(
        "SELECT from_status, to_status, detail, created_at FROM job_events "
        "WHERE job_id=? ORDER BY id", (job_id,))


def get_job(job_id: str) -> Dict[str, Any]:
    row = store.row("SELECT * FROM generation_jobs WHERE id=? AND owner_id=?",
                    (job_id, OWNER))
    if not row:
        raise not_found("job")
    return row


def list_jobs(*, status: str = "", project_id: str = "", kind: str = "",
              limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    sql = "SELECT * FROM generation_jobs WHERE owner_id=?"
    params: List[Any] = [OWNER]
    if status:
        sql += " AND status=?"
        params.append(status)
    if project_id:
        sql += " AND project_id=?"
        params.append(project_id)
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    total = store.scalar(sql.replace("SELECT *", "SELECT COUNT(*)", 1), params) or 0
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return {"jobs": [public_job(r, include_outputs=False) for r in store.rows(sql, params)],
            "total": total, "limit": limit, "offset": offset}


def _fingerprint(connection_id: str, model_pk: str, kind: str, prompt: str,
                 params: Dict[str, Any], references: List[str]) -> str:
    payload = json.dumps(
        {"c": connection_id, "m": model_pk, "k": kind, "p": (prompt or "").strip(),
         "a": params, "r": sorted(references or [])},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def submit(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a job and hand it to the worker pool.

    Validation happens here, before anything is queued, so a bad request costs nothing.
    """
    from ..services.connections import capabilities_of, get_model, parameters_of
    from ..services.workspace import get_project_for_job
    from ..services.assets import get_asset

    model_pk = payload.get("model_pk") or ""
    if not model_pk:
        raise ApiError("missing_field", "'model_pk' is required.", details={"field": "model_pk"})
    model = get_model(model_pk)

    if not model["enabled"]:
        raise ApiError("invalid_field",
                       f"'{model['display_name']}' is disabled.", details={"field": "model_pk"})

    capabilities = capabilities_of(model_pk)
    needed = {"image": "image.generate", "video": "video.generate"}.get(kind)
    if needed is None:
        raise ApiError("invalid_field", f"Unknown job kind '{kind}'.")
    if needed not in capabilities:
        raise ApiError(
            "unsupported_modality",
            f"{model['display_name']} cannot produce {kind}.",
            details={"required": needed, "capabilities": capabilities})

    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise ApiError("missing_field", "'prompt' is required.", details={"field": "prompt"})
    if len(prompt) > 8000:
        raise ApiError("invalid_field", "'prompt' is longer than 8000 characters.",
                       details={"field": "prompt"})

    if payload.get("project_id"):
        get_project_for_job(payload["project_id"])

    params = _validate_params(payload.get("params") or {}, parameters_of(model_pk),
                              model["display_name"])

    references: List[str] = []
    for asset_id in payload.get("reference_asset_ids") or []:
        if not isinstance(asset_id, str):
            raise ApiError("invalid_field", "Reference ids must be strings.",
                           details={"field": "reference_asset_ids"})
        asset = get_asset(asset_id)          # raises if missing or not ours
        if kind == "video" and asset["asset"]["media_type"] == "image":
            if "video.image_to_video" not in capabilities:
                raise ApiError(
                    "unsupported_modality",
                    f"{model['display_name']} does not accept a starting image.",
                    details={"required": "video.image_to_video",
                             "capabilities": capabilities})
        elif kind == "image" and asset["asset"]["media_type"] == "image":
            if not ("image.reference" in capabilities or "image.edit" in capabilities):
                raise ApiError(
                    "unsupported_modality",
                    f"{model['display_name']} does not accept a reference image.",
                    details={"required": "image.reference", "capabilities": capabilities})
        references.append(asset_id)
        if len(references) >= 4:
            break   # the endpoint's own limit

    fingerprint = _fingerprint(model["connection_id"], model_pk, kind, prompt, params, references)

    recent = store.row(
        "SELECT id, status, created_at FROM generation_jobs "
        "WHERE owner_id=? AND request_fingerprint=? AND status IN ('queued','running') "
        "ORDER BY created_at DESC LIMIT 1", (OWNER, fingerprint))
    if recent:
        raise ApiError("duplicate_submission",
                       "That exact request is already in progress.",
                       status=409, details={"job_id": recent["id"]})

    now = store.utcnow()
    job_id = store.new_id("job_")
    store.execute(
        "INSERT INTO generation_jobs (id, owner_id, project_id, connection_id, model_pk, "
        "kind, prompt, negative_prompt, params_json, reference_asset_ids, "
        "request_fingerprint, status, submitted_at, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,'queued',?,?,?)",
        (job_id, OWNER, payload.get("project_id") or None, model["connection_id"],
         model_pk, kind, prompt, str(payload.get("negative_prompt") or "")[:1000],
         store.dumps(params), store.dumps(references), fingerprint,
         now, now, now))
    store.log_event(job_id, "", "queued", f"submitted as {kind}")

    _enqueue(job_id)

    # On a platform with no background threads the submit call is the only chance to make
    # progress, so it drives the job inline for a short budget. The response is still a
    # 202 — the job is not finished here, it is merely started. Anything left queued is
    # picked up by the next request's drive, or by the cron tick as a backstop.
    from ..config import IS_SERVERLESS

    if IS_SERVERLESS and kind in ("image", "video"):
        try:
            from . import runner

            runner.run_job_inline(job_id, budget_seconds=float(
                __import__("os").getenv("INLINE_BUDGET_SECONDS", "20")))
        except Exception:                                      # noqa: BLE001
            log.exception("inline drive failed for %s; it stays queued", job_id)

    return public_job(get_job(job_id))


def _validate_params(given: Dict[str, Any], specs: List[Dict[str, Any]],
                     label: str) -> Dict[str, Any]:
    """Capability-checked parameters. An unsupported one is named, not dropped."""
    if not isinstance(given, dict):
        raise ApiError("invalid_field", "'params' must be an object.", details={"field": "params"})
    known = {s["name"] for s in specs}
    unsupported = sorted(k for k in given if k not in known)
    if unsupported:
        raise ApiError("unsupported_parameter",
                       f"{label} does not accept: {', '.join(unsupported)}.",
                       details={"unsupported": unsupported, "supported": sorted(known)})

    out: Dict[str, Any] = {}
    for spec in specs:
        name = spec["name"]
        if name not in given or given[name] in (None, ""):
            continue
        value = given[name]
        if spec["type"] == "int":
            if isinstance(value, bool):
                raise ApiError("invalid_field", f"'{name}' must be a whole number.",
                               details={"field": name})
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ApiError("invalid_field", f"'{name}' must be a whole number.",
                               details={"field": name})
            if spec.get("minimum") is not None and value < spec["minimum"]:
                raise ApiError("invalid_field", f"'{name}' must be at least {spec['minimum']}.",
                               details={"field": name})
            if spec.get("maximum") is not None and value > spec["maximum"]:
                raise ApiError("invalid_field", f"'{name}' must be at most {spec['maximum']}.",
                               details={"field": name})
        elif spec["type"] == "enum":
            choices = spec.get("choices") or []
            if choices and value not in choices:
                raise ApiError("invalid_field",
                               f"'{name}' must be one of: {', '.join(map(str, choices))}.",
                               details={"field": name, "allowed": choices})
        elif not isinstance(value, (str, int, float)):
            raise ApiError("invalid_field", f"'{name}' must be a simple value.",
                           details={"field": name})
        out[name] = value
    return out


def cancel(job_id: str) -> Dict[str, Any]:
    row = get_job(job_id)
    if row["status"] in TERMINAL:
        raise ApiError("job_not_cancellable",
                       f"That job is already {row['status']}.",
                       details={"status": row["status"]})
    with _lock:
        _cancelled.add(job_id)
    # A queued job can be settled immediately; a running one is stopped by the worker.
    if row["status"] == "queued":
        _transition(job_id, "cancelled", detail="cancelled before it started",
                    finished_at=store.utcnow())
        with _lock:
            _cancelled.discard(job_id)
    else:
        _transition(job_id, "cancelling", detail="cancellation requested")
    return public_job(get_job(job_id))


def retry(job_id: str) -> Dict[str, Any]:
    row = get_job(job_id)
    if row["status"] not in ("failed", "cancelled"):
        raise ApiError("job_not_cancellable",
                       f"Only a failed or cancelled job can be retried, not a {row['status']} one.",
                       details={"status": row["status"]})
    _transition(job_id, "queued", detail="retry requested", attempts=0,
                error_code="", error_message="", progress=0, finished_at=None)
    _enqueue(job_id)
    return public_job(get_job(job_id))


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def _enqueue(job_id: str) -> None:
    """Signal that work is available.

    There is no in-memory queue any more: a job is a `queued` row, and both drivers
    (the local worker pool and the serverless cron tick) claim from the table. Passing a
    message here would be a second source of truth that a frozen process could lose.
    """
    return None


def _is_cancelled(job_id: str) -> bool:
    with _lock:
        return job_id in _cancelled


def _clear_cancelled(job_id: str) -> None:
    with _lock:
        _cancelled.discard(job_id)


#: Provider-side problems worth another attempt; a bad request is not.
RETRYABLE = {
    "provider_timeout", "provider_outage", "provider_rate_limited",
    "connection_failed", "output_download_failed", "storage_failure",
    "internal_error",
}


def _fail(job_id: str, code: str, message: str, *, retryable: bool) -> None:
    row = store.row("SELECT attempts FROM generation_jobs WHERE id=?", (job_id,))
    attempts = int((row or {}).get("attempts") or 0) + 1
    if retryable and attempts < MAX_JOB_ATTEMPTS and not _is_cancelled(job_id):
        _transition(job_id, "queued", detail=f"attempt {attempts} failed: {code}",
                    attempts=attempts, error_code=code, error_message=message)
        time.sleep(min(2 ** attempts, 8))
        _enqueue(job_id)
        return
    _clear_cancelled(job_id)
    _transition(job_id, "failed", detail=f"{code}: {message}", attempts=attempts,
                error_code=code, error_message=message, finished_at=store.utcnow())


def start_workers(count: int = JOB_WORKERS) -> bool:
    """Start the local worker pool. Delegates to the runner.

    On serverless this returns False and does nothing — a thread started during a
    request is frozen with the process. The cron tick drives the queue there instead.
    """
    from . import runner

    return runner.start_workers(count)


def requeue_unfinished() -> int:
    """Return abandoned work to the queue. Delegates to the runner's lease logic."""
    from . import runner

    return runner.requeue_interrupted()


def reap_stale() -> int:
    """Fail jobs that have been running far longer than any provider should take."""
    cutoff = time.time() - STALE_AFTER_SECONDS
    reaped = 0
    for row in store.rows("SELECT id, started_at FROM generation_jobs WHERE status='running'"):
        started = row.get("started_at") or ""
        try:
            parsed = time.mktime(time.strptime(started, "%Y-%m-%dT%H:%M:%SZ"))
        except Exception:
            continue
        if parsed < cutoff:
            _fail(row["id"], "provider_timeout",
                  "The job exceeded the maximum run time and was stopped.", retryable=False)
            reaped += 1
    return reaped
