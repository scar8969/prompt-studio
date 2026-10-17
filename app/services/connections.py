"""Connections and the model registry (spec §16, §23 Phase 2, §25 BYOK/BYOM).

The flow this implements is the spec's first vertical slice: a user enters a base URL,
a key and a model id; the key is encrypted; the connection is *tested* against the
provider; models are registered with an explicit capability profile; and only those
capabilities drive the UI.

Two things are deliberately strict:

* `test_connection` makes a real request. A connection is never marked ``ok`` because
  the URL parsed — an untested connection stays ``untested``.
* `register_models` stores what the provider reported. A model whose capabilities
  cannot be discovered is registered with ``image.generate`` only, because assuming
  more is what produces buttons that fail.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .. import crypto, store
from ..adapters import Connection, get as get_adapter, install_defaults, known
from ..adapters.base import Adapter, ModelInfo
from ..config import IS_PRODUCTION
from ..errors import ApiError, not_found
from ..security_url import check_url, resolve_pinned_ip

OWNER = "local"           # single-tenant local deployment; see docs/limits.md
WORKSPACE = "default"

install_defaults()


def _resolve(conn_row: Dict[str, Any]) -> Connection:
    """Build an adapter connection, pinning the IP that was validated."""
    base_url = conn_row["base_url"]
    secret = crypto.decrypt(conn_row.get("secret_encrypted", ""))
    pinned = ""
    if not IS_PRODUCTION and conn_row.get("kind") != "mock":
        try:
            pinned = resolve_pinned_ip(base_url)
        except ApiError:
            pinned = ""
    return Connection(base_url=base_url, secret=secret, provider=conn_row["kind"],
                      pinned_ip=pinned)


def public_connection(row: Dict[str, Any]) -> Dict[str, Any]:
    """The only serialiser for a connection. Never includes the secret."""
    if not row:
        return {}
    out = {
        "id": row["id"],
        "name": row["name"],
        "base_url": row["base_url"],
        "kind": row["kind"],
        "status": row["status"],
        "key_hint": row["key_hint"],
        "has_key": bool(row["secret_encrypted"]),
        "last_tested_at": row["last_tested_at"],
        "last_error": row["last_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    return out


def list_connections() -> List[Dict[str, Any]]:
    rows = store.rows(
        "SELECT * FROM connections WHERE owner_id=? ORDER BY created_at DESC", (OWNER,))
    out = []
    for row in rows:
        item = public_connection(row)
        item["model_count"] = store.scalar(
            "SELECT COUNT(*) FROM models WHERE connection_id=?", (row["id"],)) or 0
        out.append(item)
    return out


def get_connection(connection_id: str) -> Dict[str, Any]:
    row = store.row("SELECT * FROM connections WHERE id=? AND owner_id=?",
                    (connection_id, OWNER))
    if not row:
        raise not_found("connection")
    return row


def create_connection(payload: Dict[str, Any]) -> Dict[str, Any]:
    name = (payload.get("name") or "").strip() or "Untitled connection"
    kind = (payload.get("kind") or "openai-compatible").strip().lower()
    if kind == "mock":
        base_url = "mock://offline"
        secret = ""
    else:
        base_url = check_url(payload.get("base_url") or "")
        secret = (payload.get("api_key") or "").strip()
        if not secret:
            raise ApiError("missing_field", "'api_key' is required for this connection.",
                           details={"field": "api_key"})
        if not known(kind) and kind not in ("openai-compatible", "openai", "custom"):
            raise ApiError("invalid_field",
                           f"Unknown connection kind '{kind}'. Known: "
                           f"{', '.join(['mock', 'openai-compatible'])}.")

    existing = store.row(
        "SELECT id FROM connections WHERE owner_id=? AND base_url=? AND kind=?",
        (OWNER, base_url, kind))
    if existing:
        raise ApiError("duplicate_connection",
                       "A connection to that base URL already exists.",
                       status=409, details={"connection_id": existing["id"]})

    now = store.utcnow()
    connection_id = store.new_id("conn_")
    store.execute(
        "INSERT INTO connections (id, owner_id, workspace_id, name, base_url, "
        "secret_encrypted, key_hint, kind, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (connection_id, OWNER, WORKSPACE, name, base_url, crypto.encrypt(secret),
         crypto.mask(secret), kind, "untested", now, now))

    row = get_connection(connection_id)
    return public_connection(row)


def update_connection(connection_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    row = get_connection(connection_id)
    sets: List[str] = []
    params: List[Any] = []

    if "name" in payload:
        name = (payload.get("name") or "").strip()
        if not name:
            raise ApiError("invalid_field", "'name' cannot be empty.", details={"field": "name"})
        sets.append("name=?"); params.append(name)

    if "api_key" in payload:
        secret = (payload.get("api_key") or "").strip()
        if secret:
            sets.append("secret_encrypted=?"); params.append(crypto.encrypt(secret))
            sets.append("key_hint=?"); params.append(crypto.mask(secret))
            # A rotated key invalidates the previous test result.
            sets.append("status=?"); params.append("untested")

    if "base_url" in payload:
        new_url = check_url(payload.get("base_url") or "")
        if new_url != row["base_url"]:
            clash = store.row(
                "SELECT id FROM connections WHERE owner_id=? AND base_url=? AND kind=? AND id<>?",
                (OWNER, new_url, row["kind"], connection_id))
            if clash:
                raise ApiError("duplicate_connection",
                               "Another connection already uses that base URL.", status=409)
            sets.append("base_url=?"); params.append(new_url)
            sets.append("status=?"); params.append("untested")

    if not sets:
        return public_connection(row)

    sets.append("updated_at=?"); params.append(store.utcnow())
    params.append(connection_id)
    store.execute(f"UPDATE connections SET {', '.join(sets)} WHERE id=?", params)
    return public_connection(get_connection(connection_id))


def delete_connection(connection_id: str) -> Dict[str, Any]:
    """Delete a connection, its models, and anything that referenced it.

    After this, the encrypted secret is gone from the database — spec §25 lists
    "deleting or disabling a connection is handled safely" and the end-to-end test
    step 14 checks that secrets are no longer available.
    """
    get_connection(connection_id)
    # Jobs keep their history but lose the pointer, so the record survives.
    store.execute("UPDATE generation_jobs SET connection_id=NULL WHERE connection_id=?",
                  (connection_id,))
    store.execute("DELETE FROM models WHERE connection_id=?", (connection_id,))
    store.execute("DELETE FROM connections WHERE id=?", (connection_id,))
    return {"deleted": connection_id, "secret_removed": True}


def rotate_key(connection_id: str, new_key: str) -> Dict[str, Any]:
    if not (new_key or "").strip():
        raise ApiError("missing_field", "'api_key' is required.", details={"field": "api_key"})
    return update_connection(connection_id, {"api_key": new_key.strip()})


def test_connection(connection_id: str) -> Dict[str, Any]:
    """Make a real call to the provider and record the outcome (spec §25).

    Returns a result the UI can show directly. Never raises for a provider failure —
    a failed test is a result, and the connection's status reflects it.
    """
    row = get_connection(connection_id)
    adapter = get_adapter(row["kind"])
    conn = _resolve(row)

    started = time.time()
    try:
        models = adapter.list_models(conn)
        elapsed = int((time.time() - started) * 1000)
        store.execute(
            "UPDATE connections SET status='ok', last_tested_at=?, last_error='', "
            "updated_at=? WHERE id=?",
            (store.utcnow(), store.utcnow(), connection_id))
        return {
            "ok": True,
            "latency_ms": elapsed,
            "models_found": len(models),
            "models": [m.model_id for m in models[:50]],
            "offline": adapter.offline,
        }
    except ApiError as exc:
        elapsed = int((time.time() - started) * 1000)
        store.execute(
            "UPDATE connections SET status='error', last_tested_at=?, last_error=?, "
            "updated_at=? WHERE id=?",
            (store.utcnow(), exc.code, store.utcnow(), connection_id))
        return {"ok": False, "latency_ms": elapsed, "error": exc.to_dict()["error"]}


def list_provider_models(connection_id: str) -> List[Dict[str, Any]]:
    """Ask the provider what it has. Nothing is stored by this call."""
    row = get_connection(connection_id)
    adapter = get_adapter(row["kind"])
    models = adapter.list_models(_resolve(row))
    return [_model_info_out(m) for m in models]


def _model_info_out(info: ModelInfo) -> Dict[str, Any]:
    return {
        "model_id": info.model_id,
        "display_name": info.display_name or info.model_id,
        "modality": info.modality,
        "capabilities": info.capabilities,
        "parameters": info.parameters,
        "metadata": info.metadata,
    }


def register_models(connection_id: str, models: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Register one or more models against a connection.

    Accepts either explicit entries (`{model_id, display_name, capabilities}`) or bare
    ids, in which case capabilities are *discovered* from the provider. An explicit
    capability list is stored as given — the caller may know better than the listing —
    but it is still validated against the closed vocabulary.
    """
    row = get_connection(connection_id)
    adapter = get_adapter(row["kind"])
    conn = _resolve(row)

    if not isinstance(models, list) or not models:
        raise ApiError("missing_field", "'models' must be a non-empty list.",
                       details={"field": "models"})

    discovered: Optional[List[ModelInfo]] = None

    def find(model_id: str) -> Optional[ModelInfo]:
        nonlocal discovered
        if discovered is None:
            try:
                discovered = adapter.list_models(conn)
            except ApiError:
                discovered = []
        for info in discovered:
            if info.model_id == model_id:
                return info
        return None

    out: List[Dict[str, Any]] = []
    for entry in models[:100]:
        if isinstance(entry, str):
            entry = {"model_id": entry}
        if not isinstance(entry, dict):
            continue
        model_id = (entry.get("model_id") or entry.get("id") or "").strip()
        if not model_id:
            continue

        capabilities = entry.get("capabilities")
        if not isinstance(capabilities, list) or not capabilities:
            info = find(model_id)
            capabilities = list(info.capabilities) if info else ["image.generate"]
            metadata = dict(info.metadata) if info else {}
            modality = info.modality if info else "image"
            parameters = list(info.parameters) if info else []
            display_name = (entry.get("display_name")
                            or (info.display_name if info else "") or model_id)
        else:
            from ..capabilities import validate_capabilities, modality_for
            capabilities = validate_capabilities(capabilities)
            modality = modality_for(capabilities)
            metadata = {}
            display_name = entry.get("display_name") or model_id
            parameters = [
                {"name": "aspect_ratio", "type": "enum",
                 "choices": ["16:9", "9:16", "1:1"] if modality == "video"
                            else ["1:1", "3:4", "4:3", "16:9", "9:16"]},
                {"name": "count", "type": "int", "minimum": 1,
                 "maximum": 2 if modality == "video" else 4},
            ]
            if modality == "video":
                parameters.append({"name": "duration", "type": "int",
                                   "minimum": 1, "maximum": 60})

        existing = store.row(
            "SELECT id FROM models WHERE connection_id=? AND model_id=?",
            (connection_id, model_id))
        if existing:
            store.execute(
                "UPDATE models SET display_name=?, modality=?, metadata_json=?, "
                "updated_at=? WHERE id=?",
                (display_name, modality, store.dumps(metadata), store.utcnow(),
                 existing["id"]))
            model_pk = existing["id"]
        else:
            model_pk = store.new_id("mdl_")
            now = store.utcnow()
            store.execute(
                "INSERT INTO models (id, connection_id, owner_id, model_id, "
                "display_name, modality, enabled, metadata_json, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,1,?,?,?)",
                (model_pk, connection_id, OWNER, model_id, display_name, modality,
                 store.dumps(metadata), now, now))

        _store_capabilities(model_pk, capabilities)
        _store_parameters(model_pk, parameters)
        out.append(public_model(store.row("SELECT * FROM models WHERE id=?", (model_pk,))))

    return out


