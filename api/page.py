"""The page: /  → the studio UI.

A separate function from the API so the shell can be cached and an API cold start does
not block the first paint. It serves three files and nothing else — the allowlist is the
same one the local server uses, so what is reachable cannot differ between environments.
"""

from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.web.static_files import resolve_static  # noqa: E402

STATIC_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "app", "web")

#: Vercel rewrites /(.*) here, so the original path arrives in this header.
_ORIGINAL = "x-vercel-proxy-path"


class handler(BaseHTTPRequestHandler):  # noqa: N801 - Vercel looks for this name
    def do_GET(self):                      # noqa: N802
        self._serve()

    def do_HEAD(self):                     # noqa: N802
        self._serve(head_only=True)

    def _serve(self, head_only: bool = False) -> None:
        requested = self.headers.get(_ORIGINAL) or self.path or "/"
        # A query string is not part of the path. `/` must serve the page whether or not
        # a cache-buster or tracking parameter is attached — without this,
        # `/?cb=1` fell through to the static resolver, found no file named "?cb=1", and
        # returned 404 for the site's own homepage.
        requested = requested.split("?", 1)[0].split("#", 1)[0] or "/"
        try:
            from pathlib import Path

            file_path, content_type = resolve_static(Path(STATIC_ROOT), requested)
            data = file_path.read_bytes()
        except Exception:
            # A miss is a 404 with no detail — the same as the local server, and no
            # directory listing or stack trace.
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", "9")
            self.end_headers()
            if not head_only:
                self.wfile.write(b"Not Found")
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=0, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def log_message(self, *args):
        return
