"""The model catalogue: every model Higgsfield offers, as configuration (spec §16, §24.7).

You asked for a section in the env file where each model's API key and model name can be
filled in, and for all of Higgsfield's models to be there. This module is the list those
variables refer to — one place, so a model is added by adding a row here and setting two
variables, not by editing code.

**What this can and cannot do, stated plainly.** Higgsfield does not publish a public
API, so there are no documented base URLs or keys for *their* models. What is real is
that each of these models is served by a provider that does have an API. So the catalogue
records, per model:

  * the provider that actually serves it, and its documented model id
  * what the model can do (used to gate the UI, as everywhere else)
  * the variable *prefix* to fill in

A model with no key configured is reported as `configured: false` and does not appear in
the studios. It is never given a fake base URL or a plausible-looking id.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .capabilities import CAPABILITIES, validate_capabilities

#: A model entry. `env` is the variable prefix: `<PREFIX>_API_KEY`, `<PREFIX>_BASE_URL`,
#: `<PREFIX>_MODEL`. `served_by` is the API that can actually be called for it.
CATALOG: List[Dict[str, Any]] = [
    # ---------------------------------------------------------------- image (text->image)
    {
        "slug": "nano-banana-pro",
        "label": "Nano Banana Pro",
        "display": "Nano Banana Pro (Gemini 3 Pro Image)",
        "served_by": "Google Gemini API",
        "documented_id": "gemini-3-pro-image-preview",
        "env": "NANO_BANANA_PRO",
        "modality": "image",
        "capabilities": ["image.generate", "image.edit", "image.reference"],
    },
    {
        "slug": "nano-banana",
        "label": "Nano Banana",
        "display": "Nano Banana (Gemini 2.5 Flash Image)",
        "served_by": "Google Gemini API",
        "documented_id": "gemini-2.5-flash-image",
        "env": "NANO_BANANA",
        "modality": "image",
        "capabilities": ["image.generate", "image.edit", "image.reference"],
    },
    {
        "slug": "seedream-4",
        "label": "Seedream 4",
        "display": "Seedream 4 (ByteDance)",
        "served_by": "fal.ai",
        "documented_id": "fal-ai/bytedance/seedream/v4/text-to-image",
        "env": "SEEDREAM_4",
        "modality": "image",
        "capabilities": ["image.generate", "image.edit", "image.reference"],
    },
    {
        "slug": "flux-2-pro",
        "label": "FLUX 2 Pro",
        "display": "FLUX 2 Pro (Black Forest Labs)",
        "served_by": "fal.ai",
        "documented_id": "fal-ai/flux-2-pro",
        "env": "FLUX_2_PRO",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
    },
    {
        "slug": "flux-kontext-pro",
        "label": "FLUX Kontext Pro",
        "display": "FLUX Kontext Pro (Black Forest Labs)",
        "served_by": "fal.ai",
        "documented_id": "fal-ai/flux-pro/kontext",
        "env": "FLUX_KONTEXT_PRO",
        "modality": "image",
        "capabilities": ["image.edit", "image.generate", "image.reference"],
    },
    {
        "slug": "gpt-image-1",
        "label": "GPT Image",
        "display": "GPT Image 1 (OpenAI)",
        "served_by": "OpenAI API",
        "documented_id": "gpt-image-1",
        "env": "GPT_IMAGE",
        "modality": "image",
        "capabilities": ["image.generate", "image.edit", "image.reference"],
    },
    {
        "slug": "ideogram-v3",
        "label": "Ideogram v3",
        "display": "Ideogram v3",
        "served_by": "fal.ai",
        "documented_id": "fal-ai/ideogram/v3",
        "env": "IDEOGRAM_V3",
        "modality": "image",
        "capabilities": ["image.generate"],
    },
    {
        "slug": "qwen-image",
        "label": "Qwen Image",
        "display": "Qwen Image",
        "served_by": "fal.ai",
        "documented_id": "fal-ai/qwen-image",
        "env": "QWEN_IMAGE",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
    },
    {
        "slug": "soul",
        "label": "Soul",
        "display": "Soul (Higgsfield)",
        "served_by": "Higgsfield (no public API)",
        "documented_id": "",
        "env": "SOUL",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "note": "Higgsfield's own model, with no published API. Leave unconfigured "
                "unless you have a gateway that serves it; then set its base URL too.",
    },

    # ---------------------------------------------------------------- video
    {
        "slug": "kling-o1",
        "label": "Kling O1",
        "display": "Kling O1 (Kuaishou)",
        "served_by": "fal.ai",
        "documented_id": "fal-ai/kling-video/o1/standard/text-to-video",
        "env": "KLING_O1",
        "modality": "video",
        "capabilities": ["video.generate", "video.async"],
    },
    {
        "slug": "kling-2-5-turbo",
        "label": "Kling 2.5 Turbo",
        "display": "Kling 2.5 Turbo (Kuaishou)",
        "served_by": "fal.ai",
        "documented_id": "fal-ai/kling-video/v2.5-turbo/pro/text-to-video",
        "env": "KLING_2_5_TURBO",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video"],
    },
    {
        "slug": "sora-2",
        "label": "Sora 2",
        "display": "Sora 2 (OpenAI)",
        "served_by": "OpenAI API",
        "documented_id": "sora-2",
        "env": "SORA_2",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video"],
    },
    {
        "slug": "sora-2-pro",
        "label": "Sora 2 Pro",
        "display": "Sora 2 Pro (OpenAI)",
        "served_by": "OpenAI API",
        "documented_id": "sora-2-pro",
        "env": "SORA_2_PRO",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video"],
    },
    {
        "slug": "veo-3-fast",
        "label": "Veo 3 Fast",
        "display": "Veo 3 Fast (Google)",
        "served_by": "fal.ai",
        "documented_id": "fal-ai/veo3/fast",
        "env": "VEO_3_FAST",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video"],
    },
    {
        "slug": "veo-3",
        "label": "Veo 3",
        "display": "Veo 3 (Google)",
        "served_by": "fal.ai",
        "documented_id": "fal-ai/veo3",
        "env": "VEO_3",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video"],
    },
    {
        "slug": "seedance-1-pro",
        "label": "Seedance 1 Pro",
        "display": "Seedance 1.0 Pro (ByteDance)",
        "served_by": "fal.ai",
        "documented_id": "fal-ai/bytedance/seedance/v1/pro/text-to-video",
        "env": "SEEDANCE_1_PRO",
        "modality": "video",
        "capabilities": ["video.generate", "video.async"],
    },
    {
        "slug": "hailuo-02",
        "label": "Hailuo 02",
        "display": "Hailuo 02 (MiniMax)",
        "served_by": "fal.ai",
        "documented_id": "fal-ai/minimax/hailuo-02/standard/text-to-video",
        "env": "HAILUO_02",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video"],
    },
    {
        "slug": "wan-2-5",
        "label": "Wan 2.5",
        "display": "Wan 2.5 (Alibaba)",
        "served_by": "fal.ai",
        "documented_id": "fal-ai/wan-25-preview/text-to-video",
        "env": "WAN_2_5",
        "modality": "video",
        "capabilities": ["video.generate", "video.async"],
    },
    {
        "slug": "ltx-2",
        "label": "LTX-2",
        "display": "LTX-2 (Lightricks)",
        "served_by": "fal.ai",
        "documented_id": "fal-ai/ltx-2/text-to-video",
        "env": "LTX_2",
        "modality": "video",
        "capabilities": ["video.generate", "video.async"],
    },

    # ---------------------------------------------------------------- editing / utility
    {
        "slug": "topaz-upscale",
        "label": "Topaz Upscale",
        "display": "Topaz Video Upscale",
        "served_by": "fal.ai",
        "documented_id": "fal-ai/topaz/upscale/video",
        "env": "TOPAZ_UPSCALE",
        "modality": "video",
        "capabilities": ["video.transform", "video.async"],
    },
    {
        "slug": "recraft-v3",
        "label": "Recraft v3",
        "display": "Recraft v3",
        "served_by": "fal.ai",
        "documented_id": "fal-ai/recraft/v3/text-to-image",
        "env": "RECRAFT_V3",
        "modality": "image",
        "capabilities": ["image.generate"],
    },
]

#: Sensible default base URLs, when the provider is well known. A model whose provider
#: is not listed here needs `<PREFIX>_BASE_URL` set explicitly.
DEFAULT_BASE_URLS = {
    "OpenAI API": "https://api.openai.com/v1",
    "Google Gemini API": "https://generativelanguage.googleapis.com/v1beta/openai",
}


def _var(prefix: str, suffix: str) -> str:
    return os.getenv(f"{prefix}_{suffix}", "").strip()


def resolve(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Read the environment for one catalogue entry.

    Never invents a value: a missing key or model id means `configured: false`, and the
    reason says which variable is missing.
    """
    prefix = entry["env"]
    key = _var(prefix, "API_KEY")
    model_id = _var(prefix, "MODEL") or entry.get("documented_id", "")
    base_url = _var(prefix, "BASE_URL") or DEFAULT_BASE_URLS.get(entry["served_by"], "")
    enabled = os.getenv(f"{prefix}_ENABLED", "1").strip() not in ("0", "false", "no", "")

    missing = []
    if not key:
        missing.append(f"{prefix}_API_KEY")
    if not model_id:
        missing.append(f"{prefix}_MODEL")
    if not base_url:
        missing.append(f"{prefix}_BASE_URL")

    configured = not missing and enabled
    return {
        "slug": entry["slug"],
        "label": entry["label"],
        "display": entry["display"],
        "served_by": entry["served_by"],
        "model_id": model_id,
        "base_url": base_url,
        "modality": entry["modality"],
        "capabilities": entry["capabilities"],
        "env_prefix": prefix,
        "configured": configured,
        "enabled": enabled,
        "missing": missing,
        "has_key": bool(key),
        "note": entry.get("note", ""),
        # The key itself is never returned — only whether one is present.
    }


