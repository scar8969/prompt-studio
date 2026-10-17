"""Write `.env.example` from the model catalogue, so the two cannot drift.

The requirement was specific: the env file must have a **model-specific** section where
each model's key and model name can be filled in, covering **every** model Higgsfield
offers. Typing 58 blocks by hand would guarantee an omission, so this generates them.

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

HEADER = """\
# =============================================================================
# Prompt Studio — environment
#
# Local:   copy to .env and fill in.
# Vercel:  Project → Settings → Environment Variables. Add each name below and
#          the value; nothing here is read from the repo at runtime.
#
# Two sections matter. Section 2 is required for the deployment to be durable.
# Section 7 is where per-model keys and model names go — every model Higgsfield
# offers is listed there, so nothing has to be looked up.
# =============================================================================


# -----------------------------------------------------------------------------
# 1. REQUIRED — encrypts stored provider credentials
# -----------------------------------------------------------------------------
# A Fernet key (44 chars) or any passphrase; a passphrase is stretched with PBKDF2.
# Generate one with:  python -m app.cli init-key
#
# Changing this after credentials are stored makes them unreadable, because each
# one is encrypted with this key. Set it once and keep it.
APP_ENCRYPTION_KEY=


# -----------------------------------------------------------------------------
# 2. REQUIRED ON VERCEL — durable storage
# -----------------------------------------------------------------------------
# A serverless filesystem belongs to one invocation and is wiped between them, so
# both of these are required for data to survive. Without them the app still runs,
# but /api/health reports "degraded" and lists exactly what is missing.
#
# DATABASE_URL
#   Any Postgres connection string. Neon, Supabase and Vercel Postgres all work.
#   Example: postgresql://user:pass@host/db?sslmode=require
DATABASE_URL=
#
# BLOB_READ_WRITE_TOKEN
#   Vercel → Storage → Blob → create a store, then copy the token. Uploaded and
#   generated files are written here instead of a local directory.
BLOB_READ_WRITE_TOKEN=


# -----------------------------------------------------------------------------
# 3. REQUIRED ON VERCEL — the job queue driver
# -----------------------------------------------------------------------------
# A serverless process is frozen after it responds, so there are no background
# threads. Submitting a job drives it inline; this secret protects the cron
# endpoint that picks up anything left over. Use a long random string.
#
# The cron is a backstop, not the main driver — so a daily schedule is enough.
# Vercel Hobby accounts reject anything more frequent.
CRON_SECRET=


# -----------------------------------------------------------------------------
# 4. Optional — server
# -----------------------------------------------------------------------------
# ENV=production           # development | production (production refuses unsafe defaults)
HOST=127.0.0.1
PORT=8765
LOG_LEVEL=INFO
# PUBLIC_APP_URL=https://your-deployment.vercel.app
# MAX_FILE_SIZE=52428800   # 50 MB upload ceiling
# INLINE_BUDGET_SECONDS=20 # how long a submit may spend driving its own job


# -----------------------------------------------------------------------------
# 5. Optional — local development only
# -----------------------------------------------------------------------------
# Lets the app connect to a model server on localhost. This disables the SSRF
# guard, so it must never be set in production.
# ALLOW_PRIVATE_UPSTREAM=1


# -----------------------------------------------------------------------------
# 6. Optional — where files live when not on Vercel
# -----------------------------------------------------------------------------
# DATA_DIR=./data
# UPLOAD_DIR=./uploads


# -----------------------------------------------------------------------------
# 7. MODEL-SPECIFIC API KEYS AND MODEL NAMES
# -----------------------------------------------------------------------------
#
# Every model Higgsfield offers. For each one:
#
#   <PREFIX>_API_KEY   the credential
#   <PREFIX>_MODEL     the model name to send (defaults to the documented id below,
#                      so leave it blank unless you are overriding it)
#   <PREFIX>_ENABLED   set to 0 to hide a model without removing its key
#
# Leave a key blank and that model reports `configured: false` and is hidden from
# the studios. Nothing is guessed: no key, no model.
#
# Check what is configured at any time:   python -m app.cli catalog
#
# How these are reached: Higgsfield publishes no REST API key. Its supported
# programmatic path is the official CLI (`higgsfield auth login`, then
# `higgsfield generate create <job_set_type>`). The id shown for each model below
# is that CLI's job-set type — the real one, copied from Higgsfield's own model
# reference. If you have run `higgsfield auth login`, that stored session is used
# and the keys here are optional.
"""

SECTION_TITLES = {
    "image": "7a. IMAGE MODELS",
    "video": "7b. VIDEO MODELS",
    "3d": "7c. 3D MODELS",
    "audio": "7d. AUDIO MODELS",
}

SECTION_NOTES = {
    "image": "Text-to-image, editing and reference-guided generation.",
    "video": "Text-to-video, image-to-video, motion control and analysis.",
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

    parts = [HEADER]
    for modality, group in ordered:
        title = SECTION_TITLES[modality]
        note = SECTION_NOTES[modality]
        parts.append(f"\n# {'-' * 77}\n# {title} ({len(group)})\n# {note}\n"
                     f"# {'-' * 77}\n")
        for model in group:
            prefix = model["env"]
            parts.append(
                f"\n# {model['label']}  —  job_set_type: {model['job_set_type']}\n"
                f"{prefix}_API_KEY=\n"
                f"{prefix}_MODEL={model['job_set_type']}\n"
            )
    parts.append(
        "\n\n# =============================================================================\n"
        f"# {len(models)} models: "
        + ", ".join(f"{sum(1 for m in models if m['modality'] == k)} {k}" for k in
                    ("image", "video", "3d", "audio"))
        + "\n# Generated by scripts/gen_env_example.py from app/catalog.py — keep both in\n"
        "# step with `python scripts/gen_env_example.py`.\n"
        "# =============================================================================\n"
    )
    return "".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    rendered = render()
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current.strip() != rendered.strip():
            print(".env.example is out of date — run: python scripts/gen_env_example.py",
                  file=sys.stderr)
            return 1
        print(".env.example is up to date")
        return 0

    TARGET.write_text(rendered, encoding="utf-8")
    from app import catalog
    print(f"wrote {TARGET.name}: {len(catalog.CATALOG)} model blocks, "
          f"{len(rendered.splitlines())} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
