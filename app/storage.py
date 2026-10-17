"""Where media bytes live (spec §18, §21).

Three backends, one interface:

* **disk** — local runs. Files under `uploads/`, served by the app.
* **supabase** — Supabase Storage, over its S3-compatible endpoint. The durable choice
  on Vercel, and it pairs with the Supabase database: one project, one bill, and no
  second provider to configure.
* **vercel-blob** — Vercel Blob. Kept because it needs no extra setup when a project
  already has a Blob store.

The choice is by configuration, never by guessing, and the order is deliberate:
Supabase wins when it is fully configured, because whoever set up Supabase has built the
rest of the deployment around it. A backend that is configured but broken raises
`StorageError` — it never silently falls back to a filesystem that will lose the file.

**Why S3 rather than the Storage REST API.** Supabase serves Storage at
`/storage/v1/object/...`, but it wants an `apikey` header plus a bearer token carrying
the database's own credential. Its S3-compatible endpoint accepts ordinary HMAC-signed
requests instead: no SDK, no `service_role` key in the app, and a signing routine that
is testable offline to the byte. That last part matters — a signature either matches
what AWS computes or it does not, and a test can prove which without a network.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from .config import UPLOAD_DIR

BLOB_TOKEN = os.getenv("BLOB_READ_WRITE_TOKEN", "").strip()

#: Supabase Storage, S3-compatible. `SUPABASE_URL` or `SUPABASE_PROJECT_REF` is enough
#: to derive the endpoint, so a deployment only sets what it actually has.
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_PROJECT_REF = os.getenv("SUPABASE_PROJECT_REF", "").strip()
SUPABASE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "").strip()
SUPABASE_ACCESS_KEY = os.getenv("SUPABASE_S3_ACCESS_KEY_ID", "").strip()
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_S3_SECRET_ACCESS_KEY", "").strip()
SUPABASE_REGION = os.getenv("SUPABASE_REGION", "").strip()


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
    return ""


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
# SigV4 — signing an S3 request by hand
# ---------------------------------------------------------------------------
#
# Supabase Storage exposes an S3-compatible endpoint, so a signed PUT/GET/DELETE needs
# nothing beyond `hashlib` and `hmac`. Writing the signature rather than adding boto3
# keeps the function bundle small (cold starts) and lets a test pin the exact canonical
# request and signature against a known input.

def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date: str, region: str, service: str) -> bytes:
    key = _sign(("AWS4" + secret).encode("utf-8"), date)
    key = _sign(key, region)
    key = _sign(key, service)
    return _sign(key, "aws4_request")


def _canonical_query(params: Dict[str, str]) -> str:
    """Query parameters, sorted and percent-encoded as SigV4 requires."""
    if not params:
        return ""
    encoded = [(urllib.parse.quote(k, safe="-_.~"),
                urllib.parse.quote(str(v), safe="-_.~"))
               for k, v in params.items()]
    return "&".join(f"{k}={v}" for k, v in sorted(encoded))


def _canonical_uri(bucket: str, key: str) -> str:
    """`/bucket/key`, each segment encoded with slashes preserved."""
    return urllib.parse.quote(f"/{bucket}/{key.lstrip('/')}", safe="/-_.~")


def _header_set(content_type: str, payload_hash: str,
                amz_date: str) -> Tuple[list, Dict[str, str]]:
    """The headers that get signed, and their sorted names."""
    values = {"x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    if content_type:
        values["content-type"] = content_type
    return sorted(values), values


def canonical_request(method: str, host: str, bucket: str, key: str, *,
                      region: str, payload: bytes = b"", content_type: str = "",
                      query: Optional[Dict[str, str]] = None,
                      when: Optional[datetime] = None) -> Tuple[str, str]:
    """`(canonical_request, string_to_sign)` — exposed so a test can pin both."""
    moment = when or datetime.now(timezone.utc)
    amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = moment.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()

    _, values = _header_set(content_type, payload_hash, amz_date)
    values["host"] = host
    names = sorted(values)
    signed_headers = ";".join(names)
    rendered = "".join(f"{name}:{values[name]}\n" for name in names)

    request = "\n".join([method, _canonical_uri(bucket, key),
                         _canonical_query(query or {}), rendered,
                         signed_headers, payload_hash])
    scope = f"{date_stamp}/{region}/s3/aws4_request"
    string = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                        hashlib.sha256(request.encode("utf-8")).hexdigest()])
    return request, string


def sign_request(method: str, host: str, bucket: str, key: str, *,
                 region: str, access_key: str, secret_key: str,
                 payload: bytes = b"", content_type: str = "",
                 query: Optional[Dict[str, str]] = None,
                 when: Optional[datetime] = None) -> Dict[str, str]:
    """The headers that authenticate one S3 request.

    Deterministic: pass `when` and the same inputs always produce the same signature,
    which is what makes this testable against a known example.
    """
    moment = when or datetime.now(timezone.utc)
    amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = moment.strftime("%Y%m%d")

    _, string_to_sign = canonical_request(
        method, host, bucket, key, region=region, payload=payload,
        content_type=content_type, query=query, when=moment)

    payload_hash = hashlib.sha256(payload).hexdigest()
    _, values = _header_set(content_type, payload_hash, amz_date)
    values["host"] = host
    names = sorted(values)
    signed_headers = ";".join(names)

    scope = f"{date_stamp}/{region}/s3/aws4_request"
    signature = hmac.new(
        _signing_key(secret_key, date_stamp, region, "s3"),
        string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    out = {
        "Host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"),
    }
    if content_type:
        out["Content-Type"] = content_type
    return out


# ---------------------------------------------------------------------------
# Supabase Storage
# ---------------------------------------------------------------------------

def supabase_host() -> str:
    """The S3 endpoint host for the configured project.

    Supabase serves Storage from a `<ref>.storage.supabase.co` subdomain, not the
    project's own host — signing against the wrong host yields a 403 that reads like a
    bad key.
    """
    if SUPABASE_URL:
        parsed = urllib.parse.urlsplit(SUPABASE_URL)
        ref = parsed.hostname.split(".")[0] if parsed.hostname else ""
        if ref:
            return f"{ref}.storage.supabase.co"
    if SUPABASE_PROJECT_REF:
        return f"{SUPABASE_PROJECT_REF}.storage.supabase.co"
    raise StorageError(
        "Supabase storage is configured without an endpoint: set SUPABASE_URL or "
        "SUPABASE_PROJECT_REF")


def supabase_region() -> str:
    """The signing region. Supabase publishes the project's own region for this."""
    return SUPABASE_REGION or "us-east-1"


