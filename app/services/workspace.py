"""Projects, presets, saved prompts, characters, usage and job history (spec §16, §23 Phase 5).

The creative workspace: everything a user organises work with. Every read is scoped by
owner, so one workspace cannot see another's rows even though the local deployment has
a single owner today.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import store
from ..errors import ApiError, not_found

OWNER = "local"
WORKSPACE = "default"


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def public_project(row: Dict[str, Any]) -> Dict[str, Any]:
    if not row:
        return {}
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "settings": store.load_json(row["settings_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "job_count": store.scalar(
            "SELECT COUNT(*) FROM generation_jobs WHERE project_id=?", (row["id"],)) or 0,
        "asset_count": store.scalar(
            "SELECT COUNT(*) FROM assets WHERE project_id=?", (row["id"],)) or 0,
    }


def list_projects() -> List[Dict[str, Any]]:
    return [public_project(r) for r in store.rows(
        "SELECT * FROM projects WHERE owner_id=? ORDER BY updated_at DESC", (OWNER,))]


def get_project(project_id: str) -> Dict[str, Any]:
    row = store.row("SELECT * FROM projects WHERE id=? AND owner_id=?", (project_id, OWNER))
    if not row:
        raise not_found("project")
    return row


def get_project_for_job(project_id: str) -> Dict[str, Any]:
    """Used while submitting a job: an unknown project id is refused up front."""
    return get_project(project_id)


def create_project(payload: Dict[str, Any]) -> Dict[str, Any]:
    name = (payload.get("name") or "").strip()
    if not name:
        raise ApiError("missing_field", "'name' is required.", details={"field": "name"})
    now = store.utcnow()
    project_id = store.new_id("prj_")
    store.execute(
        "INSERT INTO projects (id, owner_id, workspace_id, name, description, "
        "settings_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (project_id, OWNER, WORKSPACE, name[:200],
         str(payload.get("description") or "")[:2000],
         store.dumps(payload.get("settings") or {}), now, now))
    return public_project(get_project(project_id))


def update_project(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    get_project(project_id)
    sets: List[str] = []
    params: List[Any] = []
    if "name" in payload:
        name = (payload.get("name") or "").strip()
        if not name:
            raise ApiError("invalid_field", "'name' cannot be empty.", details={"field": "name"})
        sets.append("name=?"); params.append(name[:200])
    if "description" in payload:
        sets.append("description=?"); params.append(str(payload.get("description") or "")[:2000])
    if "settings" in payload:
        settings = payload.get("settings")
        if not isinstance(settings, dict):
            raise ApiError("invalid_field", "'settings' must be an object.",
                           details={"field": "settings"})
        sets.append("settings_json=?"); params.append(store.dumps(settings))
    if not sets:
        return public_project(get_project(project_id))
    sets.append("updated_at=?"); params.append(store.utcnow())
    params.append(project_id)
    store.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id=?", params)
    return public_project(get_project(project_id))


def delete_project(project_id: str) -> Dict[str, Any]:
    get_project(project_id)
    # Assets and jobs keep their history; only the grouping goes away.
    store.execute("UPDATE assets SET project_id=NULL WHERE project_id=?", (project_id,))
    store.execute("UPDATE generation_jobs SET project_id=NULL WHERE project_id=?", (project_id,))
    store.execute("DELETE FROM projects WHERE id=?", (project_id,))
    return {"deleted": project_id}


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

def public_preset(row: Dict[str, Any]) -> Dict[str, Any]:
    if not row:
        return {}
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "modality": row["modality"],
        "configuration": store.load_json(row["configuration_json"], {}),
        "visibility": row["visibility"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_presets(*, modality: str = "") -> List[Dict[str, Any]]:
    sql = "SELECT * FROM presets WHERE owner_id=?"
    params: List[Any] = [OWNER]
    if modality:
        sql += " AND modality=?"
        params.append(modality)
    sql += " ORDER BY updated_at DESC"
    return [public_preset(r) for r in store.rows(sql, params)]


def get_preset(preset_id: str) -> Dict[str, Any]:
    row = store.row("SELECT * FROM presets WHERE id=? AND owner_id=?", (preset_id, OWNER))
    if not row:
        raise not_found("preset")
    return row


def create_preset(payload: Dict[str, Any]) -> Dict[str, Any]:
    name = (payload.get("name") or "").strip()
    if not name:
        raise ApiError("missing_field", "'name' is required.", details={"field": "name"})
    modality = str(payload.get("modality") or "image").lower()
    if modality not in ("image", "video", "3d", "audio", "chat"):
        raise ApiError("invalid_field", "'modality' must be image, video or chat.",
                       details={"field": "modality"})
    configuration = payload.get("configuration")
    if configuration is not None and not isinstance(configuration, dict):
        raise ApiError("invalid_field", "'configuration' must be an object.",
                       details={"field": "configuration"})
    now = store.utcnow()
    preset_id = store.new_id("pre_")
    store.execute(
        "INSERT INTO presets (id, owner_id, workspace_id, name, category, modality, "
        "configuration_json, visibility, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (preset_id, OWNER, WORKSPACE, name[:200], str(payload.get("category") or "")[:100],
         modality, store.dumps(configuration or {}),
         "public" if payload.get("visibility") == "public" else "private", now, now))
    return public_preset(get_preset(preset_id))


def update_preset(preset_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    get_preset(preset_id)
    sets: List[str] = []
    params: List[Any] = []
    for column, field, limit in (("name", "name", 200), ("category", "category", 100)):
        if field in payload:
            sets.append(f"{column}=?")
            params.append(str(payload.get(field) or "")[:limit])
    if "modality" in payload:
        modality = str(payload["modality"]).lower()
        if modality not in ("image", "video", "3d", "audio", "chat"):
            raise ApiError("invalid_field", "'modality' must be image, video or chat.",
                           details={"field": "modality"})
        sets.append("modality=?"); params.append(modality)
    if "configuration" in payload:
        configuration = payload["configuration"]
        if not isinstance(configuration, dict):
            raise ApiError("invalid_field", "'configuration' must be an object.",
                           details={"field": "configuration"})
        sets.append("configuration_json=?"); params.append(store.dumps(configuration))
    if "visibility" in payload:
        sets.append("visibility=?")
        params.append("public" if payload["visibility"] == "public" else "private")
    if not sets:
        return public_preset(get_preset(preset_id))
    sets.append("updated_at=?"); params.append(store.utcnow())
    params.append(preset_id)
    store.execute(f"UPDATE presets SET {', '.join(sets)} WHERE id=?", params)
    return public_preset(get_preset(preset_id))


def delete_preset(preset_id: str) -> Dict[str, Any]:
    get_preset(preset_id)
    store.execute("DELETE FROM presets WHERE id=?", (preset_id,))
    return {"deleted": preset_id}


# ---------------------------------------------------------------------------
# Saved prompts
# ---------------------------------------------------------------------------

def public_saved_prompt(row: Dict[str, Any]) -> Dict[str, Any]:
    if not row:
        return {}
    return {
        "id": row["id"],
        "title": row["title"],
        "prompt": row["prompt"],
        "metadata": store.load_json(row["metadata_json"], {}),
        "favorite": bool(row["favorite"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_saved_prompts(*, favorite: Optional[bool] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM saved_prompts WHERE owner_id=?"
    params: List[Any] = [OWNER]
    if favorite is True:
        sql += " AND favorite=1"
    sql += " ORDER BY favorite DESC, updated_at DESC"
    return [public_saved_prompt(r) for r in store.rows(sql, params)]


def get_saved_prompt(prompt_id: str) -> Dict[str, Any]:
    row = store.row("SELECT * FROM saved_prompts WHERE id=? AND owner_id=?",
                    (prompt_id, OWNER))
    if not row:
        raise not_found("saved prompt")
    return row


def create_saved_prompt(payload: Dict[str, Any]) -> Dict[str, Any]:
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise ApiError("missing_field", "'prompt' is required.", details={"field": "prompt"})
    now = store.utcnow()
    prompt_id = store.new_id("spm_")
    store.execute(
        "INSERT INTO saved_prompts (id, owner_id, workspace_id, title, prompt, "
        "metadata_json, favorite, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (prompt_id, OWNER, WORKSPACE, str(payload.get("title") or "")[:200], prompt[:8000],
         store.dumps(payload.get("metadata") or {}), 1 if payload.get("favorite") else 0,
         now, now))
    return public_saved_prompt(get_saved_prompt(prompt_id))


def update_saved_prompt(prompt_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    get_saved_prompt(prompt_id)
    sets: List[str] = []
    params: List[Any] = []
    if "title" in payload:
        sets.append("title=?"); params.append(str(payload.get("title") or "")[:200])
    if "prompt" in payload:
        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            raise ApiError("invalid_field", "'prompt' cannot be empty.",
                           details={"field": "prompt"})
        sets.append("prompt=?"); params.append(prompt[:8000])
    if "favorite" in payload:
        sets.append("favorite=?"); params.append(1 if payload["favorite"] else 0)
    if "metadata" in payload:
        metadata = payload["metadata"]
        if not isinstance(metadata, dict):
            raise ApiError("invalid_field", "'metadata' must be an object.",
                           details={"field": "metadata"})
        sets.append("metadata_json=?"); params.append(store.dumps(metadata))
    if not sets:
        return public_saved_prompt(get_saved_prompt(prompt_id))
    sets.append("updated_at=?"); params.append(store.utcnow())
    params.append(prompt_id)
    store.execute(f"UPDATE saved_prompts SET {', '.join(sets)} WHERE id=?", params)
    return public_saved_prompt(get_saved_prompt(prompt_id))


def delete_saved_prompt(prompt_id: str) -> Dict[str, Any]:
    get_saved_prompt(prompt_id)
    store.execute("DELETE FROM saved_prompts WHERE id=?", (prompt_id,))
    return {"deleted": prompt_id}


# ---------------------------------------------------------------------------
# Characters
# ---------------------------------------------------------------------------

def public_character(row: Dict[str, Any]) -> Dict[str, Any]:
    if not row:
        return {}
    refs = store.rows(
        "SELECT cr.id, cr.asset_id, cr.role, cr.metadata_json, a.name, a.mime "
        "FROM character_references cr LEFT JOIN assets a ON a.id = cr.asset_id "
        "WHERE cr.character_id=? ORDER BY cr.created_at", (row["id"],))
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "metadata": store.load_json(row["metadata_json"], {}),
        "references": [
            {"id": r["id"], "asset_id": r["asset_id"], "role": r["role"],
             "name": r["name"], "mime": r["mime"],
             "metadata": store.load_json(r["metadata_json"], {})}
            for r in refs
        ],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_characters() -> List[Dict[str, Any]]:
    return [public_character(r) for r in store.rows(
        "SELECT * FROM characters WHERE owner_id=? ORDER BY updated_at DESC", (OWNER,))]


def get_character(character_id: str) -> Dict[str, Any]:
    row = store.row("SELECT * FROM characters WHERE id=? AND owner_id=?",
                    (character_id, OWNER))
    if not row:
        raise not_found("character")
    return row


def create_character(payload: Dict[str, Any]) -> Dict[str, Any]:
    name = (payload.get("name") or "").strip()
    if not name:
        raise ApiError("missing_field", "'name' is required.", details={"field": "name"})
    now = store.utcnow()
    character_id = store.new_id("chr_")
    store.execute(
        "INSERT INTO characters (id, owner_id, workspace_id, name, description, "
        "metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (character_id, OWNER, WORKSPACE, name[:200],
         str(payload.get("description") or "")[:2000],
         store.dumps(payload.get("metadata") or {}), now, now))

    for ref in payload.get("references") or []:
        if not isinstance(ref, dict):
            continue
        asset_id = ref.get("asset_id")
        if not asset_id:
            continue
        # Ownership is checked: a character cannot reference someone else's asset.
        owned = store.row("SELECT id FROM assets WHERE id=? AND owner_id=?",
                          (asset_id, OWNER))
        if not owned:
            raise ApiError("invalid_field",
                           f"Asset '{asset_id}' does not exist.", details={"field": "asset_id"})
        store.execute(
            "INSERT INTO character_references (id, character_id, asset_id, role, "
            "metadata_json, created_at) VALUES (?,?,?,?,?,?)",
            (store.new_id("crf_"), character_id, asset_id,
             str(ref.get("role") or "face")[:40], store.dumps(ref.get("metadata") or {}),
             now))
    return public_character(get_character(character_id))


def update_character(character_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    get_character(character_id)
    sets: List[str] = []
    params: List[Any] = []
    if "name" in payload:
        name = (payload.get("name") or "").strip()
        if not name:
            raise ApiError("invalid_field", "'name' cannot be empty.", details={"field": "name"})
        sets.append("name=?"); params.append(name[:200])
    if "description" in payload:
        sets.append("description=?"); params.append(str(payload.get("description") or "")[:2000])
    if sets:
        sets.append("updated_at=?"); params.append(store.utcnow())
        params.append(character_id)
        store.execute(f"UPDATE characters SET {', '.join(sets)} WHERE id=?", params)

    if "references" in payload:
        refs = payload["references"]
        if not isinstance(refs, list):
            raise ApiError("invalid_field", "'references' must be a list.",
                           details={"field": "references"})
        store.execute("DELETE FROM character_references WHERE character_id=?", (character_id,))
        for ref in refs:
            if not isinstance(ref, dict) or not ref.get("asset_id"):
                continue
            owned = store.row("SELECT id FROM assets WHERE id=? AND owner_id=?",
                              (ref["asset_id"], OWNER))
            if not owned:
                raise ApiError("invalid_field",
                               f"Asset '{ref['asset_id']}' does not exist.",
                               details={"field": "asset_id"})
            store.execute(
                "INSERT INTO character_references (id, character_id, asset_id, role, "
                "metadata_json, created_at) VALUES (?,?,?,?,?,?)",
                (store.new_id("crf_"), character_id, ref["asset_id"],
                 str(ref.get("role") or "face")[:40],
                 store.dumps(ref.get("metadata") or {}), store.utcnow()))
    return public_character(get_character(character_id))


def delete_character(character_id: str) -> Dict[str, Any]:
    get_character(character_id)
    store.execute("DELETE FROM characters WHERE id=?", (character_id,))
    return {"deleted": character_id}


# ---------------------------------------------------------------------------
# Usage and analytics
# ---------------------------------------------------------------------------

def _since(days: int) -> str:
    """The ISO cutoff for a window, computed here rather than in SQL.

    `datetime('now', '-30 days')` is SQLite syntax; computing the bound in Python and
    binding it as a string works identically on both engines, and keeps the timestamp
    format consistent with what `utcnow()` writes.
    """
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
            ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: Any):
    from datetime import datetime

    if not value:
        return None
    text = str(value).replace("Z", "").split(".")[0]
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _average_latency() -> Optional[float]:
    """Mean job duration in seconds, from the ISO timestamps.

    `julianday()` exists only in SQLite; rather than branch on dialect, the two stamps
    are read and subtracted here. The row count is bounded by the usage window and this
    is only a headline number, so reading them is cheaper than a dialect-specific
    expression that would silently be wrong on the other engine.
    """
    found = store.rows(
        "SELECT started_at, finished_at FROM generation_jobs "
        "WHERE owner_id=? AND started_at IS NOT NULL AND finished_at IS NOT NULL "
        "AND status='succeeded' ORDER BY created_at DESC LIMIT 500", (OWNER,))
    gaps = []
    for row in found:
        start, end = _parse_iso(row["started_at"]), _parse_iso(row["finished_at"])
        if start and end and end >= start:
            gaps.append((end - start).total_seconds())
    if not gaps:
        return None
    return sum(gaps) / len(gaps)


def usage_summary(*, days: int = 30) -> Dict[str, Any]:
    """Real totals from `usage_records`. Cost is reported as unknown when it is.

    `estimated_cost` sums only the records that have one, and `cost_known_for` says how
    many that was — so a partial total can never read as a complete one (spec §25:
    no fabricated costs).
    """
    rows = store.rows(
        "SELECT provider, model_id, usage_type, quantity, estimated_cost, currency, "
        "cost_source FROM usage_records WHERE owner_id=? "
        "AND created_at >= ?",
        (OWNER, _since(days)))

    by_model: Dict[str, Dict[str, Any]] = {}
    total_known = 0.0
    known_count = 0
    currency = "USD"
    for row in rows:
        key = f"{row['provider']}/{row['model_id']}"
        entry = by_model.setdefault(key, {
            "provider": row["provider"], "model_id": row["model_id"],
            "jobs": 0, "quantity": 0.0, "estimated_cost": None, "cost_known_for": 0,
        })
        entry["jobs"] += 1
        entry["quantity"] += float(row["quantity"] or 0)
        if row["estimated_cost"] is not None:
            total_known += float(row["estimated_cost"])
            known_count += 1
            entry["estimated_cost"] = round((entry["estimated_cost"] or 0) + float(row["estimated_cost"]), 6)
            entry["cost_known_for"] += 1
            currency = row["currency"] or currency

    by_status = {r["status"]: r["n"] for r in store.rows(
        "SELECT status, COUNT(*) AS n FROM generation_jobs WHERE owner_id=? "
        "AND created_at >= ? GROUP BY status",
        (OWNER, _since(days)))}

    latency = _average_latency()

    return {
        "window_days": max(1, int(days)),
        "jobs_by_status": by_status,
        "total_jobs": sum(by_status.values()),
        "by_model": sorted(by_model.values(), key=lambda m: -m["jobs"]),
        "estimated_cost": round(total_known, 6) if known_count else None,
        "cost_known_for": known_count,
        "cost_records": len(rows),
        "currency": currency,
        "cost_note": ("Costs are provider-rate estimates and are only summed where a "
                      "rate is known; unknown rates are excluded rather than guessed."
                      if known_count else
                      "No published rate is known for these models, so no cost is shown."),
        "avg_latency_seconds": round(float(latency), 2) if latency else None,
    }


def job_history(*, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    from . import jobs as jobs_service

    return jobs_service.list_jobs(limit=limit, offset=offset)


def job_detail(job_id: str) -> Dict[str, Any]:
    from . import jobs as jobs_service

    row = jobs_service.get_job(job_id)
    payload = jobs_service.public_job(row)
    payload["events"] = jobs_service.public_events(job_id)
    usage = store.row("SELECT * FROM usage_records WHERE generation_job_id=?", (job_id,))
    payload["usage"] = {
        "quantity": usage["quantity"], "estimated_cost": usage["estimated_cost"],
        "currency": usage["currency"], "cost_source": usage["cost_source"],
    } if usage else None
    return payload
