"""Generate docs/openapi.yaml from the live router (spec §24.14).

The spec is derived, never hand-written: every path that exists is documented, with the
methods it actually accepts. Run with `--check` to fail if the committed file is stale.

    python scripts/gen_openapi.py --write
    python scripts/gen_openapi.py --check
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

TARGET = REPO / "docs" / "openapi.yaml"

#: Paths whose response is not JSON, filled in by build() because the helpers it
#: needs are defined below. (Module-level use of them is a NameError.)

TAG_BY_PREFIX = [
    ("/api/providers", "Models"),
    ("/api/sync", "Models"),
    ("/api/catalog", "Models"),
    ("/api/models", "Models"),
    ("/api/generations", "Generation"),
    ("/api/jobs", "Jobs"),
    ("/api/projects", "Projects"),
    ("/api/assets", "Assets"),
    ("/api/presets", "Presets"),
    ("/api/prompts", "Prompts"),
    ("/api/characters", "Characters"),
    ("/api/analytics", "Analytics"),
    ("/api/assistant", "Assistant"),
    ("/api/health", "Health"),
    ("/api/capabilities", "Health"),
]

SUMMARIES = {
    ("GET", "/api/health"): "Service status and row counts",
    ("GET", "/api/capabilities"): "The capability vocabulary",
    ("GET", "/api/providers"): "The provider endpoints the configured models use",
    ("POST", "/api/sync"): "Re-read the environment and update the model registry",
    ("GET", "/api/models"): "The model registry, filtered by modality or capability",
    ("GET", "/api/models/{model_pk}"): "One registered model",
    ("GET", "/api/models/{model_pk}/capabilities"): "What this model can do",
    ("GET", "/api/models/{model_pk}/parameters"): "Parameters this model documents",
    ("PATCH", "/api/models/{model_pk}"): "Enable, disable, rename or re-declare capabilities",
    ("DELETE", "/api/models/{model_pk}"): "Remove a model from the registry",
    ("POST", "/api/generations/images"): "Submit an image job",
    ("POST", "/api/generations/videos"): "Submit a video job",
    ("GET", "/api/jobs"): "List jobs",
    ("GET", "/api/jobs/{job_id}"): "One job with outputs, usage and state history",
    ("POST", "/api/jobs/{job_id}/cancel"): "Cancel a job",
    ("POST", "/api/jobs/{job_id}/retry"): "Requeue a failed or cancelled job",
    ("GET", "/api/projects"): "List projects",
    ("POST", "/api/projects"): "Create a project",
    ("GET", "/api/projects/{project_id}"): "One project",
    ("PATCH", "/api/projects/{project_id}"): "Update a project",
    ("DELETE", "/api/projects/{project_id}"): "Delete a project (assets and jobs keep their history)",
    ("GET", "/api/assets"): "List assets",
    ("POST", "/api/assets/upload"): "Upload media (base64 or multipart)",
    ("GET", "/api/assets/{asset_id}"): "One asset's metadata",
    ("DELETE", "/api/assets/{asset_id}"): "Delete an asset and its file",
    ("GET", "/api/presets"): "List presets",
    ("POST", "/api/presets"): "Create a preset",
    ("PATCH", "/api/presets/{preset_id}"): "Update a preset",
    ("DELETE", "/api/presets/{preset_id}"): "Delete a preset",
    ("GET", "/api/prompts"): "List saved prompts",
    ("POST", "/api/prompts"): "Save a prompt",
    ("PATCH", "/api/prompts/{prompt_id}"): "Update a saved prompt",
    ("DELETE", "/api/prompts/{prompt_id}"): "Delete a saved prompt",
    ("GET", "/api/characters"): "List characters",
    ("POST", "/api/characters"): "Create a character with references",
    ("PATCH", "/api/characters/{character_id}"): "Update a character",
    ("DELETE", "/api/characters/{character_id}"): "Delete a character",
    ("GET", "/api/analytics"): "Usage and cost summary (cost only where a rate is known)",
    ("GET", "/api/assistant"): "Whether a chat model is available",
    ("POST", "/api/assistant"): "Ask the prompt assistant",
}


def _path_param(name: str) -> dict:
    return {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}


def _ref(name: str) -> dict:
    return {"$ref": f"#/components/responses/{name}"}


def _query_params(path: str, method: str) -> list:
    params = []
    if path == "/api/models":
        params = [
            {"name": "modality", "in": "query", "schema": {"enum": ["image", "video", "chat"]}},
            {"name": "capability", "in": "query", "schema": {"type": "string"}},
            {"name": "include_disabled", "in": "query", "schema": {"type": "boolean"}},
        ]
    elif path == "/api/jobs":
        params = [
            {"name": "status", "in": "query",
             "schema": {"enum": ["queued", "running", "cancelling", "succeeded",
                                 "failed", "cancelled"]}},
            {"name": "project_id", "in": "query", "schema": {"type": "string"}},
            {"name": "kind", "in": "query", "schema": {"enum": ["image", "video"]}},
            {"name": "limit", "in": "query", "schema": {"type": "integer", "maximum": 200}},
            {"name": "offset", "in": "query", "schema": {"type": "integer"}},
        ]
    elif path in ("/api/assets", "/api/presets", "/api/prompts"):
        params = [
            {"name": "limit", "in": "query", "schema": {"type": "integer"}},
            {"name": "offset", "in": "query", "schema": {"type": "integer"}},
        ]
        if path == "/api/assets":
            params.append({"name": "project_id", "in": "query", "schema": {"type": "string"}})
            params.append({"name": "media_type", "in": "query",
                           "schema": {"enum": ["image", "video"]}})
    elif path == "/api/analytics":
        params = [{"name": "days", "in": "query",
                   "schema": {"type": "integer", "minimum": 1, "maximum": 365}}]
    return params


def _success_status(method: str, path: str) -> str:
    if method == "POST" and path in ("/api/generations/images", "/api/generations/videos",
                                     "/api/jobs/{job_id}/retry"):
        return "202"
    if method == "POST":
        return "201"
    if method == "DELETE":
        return "200"
    return "200"


def build() -> dict:
    from app.web.routes import router

    raw_paths = {
        "/api/assets/{asset_id}/raw": {
            "get": {
                "summary": "Download an asset's bytes",
                "operationId": "download_asset",
                "tags": ["Assets"],
                "parameters": [_path_param("asset_id")],
                "responses": {
                    "200": {"description": "The file",
                            "content": {"image/png": {}, "video/mp4": {}}},
                    "404": _ref("NotFound"),
                },
            }
        }
    }

    paths: dict = {}
    for method, pattern, name in router._routes:
        raw = pattern.pattern
        path = raw[1:-1].replace("\\", "")
        # `(?P<connection_id>[^/]+)` -> `{connection_id}`
        path = re.sub(r"\(\?P<(\w+)>\[\^/\]\+\)", r"{\1}", path)
        if not path.startswith("/api/"):
            continue
        entry = paths.setdefault(path, {})
        verb = method.lower()
        if verb == "head":
            continue
        params = [m for m in re.findall(r"\{(\w+)\}", path)]
        entry[verb] = {
            "summary": SUMMARIES.get((method, path), f"{method} {path}"),
            "operationId": name,
            "tags": [next((tag for prefix, tag in TAG_BY_PREFIX if path.startswith(prefix)),
                          "Other")],
        }
        if params:
            entry[verb]["parameters"] = [_path_param(p) for p in params]
        qp = _query_params(path, method)
        if qp:
            entry[verb]["parameters"] = entry[verb].get("parameters", []) + qp
        if verb in ("post", "patch"):
            entry[verb]["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": {"type": "object"}}},
            }
        status = _success_status(method, path)
        entry[verb]["responses"] = {
            status: {"description": "Success",
                     "content": {"application/json": {
                         "schema": {"$ref": "#/components/schemas/Success"}}}},
            "400": _ref("BadRequest"),
            "404": _ref("NotFound"),
            "500": _ref("InternalError"),
        }

    # The raw-bytes route is served before the router, so it is added explicitly.
    for path, ops in raw_paths.items():
        paths.setdefault(path, {}).update(ops)

    document = {
        "openapi": "3.0.3",
        "info": {
            "title": "Prompt Studio API",
            "version": "1.0.0",
            "description": (
                "A model-agnostic AI creative platform. Credentials are environment "
                "variables, never stored per user: a model exists because "
                "`<PREFIX>_API_KEY`, `<PREFIX>_MODEL` and `<PREFIX>_API_URL` are set.\\n\\n"
                "Every JSON response is either `{\"data\": ...}` or "
                "`{\"error\": {\"code\", \"message\", \"hint?\"}}`. The error codes are a "
                "closed set (see `app/errors.py`), so a client can branch on them."),
        },
        "servers": [{"url": "http://127.0.0.1:8765"}],
        "tags": [{"name": tag} for tag in
                 ["Health", "Connections", "Models", "Generation", "Jobs", "Projects",
                  "Assets", "Presets", "Prompts", "Characters", "Analytics", "Assistant"]],
        "paths": dict(sorted(paths.items())),
        "components": {
            "schemas": {
                "Success": {"type": "object", "properties": {"data": {}}},
                "Error": {
                    "type": "object",
                    "properties": {
                        "error": {
                            "type": "object",
                            "required": ["code", "message"],
                            "properties": {
                                "code": {"type": "string"},
                                "message": {"type": "string"},
                                "hint": {"type": "string"},
                                "details": {"type": "object"},
                            },
                        }
                    },
                },
            },
            "responses": {
                "BadRequest": {"description": "The request was refused",
                               "content": {"application/json": {
                                   "schema": {"$ref": "#/components/schemas/Error"}}}},
                "NotFound": {"description": "No such record",
                             "content": {"application/json": {
                                 "schema": {"$ref": "#/components/schemas/Error"}}}},
                "InternalError": {"description": "A failure inside the server",
                                  "content": {"application/json": {
                                      "schema": {"$ref": "#/components/schemas/Error"}}}},
            },
        },
    }
    return document


def to_yaml(value, indent: int = 0) -> str:
    """A small YAML writer: the document is nested dicts/lists/scalars only."""
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return pad + "{}\n"
        out = []
        for key, item in value.items():
            key_text = key if re.fullmatch(r"[A-Za-z0-9_./{}-]+", str(key)) else json.dumps(key)
            if isinstance(item, (dict, list)) and item:
                out.append(f"{pad}{key_text}:\n{to_yaml(item, indent + 1)}")
            else:
                out.append(f"{pad}{key_text}: {_scalar(item)}\n")
        return "".join(out)
    if isinstance(value, list):
        if not value:
            return pad + "[]\n"
        out = []
        for item in value:
            if isinstance(item, dict) and item:
                body = to_yaml(item, indent + 1)
                first, _, rest = body.partition("\n")
                out.append(f"{pad}- {first.strip()}\n{rest}")
            else:
                out.append(f"{pad}- {_scalar(item)}\n")
        return "".join(out)
    return pad + _scalar(value) + "\n"


def _scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        return "{}"
    if isinstance(value, list):
        return "[]"
    text = str(value)
    if text == "" or re.search(r"[:#{}@&*!|>'\"%@`]", text) or text != text.strip():
        return json.dumps(text, ensure_ascii=False)
    return text


def main(argv: list) -> int:
    document = build()
    rendered = ("# Generated by scripts/gen_openapi.py — do not edit by hand.\n"
                + to_yaml(document))
    if "--check" in argv:
        if not TARGET.exists():
            print(f"FAIL: {TARGET} does not exist")
            return 1
        current = TARGET.read_text(encoding="utf-8")
        if current != rendered:
            print("FAIL: docs/openapi.yaml is out of date. Run: python scripts/gen_openapi.py --write")
            return 1
        print(f"openapi.yaml is current ({len(document['paths'])} paths)")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(rendered, encoding="utf-8")
    operations = sum(len([k for k in ops if k != "parameters"])
                     for ops in document["paths"].values())
    print(f"wrote {TARGET} — {len(document['paths'])} paths, {operations} operations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
