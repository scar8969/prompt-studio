"""The outbound HTTP client, with the SSRF pin honoured (spec §16, §21).

Built on urllib with an IP-pinned connection: the caller passes the address that was
validated and this client connects to *that address* while still presenting the real
hostname via SNI and the Host header. Without this, validating a hostname and then
connecting to it again is a time-of-check/time-of-use hole.

Also here: no redirects (a 302 to 169.254.169.254 would defeat the whole check), a
response size cap, and a timeout on every call.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .config import UPSTREAM_MAX_BYTES, UPSTREAM_TIMEOUT


@dataclass
class UpstreamResponse:
    status: int
    body: bytes
    headers: Dict[str, str]

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def text(self, limit: int = 4000) -> str:
        try:
            return self.body.decode("utf-8", "replace")[:limit]
        except Exception:
            return ""

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            return None


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse redirects outright: a redirect can point at an internal address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to a fixed IP, with SNI/Host carrying the real hostname."""

    def __init__(self, sni_host: str, ip: str, port: int, timeout: float):
        super().__init__(sni_host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip
        self._sni_host = sni_host

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self._sni_host)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Plain HTTP to a fixed IP, with the correct Host header."""

    def __init__(self, host: str, ip: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class UpstreamClient:
    def __init__(self, timeout: float = UPSTREAM_TIMEOUT, max_bytes: int = UPSTREAM_MAX_BYTES):
        self.timeout = timeout
        self.max_bytes = max_bytes

    # -- the two verbs this app needs ---------------------------------------
    def request(self, method: str, url: str, headers: Optional[Dict[str, str]] = None,
                body: Optional[bytes] = None, pinned_ip: str = "") -> UpstreamResponse:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query

        sent = {"Host": host, "Accept": "application/json",
                "User-Agent": "prompt-studio/1.0"}
        for key, value in (headers or {}).items():
            sent[key] = value

        if pinned_ip:
            conn = (_PinnedHTTPSConnection(host, pinned_ip, port, self.timeout)
                    if parts.scheme == "https"
                    else _PinnedHTTPConnection(host, pinned_ip, port, self.timeout))
            try:
                conn.request(method, path, body=body, headers=sent)
                raw = conn.getresponse()
                payload = raw.read(self.max_bytes)
                return UpstreamResponse(raw.status, payload,
                                        {k.lower(): v for k, v in raw.getheaders()})
            except socket.timeout:
                from .errors import ApiError
                raise ApiError("provider_timeout", status=504)
            except (ssl.SSLError, OSError) as exc:
                from .errors import ApiError
                raise ApiError("connection_failed", f"Could not reach the provider: {exc}", status=502)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        # No pin: a plain request. Redirects are still refused, so a hop cannot
        # move the connection somewhere that was never validated.
        opener = urllib.request.build_opener(_NoRedirects())
        req = urllib.request.Request(url, data=body, headers=sent, method=method)
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                return UpstreamResponse(resp.status, resp.read(self.max_bytes),
                                        {k.lower(): v for k, v in resp.getheaders()})
        except urllib.error.HTTPError as exc:
            return UpstreamResponse(exc.code, exc.read(self.max_bytes),
                                    {k.lower(): v for k, v in (exc.headers or {}).items()})
        except urllib.error.URLError as exc:
            from .errors import ApiError
            if isinstance(exc.reason, socket.timeout):
                raise ApiError("provider_timeout", status=504)
            raise ApiError("connection_failed", f"Could not reach the provider: {exc.reason}", status=502)

    def get_json(self, url: str, headers: Optional[Dict[str, str]] = None,
                 pinned_ip: str = "") -> UpstreamResponse:
        return self.request("GET", url, headers, None, pinned_ip)

    def post_json(self, url: str, payload: Any, headers: Optional[Dict[str, str]] = None,
                  pinned_ip: str = "") -> UpstreamResponse:
        sent = {"Content-Type": "application/json"}
        sent.update(headers or {})
        return self.request("POST", url, sent, json.dumps(payload).encode("utf-8"), pinned_ip)


def bearer(secret: str) -> Dict[str, str]:
    """The auth header an OpenAI-compatible endpoint expects."""
    return {"Authorization": f"Bearer {secret}"} if secret else {}


def _self_test() -> None:
    # The no-redirect handler must be part of the opener that plain requests use.
    import inspect
    src = inspect.getsource(UpstreamClient.request)
    assert "_NoRedirects" in src, "redirects must be refused on the unpinned path"
    assert "server_hostname" in inspect.getsource(_PinnedHTTPSConnection.connect), \
        "pinned TLS must still send SNI for the real host"


if __name__ == "__main__":  # pragma: no cover
    _self_test()
    print("http client self-test OK")
