"""Running a job: call the provider, store the output, record usage (spec §24.10).

Separated from `jobs.py` so the state machine can be tested without a provider. This
module does the I/O and nothing else.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import mimetypes
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from .. import crypto, store
from ..adapters import get as get_adapter
from ..adapters.base import Connection
from ..config import UPSTREAM_MAX_BYTES
from ..errors import ApiError, classify_upstream
from ..security_url import check_url, resolve_pinned_ip
from . import jobs as jobs_service

log = logging.getLogger("studio.execution")

MIME_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
    "image/gif": ".gif", "video/mp4": ".mp4", "video/webm": ".webm",
    "video/quicktime": ".mov",
}


def _connection_for(job: Dict[str, Any]) -> Tuple[Dict[str, Any], Connection]:
    from .connections import get_connection

    row = get_connection(job["connection_id"]) if job.get("connection_id") else None
    if not row:
        raise ApiError("connection_failed",
                       "The connection this job used has been deleted.", status=409)
    secret = crypto.decrypt(row.get("secret_encrypted", ""))
    if not secret and row["kind"] != "mock":
        # A key that cannot be decrypted is treated as missing, which the provider
        # reports as invalid_api_key — the honest outcome after a key rotation.
        raise ApiError("invalid_api_key",
                       "The stored key for this connection could not be read. "
                       "Re-enter it on the connection.")
    pinned = ""
    if row["kind"] != "mock":
        try:
            pinned = resolve_pinned_ip(row["base_url"])
        except ApiError:
            pinned = ""
    return row, Connection(base_url=row["base_url"], secret=secret,
                           provider=row["kind"], pinned_ip=pinned)


def execute_job(job: Dict[str, Any]) -> None:
    """Run one job to completion (or to a recorded failure). Never returns a raw error."""
    job_id = job["id"]
    model = store.row("SELECT * FROM models WHERE id=?", (job.get("model_pk"),))
    if not model:
        raise ApiError("invalid_model_id", "The model for this job no longer exists.")

    conn_row, conn = _connection_for(job)
    adapter = get_adapter(conn_row["kind"])
    model_id = model["model_id"]

    try:
        params = store.load_json(job.get("params_json"), {})
    except Exception:
        params = {}
    references = _load_references(job)

    jobs_service._transition(job_id, "running", detail="calling the provider", progress=15)

    result = adapter.generate(conn, model_id, job["prompt"], params, references)

    # A provider that answers with a job id is polled to completion — including the
    # offline adapter, whose demo video only materialises after a short delay. Skipping
    # the poll for it left the job with no outputs at all.
    if result.pending and result.provider_job_id:
        result = _await_provider(conn, adapter, job_id, result.provider_job_id, params)

    # Record what the provider said it did, then store the bytes.
    _record_usage(job, model, adapter, params, result)

    if result.outputs:
        _store_outputs(job, adapter, result.outputs)

    if not result.outputs:
        raise ApiError("invalid_provider_response",
                       "The provider finished without returning an output.", status=502)

    jobs_service._transition(job_id, "succeeded",
                             detail=f"{len(result.outputs)} output(s) stored",
                             progress=100, finished_at=store.utcnow())


def _await_provider(conn: Connection, adapter, job_id: str, provider_job_id: str,
                    params: Dict[str, Any], max_wait: float = 900):
    """Poll an asynchronous provider job, honouring cancellation.

    Returns the final `GenerationResult`, which carries the outputs. Returning them
    matters: the caller stores what this returns, so dropping them here would finish a
    job with nothing to show while the provider had actually produced a file.
    """
    started = time.time()
    interval = 2.0
    while time.time() - started < max_wait:
        if jobs_service._is_cancelled(job_id):
            try:
                adapter.cancel(conn, provider_job_id)
            except ApiError:
                pass          # a provider that cannot cancel still gets marked locally
            raise ApiError("job_cancelled", "Cancelled while the provider was working.")
        time.sleep(interval)
        interval = min(interval * 1.4, 10.0)

        elapsed = time.time() - started
        progress = min(90, 20 + int(elapsed))
        jobs_service._transition(job_id, "running", detail="waiting on the provider",
                                 progress=progress)

        result = adapter.poll(conn, provider_job_id)
        if result.pending:
            continue
        if result.outputs:
            return result
        raise ApiError("invalid_provider_response",
                       "The provider reported the job finished with no output.", status=502)
    raise ApiError("provider_timeout", "The provider did not finish in time.", status=504)


def _load_references(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Reference assets, in the shape a provider can accept.

    A provider needs a URL it can fetch, and this app's assets are local files. Rather
    than invent one, a reference only becomes a URL when the asset itself came from a
    provider (so it already has one); otherwise the path is passed and the adapter
    decides — the image adapter refuses it with an explanation instead of sending a
    path the provider cannot read.
    """
    ids = store.load_json(job.get("reference_asset_ids"), [])
    out: List[Dict[str, Any]] = []
    for asset_id in ids:
        row = store.row("SELECT * FROM assets WHERE id=?", (asset_id,))
        if not row:
            continue
        meta = store.load_json(row.get("metadata_json"), {})
        entry: Dict[str, Any] = {"media_type": row["media_type"], "asset_id": asset_id,
                                 "path": row["path"]}
        remote = meta.get("remote_url") if isinstance(meta, dict) else None
        if isinstance(remote, str) and remote.startswith(("http://", "https://")):
            entry["url"] = remote
        out.append(entry)
    return out


