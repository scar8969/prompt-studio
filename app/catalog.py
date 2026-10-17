"""The model catalogue: every model Higgsfield offers (spec §16, §24.7).

**Generated — do not edit by hand.** `scripts/gen_catalog.py` writes this file from
Higgsfield's published model reference, which the official CLI generates from
`higgsfield model list`:

    https://raw.githubusercontent.com/higgsfield-ai/cli/main/MODELS.md

Run `python scripts/gen_catalog.py` to refresh it, or `--check` to see whether it is
stale. `tests/test_catalog.py` runs the check, so a new model upstream fails the
suite instead of going missing.

**How a model is reached, stated plainly.** Higgsfield publishes no REST API key;
the supported programmatic path is their CLI (`higgsfield auth login`, then
`higgsfield generate create <job_set_type>`). So `job_set_type` below is the id the
CLI actually takes — `nano_banana_2`, `gpt_image_2_5`, `seedance_2_5` — not a
guessed model name. A model with no credential configured is `configured: false`
and never appears in the studios; it is never given a placeholder id.

Counts: 24 image, 25 video, 5 3D, 5 audio — 59 total.
"""

from __future__ import annotations

import os
import shutil
from typing import Any, Dict, List, Optional

from .capabilities import validate_capabilities

#: Every model, in the order Higgsfield lists them. `env` is the variable prefix:
#: `<PREFIX>_API_KEY` (or the CLI's own login) and `<PREFIX>_MODEL`.
CATALOG: List[Dict[str, Any]] = [
    {
        "slug": "cinematic-studio-2-5",
        "label": "Cinematic Studio 2.5",
        "display": "Cinematic Studio 2.5",
        "job_set_type": "cinematic_studio_2_5",
        "env": "CINEMATIC_STUDIO_2_5",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "parameters": {"aspect_ratio": ["1:1", "3:2", "2:3", "4:3", "3:4", "4:5", "5:4", "16:9", "9:16", "21:9"], "batch_size": ["1"], "image_references": ["UUID or path"], "mode": ["auto"], "resolution": ["1k", "2k", "4k"]},
    },
    {
        "slug": "flux-2",
        "label": "FLUX.2",
        "display": "FLUX.2",
        "job_set_type": "flux_2",
        "env": "FLUX_2",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "parameters": {"aspect_ratio": ["1:1", "4:3", "3:4", "16:9", "9:16"], "image_references": ["UUID or path"], "resolution": ["1k", "2k"], "variant": ["pro", "flex", "max"]},
    },
    {
        "slug": "flux-kontext",
        "label": "Flux Kontext",
        "display": "Flux Kontext",
        "job_set_type": "flux_kontext",
        "env": "FLUX_KONTEXT",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "parameters": {"aspect_ratio": ["1:1", "4:3", "3:4", "16:9", "9:16"], "image_references": ["UUID or path"]},
    },
    {
        "slug": "gpt-image-2",
        "label": "GPT Image 2",
        "display": "GPT Image 2",
        "job_set_type": "gpt_image_2",
        "env": "GPT_IMAGE_2",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference", "image.edit"],
        "parameters": {"aspect_ratio": ["auto", "1:1", "4:3", "3:4", "16:9", "21:9", "9:16", "3:2", "2:3", "4:5", "5:4"], "background": ["auto", "opaque", "transparent"], "image_references": ["UUID or path"], "is_inpaint": ["boolean; requires a mask and at least one reference image"], "quality": ["low", "medium", "high"], "resolution": ["1k", "2k", "4k"]},
    },
    {
        "slug": "gpt-image-2-5",
        "label": "GPT Image 2.5",
        "display": "GPT Image 2.5",
        "job_set_type": "gpt_image_2_5",
        "env": "GPT_IMAGE_2_5",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "parameters": {"aspect_ratio": ["auto", "1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16", "21:9", "27:16", "16:27", "9:8", "8:9", "4:5", "5:4"], "background": ["auto", "opaque", "transparent"], "image_references": ["UUID or path; at most 16"], "quality": ["low", "medium", "high", "xhigh", "max"], "resolution": ["1k", "2k", "4k"], "variant": ["flare", "sunburst"]},
    },
    {
        "slug": "grok-image",
        "label": "Grok Image",
        "display": "Grok Image",
        "job_set_type": "grok_image",
        "env": "GROK_IMAGE",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "parameters": {"aspect_ratio": ["1:1", "auto", "1:2", "2:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16"], "image_references": ["UUID or path"], "mode": ["std", "quality"], "resolution": ["1k", "2k"]},
    },
    {
        "slug": "image-auto",
        "label": "Image Auto",
        "display": "Image Auto",
        "job_set_type": "image_auto",
        "env": "IMAGE_AUTO",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "parameters": {"aspect_ratio": ["1:1", "4:3", "3:4", "16:9", "9:16"], "image_references": ["UUID or path"]},
    },
    {
        "slug": "image-background-remover",
        "label": "Image Background Remover",
        "display": "Image Background Remover",
        "job_set_type": "image_background_remover",
        "env": "IMAGE_BACKGROUND_REMOVER",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference", "image.edit"],
        "parameters": {"image_references": ["UUID or path"]},
    },
    {
        "slug": "kling-omni-image",
        "label": "Kling O1 Image",
        "display": "Kling O1 Image",
        "job_set_type": "kling_omni_image",
        "env": "KLING_OMNI_IMAGE",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "parameters": {"aspect_ratio": ["1:1", "auto", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"], "image_references": ["UUID or path"], "resolution": ["1k", "2k"]},
    },
    {
        "slug": "marketing-studio-image",
        "label": "Marketing Studio Image",
        "display": "Marketing Studio Image",
        "job_set_type": "marketing_studio_image",
        "env": "MARKETING_STUDIO_IMAGE",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "parameters": {"aspect_ratio": ["auto", "1:1", "3:2", "2:3", "4:3", "3:4", "4:5", "5:4", "9:16", "16:9", "21:9"], "image_references": ["UUID or path"], "resolution": ["1k", "2k", "4k"]},
    },
    {
        "slug": "nano-banana",
        "label": "Nano Banana",
        "display": "Nano Banana",
        "job_set_type": "nano_banana",
        "env": "NANO_BANANA",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "parameters": {"aspect_ratio": ["1:1", "3:2", "2:3", "4:3", "3:4", "4:5", "5:4", "9:16", "16:9", "21:9"], "image_references": ["UUID or path"]},
    },
    {
        "slug": "nano-banana-2",
        "label": "Nano Banana Pro",
        "display": "Nano Banana Pro",
        "job_set_type": "nano_banana_2",
        "env": "NANO_BANANA_2",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "parameters": {"aspect_ratio": ["1:1", "3:2", "2:3", "4:3", "3:4", "4:5", "5:4", "9:16", "16:9", "21:9"], "image_references": ["UUID or path"], "resolution": ["1k", "2k", "4k"]},
    },
    {
        "slug": "nano-banana-2-lite",
        "label": "Nano Banana 2 Lite",
        "display": "Nano Banana 2 Lite",
        "job_set_type": "nano_banana_2_lite",
        "env": "NANO_BANANA_2_LITE",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "parameters": {"aspect_ratio": ["auto", "1:1", "3:2", "2:3", "4:3", "3:4", "4:5", "5:4", "9:16", "16:9", "21:9"], "image_references": ["UUID or path"], "resolution": ["1k"], "thinking": ["MINIMAL", "HIGH"]},
    },
    {
        "slug": "nano-banana-flash",
        "label": "Nano Banana 2",
        "display": "Nano Banana 2",
        "job_set_type": "nano_banana_flash",
        "env": "NANO_BANANA_FLASH",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "parameters": {"aspect_ratio": ["1:1", "3:2", "2:3", "4:3", "3:4", "4:5", "5:4", "9:16", "16:9", "21:9"], "image_references": ["UUID or path"], "resolution": ["1k", "2k", "4k"]},
    },
    {
        "slug": "openai-hazel",
        "label": "OpenAI Hazel",
        "display": "OpenAI Hazel",
        "job_set_type": "openai_hazel",
        "env": "OPENAI_HAZEL",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "parameters": {"aspect_ratio": ["1:1", "3:2", "2:3", "auto"], "image_references": ["UUID or path"], "quality": ["low", "medium", "high"]},
    },
    {
        "slug": "outpaint",
        "label": "Outpaint",
        "display": "Outpaint",
        "job_set_type": "outpaint",
        "env": "OUTPAINT",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference", "image.edit"],
        "parameters": {"aspect_ratio": ["auto", "1:1", "3:2", "2:3", "4:3", "3:4", "4:5", "5:4", "9:16", "16:9", "21:9"], "image_references": ["UUID or path"]},
    },
    {
        "slug": "recraft-v4-1",
        "label": "Recraft V4.1",
        "display": "Recraft V4.1",
        "job_set_type": "recraft_v4_1",
        "env": "RECRAFT_V4_1",
        "modality": "image",
        "capabilities": ["image.generate"],
        "parameters": {"aspect_ratio": ["1:1", "3:4", "4:3", "4:5", "5:4", "3:2", "2:3", "16:9", "9:16"], "colors": ["array"], "model_type": ["standard", "vector", "utility", "utility_vector"], "resolution": ["1k", "2k"]},
    },
    {
        "slug": "seedream-v4-5",
        "label": "Seedream 4.5",
        "display": "Seedream 4.5",
        "job_set_type": "seedream_v4_5",
        "env": "SEEDREAM_V4_5",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "parameters": {"aspect_ratio": ["1:1", "4:3", "16:9", "3:2", "21:9", "3:4", "9:16", "2:3"], "image_references": ["UUID or path"], "quality": ["basic", "high"]},
    },
    {
        "slug": "seedream-v5-lite",
        "label": "Seedream V5 Lite",
        "display": "Seedream V5 Lite",
        "job_set_type": "seedream_v5_lite",
        "env": "SEEDREAM_V5_LITE",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "parameters": {"aspect_ratio": ["1:1", "4:3", "3:4", "16:9", "9:16"], "image_references": ["UUID or path"], "quality": ["basic", "high"]},
    },
    {
        "slug": "soul-cast",
        "label": "Soul Cast",
        "display": "Soul Cast",
        "job_set_type": "soul_cast",
        "env": "SOUL_CAST",
        "modality": "image",
        "capabilities": ["image.generate"],
        "parameters": {"aspect_ratio": ["16:9"], "budget": ["50"]},
    },
    {
        "slug": "soul-cinematic",
        "label": "Soul Cinematic",
        "display": "Soul Cinematic",
        "job_set_type": "soul_cinematic",
        "env": "SOUL_CINEMATIC",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "parameters": {"aspect_ratio": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"], "image_references": ["UUID or path"], "quality": ["1.5k", "2k"], "soul_id": ["Soul UUID"]},
    },
    {
        "slug": "soul-location",
        "label": "Soul Location",
        "display": "Soul Location",
        "job_set_type": "soul_location",
        "env": "SOUL_LOCATION",
        "modality": "image",
        "capabilities": ["image.generate"],
        "parameters": {"aspect_ratio": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9", "9:21"]},
    },
    {
        "slug": "text2image-soul-v2",
        "label": "Higgsfield Soul V2",
        "display": "Higgsfield Soul V2",
        "job_set_type": "text2image_soul_v2",
        "env": "TEXT2IMAGE_SOUL_V2",
        "modality": "image",
        "capabilities": ["image.generate", "image.reference"],
        "parameters": {"aspect_ratio": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"], "image_references": ["UUID or path"], "quality": ["1.5k", "2k"], "soul_id": ["Soul UUID"]},
    },
    {
        "slug": "z-image",
        "label": "Z Image",
        "display": "Z Image",
        "job_set_type": "z_image",
        "env": "Z_IMAGE",
        "modality": "image",
        "capabilities": ["image.generate"],
        "parameters": {"aspect_ratio": ["1:1", "4:3", "3:4", "16:9", "9:16"]},
    },
    {
        "slug": "brain-activity",
        "label": "Virality Predictor",
        "display": "Virality Predictor",
        "job_set_type": "brain_activity",
        "env": "BRAIN_ACTIVITY",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.transform"],
        "parameters": {"video": ["UUID or path"]},
    },
    {
        "slug": "cinematic-studio-3-0",
        "label": "Cinematic Studio 3.0",
        "display": "Cinematic Studio 3.0",
        "job_set_type": "cinematic_studio_3_0",
        "env": "CINEMATIC_STUDIO_3_0",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video", "video.transform", "video.audio"],
        "parameters": {"aspect_ratio": ["auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"], "audio_references": ["UUID or path"], "batch_size": ["1"], "duration": ["5"], "end_image": ["UUID or path"], "enhance_prompt": ["false"], "generate_audio": ["false"], "genre": ["auto", "action", "horror", "comedy", "noir", "drama", "epic"], "image_references": ["UUID or path"], "multi_prompt": ["array"], "multi_shot_mode": ["auto", "custom"], "multi_shots": ["false"], "prompt_language": ["en", "zh"], "resolution": ["480p", "720p", "1080p", "4k"], "speedramp": ["auto", "linear", "slowmo", "speedup", "fast_to_slowmo", "slowmo_to_fast", "super_slowmo", "impact"], "start_image": ["UUID or path"], "video_references": ["UUID or path"]},
    },
    {
        "slug": "cinematic-studio-video",
        "label": "Cinematic Studio Video",
        "display": "Cinematic Studio Video",
        "job_set_type": "cinematic_studio_video",
        "env": "CINEMATIC_STUDIO_VIDEO",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video", "video.audio"],
        "parameters": {"aspect_ratio": ["1:1", "4:3", "3:4", "16:9", "9:16"], "duration": ["5", "10"], "end_image": ["UUID or path"], "slow_motion": ["false"], "sound": ["true"], "start_image": ["UUID or path"]},
    },
    {
        "slug": "cinematic-studio-video-3-5",
        "label": "Cinematic Studio Video 3.5",
        "display": "Cinematic Studio Video 3.5",
        "job_set_type": "cinematic_studio_video_3_5",
        "env": "CINEMATIC_STUDIO_VIDEO_3_5",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video", "video.transform", "video.audio"],
        "parameters": {"aspect_ratio": ["auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"], "audio_references": ["UUID or path"], "camera_style": ["classic_static", "silent_machine", "one_take", "epic_scale", "intimate_observer", "impossible_camera", "documentary_snap", "raw_chaos", "dreamy_flow"], "color_grading": ["naturalistic_clean", "bleached_warm", "hyper_neon", "teal_orange_epic", "sodium_decay", "cold_steel", "bleach_bypass", "classic_bw"], "duration": ["15"], "end_image": ["UUID or path"], "enhance_prompt": ["false"], "generate_audio": ["false"], "genre": ["auto", "action", "horror", "comedy", "noir", "drama", "epic"], "image_references": ["UUID or path"], "light_scheme": ["soft_cross", "contre_jour", "overhead_fall", "window", "practicals", "silhouette"], "multi_prompt": ["array"], "multi_shot_mode": ["auto", "custom"], "multi_shots": ["false"], "prompt_language": ["en", "zh"], "resolution": ["480p", "720p", "1080p"], "start_image": ["UUID or path"], "video_references": ["UUID or path"]},
    },
    {
        "slug": "cinematic-studio-video-v2",
        "label": "Cinematic Studio Video V2",
        "display": "Cinematic Studio Video V2",
        "job_set_type": "cinematic_studio_video_v2",
        "env": "CINEMATIC_STUDIO_VIDEO_V2",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video", "video.audio"],
        "parameters": {"aspect_ratio": ["1:1", "4:3", "3:4", "16:9", "9:16"], "batch_size": ["1"], "cfg_scale": ["number"], "duration": ["5"], "end_image": ["UUID or path"], "genre": ["auto", "action", "horror", "comedy", "western", "suspense", "intimate", "spectacle"], "image_references": ["UUID or path"], "kling_element_ids": ["array"], "mode": ["pro", "std"], "multi_prompt": ["array"], "multi_shot_mode": ["auto", "custom"], "multi_shots": ["false"], "sound": ["on", "off"], "speedramp": ["auto", "custom", "linear", "slowmo", "speedup", "impact"], "start_image": ["UUID or path"]},
    },
    {
        "slug": "gemini-omni",
        "label": "Gemini Omni Flash",
        "display": "Gemini Omni Flash",
        "job_set_type": "gemini_omni",
        "env": "GEMINI_OMNI",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.transform"],
        "parameters": {"aspect_ratio": ["16:9", "9:16"], "duration": ["4", "6", "8", "10"], "image_references": ["UUID or path"], "resolution": ["720p"], "video_references": ["UUID or path"]},
    },
    {
        "slug": "grok-video",
        "label": "Grok Video",
        "display": "Grok Video",
        "job_set_type": "grok_video",
        "env": "GROK_VIDEO",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video"],
        "parameters": {"aspect_ratio": ["16:9", "9:16", "1:1"], "duration": ["5"], "start_image": ["UUID or path"]},
    },
    {
        "slug": "grok-video-v15",
        "label": "Grok Video 1.5",
        "display": "Grok Video 1.5",
        "job_set_type": "grok_video_v15",
        "env": "GROK_VIDEO_V15",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video"],
        "parameters": {"duration": ["5"], "resolution": ["480p", "720p"], "start_image": ["UUID or path"]},
    },
    {
        "slug": "kling2-6",
        "label": "Kling 2.6 Video",
        "display": "Kling 2.6 Video",
        "job_set_type": "kling2_6",
        "env": "KLING2_6",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video", "video.audio"],
        "parameters": {"aspect_ratio": ["16:9", "9:16", "1:1"], "duration": ["5", "10"], "sound": ["true"], "start_image": ["UUID or path"]},
    },
    {
        "slug": "kling3-0",
        "label": "Kling v3.0",
        "display": "Kling v3.0",
        "job_set_type": "kling3_0",
        "env": "KLING3_0",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video", "video.audio"],
        "parameters": {"aspect_ratio": ["16:9", "9:16", "1:1"], "duration": ["5"], "end_image": ["UUID or path"], "mode": ["std", "pro", "4k"], "sound": ["on", "off"], "start_image": ["UUID or path"]},
    },
    {
        "slug": "kling3-0-turbo",
        "label": "Kling 3.0 Turbo",
        "display": "Kling 3.0 Turbo",
        "job_set_type": "kling3_0_turbo",
        "env": "KLING3_0_TURBO",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video"],
        "parameters": {"aspect_ratio": ["16:9", "9:16", "1:1"], "duration": ["5"], "resolution": ["720p", "1080p"], "start_image": ["UUID or path"]},
    },
    {
        "slug": "marketing-studio-video",
        "label": "Marketing Studio Video",
        "display": "Marketing Studio Video",
        "job_set_type": "marketing_studio_video",
        "env": "MARKETING_STUDIO_VIDEO",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video", "video.audio"],
        "parameters": {"ad_reference_id": ["ad-reference UUID"], "aspect_ratio": ["auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"], "avatar_ids": ["array"], "avatars": ["array"], "duration": ["15"], "end_image": ["UUID or path"], "generate_audio": ["false"], "hook_id": ["hook UUID"], "image_references": ["UUID or path"], "mode": ["ugc"], "product_ids": ["array"], "resolution": ["480p", "720p", "1080p"], "setting_id": ["setting UUID"], "specific_mode": ["default", "web_product", "from_storyboard"], "start_image": ["UUID or path"], "storyboard_id": ["storyboard UUID"], "web_product_ids": ["array"], "web_product_type": ["desktop", "mobile"]},
    },
    {
        "slug": "minimax-hailuo",
        "label": "Minimax Hailuo",
        "display": "Minimax Hailuo",
        "job_set_type": "minimax_hailuo",
        "env": "MINIMAX_HAILUO",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video"],
        "parameters": {"duration": ["6", "10"], "end_image": ["UUID or path"], "resolution": ["512", "768", "1080"], "start_image": ["UUID or path"], "variant": ["minimax", "minimax-fast", "minimax-2.3", "minimax-2.3-fast"]},
    },
    {
        "slug": "seedance1-5",
        "label": "Seedance 1.5 Pro",
        "display": "Seedance 1.5 Pro",
        "job_set_type": "seedance1_5",
        "env": "SEEDANCE1_5",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video", "video.audio"],
        "parameters": {"aspect_ratio": ["auto", "16:9", "9:16", "4:3", "3:4", "1:1", "21:9"], "duration": ["4", "8", "12"], "end_image": ["UUID or path"], "generate_audio": ["true"], "resolution": ["480p", "720p", "1080p"], "start_image": ["UUID or path"]},
    },
    {
        "slug": "seedance-2-0",
        "label": "Seedance 2.0",
        "display": "Seedance 2.0",
        "job_set_type": "seedance_2_0",
        "env": "SEEDANCE_2_0",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video", "video.transform", "video.audio"],
        "parameters": {"aspect_ratio": ["auto", "16:9", "9:16", "4:3", "3:4", "1:1", "21:9"], "audio_references": ["UUID or path"], "bitrate_mode": ["standard", "high"], "duration": ["5"], "end_image": ["UUID or path"], "generate_audio": ["true"], "genre": ["auto", "action", "horror", "comedy", "noir", "drama", "epic"], "image_references": ["UUID or path"], "mode": ["std", "fast"], "resolution": ["480p", "720p", "1080p", "4k"], "start_image": ["UUID or path"], "video_references": ["UUID or path"]},
    },
    {
        "slug": "seedance-2-0-mini",
        "label": "Seedance 2.0 Mini",
        "display": "Seedance 2.0 Mini",
        "job_set_type": "seedance_2_0_mini",
        "env": "SEEDANCE_2_0_MINI",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video", "video.transform", "video.audio"],
        "parameters": {"aspect_ratio": ["auto", "16:9", "9:16", "4:3", "3:4", "1:1", "21:9"], "audio_references": ["UUID or path"], "bitrate_mode": ["standard", "high"], "duration": ["5"], "end_image": ["UUID or path"], "generate_audio": ["true"], "genre": ["auto", "action", "horror", "comedy", "noir", "drama", "epic"], "image_references": ["UUID or path"], "resolution": ["480p", "720p"], "start_image": ["UUID or path"], "video_references": ["UUID or path"]},
    },
    {
        "slug": "veo3",
        "label": "Google Veo 3",
        "display": "Google Veo 3",
        "job_set_type": "veo3",
        "env": "VEO3",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video"],
        "parameters": {"aspect_ratio": ["16:9", "9:16"], "start_image": ["UUID or path"], "variant": ["veo-3-preview", "veo-3-fast"]},
    },
    {
        "slug": "veo3-1",
        "label": "Google Veo 3.1",
        "display": "Google Veo 3.1",
        "job_set_type": "veo3_1",
        "env": "VEO3_1",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video"],
        "parameters": {"aspect_ratio": ["16:9", "9:16"], "duration": ["4", "6", "8"], "quality": ["basic", "high", "ultra"], "start_image": ["UUID or path"], "variant": ["veo-3-1-preview", "veo-3-1-fast"]},
    },
    {
        "slug": "veo3-1-lite",
        "label": "Google Veo 3.1 Lite",
        "display": "Google Veo 3.1 Lite",
        "job_set_type": "veo3_1_lite",
        "env": "VEO3_1_LITE",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video", "video.audio"],
        "parameters": {"aspect_ratio": ["16:9", "9:16", "auto"], "duration": ["4", "6", "8"], "end_image": ["UUID or path"], "generate_audio": ["false"], "start_image": ["UUID or path"]},
    },
    {
        "slug": "video-background-remover",
        "label": "Video Background Remover",
        "display": "Video Background Remover",
        "job_set_type": "video_background_remover",
        "env": "VIDEO_BACKGROUND_REMOVER",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.transform"],
        "parameters": {"video_references": ["UUID or path"]},
    },
    {
        "slug": "wan2-6",
        "label": "Wan 2.6 Video",
        "display": "Wan 2.6 Video",
        "job_set_type": "wan2_6",
        "env": "WAN2_6",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.transform", "video.audio"],
        "parameters": {"aspect_ratio": ["16:9", "9:16", "1:1"], "audio_references": ["UUID or path"], "duration": ["5", "10", "15"], "image_references": ["UUID or path"], "quality": ["720p", "1080p"], "video_references": ["UUID or path"]},
    },
    {
        "slug": "wan2-7",
        "label": "Wan 2.7",
        "display": "Wan 2.7",
        "job_set_type": "wan2_7",
        "env": "WAN2_7",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video", "video.audio"],
        "parameters": {"aspect_ratio": ["16:9", "9:16", "1:1", "4:3", "3:4"], "audio_references": ["UUID or path"], "duration": ["5"], "end_image": ["UUID or path"], "resolution": ["720p", "1080p"], "start_image": ["UUID or path"]},
    },
    {
        "slug": "video-explainer",
        "label": "Explainer Video",
        "display": "Explainer Video",
        "job_set_type": "video_explainer",
        "env": "VIDEO_EXPLAINER",
        "modality": "video",
        "capabilities": ["video.generate", "video.async"],
        "parameters": {"aspect_ratio": ["16:9", "9:16"], "duration": ["20\u2013600", "multiple of 10"], "medias": ["JSON array via `@file`; up to 14 images/files"], "preset_id": ["UUID from `higgsfield preset list video-explainer"], "voice_id": ["voice ID from `higgsfield voices list"], "voice_type": ["preset", "element"]},
    },
    {
        "slug": "explainer-video",
        "label": "Explainer Video Assembler",
        "display": "Explainer Video Assembler",
        "job_set_type": "explainer_video",
        "env": "EXPLAINER_VIDEO",
        "modality": "video",
        "capabilities": ["video.generate", "video.async"],
        "parameters": {"height": ["even integer greater than 1"], "items": ["JSON array via `@file`; at least 2 items"], "subtitles": ["JSON object; font: `patrick", "caveat", "marker", "anton"], "width": ["even integer greater than 1"]},
    },
    {
        "slug": "3d-rigging",
        "label": "3D Rigging",
        "display": "3D Rigging",
        "job_set_type": "3d_rigging",
        "env": "3D_RIGGING",
        "modality": "3d",
        "capabilities": ["3d.generate"],
        "parameters": {"enable_animation": ["false"], "height_meters": ["number"]},
    },
    {
        "slug": "image-to-3d",
        "label": "Image to 3D",
        "display": "Image to 3D",
        "job_set_type": "image_to_3d",
        "env": "IMAGE_TO_3D",
        "modality": "3d",
        "capabilities": ["3d.generate"],
        "parameters": {"enable_animation": ["false"], "enable_rigging": ["false"], "image_references": ["UUID or path"], "pose_mode": ["a-pose", "t-pose"], "rigging_height_meters": ["number"], "should_texture": ["false"], "symmetry_mode": ["off", "auto", "on"], "topology": ["quad", "triangle"]},
    },
    {
        "slug": "multi-image-to-3d",
        "label": "Multi-Image to 3D",
        "display": "Multi-Image to 3D",
        "job_set_type": "multi_image_to_3d",
        "env": "MULTI_IMAGE_TO_3D",
        "modality": "3d",
        "capabilities": ["3d.generate"],
        "parameters": {"enable_animation": ["false"], "enable_rigging": ["false"], "image_references": ["UUID or path"], "pose_mode": ["a-pose", "t-pose"], "rigging_height_meters": ["number"], "should_texture": ["false"], "symmetry_mode": ["off", "auto", "on"], "topology": ["quad", "triangle"]},
    },
    {
        "slug": "sam-3-3d",
        "label": "3D Objects",
        "display": "3D Objects",
        "job_set_type": "sam_3_3d",
        "env": "SAM_3_3D",
        "modality": "3d",
        "capabilities": ["3d.generate"],
        "parameters": {"detection_threshold": ["number"], "export_textured_glb": ["true"], "image_references": ["UUID or path"]},
    },
    {
        "slug": "tripo-3d",
        "label": "Text to 3D",
        "display": "Text to 3D",
        "job_set_type": "tripo_3d",
        "env": "TRIPO_3D",
        "modality": "3d",
        "capabilities": ["3d.generate"],
        "parameters": {"auto_size": ["false"], "geometry_quality": ["standard", "detailed"], "pbr": ["true"], "texture": ["true"], "texture_quality": ["standard", "detailed"]},
    },
    {
        "slug": "inworld-text-to-speech",
        "label": "Inworld Text to Speech",
        "display": "Inworld Text to Speech",
        "job_set_type": "inworld_text_to_speech",
        "env": "INWORLD_TEXT_TO_SPEECH",
        "modality": "audio",
        "capabilities": ["audio.generate", "audio.speech"],
        "parameters": {"voice": ["113 built-in voices \u2014 run `higgsfield model get inworld_text_to_speech` for the list"]},
    },
    {
        "slug": "mirelo-text-to-audio",
        "label": "Mirelo Text to Audio",
        "display": "Mirelo Text to Audio",
        "job_set_type": "mirelo_text_to_audio",
        "env": "MIRELO_TEXT_TO_AUDIO",
        "modality": "audio",
        "capabilities": ["audio.generate", "audio.music"],
        "parameters": {"duration": ["number"]},
    },
    {
        "slug": "seed-audio",
        "label": "Seed Audio 1.0",
        "display": "Seed Audio 1.0",
        "job_set_type": "seed_audio",
        "env": "SEED_AUDIO",
        "modality": "audio",
        "capabilities": ["audio.generate", "audio.speech"],
        "parameters": {"audio_references": ["UUID or path"], "format": ["wav", "mp3", "pcm", "ogg_opus"], "image_references": ["UUID or path"], "loudness_rate": ["0"], "pitch_rate": ["0"], "sample_rate": ["8000", "16000", "24000", "32000", "44100", "48000"], "speech_rate": ["0"], "voice_type": ["preset", "element"]},
    },
    {
        "slug": "sonilo-music",
        "label": "Sonilo Music",
        "display": "Sonilo Music",
        "job_set_type": "sonilo_music",
        "env": "SONILO_MUSIC",
        "modality": "audio",
        "capabilities": ["audio.generate", "audio.music"],
        "parameters": {"duration": ["number"]},
    },
    {
        "slug": "text2speech-v2",
        "label": "Text to Speech",
        "display": "Text to Speech",
        "job_set_type": "text2speech_v2",
        "env": "TEXT2SPEECH_V2",
        "modality": "audio",
        "capabilities": ["audio.generate", "audio.speech"],
        "parameters": {"variant": ["elevenlabs", "minimax", "seed_speech", "vibe_voice", "cozy_voice"], "voice_type": ["preset", "element"]},
    },
    {
        "slug": "seedance-2-5",
        "label": "Seedance 2.5",
        "display": "Seedance 2.5",
        "job_set_type": "seedance_2_5",
        "env": "SEEDANCE_2_5",
        "modality": "video",
        "capabilities": ["video.generate", "video.async", "video.image_to_video"],
        "parameters": {"aspect_ratio": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"], "bitrate_mode": ["standard", "high"], "duration": ["5", "10"], "mode": ["t2v", "i2v"], "resolution": ["480p", "720p", "1080p"]},
    },
]

#: How each model is reached. Every entry above is served by Higgsfield itself,
#: through their CLI protocol — there is no per-model third-party base URL to
#: invent, and no API key to pretend exists.
SERVED_BY = "Higgsfield CLI"


def _already_authorised() -> bool:
    """True when the Higgsfield CLI is installed and has a stored login.

    This is the honest check: the credential is the CLI's own session, not an API
    key in an environment variable. `higgsfield auth status` is not run here — a
    subprocess per catalogue read would be slow, and its exit code is the same
    information as the binary being present.
    """
    return bool(shutil.which("higgsfield")) or bool(
        os.getenv("HIGGSFIELD_API_KEY", "").strip())


def _env(prefix: str, suffix: str) -> str:
    return os.getenv(f"{prefix}_{suffix}", "").strip()


def resolve(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Read the configuration for one catalogue entry.

    Never invents a value: a missing credential means `configured: false`, and
    `missing` names the variable that would fix it."""
    prefix = entry["env"]
    key = _env(prefix, "API_KEY")
    model_id = _env(prefix, "MODEL") or entry["job_set_type"]
    enabled = _env(prefix, "ENABLED").lower() not in ("0", "false", "no")

    missing = []
    if not key and not _already_authorised():
        missing.append(f"{prefix}_API_KEY")

    return {
        "slug": entry["slug"],
        "label": entry["label"],
        "display": entry["display"],
        "job_set_type": entry["job_set_type"],
        "model_id": model_id,
        "served_by": SERVED_BY,
        "modality": entry["modality"],
        "capabilities": entry["capabilities"],
        "parameters": entry["parameters"],
        "env_prefix": prefix,
        "configured": not missing and enabled,
        "enabled": enabled,
        "missing": missing,
        "has_key": bool(key),
    }


def all_models() -> List[Dict[str, Any]]:
    return [resolve(entry) for entry in CATALOG]


def configured_models() -> List[Dict[str, Any]]:
    return [model for model in all_models() if model["configured"]]


def by_modality(modality: str) -> List[Dict[str, Any]]:
    return [model for model in all_models() if model["modality"] == modality]


def get(slug_or_id: str) -> Optional[Dict[str, Any]]:
    """Look a model up by slug or job-set id, so either naming works."""
    for model in all_models():
        if slug_or_id in (model["slug"], model["job_set_type"], model["env_prefix"]):
            return model
    return None


def summary() -> Dict[str, Any]:
    models = all_models()
    ready = [m for m in models if m["configured"]]
    counts = {}
    for model in models:
        counts[model["modality"]] = counts.get(model["modality"], 0) + 1
    return {
        "total": len(models),
        "configured": len(ready),
        "by_modality": counts,
        "served_by": SERVED_BY,
        "authorised": _already_authorised(),
    }


def seed_missing_models() -> Dict[str, int]:
    """Register every configured model against the Higgsfield connection.

    Idempotent — `register_models` updates rather than duplicates, so this can run on
    every boot and on the cron tick.
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
                [{"model_id": model["job_set_type"],
                  "display_name": model["display"],
                  "capabilities": validate_capabilities(model["capabilities"])}],
            )
            created += 1
        except Exception:                                   # noqa: BLE001
            skipped += 1
    return {"registered": created, "skipped": skipped}


def _connection_for(model: Dict[str, Any]) -> Dict[str, Any]:
    """One connection for Higgsfield, found or created."""
    from . import store
    from .services import connections as connection_service

    existing = store.row(
        "SELECT * FROM connections WHERE owner_id='local' AND name=?",
        (SERVED_BY,))
    if existing:
        return existing

    # The CLI owns the credential, so this connection carries a marker rather than a
    # key: a value here would imply an HTTP endpoint that does not exist.
    return connection_service.create_connection({
        "name": SERVED_BY,
        "kind": "higgsfield-cli",
        "base_url": "cli://higgsfield",
    })
