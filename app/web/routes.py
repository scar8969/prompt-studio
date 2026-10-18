"""Every endpoint (spec §17).

Route groups follow the spec's list. A handler receives a `Request` and returns
``(payload, status)``; anything it raises is turned into the documented error shape by
the dispatcher, so no handler formats an error itself and none can leak a traceback.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Tuple

from .. import capabilities as caps
from .. import store
from ..errors import ApiError, not_found
from ..services import assistant, jobs as jobs_service, workspace
from ..services.assets import (decode_base64_upload, delete_asset, get_asset,
                               list_assets, store_upload)

from ..services import connections as conn_service
from .request import Request, Router

log = logging.getLogger("studio.routes")

router = Router()

# ===========================================================================
# Health
# ===========================================================================

@router.get("/api/health")
def health(req: Request) -> Tuple[Dict[str, Any], int]:
    from ..adapters import names as adapter_names
    from ..config import ALLOW_PRIVATE_UPSTREAM, ENV

    from .. import storage
    from ..config import IS_PRODUCTION, IS_SERVERLESS

    db_health = store.health()
    # On a serverless deployment a missing DATABASE_URL or blob token is the difference
    # between a working app and one that silently loses every job and file. It is
    # reported here rather than discovered on the first upload.
    problems = []
    if IS_SERVERLESS:
        if store.dialect() != "postgres":
            problems.append("DATABASE_URL is not set: the database is a local file that "
                            "will not survive this invocation.")
        if storage.backend() == "disk":
            # Name the Supabase variables first, because that is the documented path;
            # Blob is the fallback and mentioning it alone sent people the wrong way.
            problems.append(
                "No durable file storage: set SUPABASE_STORAGE_BUCKET with "
                "SUPABASE_S3_ACCESS_KEY_ID and SUPABASE_S3_SECRET_ACCESS_KEY (or "
                "BLOB_READ_WRITE_TOKEN). Without one, uploaded and generated files are "
                "lost between invocations.")
        elif storage.backend() == "supabase":
            described = storage.describe()
            if not described.get("supabase_region"):
                problems.append(
                    "SUPABASE_REGION is not set. Storage requests are signed with a "
                    "region, so a wrong one is rejected as a bad key.")
    if IS_PRODUCTION and not os.getenv("CRON_SECRET", "").strip():
        problems.append("CRON_SECRET is not set: the job tick will refuse to run.")
    from ..config import ensure_dirs

    for failure in ensure_dirs():
        problems.append(f"A writable directory could not be created ({failure}).")

    return {
        "status": "ok" if not problems else "degraded",
        "env": ENV,
        "serverless": IS_SERVERLESS,
        "storage": storage.backend(),
        "storage_detail": storage.describe(),
        "database": db_health,
        "problems": problems,
        "adapters": adapter_names(),
        "schema_version": store.schema_version(),
        "counts": {
            "providers": store.scalar("SELECT COUNT(*) FROM connections") or 0,
            "models": store.scalar("SELECT COUNT(*) FROM models") or 0,
            "jobs": store.scalar("SELECT COUNT(*) FROM generation_jobs") or 0,
            "assets": store.scalar("SELECT COUNT(*) FROM assets") or 0,
        },
        "private_upstream_allowed": ALLOW_PRIVATE_UPSTREAM,
    }, 200


@router.get("/api/capabilities")
def capabilities_index(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": caps.CAPABILITIES}, 200


# ===========================================================================
# Providers and models — read-only, because credentials are environment-only
# ===========================================================================
#
# There is no POST here on purpose. A key, a model name and an API URL are set in the
# environment (Vercel → Settings → Environment Variables), so the API can show what is
# configured and hide a model that is not, but it cannot accept a credential. That is
# the BYOK removal: the only writer is the deployment's environment.

@router.get("/api/providers")
def list_providers(req: Request) -> Tuple[Dict[str, Any], int]:
    """The provider endpoints the configured models point at."""
    return {"data": conn_service.list_providers()}, 200


@router.post("/api/sync")
def sync_models(req: Request) -> Tuple[Dict[str, Any], int]:
    """Re-read the environment and update the registry.

    Useful after adding a key on Vercel without waiting for a cold start, and it is what
    the CLI calls. Idempotent, and it never deletes a model (jobs reference them), it
    only enables and disables.
    """
    from ..services import sync

    return {"data": sync.sync_from_env()}, 200


# ===========================================================================
# Model registry
# ===========================================================================

@router.get("/api/models")
def list_models(req: Request) -> Tuple[Dict[str, Any], int]:
    modality = req.q("modality")
    if modality and modality not in ("image", "video", "3d", "audio", "chat"):
        raise ApiError("invalid_field", "Query 'modality' must be image, video or chat.",
                       details={"field": "modality"})
    capability = req.q("capability")
    if capability and capability not in caps.CAPABILITIES:
        raise ApiError("invalid_field", f"Unknown capability '{capability}'.",
                       details={"field": "capability", "known": sorted(caps.CAPABILITIES)})
    models = conn_service.list_models(
        modality=modality,
        enabled_only=not req.q_bool("include_disabled"),
        capability=capability)
    return {"data": models, "total": len(models)}, 200


@router.get("/api/models/{model_pk}")
def get_model_route(req: Request) -> Tuple[Dict[str, Any], int]:
    row = conn_service.get_model(req.path_params["model_pk"])
    return {"data": conn_service.public_model(row)}, 200


@router.get("/api/models/{model_pk}/capabilities")
def model_capabilities(req: Request) -> Tuple[Dict[str, Any], int]:
    row = conn_service.get_model(req.path_params["model_pk"])
    found = conn_service.capabilities_of(row["id"])
    return {"data": {"capabilities": found,
                     "described": {c: caps.CAPABILITIES.get(c, "") for c in found}}}, 200


@router.get("/api/models/{model_pk}/parameters")
def model_parameters(req: Request) -> Tuple[Dict[str, Any], int]:
    row = conn_service.get_model(req.path_params["model_pk"])
    return {"data": conn_service.parameters_of(row["id"])}, 200


@router.patch("/api/models/{model_pk}")
def update_model(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": conn_service.update_model(req.path_params["model_pk"], req.body)}, 200


@router.delete("/api/models/{model_pk}")
def delete_model(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": conn_service.delete_model(req.path_params["model_pk"])}, 200


# ===========================================================================
# Generation
# ===========================================================================

@router.post("/api/generations/images")
def create_image_job(req: Request) -> Tuple[Dict[str, Any], int]:
    job = jobs_service.submit("image", req.body)
    return {"data": job}, 202


@router.post("/api/generations/videos")
def create_video_job(req: Request) -> Tuple[Dict[str, Any], int]:
    job = jobs_service.submit("video", req.body)
    return {"data": job}, 202


@router.get("/api/jobs")
def list_jobs_route(req: Request) -> Tuple[Dict[str, Any], int]:
    status = req.q("status")
    if status and status not in ("queued", "running", "cancelling", "succeeded",
                                 "failed", "cancelled"):
        raise ApiError("invalid_field", f"Unknown status '{status}'.",
                       details={"field": "status"})
    limit, offset = store.paginate(req.q_int("limit"), req.q_int("offset"))
    return jobs_service.list_jobs(status=status, project_id=req.q("project_id"),
                                  kind=req.q("kind"), limit=limit, offset=offset), 200


@router.get("/api/jobs/{job_id}")
def get_job_route(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": workspace.job_detail(req.path_params["job_id"])}, 200


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": jobs_service.cancel(req.path_params["job_id"])}, 200


@router.post("/api/jobs/{job_id}/retry")
def retry_job(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": jobs_service.retry(req.path_params["job_id"])}, 202


# ===========================================================================
# Projects and assets
# ===========================================================================

@router.get("/api/projects")
def list_projects(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": workspace.list_projects()}, 200


@router.post("/api/projects")
def create_project(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": workspace.create_project(req.body)}, 201


@router.get("/api/projects/{project_id}")
def get_project_route(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": workspace.public_project(workspace.get_project(req.path_params["project_id"]))}, 200


@router.patch("/api/projects/{project_id}")
def update_project(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": workspace.update_project(req.path_params["project_id"], req.body)}, 200


@router.delete("/api/projects/{project_id}")
def delete_project(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": workspace.delete_project(req.path_params["project_id"])}, 200


@router.get("/api/assets")
def list_assets_route(req: Request) -> Tuple[Dict[str, Any], int]:
    limit, offset = store.paginate(req.q_int("limit"), req.q_int("offset"))
    return list_assets(project_id=req.q("project_id"), media_type=req.q("media_type"),
                       limit=limit, offset=offset), 200


@router.post("/api/assets/upload")
def upload_asset(req: Request) -> Tuple[Dict[str, Any], int]:
    """Two accepted shapes: base64 in JSON, or multipart (handled by the server)."""
    filename = req.string("filename", "upload")
    declared = req.string("mime", "")
    project_id = req.get("project_id") or None
    if project_id:
        workspace.get_project_for_job(project_id)
    if req.get("data"):
        data = decode_base64_upload(req.get("data"))
    else:
        raise ApiError("missing_field",
                       "Send the file as base64 in 'data', with 'filename' and 'mime'.",
                       details={"field": "data"})
    asset = store_upload(filename, declared, data, project_id=project_id)
    return {"data": asset}, 201


@router.get("/api/assets/{asset_id}")
def get_asset_route(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": get_asset(req.path_params["asset_id"])["asset"]}, 200


@router.delete("/api/assets/{asset_id}")
def delete_asset_route(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": delete_asset(req.path_params["asset_id"])}, 200


# ===========================================================================
# Presets, saved prompts, characters
# ===========================================================================

@router.get("/api/presets")
def list_presets(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": workspace.list_presets(modality=req.q("modality"))}, 200


@router.post("/api/presets")
def create_preset(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": workspace.create_preset(req.body)}, 201


@router.patch("/api/presets/{preset_id}")
def update_preset(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": workspace.update_preset(req.path_params["preset_id"], req.body)}, 200


@router.delete("/api/presets/{preset_id}")
def delete_preset(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": workspace.delete_preset(req.path_params["preset_id"])}, 200


@router.get("/api/prompts")
def list_saved_prompts(req: Request) -> Tuple[Dict[str, Any], int]:
    favorite = req.q_bool("favorite") or None
    return {"data": workspace.list_saved_prompts(favorite=favorite)}, 200


@router.post("/api/prompts")
def create_saved_prompt(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": workspace.create_saved_prompt(req.body)}, 201


@router.patch("/api/prompts/{prompt_id}")
def update_saved_prompt(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": workspace.update_saved_prompt(req.path_params["prompt_id"], req.body)}, 200


@router.delete("/api/prompts/{prompt_id}")
def delete_saved_prompt(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": workspace.delete_saved_prompt(req.path_params["prompt_id"])}, 200


@router.get("/api/characters")
def list_characters(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": workspace.list_characters()}, 200


@router.post("/api/characters")
def create_character(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": workspace.create_character(req.body)}, 201


@router.patch("/api/characters/{character_id}")
def update_character(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": workspace.update_character(req.path_params["character_id"], req.body)}, 200


@router.delete("/api/characters/{character_id}")
def delete_character(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": workspace.delete_character(req.path_params["character_id"])}, 200


# ===========================================================================
# Analytics and the assistant
# ===========================================================================

@router.get("/api/analytics")
def analytics(req: Request) -> Tuple[Dict[str, Any], int]:
    days = req.q_int("days", 30, minimum=1, maximum=365)
    return {"data": workspace.usage_summary(days=days or 30)}, 200


@router.get("/api/assistant")
def assistant_status(req: Request) -> Tuple[Dict[str, Any], int]:
    return {"data": assistant.available()}, 200


@router.post("/api/assistant")
def assistant_reply(req: Request) -> Tuple[Dict[str, Any], int]:
    message = req.require("message")
    if not isinstance(message, str):
        raise ApiError("invalid_field", "'message' must be text.", details={"field": "message"})
    history = req.array("history")
    model_pk = req.string("model_pk", "")
    return {"data": assistant.reply(message, model_pk=model_pk,
                                    history=[h for h in history if isinstance(h, dict)])}, 200


# ===========================================================================
# The model catalogue (§24.7) — every model, and whether its key is set
# ===========================================================================

@router.get("/api/catalog")
def catalog_index(req: Request) -> Tuple[Dict[str, Any], int]:
    """Every catalogue model, with whether it is configured.

    A model with no key reports `configured: false` and the variables it needs, so an
    operator can see what is missing without reading the env file.
    """
    from .. import catalog as catalog_module

    return {
        "data": catalog_module.all_models(),
        "summary": catalog_module.summary(),
    }, 200


@router.post("/api/catalog/seed")
def catalog_seed(req: Request) -> Tuple[Dict[str, Any], int]:
    """Register every configured catalogue model. Idempotent."""
    from .. import catalog as catalog_module

    return {"data": catalog_module.seed_missing_models()}, 200


# ===========================================================================
# The job tick (§24.10 on a platform without background threads)
# ===========================================================================

@router.get("/api/cron/tick")
def cron_tick(req: Request) -> Tuple[Dict[str, Any], int]:
    """Claim and run jobs. This is what drives the queue on serverless.

    Guarded by `CRON_SECRET` when it is set. In production with no secret the endpoint
    refuses: an open endpoint that spends provider credit on demand is worse than a
    stalled queue, and a stalled queue is visible immediately.
    """
    import hmac
    import os

    from ..config import IS_PRODUCTION
    from ..services import runner

    secret = os.getenv("CRON_SECRET", "").strip()
    if secret:
        header = (req.headers.get("authorization") or "").strip()
        expected = f"Bearer {secret}"
        if not hmac.compare_digest(header, expected):
            raise ApiError("forbidden", "That cron call is not authorised.", status=401)
    elif IS_PRODUCTION:
        raise ApiError(
            "forbidden",
            "CRON_SECRET is not set, so the job tick will not run. Set it and add the "
            "same value to the cron request header.", status=403)

    budget = float(os.getenv("TICK_BUDGET_SECONDS", "8"))
    result = runner.run_batch(limit=3, budget_seconds=budget)
    return {"data": result}, 200
