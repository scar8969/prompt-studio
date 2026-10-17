"""The job tick: what replaces background workers on serverless.

Locally a worker pool runs jobs in threads. On Vercel nothing runs after a response, so
a job would sit `queued` forever. This endpoint is the driver instead: a cron hits it,
it claims the next job with a lease, runs it inside a time budget, and returns.

    GET /api/cron/tick

Protected by `CRON_SECRET`: if that variable is set, the caller must send
`Authorization: Bearer <secret>`. If it is *not* set the endpoint refuses to run at all
in production, because an unauthenticated endpoint that spends provider credit is worse
than a stalled queue.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.web.server import StudioHandler  # noqa: E402

_PATH_HEADER = "x-vercel-proxy-path"


class handler(StudioHandler):  # noqa: N801
    def do_GET(self):                      # noqa: N802
        self.path = "/api/cron/tick"
        return super().do_GET()

    def do_POST(self):                     # noqa: N802
        self.path = "/api/cron/tick"
        return super().do_POST()
