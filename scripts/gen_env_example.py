"""Write `.env.example` from the model catalogue, so the two cannot drift.

The format this documents is deliberately small: one shared `API_BASE_URL`, then a model
name and an API key per model. Writing that by hand for 59 models would guarantee a typo,
so it is generated:

    python scripts/gen_env_example.py           # write .env.example
    python scripts/gen_env_example.py --check   # fail if it is stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / ".env.example"
sys.path.insert(0, str(ROOT))

HEADER = """# =============================================================================
# Prompt Studio — environment
#
# Vercel:  Project -> Settings -> Environment Variables, add each name below.
# Local:   copy this file to .env
#
# THE FORMAT, IN ONE SENTENCE
#   Set API_BASE_URL once. Then, for each model you want, give its model name and
#   its API key. That is all - everything else has a working default.
#
#   API_BASE_URL=https://your-gateway.example.com/v1
#   NANO_BANANA_2_MODEL=gemini-3-pro-image-preview
#   NANO_BANANA_2_API_KEY=sk-...
#
# The prefix (NANO_BANANA_2) identifies which model row the app is configuring.
# The code never guesses one: a model with no key is simply not shown.
# =============================================================================


# -----------------------------------------------------------------------------
# 1. ONE ENDPOINT FOR EVERY MODEL
# -----------------------------------------------------------------------------
# The base URL is shared: the app appends the usual paths to it
# (/models, /images/generations, /video/generations, /chat/completions).
API_BASE_URL=


# -----------------------------------------------------------------------------
# 2. DATABASE AND FILE STORAGE - required for a real deployment
# -----------------------------------------------------------------------------
# Without these the app still runs, but /api/health says "degraded" and data is
# not durable. One Supabase project provides both.
#
#   DATABASE_URL       Project Settings -> Database -> Connection string
#                      Use the POOLER host (:6543) and append ?sslmode=require
#   SUPABASE_URL       Project Settings -> API -> Project URL
#   SUPABASE_S3_*      Storage -> S3 access keys -> create
#   SUPABASE_STORAGE_BUCKET  the bucket you made, e.g. media
#   SUPABASE_REGION    Project Settings -> General -> Region
DATABASE_URL=
SUPABASE_URL=
SUPABASE_S3_ACCESS_KEY_ID=
SUPABASE_S3_SECRET_ACCESS_KEY=
SUPABASE_STORAGE_BUCKET=
SUPABASE_REGION=


# -----------------------------------------------------------------------------
# 3. JOB QUEUE - protects the cron endpoint on Vercel
# -----------------------------------------------------------------------------
# Any long random string. Set the same value on the cron request header.
CRON_SECRET=


# -----------------------------------------------------------------------------
# 4. OPTIONAL
# -----------------------------------------------------------------------------
# ENV=production
# PORT=8765
# LOG_LEVEL=INFO
# ALLOW_PRIVATE_UPSTREAM=1   local development only; disables the SSRF guard
# A single model on a different host still works: <PREFIX>_API_URL overrides
# API_BASE_URL for that model alone.
"""

SECTION_TITLES = {
    "image": "IMAGE MODELS",
    "video": "VIDEO MODELS",
    "3d": "3D MODELS",
    "audio": "AUDIO MODELS",
}

SECTION_NOTES = {
    "image": "Text-to-image, editing and reference-guided generation.",
    "video": "Text-to-video, image-to-video and motion control.",
    "3d": "Meshes and 3D assets.",
    "audio": "Speech, music and sound.",
}


def render() -> str:
    from app import catalog

    models = catalog.CATALOG
    ordered = []
    for modality in ("image", "video", "3d", "audio"):
        group = [m for m in models if m["modality"] == modality]
        if group:
            ordered.append((modality, group))

    rule = "# " + "-" * 70
    parts = [HEADER, "\n\n"]

    for modality, group in ordered:
        parts.append(rule + "\n")
        parts.append(f"# {SECTION_TITLES[modality]} ({len(group)}) - "
                     f"{SECTION_NOTES[modality]}\n")
        parts.append(rule + "\n")
        parts.append("# Uncomment a pair and fill it in. Leave a model commented out to\n")
        parts.append("# ignore it. The prefix on the left is fixed by the app.\n")
        for model in group:
            prefix = model["env"]
            parts.append("\n")
            parts.append(f"# {model['label']}\n")
            parts.append(f"# {prefix}_MODEL={model['job_set_type']}\n")
            parts.append(f"# {prefix}_API_KEY=\n")

    counts = ", ".join(
        f"{sum(1 for m in models if m['modality'] == k)} {k}"
        for k in ("image", "video", "3d", "audio"))
    parts.append("\n\n" + "# " + "=" * 70 + "\n")
    parts.append(f"# {len(models)} models available: {counts}\n")
    parts.append("# Generated by scripts/gen_env_example.py from app/catalog.py\n")
    return "".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    rendered = render()
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current.strip() != rendered.strip():
            print(".env.example is out of date - run: python scripts/gen_env_example.py",
                  file=sys.stderr)
            return 1
        print(".env.example is up to date")
        return 0

    TARGET.write_text(rendered, encoding="utf-8")
    from app import catalog
    print(f"wrote {TARGET.name}: {len(catalog.CATALOG)} models, "
          f"{len(rendered.splitlines())} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