def _supabase_request(method: str, key: str, data: bytes = b"",
                      content_type: str = "") -> bytes:
    host = supabase_host()
    headers = sign_request(
        method, host, SUPABASE_BUCKET, key,
        region=supabase_region(), access_key=SUPABASE_ACCESS_KEY,
        secret_key=SUPABASE_SECRET_KEY, payload=data, content_type=content_type)
    url = f"https://{host}{_canonical_uri(SUPABASE_BUCKET, key)}"
    request = urllib.request.Request(url, data=data if data else None,
                                     method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:400].decode("utf-8", "replace")
        raise StorageError(f"supabase {method} failed ({exc.code}): {detail}")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise StorageError(f"supabase {method} failed: {exc}")


def _supabase_public_url(key: str) -> str:
    """The public URL for an object, when the bucket is public.

    Empty for a private bucket: handing out a URL that 403s is worse than serving the
    bytes through `/api/assets/{id}/raw`, which works either way.
    """
    if os.getenv("SUPABASE_STORAGE_PUBLIC", "").strip() not in ("1", "true", "yes"):
        return ""
    base = SUPABASE_URL or f"https://{SUPABASE_PROJECT_REF}.supabase.co"
    path = urllib.parse.quote(key.lstrip("/"))
    return f"{base}/storage/v1/object/public/{SUPABASE_BUCKET}/{path}"


def _supabase_put(key: str, data: bytes, mime: str) -> str:
    _supabase_request("PUT", key, data, mime or "application/octet-stream")
    return _supabase_public_url(key)


def _supabase_get(key: str) -> Tuple[bytes, str]:
    return _supabase_request("GET", key), ""


def _supabase_delete(key: str) -> None:
    try:
        _supabase_request("DELETE", key)
    except StorageError:
        pass


# ---------------------------------------------------------------------------
# Vercel Blob
# ---------------------------------------------------------------------------
#
# The Blob API is a plain HTTP PUT with a bearer token, so it needs no SDK. Content is
# addressed by a key we choose, which keeps `assets.path` meaningful in every backend.

BLOB_API = "https://blob.vercel-storage.com"


