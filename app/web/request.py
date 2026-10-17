"""Request parsing and the one response envelope (spec §17, §19).

Routing is table-driven: a list of (method, compiled pattern, handler name). Handlers
raise `ApiError` and the dispatcher turns it into the documented shape, so no handler
ever writes its own error format and no failure leaks a stack trace.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Pattern, Tuple

from ..errors import ApiError


class Request:
    """A parsed request. `body` is always a dict — never raw, never trusted."""
    def __init__(self, handler, method: str, path: str, query: Dict[str, List[str]],
                 body: Any, headers: Dict[str, str], path_params: Dict[str, str]):
        self._handler = handler
        self.method = method
        self.path = path
        self.query = query
        self.body = body
        self.headers = headers
        self.path_params = path_params

    # -- body access --------------------------------------------------------
    def get(self, field: str, default: Any = None) -> Any:
        if not isinstance(self.body, dict):
            return default
        value = self.body.get(field, default)
        return default if value is None else value

    def require(self, field: str) -> Any:
        value = self.body.get(field) if isinstance(self.body, dict) else None
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ApiError("missing_field", f"'{field}' is required.", details={"field": field})
        return value

    def string(self, field: str, default: str = "", *, max_length: int = 20000,
               required: bool = False) -> str:
        value = self.body.get(field) if isinstance(self.body, dict) else None
        if value is None or value == "":
            if required:
                raise ApiError("missing_field", f"'{field}' is required.", details={"field": field})
            return default
        if not isinstance(value, str):
            raise ApiError("invalid_field", f"'{field}' must be text.", details={"field": field})
        if len(value) > max_length:
            raise ApiError("invalid_field",
                           f"'{field}' is longer than {max_length} characters.",
                           details={"field": field})
        return value.strip() if required else value

    def integer(self, field: str, default: Optional[int] = None, *,
                minimum: Optional[int] = None, maximum: Optional[int] = None) -> Optional[int]:
        value = self.body.get(field) if isinstance(self.body, dict) else None
        if value is None or value == "":
            return default
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ApiError("invalid_field", f"'{field}' must be a whole number.",
                           details={"field": field})
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ApiError("invalid_field", f"'{field}' must be a whole number.",
                           details={"field": field})
        if minimum is not None and number < minimum:
            raise ApiError("invalid_field", f"'{field}' must be at least {minimum}.",
                           details={"field": field})
        if maximum is not None and number > maximum:
            raise ApiError("invalid_field", f"'{field}' must be at most {maximum}.",
                           details={"field": field})
        return number

    def bool(self, field: str, default: bool = False) -> bool:
        value = self.body.get(field) if isinstance(self.body, dict) else None
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "1", "yes", "on"):
                return True
            if lowered in ("false", "0", "no", "off", ""):
                return False
        raise ApiError("invalid_field", f"'{field}' must be true or false.", details={"field": field})

    def obj(self, field: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        value = self.body.get(field) if isinstance(self.body, dict) else None
        if value is None:
            return default if default is not None else {}
        if not isinstance(value, dict):
            raise ApiError("invalid_field", f"'{field}' must be an object.", details={"field": field})
        return value

    def array(self, field: str, default: Optional[List[Any]] = None) -> List[Any]:
        value = self.body.get(field) if isinstance(self.body, dict) else None
        if value is None:
            return default if default is not None else []
        if not isinstance(value, list):
            raise ApiError("invalid_field", f"'{field}' must be a list.", details={"field": field})
        return value

    # -- query --------------------------------------------------------------
    def q(self, name: str, default: str = "") -> str:
        values = self.query.get(name)
        if not values:
            return default
        return values[0]

    def q_int(self, name: str, default: Optional[int] = None, *,
              minimum: Optional[int] = None, maximum: Optional[int] = None) -> Optional[int]:
        raw = self.q(name, "")
        if raw == "":
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ApiError("invalid_field", f"Query '{name}' must be a whole number.",
                           details={"field": name})
        if minimum is not None and value < minimum:
            raise ApiError("invalid_field", f"Query '{name}' must be at least {minimum}.",
                           details={"field": name})
        if maximum is not None and value > maximum:
            raise ApiError("invalid_field", f"Query '{name}' must be at most {maximum}.",
                           details={"field": name})
        return value

    def q_bool(self, name: str, default: bool = False) -> bool:
        raw = self.q(name, "")
        if raw == "":
            return default
        return raw.strip().lower() in ("1", "true", "yes", "on")


def _passthrough(fn):
    """Identity decorator: `@router.get(path, "name")` returns the function unchanged.

    Registering by name is convenient (it keeps the table readable and fails loudly at
    dispatch if a name is wrong), but a decorator must return something callable, so
    the function itself comes back rather than the name string.
    """
    return fn


class Router:
    """Method + path table. `{name}` captures a path segment."""

    def __init__(self):
        self._routes: List[Tuple[str, Pattern[str], str]] = []

    def add(self, method: str, path: str, handler: Any = None) -> Any:
        """Register a route, used as a decorator.

            @router.get("/api/health")
            def health(req): ...

        The *function* is recorded, and its own name is what dispatch looks up. Taking
        a name string was a mistake: it silently allowed a name that did not match the
        function (`"get_job"` against `get_job_route`), which then resolved to nothing
        — or worse, to an imported helper of the same name — and surfaced as a 501 on a
        route that looked registered.
        """
        pattern = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path) + "$")

        def register(fn):
            name = getattr(fn, "__name__", "")
            if not name:
                raise ValueError(f"cannot register {path}: the handler has no name")
            self._routes.append((method.upper(), pattern, name))
            return fn

        if handler is None:
            return register
        return register(handler)

    def get(self, path: str, handler: Any = None) -> Any:
        return self.add("GET", path, handler)

    def post(self, path: str, handler: Any = None) -> Any:
        return self.add("POST", path, handler)

    def patch(self, path: str, handler: Any = None) -> Any:
        return self.add("PATCH", path, handler)

    def delete(self, path: str, handler: Any = None) -> Any:
        return self.add("DELETE", path, handler)

    def resolve(self, method: str, path: str) -> Tuple[Optional[str], Dict[str, str], List[str]]:
        """Find a handler. Returns (handler, params, allowed_methods_for_this_path)."""
        method = method.upper()
        allowed: List[str] = []
        for route_method, pattern, handler in self._routes:
            match = pattern.match(path)
            if not match:
                continue
            allowed.append(route_method)
            if route_method == method:
                return handler, match.groupdict(), allowed
        return None, {}, allowed


def parse_body(raw: bytes, content_type: str, max_bytes: int) -> Any:
    """Parse a JSON body, refusing anything unexpected rather than coercing it."""
    if not raw:
        return {}
    if len(raw) > max_bytes:
        raise ApiError("payload_too_large",
                       f"The body is larger than {max_bytes // 1024} KB.", status=413)
    if content_type and "json" not in content_type.lower():
        raise ApiError("invalid_request",
                       f"Content-Type must be application/json, not '{content_type}'.",
                       status=415)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ApiError("invalid_request", f"The body is not valid JSON: {exc}.")
    if not isinstance(parsed, dict):
        raise ApiError("invalid_request", "The body must be a JSON object.")
    return parsed


def one_of(value: str, allowed: tuple, field: str) -> str:
    if value not in allowed:
        raise ApiError("invalid_field",
                       f"'{field}' must be one of: {', '.join(allowed)}.",
                       details={"field": field, "allowed": list(allowed)})
    return value
