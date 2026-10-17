"""Where media bytes live (spec §18, §21).

Two backends, one interface:

* **disk** — local runs. Files under `uploads/`, served by the app.
* **vercel-blob** — Vercel. The filesystem there is ephemeral and per-instance, so a
  file written during one request is gone by the next. Blob storage is the durable
  option and is what makes an upload survive its own response.

The choice is by configuration, never by guessing: if `BLOB_READ_WRITE_TOKEN` is set,
Blob is used, and if it is set but broken the app reports that instead of silently
falling back to a disk that will lose the file.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple

from .config import UPLOAD_DIR

BLOB_TOKEN = os.getenv("BLOB_READ_WRITE_TOKEN", "").strip()
USE_BLOB = bool(BLOB_TOKEN)


class StorageError(RuntimeError):
    """A storage backend failed. Surfaced as a `storage_failure`, never swallowed."""


# ---------------------------------------------------------------------------
# Disk
# ---------------------------------------------------------------------------

def disk_path(key: str) -> Path:
    """Resolve a storage key inside the upload directory, refusing escapes."""
    candidate = (UPLOAD_DIR / key).resolve()
    root = UPLOAD_DIR.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise StorageError("storage key escaped the upload directory")
    return candidate


def _disk_put(key: str, data: bytes) -> str:
    target = disk_path(key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return f"/api/assets/{{id}}/raw"


def _disk_get(key: str) -> Tuple[bytes, str]:
    target = disk_path(key)
    if not target.is_file():
        raise StorageError("file is missing from disk")
    return target.read_bytes(), ""


def _disk_delete(key: str) -> None:
    try:
        target = disk_path(key)
        if target.is_file():
            target.unlink()
    except (OSError, StorageError):
        pass


# ---------------------------------------------------------------------------
# Vercel Blob
# ---------------------------------------------------------------------------
#
# The Blob API is a plain HTTP PUT with a bearer token, so it needs no SDK. Content is
# addressed by a key we choose, which keeps `assets.path` meaningful in both backends.

BLOB_API = "https://blob.vercel-storage.com"


def _blob_request(method: str, url: str, data: Optional[bytes] = None,
                  headers: Optional[Dict[str, str]] = None, timeout: float = 60) -> bytes:
    sent = {
        "Authorization": f"Bearer {BLOB_TOKEN}",
        "x-api-version": "7",
        "x-content-type": headers.pop("Content-Type", "application/octet-stream") if headers else "application/octet-stream",
    }
    sent.update(headers or {})
    request = urllib.request.Request(url, data=data, method=method, headers=sent)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise StorageError(f"blob {method} failed ({exc.code}): {detail}")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise StorageError(f"blob {method} failed: {exc}")


def _blob_put(key: str, data: bytes, mime: str = "application/octet-stream") -> str:
    """Upload one object and return its public URL."""
    path = urllib.parse.quote(key.lstrip("/"))
    raw = _blob_request(
        "PUT", f"{BLOB_API}/{path}", data,
        {"Content-Type": mime, "x-add-random-suffix": "0", "x-cache-control-max-age": "31536000"},
    )
    try:
        return json.loads(raw.decode("utf-8")).get("url", "")
    except (ValueError, AttributeError):
        return ""


def _blob_get(key: str) -> Tuple[bytes, str]:
    path = urllib.parse.quote(key.lstrip("/"))
    data = _blob_request("GET", f"{BLOB_API}/{path}")
    return data, ""


def _blob_delete(key: str) -> None:
    path = urllib.parse.quote(key.lstrip("/"))
    try:
        _blob_request("DELETE", f"{BLOB_API}/{path}")
    except StorageError:
        pass


# ---------------------------------------------------------------------------
# The interface the app uses
# ---------------------------------------------------------------------------

def put(key: str, data: bytes, mime: str = "application/octet-stream") -> Dict[str, str]:
    """Store bytes under `key`. Returns `{backend, url}`.

    `url` is a public URL when the backend has one (Blob), and empty when the app
    serves the bytes itself (disk).
    """
    digest = hashlib.sha256(data).hexdigest()
    if USE_BLOB:
        url = _blob_put(key, data, mime)
        if not url:
            raise StorageError("blob upload returned no URL")
        return {"backend": "vercel-blob", "url": url, "sha256": digest}
    _disk_put(key, data)
    return {"backend": "disk", "url": "", "sha256": digest}


def get(key: str) -> Tuple[bytes, str]:
    """Read bytes for `key`. Returns (data, content_type-or-empty)."""
    if USE_BLOB:
        return _blob_get(key)
    return _disk_get(key)


def delete(key: str) -> None:
    if USE_BLOB:
        _blob_delete(key)
    else:
        _disk_delete(key)


def backend() -> str:
    return "vercel-blob" if USE_BLOB else "disk"


def is_local() -> bool:
    return not USE_BLOB


def verify() -> Dict[str, object]:
    """A cheap check that the configured backend actually works.

    On Blob this performs a real round trip with a tiny object, because a token can be
    present and still be revoked — which would otherwise only surface on a user's first
    upload. Local disk only checks the directory is writable.
    """
    try:
        if USE_BLOB:
            probe_key = "health/probe.txt"
            _blob_put(probe_key, b"prompt-studio-probe", "text/plain")
            data, _ = _blob_get(probe_key)
            ok = data == b"prompt-studio-probe"
            _blob_delete(probe_key)
            return {"ok": ok, "backend": "vercel-blob"}
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        probe = UPLOAD_DIR / ".probe"
        probe.write_bytes(b"ok")
        probe.unlink()
        return {"ok": True, "backend": "disk"}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "backend": backend(), "error": str(exc)[:200]}
