"""Configuration, validated at startup (spec §22).

Required values are checked once, at import, and a missing one is reported by name
rather than surfacing later as a 500 on the first request that needs it. Secrets are
never given defaults: a default encryption key would mean every deployment of this
software shares one, which is worse than refusing to start.
"""

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR") or (BASE_DIR / "data"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR") or (BASE_DIR / "uploads"))
DB_PATH = Path(os.getenv("DATABASE_PATH") or (DATA_DIR / "studio.db"))

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8765"))
PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", f"http://127.0.0.1:{PORT}")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Development mode relaxes two things: it will generate a throwaway encryption key
# (so a fresh clone runs) and it permits private upstream addresses (so a
# localhost model server can be connected). Production refuses both.
ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = ENV in ("production", "prod")

# --- limits -----------------------------------------------------------------
MAX_JSON_BODY = int(os.getenv("MAX_JSON_BODY", str(2 * 1024 * 1024)))       # 2 MB
MAX_UPLOAD_BYTES = int(os.getenv("MAX_FILE_SIZE", str(50 * 1024 * 1024)))   # 50 MB
MAX_IMAGE_DIM = int(os.getenv("MAX_IMAGE_DIM", "8192"))
UPSTREAM_TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT", "120"))
UPSTREAM_MAX_BYTES = int(os.getenv("UPSTREAM_MAX_BYTES", str(32 * 1024 * 1024)))
MAX_JOB_ATTEMPTS = int(os.getenv("MAX_JOB_ATTEMPTS", "3"))
JOB_WORKERS = int(os.getenv("JOB_WORKERS", "2"))
ALLOW_PRIVATE_UPSTREAM = os.getenv("ALLOW_PRIVATE_UPSTREAM", "").strip() == "1"

# Total seconds a job may spend talking to a provider before it is failed.
JOB_TIMEOUT = float(os.getenv("JOB_TIMEOUT", "600"))


class ConfigError(RuntimeError):
    """Raised at startup when configuration is unusable. Never carries a secret."""


def _load_encryption_key() -> bytes:
    """The key that encrypts stored provider credentials (spec §16).

    Accepts a Fernet key, or any passphrase which is then stretched with PBKDF2 —
    so an operator can paste something memorable without this storing it verbatim.
    """
    raw = os.getenv("APP_ENCRYPTION_KEY", "").strip()
    if not raw:
        if IS_PRODUCTION:
            raise ConfigError(
                "APP_ENCRYPTION_KEY is required in production: without it, stored "
                "provider keys cannot be encrypted at rest."
            )
        # Development only: a per-process key. Keys saved in one run cannot be read
        # in the next, which is deliberate — it makes the failure obvious locally
        # instead of silently persisting. The banner says so on startup.
        return _derive_key(secrets.token_urlsafe(32))
    return _derive_key(raw)


def _derive_key(raw: str) -> bytes:
    import base64
    import hashlib
    # A real Fernet key is 44 chars of urlsafe-base64 and decodes to 32 bytes.
    try:
        decoded = base64.urlsafe_b64decode(raw.encode())
        if len(decoded) == 32:
            return raw.encode()
    except Exception:
        pass
    salt = os.getenv("APP_ENCRYPTION_SALT", "prompt-studio-v1").encode()
    return base64.urlsafe_b64encode(hashlib.pbkdf2_hmac("sha256", raw.encode(), salt, 200_000))


APP_ENCRYPTION_KEY = _load_encryption_key()
EPHEMERAL_KEY = not os.getenv("APP_ENCRYPTION_KEY", "").strip()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
