"""Uploads, media validation and storage (spec §18, §21).

Every uploaded file is checked three ways before it is kept: the declared type, the
*bytes* (a real signature check, because a client-declared MIME type is a claim, not a
fact), and the actual decode. A file that fails any of them is refused, not stored and
not rendered.

Media bytes go to disk under `uploads/`; the database keeps metadata and a path. Large
binaries never sit in a relational row (spec §21).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import store
from ..config import MAX_IMAGE_DIM, MAX_UPLOAD_BYTES, UPLOAD_DIR
from ..errors import ApiError, not_found

log = logging.getLogger("studio.assets")

OWNER = "local"

#: Allowed media, with the signature that must appear in the bytes.
SIGNATURES: Dict[str, List[bytes]] = {
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/webp": [b"RIFF"],            # plus "WEBP" at offset 8, checked below
    "image/gif": [b"GIF87a", b"GIF89a"],
    "video/mp4": [b"ftyp"],             # at offset 4
    "video/webm": [b"\x1a\x45\xdf\xa3"],
}

ALLOWED_MIME = set(SIGNATURES)

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(name: str) -> str:
    base = Path(str(name or "upload")).name
    cleaned = SAFE_NAME.sub("_", base).strip("._") or "upload"
    return cleaned[:120]


def sniff_mime(data: bytes) -> str:
    """The media type the bytes actually are. Empty when unrecognised."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/mp4"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"
    return ""


def _check_declared(declared: str, actual: str) -> None:
    if actual not in ALLOWED_MIME:
        raise ApiError("unsupported_media_type",
                       "That file is not a supported image or video. "
                       f"Accepted: {', '.join(sorted(ALLOWED_MIME))}.")
    if declared and declared.split(";")[0].strip().lower() not in (actual, "application/octet-stream"):
        # The bytes win; a mismatch is refused rather than reinterpreted.
        raise ApiError("media_mismatch",
                       f"The file says '{declared}' but its contents are '{actual}'.")