def _blob_request(method: str, url: str, data: Optional[bytes] = None,
                  headers: Optional[Dict[str, str]] = None,
                  timeout: float = 60) -> bytes:
    supplied = dict(headers or {})
    declared = supplied.pop("Content-Type", "application/octet-stream")
    sent = {
        "Authorization": f"Bearer {BLOB_TOKEN}",
        "x-api-version": "7",
        "x-content-type": declared,
    }
    sent.update(supplied)
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
    path = urllib.parse.quote(key.lstrip("/"))
    raw = _blob_request(
        "PUT", f"{BLOB_API}/{path}", data,
        {"Content-Type": mime, "x-add-random-suffix": "0",
         "x-cache-control-max-age": "31536000"},
    )
    try:
        return json.loads(raw.decode("utf-8")).get("url", "")
    except (ValueError, AttributeError):
        return ""


def _blob_get(key: str) -> Tuple[bytes, str]:
    path = urllib.parse.quote(key.lstrip("/"))
    return _blob_request("GET", f"{BLOB_API}/{path}"), ""


def _blob_delete(key: str) -> None:
    path = urllib.parse.quote(key.lstrip("/"))
    try:
        _blob_request("DELETE", f"{BLOB_API}/{path}")
    except StorageError:
        pass


# ---------------------------------------------------------------------------
# The interface the app uses
# ---------------------------------------------------------------------------

def use_supabase() -> bool:
    """Supabase is chosen only when it is completely configured.

    A bucket with no credentials would fail on every upload, so the check needs all
    three values. A partial configuration is not silently downgraded: `describe()`
    reports which piece is missing and `/api/health` surfaces it.
    """
    return bool(SUPABASE_BUCKET and SUPABASE_ACCESS_KEY and SUPABASE_SECRET_KEY)


def use_blob() -> bool:
    return bool(BLOB_TOKEN) and not use_supabase()


def put(key: str, data: bytes, mime: str = "application/octet-stream") -> Dict[str, str]:
    """Store bytes under `key`. Returns `{backend, url, sha256}`.

    `url` is a public URL when the backend has one, and empty when the app serves the
    bytes itself.
    """
    digest = hashlib.sha256(data).hexdigest()
    if use_supabase():
        url = _supabase_put(key, data, mime)
        return {"backend": "supabase", "url": url, "sha256": digest}
    if use_blob():
        url = _blob_put(key, data, mime)
        if not url:
            raise StorageError("blob upload returned no URL")
        return {"backend": "vercel-blob", "url": url, "sha256": digest}
    _disk_put(key, data)
    return {"backend": "disk", "url": "", "sha256": digest}


def get(key: str) -> Tuple[bytes, str]:
    """Read bytes for `key`. Returns `(data, content_type-or-empty)`."""
    if use_supabase():
        return _supabase_get(key)
    if use_blob():
        return _blob_get(key)
    return _disk_get(key)


def delete(key: str) -> None:
    if use_supabase():
        _supabase_delete(key)
    elif use_blob():
        _blob_delete(key)
    else:
        _disk_delete(key)


def backend() -> str:
    if use_supabase():
        return "supabase"
    return "vercel-blob" if use_blob() else "disk"


def is_local() -> bool:
    return backend() == "disk"


def describe() -> Dict[str, object]:
    """What is configured, for the health endpoint. Presence only, never a secret."""
    return {
        "backend": backend(),
        "supabase_bucket": SUPABASE_BUCKET or None,
        "supabase_endpoint": supabase_host() if use_supabase() else None,
        "supabase_region": supabase_region() if use_supabase() else None,
        "has_access_key": bool(SUPABASE_ACCESS_KEY),
        "has_secret_key": bool(SUPABASE_SECRET_KEY),
        "public_urls": bool(_supabase_public_url("probe")),
    }


def verify() -> Dict[str, object]:
    """A cheap check that the configured backend actually works.

    A real round trip with a tiny object, because credentials can be present and still
    be wrong — which would otherwise only surface on a user's first upload.
    """
    try:
        probe_key = "health/probe.txt"
        payload = b"prompt-studio-probe"
        if use_supabase():
            _supabase_put(probe_key, payload, "text/plain")
            data, _ = _supabase_get(probe_key)
            ok = data == payload
            _supabase_delete(probe_key)
            return {"ok": ok, "backend": "supabase"}
        if use_blob():
            _blob_put(probe_key, payload, "text/plain")
            data, _ = _blob_get(probe_key)
            ok = data == payload
            _blob_delete(probe_key)
            return {"ok": ok, "backend": "vercel-blob"}
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        probe = UPLOAD_DIR / ".probe"
        probe.write_bytes(b"ok")
        probe.unlink()
        return {"ok": True, "backend": "disk"}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "backend": backend(), "error": str(exc)[:200]}
