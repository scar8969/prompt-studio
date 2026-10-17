"""The HTTP server (spec §17, §19, §21).

A stdlib `ThreadingHTTPServer`: one request per thread, so a slow provider call cannot
block the UI. Responsibilities kept deliberately thin — parse, dispatch, serialise — so
the rules live in `services/` and can be tested without a socket.

Every response carries a request id, and that id appears in the log line for the same
request, which is what makes a user's error report traceable (spec §19).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from .. import store
from ..config import (HOST, LOG_LEVEL, MAX_JSON_BODY, PORT, UPLOAD_DIR, ensure_dirs)
from ..errors import ApiError
from .request import Request, parse_body
from .routes import router
from .static_files import resolve_static

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("studio.http")

#: The UI lives next to this module, so the static root cannot drift from the code.
STATIC_ROOT = __import__("pathlib").Path(__file__).resolve().parent

#: Paths that exist but are not routable, and the methods each path supports is
#: discovered from the router — this just records the prefix that is ours.
API_PREFIX = "/api/"


class StudioHandler(BaseHTTPRequestHandler):
    server_version = "PromptStudio/1.0"
    protocol_version = "HTTP/1.1"

    # -- logging ------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep the default request line but route it through `logging`, and never
        # include headers (an Authorization header would end up in the log).
        log.info("%s %s", self.address_string(), fmt % args)

    def log_error(self, fmt: str, *args: Any) -> None:
        log.warning("%s %s", self.address_string(), fmt % args)

    # -- responses ----------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str,
              extra: Optional[Dict[str, str]] = None, request_id: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # A local studio calls itself; no cross-origin third party needs access.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if request_id:
            self.send_header("X-Request-Id", request_id)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200, request_id: str = "") -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", request_id=request_id)

    def _error(self, error: ApiError, request_id: str = "") -> None:
        self._json(error.to_dict(), error.status, request_id=request_id)

    # -- request parsing ----------------------------------------------------
    def _read_raw(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError("invalid_request", "Content-Length was not a number.")
        if length < 0:
            raise ApiError("invalid_request", "Content-Length was negative.")
        if length > MAX_JSON_BODY and not self._is_multipart():
            raise ApiError("payload_too_large",
                           f"The body is larger than {MAX_JSON_BODY // 1024} KB.",
                           status=413)
        return self.rfile.read(length) if length else b""

    def _is_multipart(self) -> bool:
        return "multipart/form-data" in (self.headers.get("Content-Type") or "").lower()

    def _read_multipart(self) -> Dict[str, Any]:
        """A small multipart parser for one file field plus simple text fields.

        Kept here rather than pulling in a dependency: the only upload the UI makes is
        a single file with an optional project id.
        """
        from ..config import MAX_UPLOAD_BYTES

        content_type = self.headers.get("Content-Type") or ""
        marker = "boundary="
        if marker not in content_type:
            raise ApiError("invalid_request", "The multipart body had no boundary.")
        boundary = content_type.split(marker, 1)[1].strip().strip('"')
        if not boundary:
            raise ApiError("invalid_request", "The multipart boundary was empty.")

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError("invalid_request", "Content-Length was not a number.")
        if length > MAX_UPLOAD_BYTES + 1024 * 1024:
            raise ApiError("media_too_large",
                           f"The upload is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                           status=413)
        raw = self.rfile.read(length) if length else b""
        delimiter = b"--" + boundary.encode("utf-8")
        fields: Dict[str, Any] = {}
        for part in raw.split(delimiter)[1:]:
            if part in (b"--", b"--\r\n", b"\r\n"):
                continue
            head, _, body = part.partition(b"\r\n\r\n")
            if not _:
                continue
            body = body.rstrip(b"\r\n")
            text = head.decode("utf-8", "replace")
            name = _disposition_value(text, "name")
            filename = _disposition_value(text, "filename")
            if not name:
                continue
            if filename:
                fields["data"] = body
                fields["filename"] = filename
                mime = ""
                for line in text.splitlines():
                    if line.lower().startswith("content-type:"):
                        mime = line.split(":", 1)[1].strip()
                fields["mime"] = mime
            else:
                fields[name] = body.decode("utf-8", "replace")
        return fields

    # -- dispatch -----------------------------------------------------------
    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_OPTIONS(self) -> None:
        self._send(204, b"", "text/plain", {"Allow": "GET, HEAD, POST, PATCH, DELETE"})

    def _dispatch(self, method: str) -> None:
        request_id = uuid.uuid4().hex[:12]
        started = time.time()
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        query = parse_qs(parts.query, keep_blank_values=True)

        try:
            if path.startswith(API_PREFIX):
                self._handle_api(method, path, query, request_id)
            else:
                self._handle_static(method, path, request_id)
        except ApiError as exc:
            self._error(exc, request_id)
            log.info("rid=%s %s %s -> %s %s", request_id, method, path, exc.status, exc.code)
        except BrokenPipeError:
            log.info("rid=%s client went away", request_id)
        except Exception as exc:                                   # noqa: BLE001
            # Log everything internally; tell the caller nothing but a code.
            log.error("rid=%s unhandled error on %s %s: %s\n%s", request_id, method, path,
                      exc, traceback.format_exc())
            self._error(ApiError("internal_error", status=500), request_id)
        finally:
            log.info("rid=%s %s %s %d ms", request_id, method, path,
                     int((time.time() - started) * 1000))

    def _handle_api(self, method: str, path: str, query: Dict[str, Any],
                    request_id: str) -> None:
        # A raw-bytes route (asset downloads) is resolved before the router so it can
        # stream a file rather than JSON.
        if method in ("GET", "HEAD") and path.startswith("/api/assets/") and path.endswith("/raw"):
            return self._serve_asset(path, request_id)

        handler_name, params, allowed = router.resolve(method, path)
        if handler_name is None:
            if allowed:
                raise ApiError("method_not_allowed",
                               f"{path} does not accept {method}.",
                               status=405, details={"allowed": sorted(set(allowed))})
            raise ApiError("not_found", "No such endpoint.", status=404)

        body: Any = {}
        if method in ("POST", "PATCH", "PUT"):
            if self._is_multipart():
                body = self._read_multipart()
                # A multipart file arrives as raw bytes; hand it on as base64 so the
                # upload service has one code path.
                import base64
                if isinstance(body.get("data"), (bytes, bytearray)):
                    body["data"] = base64.b64encode(body["data"]).decode("ascii")
            else:
                body = parse_body(self._read_raw(), self.headers.get("Content-Type") or "",
                                  MAX_JSON_BODY)

        request = Request(self, method, path, query, body,
                          {k.lower(): v for k, v in self.headers.items()}, params)
        from . import routes as routes_module

        handler = getattr(routes_module, handler_name, None)
        if handler is None or not callable(handler):
            raise ApiError("internal_error", "That route is not implemented.", status=501)
        payload, status = handler(request)
        self._json(payload, status, request_id=request_id)

    def _serve_asset(self, path: str, request_id: str) -> None:
        from ..services.assets import asset_file

        asset_id = path[len("/api/assets/"):-len("/raw")].strip("/")
        if not asset_id or "/" in asset_id:
            raise ApiError("not_found", "No such asset.", status=404)
        data, mime, name = asset_file(asset_id)
        self._send(200, data, mime,
                   {"Content-Disposition": f'inline; filename="{_safe_header(name)}"'},
                   request_id=request_id)

    def _handle_static(self, method: str, path: str, request_id: str) -> None:
        if method not in ("GET", "HEAD"):
            raise ApiError("method_not_allowed", "Static files are served over GET.",
                           status=405)
        file_path, content_type = resolve_static(STATIC_ROOT, path)
        data = file_path.read_bytes()
        self._send(200, data, content_type, request_id=request_id)


def _disposition_value(header_text: str, key: str) -> str:
    for line in header_text.splitlines():
        if "content-disposition" not in line.lower():
            continue
        for piece in line.split(";"):
            if "=" not in piece:
                continue
            name, _, value = piece.partition("=")
            if name.strip().lower() == key:
                return value.strip().strip('"')
    return ""


def _safe_header(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isalnum() or ch in "._- ")[:120] or "file"


def build_server(host: str = HOST, port: int = PORT) -> ThreadingHTTPServer:
    """Create the server, with the job workers running.

    The workers start here rather than only in `main()`: anything that builds a server
    should get a working queue, otherwise a job is accepted and never picked up — which
    looks exactly like a hang. `start_workers` is idempotent, so calling it twice is safe.
    """
    from ..services.jobs import reap_stale, requeue_unfinished, start_workers

    ensure_dirs()
    store.init()
    start_workers()
    requeue_unfinished()
    reap_stale()
    server = ThreadingHTTPServer((host, port), StudioHandler)
    server.daemon_threads = True
    return server


def main() -> None:                                            # pragma: no cover
    from ..config import EPHEMERAL_KEY, IS_PRODUCTION, PUBLIC_APP_URL

    server = build_server()
    if EPHEMERAL_KEY and not IS_PRODUCTION:
        log.warning("APP_ENCRYPTION_KEY is not set: using a throwaway key. "
                    "Saved credentials will not survive a restart. "
                    "Set APP_ENCRYPTION_KEY to keep them.")

    log.info("Prompt Studio listening on %s (uploads in %s)", PUBLIC_APP_URL, UPLOAD_DIR)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":                                     # pragma: no cover
    main()