def _store_capabilities(model_pk: str, capabilities: List[str]) -> None:
    store.execute("DELETE FROM model_capabilities WHERE model_pk=?", (model_pk,))
    for capability in capabilities:
        # `ON CONFLICT DO NOTHING` is understood by both SQLite (>=3.24) and Postgres;
        # `INSERT OR IGNORE` is SQLite-only and would be a syntax error in production.
        store.execute(
            "INSERT INTO model_capabilities (model_pk, capability) VALUES (?,?) "
            "ON CONFLICT (model_pk, capability) DO NOTHING",
            (model_pk, capability))


def _store_parameters(model_pk: str, parameters: List[Dict[str, Any]]) -> None:
    store.execute("DELETE FROM model_parameters WHERE model_pk=?", (model_pk,))
    for spec in parameters:
        if not isinstance(spec, dict) or not spec.get("name"):
            continue
        # The columns are deleted first, so a plain insert is enough and portable.
        store.execute(
            "INSERT INTO model_parameters (model_pk, name, type, "
            "default_value, minimum, maximum, choices_json, required) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT (model_pk, name) DO UPDATE SET "
            "type=excluded.type, default_value=excluded.default_value, "
            "minimum=excluded.minimum, maximum=excluded.maximum, "
            "choices_json=excluded.choices_json, required=excluded.required",
            (model_pk, spec["name"], spec.get("type", "string"),
             None if spec.get("default") is None else str(spec["default"]),
             spec.get("minimum"), spec.get("maximum"),
             store.dumps(spec.get("choices", [])), 1 if spec.get("required") else 0))


