"""The creative assistant (spec §23 Phase 5).

A conversational helper that talks to a registered chat model *if one is registered*
and otherwise says so. It never invents a suggestion while pretending a model produced
it — the reply carries `source: "model"` or `source: "unavailable"`, and the UI shows
which.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import store
from ..adapters import get as get_adapter
from ..adapters.base import Connection
from .. import crypto
from ..errors import ApiError
from ..security_url import resolve_pinned_ip
from .connections import capabilities_of, get_connection, list_models, public_model

SYSTEM_PROMPT = (
    "You are a prompt assistant inside an image and video generation studio. "
    "You help the user write clearer generation prompts: concrete subjects, a stated "
    "setting, one lighting condition, and at most one camera move. You do not claim to "
    "have generated anything, and you do not invent model names, prices or capabilities. "
    "Keep replies short and practical."
)


def available() -> Dict[str, Any]:
    """Whether a chat model is registered, so the UI can hide the feature when not."""
    models = list_models(modality="", enabled_only=True, capability="chat.complete")
    if models:
        return {"available": True, "model": models[0]["display_name"], "model_pk": models[0]["id"]}
    return {"available": False,
            "reason": "No chat model is registered. Add one from a connection that "
                      "lists a chat-capable model."}


def _chat_cfg(model_pk: str):
    model = store.row("SELECT * FROM models WHERE id=?", (model_pk,))
    if not model:
        raise ApiError("not_found", "No such model.", status=404)
    caps = capabilities_of(model_pk)
    if "chat.complete" not in caps:
        raise ApiError("unsupported_modality",
                       f"{model['display_name']} is not a chat model.",
                       details={"capabilities": caps})
    conn_row = get_connection(model["connection_id"])
    secret = crypto.decrypt(conn_row.get("secret_encrypted", ""))
    if not secret and conn_row["kind"] != "mock":
        raise ApiError("invalid_api_key",
                       "The stored key for this connection could not be read.")
    pinned = ""
    if conn_row["kind"] != "mock":
        try:
            pinned = resolve_pinned_ip(conn_row["base_url"])
        except ApiError:
            pinned = ""
    conn = Connection(base_url=conn_row["base_url"], secret=secret,
                      provider=conn_row["kind"], pinned_ip=pinned)
    return model, get_adapter(conn_row["kind"]), conn


def reply(message: str, *, model_pk: str = "", history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """Answer a prompt-writing question. Raises when no chat model can answer."""
    text = (message or "").strip()
    if not text:
        raise ApiError("missing_field", "'message' is required.", details={"field": "message"})
    if len(text) > 4000:
        raise ApiError("invalid_field", "'message' is longer than 4000 characters.",
                       details={"field": "message"})

    if not model_pk:
        found = available()
        if not found["available"]:
            raise ApiError("unsupported_modality", found["reason"])
        model_pk = found["model_pk"]

    model, adapter, conn = _chat_cfg(model_pk)
    if adapter.offline:
        # The demo adapter is not a language model. Saying so is the honest answer;
        # a canned suggestion would look like a model wrote it.
        return {
            "source": "unavailable",
            "model": model["display_name"],
            "reply": "",
            "message": ("The assistant needs an OpenAI-compatible chat model. The "
                        "registered model is the offline demo, which cannot answer. "
                        "Add a connection with a chat model to enable this."),
        }

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in (history or [])[-8:]:
        if isinstance(turn, dict) and turn.get("role") in ("user", "assistant") \
                and isinstance(turn.get("content"), str):
            messages.append({"role": turn["role"], "content": turn["content"][:4000]})
    messages.append({"role": "user", "content": text})

    answer = adapter.chat(conn, model["model_id"], messages)
    return {"source": "model", "model": model["display_name"], "reply": answer}
