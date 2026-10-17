"""One Vercel function that serves the whole API.

Vercel mounts each file in `api/` as a function, and a Python function exports `handler`
as a `BaseHTTPRequestHandler` subclass. Rather than one file per route (nine files, nine
cold starts, nine copies of the schema check), everything below `/api/` is dispatched
here — the same `Router` and the same services the local server uses, so there is exactly
one implementation of the behaviour.

The important part is `_prepare`: a serverless process is new every time, so the schema
and the configured catalogue models are ensured on the way in, once per warm instance.
"""

from __future__ import annotations

import logging
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.web.server import StudioHandler  # noqa: E402

log = logging.getLogger("app.vercel")

#: Vercel rewrites /api/(.*) here, so the original path arrives in this header.
_PATH_HEADER = "x-vercel-proxy-path"

_prepared = False
_prepare_lock = threading.Lock()


def _prepare() -> None:
    """Create the schema once per instance, and register configured models.

    Idempotent and concurrency-safe: two requests on a cold instance both call this and
    the lock means the migration runs once. A failure is logged and left to surface on
    the request itself, so the client gets a real error with a code, not a silent hang.
    """
    global _prepared
    if _prepared:
        return
    with _prepare_lock:
        if _prepared:
            return
        try:
            from app import store

            store.init()
            # Register whichever catalogue models have keys in this environment.
            # Harmless when none are set, and it means adding a key needs no extra step.
            try:
                from app import catalog

                catalog.seed_missing_models()
            except Exception:                              # noqa: BLE001
                log.exception("could not register catalogue models")
            _prepared = True
        except Exception:                                  # noqa: BLE001
            log.exception("schema preparation failed; the request will report it")


class handler(StudioHandler):  # noqa: N801 - Vercel looks for this exact name
    """The API. Inherits the whole dispatcher, so routing cannot drift."""

    def _vercel_path(self) -> str:
        incoming = self.path or "/"
        # Vercel rewrites /api/(.*) to this function without preserving the prefix, so
        # the original path is restored from the header the rewrite sets.
        if incoming.startswith("/api"):
            return incoming
        original = self.headers.get(_PATH_HEADER) or ""
        if original.startswith("/api"):
            return original
        return "/api" + ("" if incoming == "/" else incoming)

    def _dispatch(self, method: str) -> None:
        """Prepare once, restore the rewritten path, then let the real dispatcher run.

        This overrides `StudioHandler._dispatch` rather than the verbs on purpose. The
        base class already routes every verb through `_dispatch`, so overriding that one
        method covers GET/POST/PATCH/DELETE/PUT with no per-verb code to keep in sync —
        and no chance of the mutual recursion that overriding `do_GET` while calling
        `super().do_GET()` produces (the base `do_GET` calls `self._dispatch`, which is
        the override again).
        """
        _prepare()
        self.path = self._vercel_path()
        return super()._dispatch(method)