def _store_outputs(job: Dict[str, Any], adapter, outputs: List[Dict[str, Any]]) -> None:
    from .assets import store_generated_bytes, store_remote_output

    existing = store.scalar("SELECT COUNT(*) FROM outputs WHERE job_id=?", (job["id"],)) or 0
    for index, item in enumerate(outputs):
        position = existing + index
        try:
            if item.get("png") or item.get("bytes"):
                asset_id = store_generated_bytes(
                    job, item.get("png") or item.get("bytes"),
                    item.get("mime") or "image/png", position)
                store.execute(
                    "INSERT INTO outputs (id, job_id, owner_id, project_id, asset_id, "
                    "mime, position, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (store.new_id("out_"), job["id"], job["owner_id"], job["project_id"],
                     asset_id, item.get("mime") or "image/png", position, store.utcnow()))
            elif item.get("remote_url"):
                asset_id, mime, meta = store_remote_output(job, item["remote_url"], position,
                                                           adapter)
                store.execute(
                    "INSERT INTO outputs (id, job_id, owner_id, project_id, asset_id, "
                    "remote_url, mime, width, height, duration_ms, position, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (store.new_id("out_"), job["id"], job["owner_id"], job["project_id"],
                     asset_id, item["remote_url"], mime, meta.get("width"),
                     meta.get("height"), meta.get("duration_ms"), position, store.utcnow()))
            elif item.get("video_placeholder"):
                asset_id = store_generated_bytes(job, _placeholder_video(), "video/mp4", position)
                store.execute(
                    "INSERT INTO outputs (id, job_id, owner_id, project_id, asset_id, "
                    "mime, duration_ms, position, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (store.new_id("out_"), job["id"], job["owner_id"], job["project_id"],
                     asset_id, "video/mp4", int(item.get("duration") or 4) * 1000,
                     position, store.utcnow()))
        except ApiError:
            raise
        except Exception:                                    # noqa: BLE001
            log.exception("could not store output %s for job %s", index, job["id"])
            raise ApiError("storage_failure", "An output could not be stored.", status=500)


def _placeholder_video() -> bytes:
    """A tiny, valid MP4 so the demo video path has a real file behind it.

    The bytes are a minimal MPEG-4 container header; the UI labels anything from the
    mock adapter as a demo, so this cannot be mistaken for a real generation.
    """
    return (b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
            b"\x00\x00\x00\x08free" + b"\x00" * 32)


def _record_usage(job: Dict[str, Any], model: Dict[str, Any], adapter,
                  params: Dict[str, Any], result) -> None:
    """One usage record per job. Cost is the adapter's real number, or unknown."""
    cost = adapter.estimate_cost(model["model_id"], params)
    quantity = len(result.outputs) or 1
    store.execute(
        "INSERT INTO usage_records (id, owner_id, workspace_id, generation_job_id, "
        "provider, model_id, usage_type, quantity, estimated_cost, currency, "
        "cost_source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (store.new_id("use_"), job["owner_id"], "default", job["id"],
         model["model_id"].split("/")[0] if "/" in model["model_id"] else adapter.name,
         model["model_id"], job["kind"], quantity, cost, adapter.cost_currency,
         "estimated" if cost is not None else "unknown", store.utcnow()))


def download_for_storage(url: str, adapter, conn: Optional[Connection] = None) -> Tuple[bytes, str]:
    """Fetch a generated artefact for local storage (spec §21).

    The URL comes from a provider, so it is treated as untrusted input: it must be
    http(s) and it is fetched with redirects disabled.
    """
    check_url(url)
    client = adapter.client
    response = client.request("GET", url, {}, None, "")
    if not response.ok:
        raise classify_upstream(response.status, response.text())
    if len(response.body) > UPSTREAM_MAX_BYTES:
        raise ApiError("media_too_large",
                       f"The output is larger than {UPSTREAM_MAX_BYTES // (1024 * 1024)} MB.",
                       status=502)
    mime = response.headers.get("content-type", "").split(";")[0].strip()
    if not mime:
        mime = mimetypes.guess_type(urllib.parse.urlsplit(url).path)[0] or "application/octet-stream"
    return response.body, mime
