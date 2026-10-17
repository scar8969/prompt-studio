"""Small operator commands.

    python -m app.cli init-key     # a fresh APP_ENCRYPTION_KEY
    python -m app.cli check        # configuration and schema, without starting a server
"""

from __future__ import annotations

import sys


def init_key() -> int:
    from .crypto import generate_key

    print(generate_key())
    print("\nPut that in your .env as APP_ENCRYPTION_KEY. Keep it: credentials stored "
          "with a different key cannot be decrypted.", file=sys.stderr)
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
    print(f"unknown command '{command}'. Use: init-key | check", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