def all_models() -> List[Dict[str, Any]]:
    return [resolve(entry) for entry in CATALOG]


def configured_models() -> List[Dict[str, Any]]:
    return [model for model in all_models() if model["configured"]]


def summary() -> Dict[str, Any]:
    models = all_models()
    ready = [m for m in models if m["configured"]]
    return {
        "total": len(models),
        "configured": len(ready),
        "by_modality": {
            "image": sum(1 for m in models if m["modality"] == "image"),
            "video": sum(1 for m in models if m["modality"] == "video"),
        },
        "providers": sorted({m["served_by"] for m in models}),
    }


def seed_missing_models() -> Dict[str, int]:
    """Register every configured catalogue model against its provider connection.

    Called on first boot (and by the cron tick) so an operator who fills in the env file
    finds the models already in the studios. Idempotent: existing rows are updated, not
    duplicated, which is what `register_models` already does.
    """
    from .services import connections as connection_service

    created = 0
    skipped = 0
    for model in all_models():
        if not model["configured"]:
            skipped += 1
            continue
        try:
            connection = _connection_for(model)
            connection_service.register_models(
                connection["id"],
                [{"model_id": model["model_id"],
                  "display_name": model["display"],
                  "capabilities": validate_capabilities(model["capabilities"])}],
            )
            created += 1
        except Exception:                                       # noqa: BLE001
            skipped += 1
    return {"registered": created, "skipped": skipped}


def _connection_for(model: Dict[str, Any]) -> Dict[str, Any]:
    """Find or create the connection this catalogue model uses.

    One connection per provider, named after it, so the user sees "fal.ai" and "OpenAI"
    rather than one row per model.
    """
    from . import crypto, store
    from .services import connections as connection_service

    name = model["served_by"]
    existing = store.row(
        "SELECT * FROM connections WHERE owner_id='local' AND base_url=? AND kind=?",
        (model["base_url"], "openai-compatible"))
    if existing:
        return existing

    key = _var(model["env_prefix"], "API_KEY")
    return connection_service.create_connection({
        "name": name,
        "kind": "openai-compatible",
        "base_url": model["base_url"],
        "api_key": key,
    })
