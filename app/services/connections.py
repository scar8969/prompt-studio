"""The provider/model registry the app reads (spec §16, §25).

**There is no BYOK here.** A model is not added by typing a key into a form: it is
configured when the environment supplies `<PREFIX>_API_KEY`, `<PREFIX>_MODEL` and
`<PREFIX>_API_URL`, and `services.sync` turns that into rows. This module therefore has
no create/update/delete for a connection and no key decryption — a credential never
enters the database, so there is nothing here to encrypt.

What remains is read-side: resolving a model's endpoint and dialect for the adapter,
and serving the model registry the studios and the UI read.

The `connections` table survives as an internal provider row (one per endpoint host)
only because `generation_jobs.model_pk` references `models(id)` and a model belongs to
a connection. It is not exposed as something a user manages; `public_provider()` is the
read-only view of it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from .. import store
from ..adapters import Connection, get as get_adapter, install_defaults
from ..config import IS_PRODUCTION
from ..errors import ApiError, not_found
from ..security_url import resolve_pinned_ip

log = logging.getLogger("studio.connections")

OWNER = "local"           # single-tenant deployment; see docs/limits.md
WORKSPACE = "default"

install_defaults()


def _resolve(conn_row: Dict[str, Any]) -> Connection:
    """Build an adapter connection for an internal provider row.

    No secret is attached: the credential travels separately (see `execution`), because
    the key belongs to a model in the environment, not to the provider row.
    """
    base_url = conn_row["base_url"]
    pinned = ""
    if not IS_PRODUCTION and conn_row.get("kind") not in ("mock", "higgsfield"):
        try:
            pinned = resolve_pinned_ip(base_url)
        except ApiError:
            pinned = ""
    return Connection(base_url=base_url, secret="", provider=conn_row["kind"],
                      pinned_ip=pinned)


def public_provider(row: Dict[str, Any]) -> Dict[str, Any]:
    """A provider row, safe to serialise. It has no credential to leak."""
    if not row:
        return {}
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "api_url": row["base_url"],
        "source": "environment",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_providers() -> List[Dict[str, Any]]:
    rows = store.rows(
        "SELECT * FROM connections WHERE owner_id=? ORDER BY name", (OWNER,))
    out = []
    for row in rows:
        item = public_provider(row)
        item["model_count"] = store.scalar(
            "SELECT COUNT(*) FROM models WHERE connection_id=? AND enabled=1",
            (row["id"],)) or 0
        out.append(item)
    return out


def get_connection(connection_id: str) -> Dict[str, Any]:
    """An internal provider row by id."""
    row = store.row("SELECT * FROM connections WHERE id=? AND owner_id=?",
                    (connection_id, OWNER))
    if not row:
        raise not_found("provider")
    return row


def connection_for_model(model: Dict[str, Any]) -> Dict[str, Any]:
    return get_connection(model["connection_id"])


# ---------------------------------------------------------------------------
# The model registry (read side)
# ---------------------------------------------------------------------------

def capabilities_of(model_pk: str) -> List[str]:
    return [r["capability"] for r in store.rows(
        "SELECT capability FROM model_capabilities WHERE model_pk=? ORDER BY capability",
        (model_pk,))]


def parameters_of(model_pk: str) -> List[Dict[str, Any]]:
    out = []
    for row in store.rows(
            "SELECT * FROM model_parameters WHERE model_pk=? ORDER BY name", (model_pk,)):
        out.append({
            "name": row["name"],
            "type": row["type"],
            "default": row["default_value"],
            "minimum": row["minimum"],
            "maximum": row["maximum"],
            "choices": store.load_json(row["choices_json"], []),
            "required": bool(row["required"]),
        })
    return out


def public_model(row: Dict[str, Any]) -> Dict[str, Any]:
    """One model as the API and the UI see it.

    `configured` is not stored: it is true when the row is enabled, because the row only
    exists in this state after the environment supplied a key, a name and a URL.
    """
    if not row:
        return {}
    provider = store.row("SELECT name, kind, base_url FROM connections WHERE id=?",
                         (row["connection_id"],)) or {}
    metadata = store.load_json(row["metadata_json"], {})
    return {
        "id": row["id"],
        "provider_id": row["connection_id"],
        "provider": provider.get("name", ""),
        "api_url": provider.get("base_url", ""),
        "model_id": row["model_id"],
        "display_name": row["display_name"],
        "modality": row["modality"],
        "enabled": bool(row["enabled"]),
        "is_default": bool(row["is_default"]),
        "capabilities": capabilities_of(row["id"]),
        "parameters": parameters_of(row["id"]),
        "env_prefix": (metadata or {}).get("env_prefix", ""),
        "metadata": metadata,
        "created_at": row["created_at"],
    }


def get_model(model_pk: str) -> Dict[str, Any]:
    row = store.row("SELECT * FROM models WHERE id=? AND owner_id=?", (model_pk, OWNER))
    if not row:
        raise not_found("model")
    return row


def list_models(*, modality: str = "", enabled_only: bool = True,
                capability: str = "") -> List[Dict[str, Any]]:
    sql = "SELECT * FROM models WHERE owner_id=?"
    params: List[Any] = [OWNER]
    if modality:
        sql += " AND modality=?"
        params.append(modality)
    if enabled_only:
        sql += " AND enabled=1"
    sql += " ORDER BY is_default DESC, display_name, model_id"
    out = [public_model(r) for r in store.rows(sql, params)]
    if capability:
        out = [m for m in out if capability in m["capabilities"]]
    return out


def update_model(model_pk: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Only presentation is editable. A key, a name or a URL is an environment change."""
    get_model(model_pk)
    sets: List[str] = []
    params: List[Any] = []
    if "enabled" in payload:
        sets.append("enabled=?")
        params.append(1 if payload["enabled"] else 0)
    if "is_default" in payload:
        if payload["is_default"]:
            row = store.row("SELECT modality FROM models WHERE id=?", (model_pk,))
            store.execute(
                "UPDATE models SET is_default=0 WHERE owner_id=? AND modality=?",
                (OWNER, row["modality"]))
        sets.append("is_default=?")
        params.append(1 if payload["is_default"] else 0)
    if not sets:
        return public_model(get_model(model_pk))
    sets.append("updated_at=?")
    params.append(store.utcnow())
    params.append(model_pk)
    store.execute(f"UPDATE models SET {', '.join(sets)} WHERE id=?", params)
    return public_model(get_model(model_pk))


