"""Static file serving with a real allowlist (spec §18, §21).

Only files resolved *inside* the static root are served, symlinks are resolved first,
and a request for anything else is refused. This replaces the pattern where a path is
joined onto a root and trusted.
"""

from __future__ import annotations

import mimetypes
import posixpath
from pathlib import Path
from typing import Optional, Tuple

from ..errors import ApiError

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".map": "application/json",
}

# Everything the UI is allowed to fetch by URL. Adding a file means adding it here —
# the point is that serving is opt-in, not opt-out.
PUBLIC_FILES = (
    "/",
    "/studio.html",
)


def resolve_static(root: Path, request_path: str) -> Tuple[Path, str]:
    """Map a URL to a file inside `root`, or raise.

    Returns (path, content_type).
    """
    path = request_path or "/"
    if path == "/":
        # The studio is the page this app serves; the old Vite shell is not used.
        path = "/studio.html"
    if "?" in path:
        path = path.split("?", 1)[0]
    if "#" in path:
        path = path.split("#", 1)[0]

    # Decode is the caller's job; by here the path is already a plain string. Reject
    # anything that could escape, before touching the filesystem.
    if path.startswith("/") is False:
        raise ApiError("not_found", "No such file.", status=404)
    normalised = posixpath.normpath(path)
    if normalised.startswith("..") or normalised.startswith("/..") or "\x00" in path:
        raise ApiError("not_found", "No such file.", status=404)

    candidate = (root / normalised.lstrip("/")).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        # A traversal or a symlink pointing outside the root.
        raise ApiError("not_found", "No such file.", status=404)

    if candidate.is_dir():
        candidate = (candidate / "index.html").resolve()
        try:
            candidate.relative_to(root_resolved)
        except ValueError:
            raise ApiError("not_found", "No such file.", status=404)

    if not candidate.is_file():
        raise ApiError("not_found", "No such file.", status=404)

    suffix = candidate.suffix.lower()
    content_type = STATIC_TYPES.get(suffix)
    if content_type is None:
        guessed, _ = mimetypes.guess_type(candidate.name)
        if not guessed or not guessed.startswith(("text/", "image/", "application/json")):
            raise ApiError("not_found", "No such file.", status=404)
        content_type = guessed

    return candidate, content_type


def _self_test(tmp_root: Path) -> None:
    (tmp_root / "studio.html").write_text("<p>ok</p>", encoding="utf-8")
    (tmp_root / "sub").mkdir(exist_ok=True)
    (tmp_root / "sub" / "app.js").write_text("console.log(1)", encoding="utf-8")

    path, ctype = resolve_static(tmp_root, "/")
    assert path.name == "studio.html" and ctype.startswith("text/html")
    path, ctype = resolve_static(tmp_root, "/sub/app.js")
    assert path.name == "app.js" and "javascript" in ctype

    for bad in ("/../etc/passwd", "/sub/../../etc/passwd", "/nope.txt",
                "/\x00", "../data/studio.db"):
        try:
            resolve_static(tmp_root, bad)
        except ApiError:
            continue
        raise AssertionError(f"should have refused: {bad!r}")


if __name__ == "__main__":  # pragma: no cover
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _self_test(Path(tmp))
    print("static self-test OK")
