"""Moving work forward on a platform that has no background threads.

Locally the app runs a worker pool in-process. On Vercel there is no such thing: a
function is frozen the moment it responds, so a thread started during a request does
not continue. Jobs would sit in `queued` forever.

The design that works under both:

* **A job is claimed with a lease.** `claim_next` picks one `queued` job and stamps
  `lease_until`. Two invocations cannot claim the same job, and if the one that claimed
  it dies, the lease expires and the job is available again. That is what makes this
  resumable rather than best-effort.
* **Progress is persisted, not remembered.** Every state change is a row write, so a
  job survives the process that started it.
* **Both drivers call the same `execute_job`.** The in-process worker and the HTTP tick
  are two ways to arrive at the same function — not two implementations.

So on Vercel: `POST /api/generations/*` enqueues and returns; a `vercel.json` cron hits
`GET /api/cron/tick`, which claims and runs one job per invocation. Locally: the
worker pool does, and the tick endpoint exists but is unused.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from .. import db as database
from .. import store
from ..config import IS_SERVERLESS, JOB_WORKERS

log = logging.getLogger("studio.runner")

#: How long a claim is good for. Longer than any single provider call the app makes,
#: short enough that a dead invocation does not stall a job for long.
LEASE_SECONDS = int(os.getenv("JOB_LEASE_SECONDS", "300"))

_worker_lock = threading.Lock()
_workers_started = False


# ---------------------------------------------------------------------------
# Claiming
# ---------------------------------------------------------------------------

def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def claim_next() -> Optional[Dict[str, Any]]:
    """Claim one runnable job, or return None.

    Runnable means `queued`, or `running` with an expired lease — the latter is a job
    whose worker died, and it is picked up again rather than being lost.
    """
    now = datetime.now(timezone.utc)
    lease = _iso(now + timedelta(seconds=LEASE_SECONDS))

    # A single statement claims atomically on both engines: the UPDATE only matches a
    # row that is still claimable, and the caller then reads back the row it stamped.
    token = store.new_id("lease_")
    store.execute(
        "UPDATE generation_jobs SET lease_token=?, lease_until=?, updated_at=? "
        "WHERE id = ("
        "  SELECT id FROM generation_jobs "
        "  WHERE status='queued' OR (status='running' AND lease_until IS NOT NULL AND lease_until < ?) "
        "  ORDER BY created_at LIMIT 1"
        ")",
        (token, lease, store.utcnow(), _iso(now)),
    )

    claimed = store.row("SELECT * FROM generation_jobs WHERE lease_token=?", (token,))
    if not claimed:
        return None
    # A row can be stamped by the UPDATE above only if it was claimable; the token
    # round trip confirms it was this claim that won.
    return claimed


def release(job_id: str) -> None:
    store.execute(
        "UPDATE generation_jobs SET lease_token='', lease_until=NULL WHERE id=?", (job_id,))


def extend_lease(job_id: str) -> None:
    """Keep the claim alive during a long provider poll."""
    store.execute(
        "UPDATE generation_jobs SET lease_until=? WHERE id=?",
        (_iso(datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)), job_id))


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

def run_one() -> Dict[str, Any]:
    """Claim and run a single job. Returns what happened, for the tick endpoint."""
    job = claim_next()
    if not job:
        return {"ran": False, "reason": "nothing queued"}

    job_id = job["id"]
    log.info("running job %s", job_id)
    from . import execution
    from . import jobs as jobs_service

    # The transition the old in-process worker used to make. Without it a job that runs
    # this way never records `started_at`, which the API and the latency figure both read.
    try:
        jobs_service._transition(job_id, "running", detail="claimed by the runner",
                                 started_at=store.utcnow(), progress=5)
        job = store.row("SELECT * FROM generation_jobs WHERE id=?", (job_id,)) or job
        execution.execute_job(job)
        outcome = "ok"
    except Exception as exc:                                   # noqa: BLE001
        # `execute_job` records its own failures; anything escaping it is a bug and is
        # logged with a traceback, then surfaced as a failed job rather than a 500.
        log.exception("job %s raised out of execute_job", job_id)
        from ..errors import ApiError

        code = getattr(exc, "code", "internal_error")
        message = getattr(exc, "message", "The job failed unexpectedly.")
        jobs_service._fail(job_id, code, message, retryable=False)
        outcome = "failed"
    finally:
        release(job_id)

    row = store.row("SELECT status FROM generation_jobs WHERE id=?", (job_id,))
    return {"ran": True, "job_id": job_id, "outcome": outcome,
            "status": (row or {}).get("status")}


def run_batch(limit: int = 3, budget_seconds: float = 8.0) -> Dict[str, Any]:
    """Run a few jobs within a time budget — what a serverless tick can afford.

    The budget matters: Vercel kills a function at its `maxDuration`, and a job killed
    mid-flight would leave a lease to expire. Finishing early is better than being
    killed, so the loop stops while it still has time to write its result.
    """
    started = time.time()
    results = []
    for _ in range(max(1, limit)):
        if time.time() - started > budget_seconds:
            results.append({"ran": False, "reason": "budget spent"})
            break
        outcome = run_one()
        results.append(outcome)
        if not outcome.get("ran"):
            break
    return {"runs": results, "elapsed_ms": int((time.time() - started) * 1000)}


# ---------------------------------------------------------------------------
# The local driver: a worker pool, which serverless does not have
# ---------------------------------------------------------------------------

def _loop(worker_id: int) -> None:
    idle = 0.4
    while True:
        try:
            outcome = run_one()
        except Exception:                                      # noqa: BLE001
            log.exception("worker %s loop error", worker_id)
            time.sleep(1)
            continue
        if outcome.get("ran"):
            idle = 0.4
        else:
            time.sleep(idle)
            idle = min(idle * 1.5, 3.0)


def start_workers(count: int = JOB_WORKERS) -> bool:
    """Start the in-process pool. Returns False on serverless, where it cannot work.

    Returning the fact rather than silently doing nothing is the point: the caller can
    tell the operator that jobs need the cron tick instead.
    """
    global _workers_started
    if IS_SERVERLESS:
        log.info("serverless: in-process workers disabled; jobs run via the cron tick")
        return False
    with _worker_lock:
        if _workers_started:
            return True
        _workers_started = True
    for index in range(max(1, count)):
        threading.Thread(target=_loop, args=(index,), name=f"job-worker-{index}",
                         daemon=True).start()
    log.info("started %d job worker(s)", max(1, count))
    return True


def requeue_interrupted() -> int:
    """Return abandoned work to the queue on startup.

    A `running` job with no lease (written before leases existed, or left by a crash) is
    put back to `queued`; one whose lease is still live is left alone, because another
    invocation may genuinely be working on it.
    """
    now = _iso(datetime.now(timezone.utc))
    abandoned = store.rows(
        "SELECT id FROM generation_jobs WHERE status='running' "
        "AND (lease_until IS NULL OR lease_until < ?)", (now,))
    for row in abandoned:
        store.execute(
            "UPDATE generation_jobs SET status='queued', lease_token='', lease_until=NULL, "
            "updated_at=? WHERE id=?", (store.utcnow(), row["id"]))
        store.log_event(row["id"], "running", "queued", "requeued: the claim expired")
    return len(abandoned)


def run_job_inline(job_id: str, budget_seconds: float = 20.0) -> Dict[str, Any]:
    """Run one specific job now, inside this request.

    Used on serverless, where the submit call is the only execution opportunity before
    the process is frozen. The job is claimed first — the same lease as the cron path —
    so a concurrent tick cannot run it twice.

    The budget is a hard stop: if the provider is still working when it expires, the job
    is left `running` with a live lease and reported as such. It is *not* marked failed,
    because nothing went wrong — it is simply unfinished, and the next tick continues it.
    """
    started = time.time()
    job = store.row("SELECT * FROM generation_jobs WHERE id=?", (job_id,))
    if not job:
        return {"ran": False, "reason": "no such job"}
    if job["status"] not in ("queued", "running"):
        return {"ran": False, "reason": f"status is {job['status']}"}

    token = store.new_id("lease_")
    lease = _iso(datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS))
    store.execute(
        "UPDATE generation_jobs SET lease_token=?, lease_until=?, updated_at=? "
        "WHERE id=? AND status IN ('queued','running')",
        (token, lease, store.utcnow(), job_id))
    claimed = store.row("SELECT * FROM generation_jobs WHERE lease_token=?", (token,))
    if not claimed:
        return {"ran": False, "reason": "claimed by another invocation"}

    from . import execution
    from . import jobs as jobs_service
    from ..errors import ApiError

    try:
        jobs_service._transition(job_id, "running", detail="claimed inline",
                                 started_at=store.utcnow(), progress=5)
        fresh = store.row("SELECT * FROM generation_jobs WHERE id=?", (job_id,)) or claimed
        execution.execute_job(fresh)
        outcome = "ok"
    except ApiError as exc:
        jobs_service._fail(job_id, exc.code, exc.message,
                           retryable=exc.code in jobs_service.RETRYABLE)
        outcome = "failed"
    except Exception:                                          # noqa: BLE001
        log.exception("inline run of %s raised", job_id)
        jobs_service._fail(job_id, "internal_error", "The job failed unexpectedly.",
                           retryable=False)
        outcome = "failed"
    finally:
        release(job_id)

    final = store.row("SELECT status FROM generation_jobs WHERE id=?", (job_id,))
    return {"ran": True, "job_id": job_id, "outcome": outcome,
            "status": (final or {}).get("status"),
            "elapsed_ms": int((time.time() - started) * 1000),
            "budget_seconds": budget_seconds}