def capabilities_of(model_pk: str) -> List[str]:
    return [r["capability"] for r in store.rows(
        "SELECT capability FROM model_capabilities WHERE model_pk=?", (model_pk,))]


def parameters_of(model_pk: str) -> List[Dict[str, Any]]:
    out = []
    for row in store.rows("SELECT * FROM model_parameters WHERE model_pk=?", (model_pk,)):
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
    if not row:
        return {}
    connection = store.row("SELECT name, kind, base_url FROM connections WHERE id=?",
                           (row["connection_id"],)) or {}
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "connection_name": connection.get("name", ""),
        "model_id": row["model_id"],
        "display_name": row["display_name"],
        "modality": row["modality"],
        "enabled": bool(row["enabled"]),
        "is_default": bool(row["is_default"]),
        "capabilities": capabilities_of(row["id"]),
        "parameters": parameters_of(row["id"]),
        "metadata": store.load_json(row["metadata_json"], {}),
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
    get_model(model_pk)
    sets: List[str] = []
    params: List[Any] = []
    if "display_name" in payload:
        sets.append("display_name=?"); params.append(str(payload["display_name"])[:200])
    if "enabled" in payload:
        sets.append("enabled=?"); params.append(1 if payload["enabled"] else 0)
    if "is_default" in payload:
        if payload["is_default"]:
            row = store.row("SELECT modality FROM models WHERE id=?", (model_pk,))
            store.execute("UPDATE models SET is_default=0 WHERE owner_id=? AND modality=?",
                          (OWNER, row["modality"]))
        sets.append("is_default=?"); params.append(1 if payload["is_default"] else 0)
    if "capabilities" in payload:
        from ..capabilities import validate_capabilities, modality_for
        caps = validate_capabilities(payload["capabilities"])
        _store_capabilities(model_pk, caps)
        sets.append("modality=?"); params.append(modality_for(caps))
    if not sets:
        return public_model(get_model(model_pk))
    sets.append("updated_at=?"); params.append(store.utcnow())
    params.append(model_pk)
    store.execute(f"UPDATE models SET {', '.join(sets)} WHERE id=?", params)
    return public_model(get_model(model_pk))


def delete_model(model_pk: str) -> Dict[str, Any]:
    get_model(model_pk)
    store.execute("UPDATE generation_jobs SET model_pk=NULL WHERE model_pk=?", (model_pk,))
    store.execute("DELETE FROM models WHERE id=?", (model_pk,))
    return {"deleted": model_pk}