def get_model_by_env_prefix(prefix: str) -> Dict[str, Any]:
    """A model row by the environment prefix it was configured from.

    More stable than the provider's model id: `<PREFIX>_MODEL` can rename a model, and a
    test or an operator asking "what is NANO_BANANA_2" means the prefix.
    """
    rows = store.rows("SELECT * FROM models WHERE owner_id=? ORDER BY enabled DESC",
                      (OWNER,))
    for row in rows:
        metadata = store.load_json(row["metadata_json"], {}) or {}
        if metadata.get("env_prefix") == prefix:
            return row
    raise not_found("model")


def get_model_by_model_id(model_id: str) -> Dict[str, Any]:
    """A model row by the provider's own id. Raises `not_found` when absent."""
    row = store.row(
        "SELECT * FROM models WHERE owner_id=? AND model_id=? ORDER BY enabled DESC LIMIT 1",
        (OWNER, model_id))
    if not row:
        raise not_found("model")
    return row


def adapter_and_connection(model: Dict[str, Any]) -> tuple:
    """The adapter and connection for a model, with its key taken from the environment.

    This is the single place the credential is attached, and it comes from the catalogue
    (which reads `os.environ`) rather than from a column. If the key has since been
    removed from the environment, the model is disabled on the next sync and this
    refuses rather than calling out with an empty key.
    """
    from .. import catalog as catalog_module

    provider = connection_for_model(model)

    # The demo provider is not a real model and has no environment variables: it exists
    # so the app can be exercised without spending credit. It is reached directly.
    if provider["kind"] == "mock":
        pinned = ""
        return (get_adapter("mock"),
                Connection(base_url=provider["base_url"], secret="",
                           provider="mock", pinned_ip=pinned),
                provider)

    # Look the catalogue entry up by the *environment prefix* it was created from, not by
    # the model id: `<PREFIX>_MODEL` is allowed to rename the id, and the prefix is the
    # stable key that identifies which variables belong to this model.
    metadata = store.load_json(model.get("metadata_json"), {}) or {}
    prefix = metadata.get("env_prefix", "")
    entry = catalog_module.by_env(prefix) if prefix else None
    if entry is None:
        raise ApiError(
            "invalid_model_id",
            f"{model['display_name'] or model['model_id']} is not in the catalogue, so "
            "there is no environment variable to read its key from.")
    if not entry["configured"]:
        raise ApiError(
            "invalid_api_key",
            f"{entry['label']} is not configured: set {', '.join(entry['missing'])} in the "
            "environment.", details={"missing": entry["missing"]})

    key = catalog_module.read_key(entry["env_prefix"])
    url = entry["api_url"] or provider["base_url"]

    # The endpoint now arrives from the environment instead of a form, so this is where
    # the SSRF guard has to run. `resolve_pinned_ip` refuses a private, loopback or
    # link-local address (and a non-http scheme) unless development has explicitly
    # allowed it — the same rule that used to apply when a user typed the URL.
    try:
        pinned = resolve_pinned_ip(url)
    except ApiError:
        # A `cli://` endpoint has no host to resolve; it is not fetched over HTTP, so
        # there is nothing to pin and nothing to block.
        if url.startswith("cli://"):
            pinned = ""
        else:
            raise
    conn = Connection(base_url=url, secret=key, provider=provider["kind"],
                      pinned_ip=pinned)
    return get_adapter(provider["kind"]), conn, provider


def delete_model(model_pk: str) -> Dict[str, Any]:
    """Remove a model row. Only used by tests and by an operator cleaning up."""
    get_model(model_pk)
    store.execute("UPDATE generation_jobs SET model_pk=NULL WHERE model_pk=?", (model_pk,))
    store.execute("DELETE FROM models WHERE id=?", (model_pk,))
    return {"deleted": model_pk}
