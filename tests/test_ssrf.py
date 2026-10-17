"""SSRF protection (spec §16, §20 "SSRF protections", §25).

A BYOK platform accepts an arbitrary base URL and then has the server call it. These
tests are the guard's contract.
"""

import ipaddress

import pytest

from app import security_url
from app.errors import ApiError
from app.security_url import check_url, resolve_pinned_ip


class TestShape:
    @pytest.mark.parametrize("url", [
        "https://api.example.com/v1",
        "http://api.example.com/v1",
        "https://api.example.com",                    # no path
        "https://api.example.com/v1/",                # trailing slash normalised
        "https://api.example.com:8443/v1",
    ])
    def test_accepted(self, url):
        assert check_url(url).startswith(("http://", "https://"))

    def test_a_trailing_slash_is_stripped(self):
        assert check_url("https://api.example.com/v1/") == "https://api.example.com/v1"

    @pytest.mark.parametrize("url", [
        "",                          # empty
        "notaurl",                   # no scheme
        "ftp://api.example.com/v1",  # wrong scheme
        "file:///etc/passwd",        # local file
        "javascript:alert(1)",
        "https://user:pw@api.example.com/v1",   # embedded credentials
        "https://",                  # no host
        "://api.example.com",
    ])
    def test_refused_on_shape(self, url):
        with pytest.raises(ApiError) as exc:
            check_url(url)
        assert exc.value.code in ("invalid_base_url", "blocked_url")

    def test_a_very_long_url_is_refused(self):
        with pytest.raises(ApiError):
            check_url("https://api.example.com/" + "a" * 3000)


class TestInternalAddressesAreBlocked:
    """Every one of these is a way to reach something the server can see but we cannot."""

    INTERNAL = [
        "http://127.0.0.1:8080/v1",           # loopback
        "http://127.0.0.1/v1",
        "http://localhost:8080/v1",           # loopback by name
        "http://localhost.localdomain/v1",
        "http://[::1]:8080/v1",               # IPv6 loopback
        "http://0.0.0.0:8080/v1",             # unspecified
        "http://10.0.0.5/v1",                 # RFC1918
        "http://172.16.0.1/v1",
        "http://192.168.1.1/v1",
        "http://169.254.169.254/latest/meta-data/",   # cloud metadata
        "http://metadata.google.internal/v1",
        "http://100.64.0.1/v1",               # carrier-grade NAT
        "http://[::ffff:127.0.0.1]/v1",       # IPv4-mapped IPv6 loopback
        "http://[fd00::1]/v1",                # IPv6 unique local
    ]

    @pytest.mark.parametrize("url", INTERNAL)
    def test_blocked_when_the_guard_is_on(self, url, monkeypatch):
        monkeypatch.setattr(security_url, "ALLOW_PRIVATE_UPSTREAM", False)
        with pytest.raises(ApiError) as exc:
            check_url(url)
        assert exc.value.code in ("blocked_url", "invalid_base_url"), url

    def test_the_metadata_address_is_blocked_by_resolution_too(self, monkeypatch):
        monkeypatch.setattr(security_url, "ALLOW_PRIVATE_UPSTREAM", False)
        # A hostname is resolved before connecting; a private result is refused.
        with pytest.raises(ApiError):
            resolve_pinned_ip("http://169.254.169.254/")

    def test_private_is_allowed_when_explicitly_enabled(self, monkeypatch):
        """The escape hatch exists for a local model server, and is opt-in."""
        monkeypatch.setattr(security_url, "ALLOW_PRIVATE_UPSTREAM", True)
        assert check_url("http://127.0.0.1:8080/v1")


class TestResolutionIsPinned:
    def test_a_literal_public_ip_resolves_to_itself(self):
        assert resolve_pinned_ip("https://1.1.1.1/v1") == "1.1.1.1"

    def test_a_hostname_resolves_to_an_address(self):
        # Uses the real resolver; example.com must exist for this to be meaningful.
        try:
            address = resolve_pinned_ip("https://example.com/v1")
        except ApiError:
            pytest.skip("no network")
        assert ipaddress.ip_address(address)

    def test_a_host_resolving_to_a_private_address_is_refused(self, monkeypatch):
        """The DNS-rebinding case: the name looks fine, the answer does not."""
        monkeypatch.setattr(security_url, "ALLOW_PRIVATE_UPSTREAM", False)

        fake = [(2, 1, 6, "", ("127.0.0.1", 80))]
        monkeypatch.setattr(security_url.socket, "getaddrinfo", lambda *a, **k: fake)
        with pytest.raises(ApiError) as exc:
            resolve_pinned_ip("https://looks-public.example.com/v1")
        assert exc.value.code == "blocked_url"

    def test_a_name_with_one_private_answer_is_refused_entirely(self, monkeypatch):
        """Not "pick another": a partly-private name is not safe to accept."""
        monkeypatch.setattr(security_url, "ALLOW_PRIVATE_UPSTREAM", False)

        mixed = [(2, 1, 6, "", ("93.184.216.34", 80)),
                 (2, 1, 6, "", ("10.0.0.7", 80))]
        monkeypatch.setattr(security_url.socket, "getaddrinfo", lambda *a, **k: mixed)
        with pytest.raises(ApiError) as exc:
            resolve_pinned_ip("https://mixed.example.com/v1")
        assert exc.value.code == "blocked_url"


class TestThroughTheApi:
    @pytest.mark.parametrize("base_url", [
        "http://127.0.0.1:9000/v1",
        "http://169.254.169.254/latest",
        "http://10.1.2.3/v1",
        "file:///etc/passwd",
    ])
    def test_creating_a_connection_refuses_it(self, client, base_url):
        code, body = client.post("/api/connections", {
            "name": "nope", "kind": "openai-compatible",
            "base_url": base_url, "api_key": "sk-test-123456789"})
        assert code == 400, body
        assert body["error"]["code"] in ("blocked_url", "invalid_base_url")