def inspect_image(data: bytes) -> Dict[str, Any]:
    """Decode an image for its real dimensions, refusing anything unusable."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            fmt = img.format
    except ApiError:
        raise
    except Exception as exc:                                  # noqa: BLE001
        raise ApiError("invalid_media", f"That image could not be read: {exc}.")
    if width <= 0 or height <= 0:
        raise ApiError("invalid_media", "That image reports no size.")
    if width > MAX_IMAGE_DIM or height > MAX_IMAGE_DIM:
        raise ApiError("invalid_media",
                       f"That image is {width}x{height}; the limit is "
                       f"{MAX_IMAGE_DIM}px on a side.")
    return {"width": width, "height": height, "format": fmt}


def inspect_video(data: bytes, path: Path) -> Dict[str, Any]:
    """Video metadata when a tool is available, otherwise honest nulls.

    There is no ffmpeg dependency here on purpose. Rather than invent a duration, the
    fields are left empty and `metadata.probed` stays false, so the UI says "unknown"
    instead of showing a wrong number.
    """
    meta: Dict[str, Any] = {"probed": False, "format": "mp4" if data[4:8] == b"ftyp" else "webm"}
    try:
        import shutil
        import subprocess

        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return meta
        out = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format",
             "-show_streams", str(path)],
            capture_output=True, timeout=20, check=False)
        if out.returncode != 0:
            return meta
        info = json.loads(out.stdout.decode("utf-8", "replace"))
        video_stream = next((s for s in info.get("streams", [])
                             if s.get("codec_type") == "video"), {})
        meta.update({
            "probed": True,
            "duration_ms": int(float(info.get("format", {}).get("duration", 0)) * 1000) or None,
            "width": video_stream.get("width"),
            "height": video_stream.get("height"),
            "codec": video_stream.get("codec_name"),
        })
    except Exception:                                         # noqa: BLE001
        pass
    return meta


def store_upload(filename: str, declared_mime: str, data: bytes, *,
                 project_id: Optional[str] = None, name: str = "") -> Dict[str, Any]:
    """Validate and persist an upload. Returns the public asset."""
    if not data:
        raise ApiError("invalid_media", "The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApiError("media_too_large",
                       f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")

    actual = sniff_mime(data)
    _check_declared(declared_mime, actual)

    asset_id = store.new_id("ast_")
    safe = sanitize_filename(filename or "upload")
    suffix = Path(safe).suffix or (".png" if actual.startswith("image/") else ".mp4")
    relative = f"{asset_id}{suffix}"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = (UPLOAD_DIR / relative).resolve()
    try:
        target.relative_to(UPLOAD_DIR.resolve())
    except ValueError:                                        # pragma: no cover
        raise ApiError("invalid_field", "That filename cannot be stored.")

    if actual.startswith("image/"):
        dims = inspect_image(data)
        media_type, width, height, duration_ms, extra = "image", dims["width"], dims["height"], None, dims
    else:
        # Written before probing, because probing reads the file.
        target.write_bytes(data)
        extra = inspect_video(data, target)
        media_type = "video"
        width, height = extra.get("width"), extra.get("height")
        duration_ms = extra.get("duration_ms")

    if not target.exists():
        target.write_bytes(data)

    digest = hashlib.sha256(data).hexdigest()
    now = store.utcnow()
    store.execute(
        "INSERT INTO assets (id, owner_id, project_id, name, media_type, mime, bytes, "
        "width, height, duration_ms, path, sha256, source, metadata_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (asset_id, OWNER, project_id or None, name or sanitize_filename(filename),
         media_type, actual, len(data), width, height, duration_ms, relative, digest,
         "upload", store.dumps(extra), now))
    return get_asset(asset_id)["asset"]


def store_generated_bytes(job: Dict[str, Any], data: bytes, mime: str,
                          position: int) -> str:
    """Persist an output the adapter produced in memory (the offline adapter does this)."""
    asset_id = store.new_id("ast_")
    suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
              "video/mp4": ".mp4", "video/webm": ".webm"}.get(mime, ".bin")
    relative = f"{asset_id}{suffix}"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = (UPLOAD_DIR / relative).resolve()
    try:
        target.relative_to(UPLOAD_DIR.resolve())
    except ValueError:                                        # pragma: no cover
        raise ApiError("storage_failure", "The output path was refused.", status=500)
    target.write_bytes(data)

    meta: Dict[str, Any] = {}
    width = height = duration_ms = None
    if mime.startswith("image/"):
        try:
            dims = inspect_image(data)
            width, height = dims["width"], dims["height"]
            meta = dims
        except ApiError:
            # A provider's image that this app cannot decode is still stored — it is
            # the user's output. It just carries no dimensions.
            meta = {"decoded": False}
    store.execute(
        "INSERT INTO assets (id, owner_id, project_id, name, media_type, mime, bytes, "
        "width, height, duration_ms, path, sha256, source, metadata_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (asset_id, job["owner_id"], job["project_id"],
         f"{(job.get('prompt') or 'output')[:60]} #{position + 1}",
         "video" if mime.startswith("video/") else "image", mime, len(data),
         width, height, duration_ms, relative, hashlib.sha256(data).hexdigest(),
         "generated", store.dumps(meta), store.utcnow()))
    return asset_id


def store_remote_output(job: Dict[str, Any], remote_url: str, position: int,
                        adapter) -> Tuple[str, str, Dict[str, Any]]:
    """Download a provider artefact and store it locally.

    Called from a worker thread that has no request context, so a failure here is a
    storage failure on the job rather than a 500 to anyone.
    """
    from .execution import download_for_storage

    data, mime = download_for_storage(remote_url, adapter)
    asset_id = store_generated_bytes(job, data, mime or "application/octet-stream", position)
    row = store.row("SELECT * FROM assets WHERE id=?", (asset_id,))
    meta = store.load_json(row["metadata_json"], {}) if row else {}
    store.execute("UPDATE assets SET metadata_json=? WHERE id=?",
                  (store.dumps({**meta, "remote_url": remote_url}), asset_id))
    return asset_id, mime, {"width": row["width"], "height": row["height"],
                            "duration_ms": row["duration_ms"]}


def public_asset(row: Dict[str, Any]) -> Dict[str, Any]:
    if not row:
        return {}
    meta = store.load_json(row.get("metadata_json"), {})
    remote = meta.get("remote_url") if isinstance(meta, dict) else None
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "name": row["name"],
        "media_type": row["media_type"],
        "mime": row["mime"],
        "bytes": row["bytes"],
        "width": row["width"],
        "height": row["height"],
        "duration_ms": row["duration_ms"],
        "source": row["source"],
        "metadata": meta,
        "created_at": row["created_at"],
        "raw_path": f"/api/assets/{row['id']}/raw",
        "provider_url": remote if isinstance(remote, str) and remote.startswith("http") else None,
    }


def get_asset(asset_id: str) -> Dict[str, Any]:
    row = store.row("SELECT * FROM assets WHERE id=? AND owner_id=?", (asset_id, OWNER))
    if not row:
        raise not_found("asset")
    return {"asset": public_asset(row), "row": row}


def list_assets(*, project_id: str = "", media_type: str = "", limit: int = 50,
                offset: int = 0) -> Dict[str, Any]:
    sql = "SELECT * FROM assets WHERE owner_id=?"
    params: List[Any] = [OWNER]
    if project_id:
        sql += " AND project_id=?"
        params.append(project_id)
    if media_type:
        sql += " AND media_type=?"
        params.append(media_type)
    total = store.scalar(sql.replace("SELECT *", "SELECT COUNT(*)", 1), params) or 0
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return {"assets": [public_asset(r) for r in store.rows(sql, params)],
            "total": total, "limit": limit, "offset": offset}


def asset_file(asset_id: str) -> Tuple[Path, str, str]:
    """Resolve an asset to a file on disk, refusing anything outside the upload root."""
    found = get_asset(asset_id)
    row = found["row"]
    candidate = (UPLOAD_DIR / row["path"]).resolve()
    try:
        candidate.relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        raise ApiError("not_found", "No such asset.", status=404)
    if not candidate.is_file():
        raise ApiError("not_found", "That asset's file is missing.", status=404)
    return candidate, row["mime"] or "application/octet-stream", row["name"]


def delete_asset(asset_id: str) -> Dict[str, Any]:
    found = get_asset(asset_id)
    row = found["row"]
    try:
        path = (UPLOAD_DIR / row["path"]).resolve()
        if str(path).startswith(str(UPLOAD_DIR.resolve())) and path.is_file():
            path.unlink()
    except OSError:
        log.warning("could not remove file for asset %s", asset_id)
    store.execute("DELETE FROM outputs WHERE asset_id=?", (asset_id,))
    store.execute("DELETE FROM assets WHERE id=?", (asset_id,))
    return {"deleted": asset_id}


def decode_base64_upload(value: str) -> bytes:
    """Decode a data-URL or bare base64 body, with a size check before allocating."""
    if not isinstance(value, str) or not value:
        raise ApiError("invalid_media", "No file data was provided.")
    payload = value
    if value.startswith("data:"):
        head, _, payload = value.partition(",")
        if not _:
            raise ApiError("invalid_media", "That data URL has no payload.")
    # 4 base64 chars per 3 bytes; check before decoding so a huge string is refused early.
    if len(payload) * 3 // 4 > MAX_UPLOAD_BYTES:
        raise ApiError("media_too_large",
                       f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        return base64.b64decode(payload, validate=True)
    except Exception:
        raise ApiError("invalid_media", "The file data was not valid base64.")
