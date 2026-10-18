"""Turn environment variables into the model registry.

This is the BYOK replacement. There is no screen for entering a key, and no encrypted
credential column is written: a model is **configured** when the environment gives it a
key, and its name and endpoint come from the environment too. Vercel's dashboard is the
only place credentials live, which is also the only place they are rotated or revoked.

For each model in `app/catalog.py`:

    <PREFIX>_API_KEY   the credential                     (required)
    <PREFIX>_MODEL     the model name to send             (defaults to the documented id)
    <PREFIX>_API_URL   the endpoint                       (see below)
    <PREFIX>_ENABLED   0 to hide a model without removing its key

`<PREFIX>` is the model's job-set id uppercased — `nano_banana_2` → `NANO_BANANA_2`.

**Where the API URL comes from.** If `<PREFIX>_API_URL` is set, that is used verbatim, so
a gateway or proxy needs no code change. Otherwise the catalogue's documented endpoint
for that model is used. Nothing is derived from the display name.

**What this keeps in the database.** `generation_jobs.model_pk` references `models(id)`,
and a model belongs to a *connection* row. Those stay, but they stop being user-facing:
the sync creates one internal provider row per host and points the model at it. That
keeps job history, capability gating and the adapters working unchanged, without a
single credential ever being stored.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from .. import store
from ..adapters import get as get_adapter, known
from ..capabilities import validate_capabilities
from ..errors import ApiError

log = logging.getLogger("studio.sync")

OWNER = "local"
WORKSPACE = "default"

#: The provider label used when a model's URL does not identify a known host.
GENERIC_PROVIDER = "custom"


def _provider_kind(api_url: str) -> str:
    """Which adapter dialect speaks to this endpoint.

    Chosen from the host, because the dialect is a property of the API, not of the
    key. Anything unrecognised gets `openai-compatible`, which is the widest contract
    (chat/completions + images/generations); a URL that is not an OpenAI-shaped API
    will fail with the provider's own error, not a silent wrong guess.
    """
    host = (urlsplit(api_url).hostname or "").lower()
    if not host:
        return "openai-compatible"
    if "higgsfield" in host or api_url.startswith("cli://"):
        return "higgsfield"
    return "openai-compatible"


def _connection_for(api_url: str) -> Dict[str, Any]:
    """Find or create the internal provider row for this endpoint.

    One row per host, so the model list is not one duplicate connection per model. The
    row stores the URL and the dialect and nothing else — no key, no hint.
    """
    existing = store.row(
        "SELECT * FROM connections WHERE owner_id=? AND base_url=?", (OWNER, api_url))
    if existing:
        return existing

    # A model whose endpoint changed leaves its old provider row behind, and that row
    # would keep a dead URL. Re-point a row that exists under this model's name rather
    # than creating a second one: the base URL is the correct identity, but a stale
    # duplicate makes the Providers view show a dead endpoint.
    stale = store.row(
        "SELECT * FROM connections WHERE owner_id=? AND name=?", (OWNER, _host_label(api_url)))
    if stale:
        store.execute(
            "UPDATE connections SET base_url=?, kind=?, updated_at=? WHERE id=?",
            (api_url, _provider_kind(api_url), store.utcnow(), stale["id"]))
        return store.row("SELECT * FROM connections WHERE id=?", (stale["id"],))

    connection_id = store.new_id("prov_")
    now = store.utcnow()
    store.execute(
        "INSERT INTO connections (id, owner_id, workspace_id, name, kind, base_url, "
        "secret_encrypted, key_hint, status, last_error, uses_platform_credentials, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,'','','env','',1,?,?)",
        (connection_id, OWNER, WORKSPACE, _host_label(api_url), _provider_kind(api_url),
         api_url, now, now))
    return store.row("SELECT * FROM connections WHERE id=?", (connection_id,))


def _host_label(api_url: str) -> str:
    """A readable name that is unique per endpoint.

    The port is part of it: `127.0.0.1:8000` and `127.0.0.1:8001` are different
    providers, and collapsing them to one label made a second endpoint silently reuse
    the first one's row.
    """
    parts = urlsplit(api_url)
    if not parts.hostname:
        return api_url or "provider"
    return f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname


def sync_from_env() -> Dict[str, Any]:
    """Rebuild the model registry from the environment. Idempotent.

    Configured models are inserted or updated; models that are no longer configured are
    **disabled rather than deleted**, because `generation_jobs.model_pk` references them
    and a job's history must not lose the model it used. Disabling is enough: the
    studios only list enabled models.
    """
    from .. import catalog as catalog_module
    from . import connections as connection_service

    created = 0
    updated = 0
    disabled = 0
    skipped = 0
    seen: List[str] = []

    for entry in catalog_module.all_models():
        job_set_type = entry["job_set_type"]

        if not entry["configured"]:
            disabled += _disable(job_set_type)
            skipped += 1
            continue

        try:
            connection = _connection_for(entry["api_url"])
            capabilities = validate_capabilities(entry["capabilities"])
            model_pk = _upsert_model(connection, entry, capabilities)
            _store_capabilities(model_pk, capabilities)
            _store_parameters(model_pk, entry.get("parameters") or {})
            seen.append(model_pk)
            if model_pk.endswith("_new"):
                created += 1
            else:
                updated += 1
        except ApiError:
            skipped += 1
        except Exception:                                    # noqa: BLE001
            log.exception("could not register %s from the environment", job_set_type)
            skipped += 1

    return {"configured": len(seen), "created": created, "updated": updated,
            "disabled": disabled, "skipped": skipped}


def _upsert_model(connection: Dict[str, Any], entry: Dict[str, Any],
                  capabilities: List[str]) -> str:
    """Insert or update one model row. Returns its primary key."""
    # Find the model by the environment prefix that configures it, not by its current
    # connection: when `<PREFIX>_API_URL` changes, the model must move to the new
    # provider rather than staying attached to the old endpoint.
    existing = None
    for row in store.rows("SELECT * FROM models WHERE owner_id=?", (OWNER,)):
        metadata = store.load_json(row["metadata_json"], {}) or {}
        if metadata.get("env_prefix") == entry["env_prefix"]:
            existing = row
            break
    now = store.utcnow()

    if existing:
        store.execute(
            "UPDATE models SET connection_id=?, model_id=?, display_name=?, modality=?, "
            "enabled=1, updated_at=? WHERE id=?",
            (connection["id"], entry["model_id"], entry["display"], entry["modality"],
             now, existing["id"]))
        return existing["id"]


    model_pk = store.new_id("mdl_")
    store.execute(
        "INSERT INTO models (id, connection_id, owner_id, model_id, display_name, "
        "modality, enabled, is_default, metadata_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,1,0,?,?,?)",
        (model_pk, connection["id"], OWNER, entry["model_id"], entry["display"],
         entry["modality"], store.dumps({"env_prefix": entry["env_prefix"],
                                         "source": "environment"}), now, now))
    return model_pk


def _disable(model_id: str) -> int:
    """Hide models whose key is gone, without breaking the jobs that referenced them."""
    found = store.rows(
        "SELECT id FROM models WHERE owner_id=? AND model_id=? AND enabled=1",
        (OWNER, model_id))
    for row in found:
        store.execute("UPDATE models SET enabled=0, updated_at=? WHERE id=?",
                      (store.utcnow(), row["id"]))
    return len(found)


def _store_capabilities(model_pk: str, capabilities: List[str]) -> None:
    store.execute("DELETE FROM model_capabilities WHERE model_pk=?", (model_pk,))
    for capability in capabilities:
        store.execute(
            "INSERT INTO model_capabilities (model_pk, capability) VALUES (?,?) "
            "ON CONFLICT (model_pk, capability) DO NOTHING", (model_pk, capability))


def _store_parameters(model_pk: str, parameters: Dict[str, Any]) -> None:
    """Replace a model's parameter rows from the catalogue.

    Kept in the database because the UI and the capability gate already read them from
    there; the values are the documented ones, so a parameter the model does not
    document is never offered.
    """
    store.execute("DELETE FROM model_parameters WHERE model_pk=?", (model_pk,))
    for name, values in parameters.items():
        if isinstance(values, dict):
            choices = list(values.get("values") or [])
            default = values.get("default")
            minimum = values.get("minimum")
            maximum = values.get("maximum")
            kind = values.get("type") or "enum"
        else:
            choices = list(values or [])
            default = choices[0] if choices else None
            minimum = maximum = None
            kind = "enum" if choices else "string"
        store.execute(
            "INSERT INTO model_parameters (model_pk, name, type, default_value, "
            "minimum, maximum, required, choices_json) VALUES (?,?,?,?,?,?,?,?)",
            (model_pk, name, kind,
             str(default) if default is not None else None,
             minimum, maximum, 0, store.dumps(choices)))
