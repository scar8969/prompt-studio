"""Small operator commands.

    python -m app.cli init-key     # a fresh APP_ENCRYPTION_KEY
    python -m app.cli check        # configuration and schema, without starting a server
    python -m app.cli migrate      # create/upgrade the schema (run once against a new DB)
    python -m app.cli seed-models  # register every catalogue model whose key is set
    python -m app.cli catalog      # what is configured, and what is missing
"""

from __future__ import annotations

import sys


def init_key() -> int:
    from .crypto import generate_key

    print(generate_key())
    print("\nPut that in your .env as APP_ENCRYPTION_KEY. Keep it: credentials stored "
          "with a different key cannot be decrypted.", file=sys.stderr)
    return 0


def migrate() -> int:
    from . import db, store

    version = store.init()
    print(f"schema at version {version} on {db.dialect()}")
    return 0


def seed_models() -> int:
    from . import catalog

    result = catalog.seed_missing_models()
    summary = catalog.summary()
    print(f"registered {result['registered']}, skipped {result['skipped']}")
    print(f"{summary['configured']} of {summary['total']} catalogue models are configured")
    return 0


def catalog_report() -> int:
    from . import catalog

    models = catalog.all_models()
    ready = [m for m in models if m["configured"]]
    missing = [m for m in models if not m["configured"]]
    print(f"{len(ready)} configured, {len(missing)} not")
    print()
    if ready:
        print("configured:")
        for model in ready:
            print(f"  {model['label']:22} {model['modality']:6} {model['model_id']}")
    if missing:
        print()
        print("missing (set these variables):")
        for model in missing:
            print(f"  {model['label']:22} {', '.join(model['missing']) or 'disabled'}")
    return 0


def check() -> int:
    from . import config, store
    from .adapters import install_defaults, names

    install_defaults()
    print(f"env              {config.ENV}")
    print(f"database         {config.DB_PATH}")
    print(f"uploads          {config.UPLOAD_DIR}")
    print(f"adapters         {', '.join(names())}")
    print(f"schema version   {store.init()}")
    print(f"encryption key   {'EPHEMERAL (set APP_ENCRYPTION_KEY)' if config.EPHEMERAL_KEY else 'set'}")
    print(f"private upstream {'allowed' if config.ALLOW_PRIVATE_UPSTREAM else 'blocked'}")
    ok = True
    if config.IS_PRODUCTION and config.EPHEMERAL_KEY:
        print("FAIL: production needs APP_ENCRYPTION_KEY", file=sys.stderr)
        ok = False
    if config.IS_PRODUCTION and config.ALLOW_PRIVATE_UPSTREAM:
        print("WARN: production allows private upstream addresses; SSRF checks are "
              "disabled", file=sys.stderr)
    return 0 if ok else 1


def main(argv: list) -> int:
    command = argv[1] if len(argv) > 1 else "check"
    if command == "init-key":
        return init_key()
    if command == "check":
        return check()
    if command == "migrate":
        return migrate()
    if command == "seed-models":
        return seed_models()
    if command == "catalog":
        return catalog_report()
    print(f"unknown command '{command}'. "
          f"Use: init-key | check | migrate | seed-models | catalog", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
