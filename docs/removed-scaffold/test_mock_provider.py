#!/usr/bin/env python3
"""Mock OpenAI-compatible provider used to exercise the full generation
pipeline without real API keys: logic model returns a structured plan, image
model returns a PNG. Verifies job queue -> worker -> gallery -> UI display."""
import base64
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(level=logging.INFO, format="[mock] %(message)s")
log = logging.getLogger("mock")

# 1x1 red PNG
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

PLAN = {
    "action": "generate",
    "generations": [
        {"prompt": "a red fox in a winter forest", "aspectRatio": "16:9", "count": 2},
        {"prompt": "the same fox at dusk", "aspectRatio": "1:1", "count": 1},
    ],
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.info(fmt % args)

    def _read(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = self._read()
        p = (self.path or "").rstrip("/")

        if p.endswith("/chat/completions"):
            # emulate a logic model returning a strict JSON plan
            return self._json({
                "choices": [{"message": {"role": "assistant", "content": json.dumps(PLAN)}}]
            })

        if p.endswith("/images/generations"):
            n = int(payload.get("n", 1) or 1)
            return self._json({
                "data": [{"b64_json": PNG_B64, "url": ""} for _ in range(n)]
            })

        self.send_error(404)

    def do_GET(self):
        self._json({"ok": True})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8790), Handler)
    log.info("mock provider -> http://127.0.0.1:8790/v1")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
