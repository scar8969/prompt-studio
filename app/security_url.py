"""URL safety for user-supplied base URLs (spec §16 SSRF, §21).

A BYOK platform asks users for an arbitrary base URL and then has the *server* call
it. That is an SSRF primitive unless it is constrained, so this module does the
checking in two halves:

* `check_url` — a syntactic check, before anything is saved.
* `resolve_pinned_ip` — a DNS resolution whose result the caller then *pins*, so the
  address that was validated is the address connected to.

The second half matters: checking a hostname and then connecting to the hostname
again is a time-of-check/time-of-use hole — an attacker-controlled DNS record can
answer with a public IP for the check and 127.0.0.1 for the connection. Callers get
an IP back and must use it (see `pinned_connection` in the http client).
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Tuple
from urllib.parse import urlsplit

from .config import ALLOW_PRIVATE_UPSTREAM
from .errors import ApiError

ALLOWED_SCHEMES = ("http", "https")

# Hostnames that name the local machine or the cloud metadata service. Blocking the
# 169.254.0.0/16 range covers the metadata endpoint; these are belt and braces for
# names that resolve there.
BLOCKED_NAMES = {
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
    "metadata", "metadata.google.internal", "metadata.goog",
}

BLOCKED_PORTS = {0}


def _addr_is_blocked(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
        # IPv4-mapped IPv6 (::ffff:127.0.0.1) hides a loopback behind an v6 address.
        or (getattr(ip, "ipv4_mapped", None) is not None and _addr_is_blocked(ip.ipv4_mapped))
        # 100.64.0.0/10 carrier-grade NAT, and the AWS/GCP metadata address.
        or ip in ipaddress.ip_network("100.64.0.0/10")
        or str(ip) == "169.254.169.254"
    )


def check_url(url: str) -> str:
    """Validate the shape of a base URL. Returns the normalised form.

    Raises `ApiError('invalid_base_url')` or `ApiError('blocked_url')`.
    """
    if not isinstance(url, str) or not url.strip():
        raise ApiError("invalid_base_url", "A base URL is required.")
    raw = url.strip()
    if len(raw) > 2048:
        raise ApiError("invalid_base_url", "That URL is unreasonably long.")
    parts = urlsplit(raw)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise ApiError("invalid_base_url",
                       f"Only {' and '.join(ALLOWED_SCHEMES)} URLs are supported, not '{parts.scheme or '(none)'}'.")
    if not parts.hostname:
        raise ApiError("invalid_base_url", "That URL has no host.")
    if parts.username or parts.password:
        raise ApiError("invalid_base_url", "Credentials must not be embedded in the URL.")
    try:
        port = parts.port
    except ValueError:
        raise ApiError("invalid_base_url", "That URL has an invalid port.")
    if port in BLOCKED_PORTS:
        raise ApiError("invalid_base_url", "That URL has an invalid port.")

    host = parts.hostname.lower()
    if not ALLOW_PRIVATE_UPSTREAM and host in BLOCKED_NAMES:
        raise ApiError("blocked_url", f"'{host}' resolves to this machine, which is not callable from here.")

    # A literal IP can be judged immediately, without a resolver.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not ALLOW_PRIVATE_UPSTREAM and _addr_is_blocked(literal):
            raise ApiError("blocked_url",
                           f"'{host}' is a private, loopback or link-local address.")

    # Strip the query and fragment: a base URL is a prefix, not a request.
    cleaned = f"{parts.scheme.lower()}://{parts.netloc}{parts.path.rstrip('/')}"
    return cleaned


def resolve_pinned_ip(url: str) -> str:
    """Resolve a validated URL to one checked, public IP address.

    Returns the IP the caller must connect to. Raises `blocked_url` if every address
    the name resolves to is refused — a host with both a public and a private record
    is refused outright rather than raced.
    """
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        raise ApiError("invalid_base_url", "That URL has no host.")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        if not ALLOW_PRIVATE_UPSTREAM and _addr_is_blocked(literal):
            raise ApiError("blocked_url", f"'{host}' is a private, loopback or link-local address.")
        return str(literal)

    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ApiError("connection_failed", f"Could not resolve '{host}': {exc.strerror or 'no address'}.",
                       status=502)

    addresses = []
    for info in infos:
        addr = info[4][0]
        try:
            parsed = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if not ALLOW_PRIVATE_UPSTREAM and _addr_is_blocked(parsed):
            # Refuse the host entirely: if the name is partly private, accepting
            # "some other" address from the same name is not safe.
            raise ApiError("blocked_url",
                           f"'{host}' resolves to {addr}, which is a private, loopback or "
                           "link-local address.")
        addresses.append(str(parsed))

    if not addresses:
        raise ApiError("connection_failed", f"'{host}' did not resolve to a usable address.", status=502)
    return addresses[0]


def _fake_resolve_and_check() -> str:
    """Shape contract for the app's own tests (no network)."""
    return check_url("https://api.example.com/v1")


def _self_test() -> None:
    # Accepted
    assert check_url("https://api.example.com/v1") == "https://api.example.com/v1"
    assert check_url("http://api.example.com/v1/") == "http://api.example.com/v1"
    # Refused on shape
    for bad in ("", "notaurl", "ftp://x/v1", "file:///etc/passwd",
                "https://user:pw@api.example.com/v1", "https://"):
        try:
            check_url(bad)
        except ApiError:
            continue
        raise AssertionError(f"should have refused: {bad!r}")
    # Refused as internal (only when the private-upstream escape hatch is off)
    if not ALLOW_PRIVATE_UPSTREAM:
        for bad in ("http://127.0.0.1:8080/v1", "http://localhost:8080/v1",
                    "http://169.254.169.254/latest/meta-data", "http://[::1]:8080/v1",
                    "http://10.0.0.5/v1", "http://192.168.1.1/v1", "http://100.64.0.1/v1"):
            try:
                check_url(bad)
            except ApiError:
                continue
            raise AssertionError(f"should have blocked: {bad!r}")


if __name__ == "__main__":  # pragma: no cover - manual probe
    _self_test()
    print("ssrf self-test OK (ALLOW_PRIVATE_UPSTREAM=%s)" % ALLOW_PRIVATE_UPSTREAM)
