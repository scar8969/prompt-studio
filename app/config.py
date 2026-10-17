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

#: Vercel sets VERCEL=1 in every function, where the bundle at /var/task is read-only.
#: Anything that writes — a SQLite file, an upload — has to go to /tmp. Reading this
#: before choosing defaults is what keeps a missing DATABASE_URL from turning into
#: `OSError: [Errno 30] Read-only file system` on every request, which is exactly how
#: the first deployment failed.
IS_SERVERLESS = (os.getenv("VERCEL", "").strip() == "1"
                 or bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME")))
_WRITABLE = Path("/tmp") if IS_SERVERLESS else BASE_DIR

DATA_DIR = Path(os.getenv("DATA_DIR") or (_WRITABLE / "data"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR") or (_WRITABLE / "uploads"))
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

#: On a serverless platform the process is frozen after it responds, so in-process
#: workers cannot be relied on either. `IS_SERVERLESS` itself is defined above, next to
#: the directory defaults, because the choice of writable directory depends on it.
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


def ensure_dirs() -> list:
    """Create the writable directories, and report any that cannot be created.

    Returns the failures instead of raising. A read-only bundle is a configuration
    problem, and turning it into an exception here means every request becomes a bare
    502 with no explanation; the health endpoint reports it in `problems` instead.
    """
    failures = []
    for directory in (DATA_DIR, UPLOAD_DIR):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            failures.append(f"{directory}: {exc.strerror or exc}")
    return failures
