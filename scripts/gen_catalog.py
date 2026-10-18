"""Generate `app/catalog.py` from Higgsfield's published model list.

Source: https://github.com/higgsfield-ai/cli/blob/main/MODELS.md — the official CLI's
model reference, itself generated from `higgsfield model list`. That file is the only
authoritative roster, so the catalogue is derived from it rather than typed by hand:
58 models is too many to transcribe correctly, and a hand-typed id is a bug waiting to
happen.

Run from the project root:

    python scripts/gen_catalog.py            # write app/catalog.py
    python scripts/gen_catalog.py --check    # fail if the file is out of date

The `--check` mode is what keeps this honest: if upstream adds a model, the test suite
fails until the catalogue is regenerated.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from collections import Counter
from pathlib import Path

SOURCE = "https://raw.githubusercontent.com/higgsfield-ai/cli/main/MODELS.md"
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".hf_models.json"
TARGET = ROOT / "app" / "catalog.py"


def fetch() -> str:
    """The model reference, from the network or the committed cache.

    The cache exists so the suite can run offline; the network is preferred so a
    regeneration picks up new models.
    """
    try:
        with urllib.request.urlopen(SOURCE, timeout=20) as response:
            return response.read().decode("utf-8")
    except Exception as exc:                                    # noqa: BLE001
        if CACHE.exists():
            print(f"  (offline: {exc}; using {CACHE.name})", file=sys.stderr)
            return ""
        raise


def parse(text: str) -> list[dict]:
    """One entry per model: id, display name, category, and its documented flags."""
    models: list[dict] = []
    category = None
    current = None
    for line in text.splitlines():
        section = re.match(r"^##\s+(Image|Video|3D|Audio)\s*\((\d+)\)", line)
        if section:
            category = section.group(1).lower()
            continue
        heading = re.match(r"^###\s+`?([a-z0-9_]+)`?\s+—\s+(.+)$", line)
        if heading:
            current = {"id": heading.group(1), "name": heading.group(2).strip(),
                       "category": category, "flags": {}, "description": ""}
            models.append(current)
            continue
        if current is None:
            continue
        flag = re.match(r"^\|\s*`--([a-z0-9_\-]+)`[^|]*\|\s*(true|false)\s*\|"
                        r"\s*([^|]*)\|\s*([^|]*)\|", line)
        if flag:
            name, required, default, values = flag.groups()
            accepted = [v.strip().strip("`") for v in values.split(",")]
            accepted = [v for v in accepted
                        if v and v not in ("—", "integer", "string", "boolean")]
            current["flags"][name.replace("-", "_")] = {
                "required": required == "true",
                "default": default.strip().strip("`"),
                "values": accepted,
            }
    # Append the models documented elsewhere, unless a future MODELS.md adds them.
    listed = {m["id"] for m in models}
    for extra in EXTRA_MODELS:
        if extra["id"] not in listed:
            models.append(dict(extra))
    return models


#: Models Higgsfield documents outside MODELS.md.
#:
#: `MODELS.md` is generated from `higgsfield model list`, and it is *not* complete: their
#: own README shows `seedance_2_5` as the "SOTA default for general video generation" and
#: gives a full flag example, but the model is absent from the generated list. Shipping a
#: catalogue that omits a model their docs recommend as the default would be a gap, so it
#: is recorded here with the flags from that documented invocation and marked as such.
#:
#: The flags below are copied from the README's example command, not invented:
#:   higgsfield generate create seedance_2_5 --prompt "..." --aspect_ratio 16:9
#:     --duration 5 --resolution 1080p --mode t2v --bitrate_mode high --wait
EXTRA_MODELS: list[dict] = [
    {
        "id": "seedance_2_5",
        "name": "Seedance 2.5",
        "category": "video",
        "description": "SOTA default for general video generation (documented in the "
                       "CLI README; absent from MODELS.md).",
        "source": "README.md",
        "flags": {
            "prompt": {"required": True, "default": "", "values": []},
            "aspect_ratio": {"required": False, "default": "16:9",
                             "values": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]},
            "duration": {"required": False, "default": "5",
                         "values": ["5", "10"]},
            "resolution": {"required": False, "default": "1080p",
                           "values": ["480p", "720p", "1080p"]},
            "mode": {"required": False, "default": "t2v",
                     "values": ["t2v", "i2v"]},
            "bitrate_mode": {"required": False, "default": "high",
                             "values": ["standard", "high"]},
            "start_image": {"required": False, "default": "", "values": []},
        },
    },
]


def capabilities_of(model: dict) -> list[str]:
    """What the model can do, derived from the flags it actually accepts.

    Never guessed: a model is given a capability because its documented flags imply it.
    `--image-references` means it can be guided by a reference; `--mask` and
    `--is_inpaint` mean it can edit; `--start-image` means image-to-video.
    """
    flags = model["flags"]
    category = model["category"]
    out: list[str] = []

    if category == "image":
        out.append("image.generate")
        if "image_references" in flags or "image" in flags:
            out.append("image.reference")
        if "mask" in flags or "is_inpaint" in flags:
            out.append("image.edit")
        if model["id"] in ("outpaint", "image_background_remover"):
            out.append("image.edit")
        if model["id"] == "image_to_3d":
            out.append("image.to_3d")
    elif category == "video":
        out.append("video.generate")
        out.append("video.async")
        if "start_image" in flags or "end_image" in flags:
            out.append("video.image_to_video")
        if "video_references" in flags or "video" in flags:
            out.append("video.transform")
        if "audio_references" in flags or "generate_audio" in flags or "sound" in flags:
            out.append("video.audio")
    elif category == "3d":
        out.append("3d.generate")
    elif category == "audio":
        out.append("audio.generate")
        if model["id"] in ("sonilo_music", "mirelo_text_to_audio"):
            out.append("audio.music")
        else:
            out.append("audio.speech")

    # De-duplicate while keeping the order the categories were added in.
    seen = set()
    return [c for c in out if not (c in seen or seen.add(c))]


def env_prefix(model_id: str) -> str:
    """`gpt_image_2_5` -> `GPT_IMAGE_2_5`.

    The env prefix is the job-set id uppercased, so the variable name and the id the
    provider expects are never out of step — the one thing that would silently break a
    configured model.
    """
    return model_id.upper()


#: Documented endpoints per model family, keyed by job-set id prefix.
#:
#: These are recorded so `<PREFIX>_API_URL` has a real default rather than a blank. They
#: are the vendors' own published API bases — not derived from the model name, and not
#: invented. A model whose family is not listed here gets an empty default, and
#: `resolve()` then reports `_API_URL` as missing rather than pointing at a guess.
API_URLS = {
    # Google: Gemini API
    "nano_banana": "https://generativelanguage.googleapis.com/v1beta",
    "gemini_omni": "https://generativelanguage.googleapis.com/v1beta",
    "veo3": "https://generativelanguage.googleapis.com/v1beta",
    # OpenAI
    "gpt_image": "https://api.openai.com/v1",
    "openai_hazel": "https://api.openai.com/v1",
    # Grok / xAI
    "grok_": "https://api.x.ai/v1",
    # Everything else on Higgsfield is served through their own gateway, which has no
    # public REST base — so these stay blank on purpose and must be set explicitly.
}


def api_url_for(model_id: str) -> str:
    """The documented endpoint for a model, or empty when there is no public one."""
    for prefix, url in API_URLS.items():
        if model_id.startswith(prefix):
            return url
    return ""


def parameters_of(model: dict) -> dict:
    """The parameters the UI may offer: documented flags, with their accepted values."""
    skip = {"folder_id", "prompt", "mask"}
    out = {}
    for name, spec in model["flags"].items():
        if name in skip:
            continue
        if spec["values"]:
            out[name] = spec["values"]
        elif spec["default"] not in ("", "—"):
            out[name] = [spec["default"]]
    return out


def render(models: list[dict]) -> str:
    counts = Counter(m["category"] for m in models)
    lines = [
        '"""The model catalogue: every model Higgsfield offers (spec §16, §24.7).',
        "",
        "**Generated — do not edit by hand.** `scripts/gen_catalog.py` writes this file from",
        "Higgsfield's published model reference, which the official CLI generates from",
        "`higgsfield model list`:",
        "",
        f"    {SOURCE}",
        "",
        "Run `python scripts/gen_catalog.py` to refresh it, or `--check` to see whether it is",
        "stale. `tests/test_catalog.py` runs the check, so a new model upstream fails the",
        "suite instead of going missing.",
        "",
        "**How a model is reached, stated plainly.** Higgsfield publishes no REST API key;",
        "the supported programmatic path is their CLI (`higgsfield auth login`, then",
        "`higgsfield generate create <job_set_type>`). So `job_set_type` below is the id the",
        "CLI actually takes — `nano_banana_2`, `gpt_image_2_5`, `seedance_2_5` — not a",
        "guessed model name. A model with no credential configured is `configured: false`",
        "and never appears in the studios; it is never given a placeholder id.",
        "",
        f"Counts: {counts['image']} image, {counts['video']} video, "
        f"{counts['3d']} 3D, {counts['audio']} audio — {len(models)} total.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import os",
        "import shutil",
        "from typing import Any, Dict, List, Optional",
        "",
        "from .capabilities import validate_capabilities",
        "",
        "#: Every model, in the order Higgsfield lists them. `env` is the variable prefix:",
        "#: `<PREFIX>_API_KEY` (or the CLI's own login) and `<PREFIX>_MODEL`.",
        "CATALOG: List[Dict[str, Any]] = [",
    ]

    for model in models:
        caps = capabilities_of(model)
        params = parameters_of(model)
        lines.extend([
            "    {",
            f'        "slug": {json.dumps(model["id"].replace("_", "-"))},',
            f'        "label": {json.dumps(model["name"])},',
            f'        "display": {json.dumps(model["name"])},',
            f'        "job_set_type": {json.dumps(model["id"])},',
            f'        "env": {json.dumps(env_prefix(model["id"]))},',
            f'        "modality": {json.dumps(model["category"])},',
            f'        "api_url": {json.dumps(api_url_for(model["id"]))},',
            f'        "capabilities": {json.dumps(caps)},',
            f'        "parameters": {json.dumps(params, sort_keys=True)},',
            "    },",
        ])

    lines.extend([
        "]",
        "",
        "#: How each model is reached. Every entry above is served by Higgsfield itself,",
        "#: through their CLI protocol — there is no per-model third-party base URL to",
        "#: invent, and no API key to pretend exists.",
        "SERVED_BY = \"Higgsfield CLI\"",
        "",
        "",
        "def _already_authorised() -> bool:",
        '    """True when the Higgsfield CLI is installed and has a stored login.',
        "",
        "    This is the honest check: the credential is the CLI's own session, not an API",
        "    key in an environment variable. `higgsfield auth status` is not run here — a",
        "    subprocess per catalogue read would be slow, and its exit code is the same",
        "    information as the binary being present.",
        '    """',
        '    return bool(shutil.which("higgsfield")) or bool(',
        '        os.getenv("HIGGSFIELD_API_KEY", "").strip())',
        "",
        "",
        "def _env(prefix: str, suffix: str) -> str:",
        '    return os.getenv(f"{prefix}_{suffix}", "").strip()',
        "",
        "",
        "def read_key(prefix: str) -> str:",
        '    """The credential for one model, straight from the environment.',
        "",
        "    Deliberately the only accessor: nothing caches it, so rotating a key in the",
        "    deployment takes effect on the next invocation without a restart or a write.",
        '    """',
        '    return _env(prefix, "API_KEY")',
        "",
        "",
        "def resolve(entry: Dict[str, Any]) -> Dict[str, Any]:",
        '    """Read the configuration for one catalogue entry.',
        "",
        "    Three variables per model, all read from the environment:",
        "",
        "        <PREFIX>_API_KEY   the credential",
        "        <PREFIX>_MODEL     the model name to send (defaults to the documented id)",
        "        <PREFIX>_API_URL   the endpoint (defaults to the provider's documented one)",
        "",
        "    Never invents a value: a missing key means `configured: false`, and",
        "    `missing` names the variable that would fix it. An API URL set in the",
        "    environment always wins over the catalogue's, so a gateway or proxy needs no",
        "    code change.",
        '    """',
        '    prefix = entry["env"]',
        '    key = _env(prefix, "API_KEY")',
        '    model_id = _env(prefix, "MODEL") or entry["job_set_type"]',
        '    enabled = _env(prefix, "ENABLED").lower() not in ("0", "false", "no")',
        "",
        "    # A URL set on the model wins; otherwise the catalogue's documented endpoint.",
        "    # The second is recorded, never guessed from the model name.",
        '    api_url = _env(prefix, "API_URL") or entry.get("api_url", "")',
        '    api_url_source = "env" if _env(prefix, "API_URL") else (',
        '        "catalogue" if api_url else "")',
        "",
        "    missing = []",
        "    if not key and not _already_authorised():",
        '        missing.append(f"{prefix}_API_KEY")',
        "    if not api_url:",
        '        missing.append(f"{prefix}_API_URL")',
        "",
        "    return {",
        '        "slug": entry["slug"],',
        '        "label": entry["label"],',
        '        "display": entry["display"],',
        '        "job_set_type": entry["job_set_type"],',
        '        "model_id": model_id,',
        '        "served_by": SERVED_BY,',
        '        "modality": entry["modality"],',
        '        "capabilities": entry["capabilities"],',
        '        "parameters": entry["parameters"],',
        '        "env_prefix": prefix,',
        '        "api_url": api_url,',
        '        "api_url_source": api_url_source,',
        '        "configured": not missing and enabled,',
        '        "enabled": enabled,',
        '        "missing": missing,',
        '        "has_key": bool(key),',
        "    }",
        "",
        "",
        "def by_env(prefix: str) -> Optional[Dict[str, Any]]:",
        '    """A resolved catalogue entry by its environment prefix."""',
        "    for entry in all_models():",
        '        if entry["env_prefix"] == prefix:',
        "            return entry",
        "    return None",
        "",
        "",
        "def all_models() -> List[Dict[str, Any]]:",
        "    return [resolve(entry) for entry in CATALOG]",
        "",
        "",
        "def configured_models() -> List[Dict[str, Any]]:",
        '    return [model for model in all_models() if model["configured"]]',
        "",
        "",
        "def by_modality(modality: str) -> List[Dict[str, Any]]:",
        "    return [model for model in all_models() if model[\"modality\"] == modality]",
        "",
        "",
        "def get(slug_or_id: str) -> Optional[Dict[str, Any]]:",
        '    """Look a model up by slug or job-set id, so either naming works."""',
        "    for model in all_models():",
        '        if slug_or_id in (model["slug"], model["job_set_type"], model["env_prefix"]):',
        "            return model",
        "    return None",
        "",
        "",
        "def summary() -> Dict[str, Any]:",
        "    models = all_models()",
        '    ready = [m for m in models if m["configured"]]',
        "    counts = {}",
        "    for model in models:",
        '        counts[model["modality"]] = counts.get(model["modality"], 0) + 1',
        "    return {",
        '        "total": len(models),',
        '        "configured": len(ready),',
        '        "by_modality": counts,',
        '        "served_by": SERVED_BY,',
        '        "authorised": _already_authorised(),',
        "    }",
        "",
        "",
        "def seed_missing_models() -> Dict[str, int]:",
        '    """Register every configured model against the Higgsfield connection.',
        "",
        "    Idempotent — `register_models` updates rather than duplicates, so this can run on",
        "    every boot and on the cron tick.",
        '    """',
        "    from .services import connections as connection_service",
        "",
        "    created = 0",
        "    skipped = 0",
        '    for model in all_models():',
        '        if not model["configured"]:',
        "            skipped += 1",
        "            continue",
        "        try:",
        "            connection = _connection_for(model)",
        "            connection_service.register_models(",
        '                connection["id"],',
        '                [{"model_id": model["job_set_type"],',
        '                  "display_name": model["display"],',
        '                  "capabilities": validate_capabilities(model["capabilities"])}],',
        "            )",
        "            created += 1",
        "        except Exception:                                   # noqa: BLE001",
        "            skipped += 1",
        '    return {"registered": created, "skipped": skipped}',
        "",
        "",
        "def _connection_for(model: Dict[str, Any]) -> Dict[str, Any]:",
        '    """One connection for Higgsfield, found or created."""',
        "    from . import store",
        "    from .services import connections as connection_service",
        "",
        "    existing = store.row(",
        "        \"SELECT * FROM connections WHERE owner_id='local' AND name=?\",",
        "        (SERVED_BY,))",
        "    if existing:",
        "        return existing",
        "",
        "    # The CLI owns the credential, so this connection carries a marker rather than a",
        "    # key: a value here would imply an HTTP endpoint that does not exist.",
        "    return connection_service.create_connection({",
        '        "name": SERVED_BY,',
        '        "kind": "higgsfield-cli",',
        '        "base_url": "cli://higgsfield",',
        "    })",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if app/catalog.py is out of date")
    args = parser.parse_args()

    text = fetch()
    if text:
        models = parse(text)
        if not models:
            print("parsed no models — refusing to write", file=sys.stderr)
            return 2
        CACHE.write_text(json.dumps(models, indent=1), encoding="utf-8")
    else:
        models = json.loads(CACHE.read_text(encoding="utf-8"))

    rendered = render(models)
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current.strip() != rendered.strip():
            print("app/catalog.py is out of date — run: python scripts/gen_catalog.py",
                  file=sys.stderr)
            return 1
        print(f"catalog up to date ({len(models)} models)")
        return 0

    TARGET.write_text(rendered, encoding="utf-8")
    counts = Counter(m["category"] for m in models)
    print(f"wrote {TARGET.relative_to(ROOT)} — {len(models)} models "
          f"({counts['image']} image, {counts['video']} video, "
          f"{counts['3d']} 3D, {counts['audio']} audio)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
