"""Provider behaviour against an in-process FAKE provider (spec §20).

Spec §20 requires mock provider tests and requires them to be clearly identified as
tests. They are: every test in this file is named `fake_provider_*`, and the fake is a
local HTTP server that returns canned answers. **Nothing here is a real generation.**

This is what makes the error mapping testable without spending money or depending on a
third party's availability.
"""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import crypto, store
from app.adapters.base import Connection
from app.adapters.openai_compatible import OpenAICompatibleAdapter
from app.errors import ApiError


class FakeProvider:
    """A local HTTP server that answers as an OpenAI-compatible endpoint would."""

    def __init__(self):
        self.requests = []
        self.override = {}          # path -> (status, body)
        provider = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _reply(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                provider.requests.append(("GET", self.path, {}))
                if self.path in provider.override:
                    status, payload = provider.override[self.path]
                    return self._reply(status, payload)
                if self.path.endswith("/models"):
                    return self._reply(200, {"object": "list", "data": [
                        {"id": "fake-image-1", "object": "model"},
                        {"id": "fake-video-t2v", "object": "model"},
                        {"id": "fake-edit-1", "object": "model"},
                        {"id": "fake-chat-1", "object": "model"},
                    ]})
                if "/videos/generations/" in self.path:
                    return self._reply(200, {"status": "succeeded",
                                             "video_url": "http://example.invalid/v.mp4"})
                return self._reply(404, {"error": {"message": "not found"}})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    parsed = json.loads(raw or b"{}")
                except ValueError:
                    parsed = {}
                provider.requests.append(("POST", self.path, parsed,
                                          self.headers.get("Authorization")))
                if self.path in provider.override:
                    status, payload = provider.override[self.path]
                    return self._reply(status, payload)
                if self.path.endswith("/images/generations"):
                    return self._reply(200, {"data": [
                        {"url": "http://example.invalid/one.png"}]})
                if self.path.endswith("/videos/generations"):
                    return self._reply(200, {"id": "fake-job-1", "status": "queued"})
                if self.path.endswith("/chat/completions"):
                    return self._reply(200, {"choices": [
                        {"message": {"content": "a concrete prompt suggestion"}}]})
                return self._reply(404, {"error": {"message": "not found"}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v1"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def fake_provider(monkeypatch):
    """A fake provider plus the private-upstream escape hatch it needs to be reachable."""
    from app import security_url

    monkeypatch.setattr(security_url, "ALLOW_PRIVATE_UPSTREAM", True)
    monkeypatch.setattr("app.services.connections.IS_PRODUCTION", False)
    provider = FakeProvider()
    yield provider
    provider.close()


def _connection(provider):
    return Connection(base_url=provider.base_url, secret="sk-fake-123456789",
                      provider="openai-compatible")


class TestFakeProviderDiscovery:
    def test_fake_provider_models_are_listed(self, fake_provider):
        adapter = OpenAICompatibleAdapter()
        models = adapter.list_models(_connection(fake_provider))
        ids = {m.model_id for m in models}
        assert {"fake-image-1", "fake-video-t2v", "fake-edit-1"} <= ids

    def test_fake_provider_video_model_gets_video_capabilities(self, fake_provider):
        adapter = OpenAICompatibleAdapter()
        models = {m.model_id: m for m in adapter.list_models(_connection(fake_provider))}
        assert "video.generate" in models["fake-video-t2v"].capabilities
        assert "video.async" in models["fake-video-t2v"].capabilities

    def test_fake_provider_edit_model_gets_the_edit_capability(self, fake_provider):
        adapter = OpenAICompatibleAdapter()
        models = {m.model_id: m for m in adapter.list_models(_connection(fake_provider))}
        assert "image.edit" in models["fake-edit-1"].capabilities

    def test_fake_provider_unknown_model_gets_only_the_base_capability(self, fake_provider):
        adapter = OpenAICompatibleAdapter()
        caps = adapter.capabilities(_connection(fake_provider), "not-listed-anywhere")
        assert caps == ["image.generate"]


class TestFakeProviderGeneration:
    def test_fake_provider_image_generation_reads_the_url(self, fake_provider):
        adapter = OpenAICompatibleAdapter()
        result = adapter.generate(_connection(fake_provider), "fake-image-1",
                                  "a test prompt", {"count": 1})
        assert result.outputs
        assert result.outputs[0]["remote_url"].endswith("one.png")

    def test_fake_provider_receives_the_bearer_token(self, fake_provider):
        adapter = OpenAICompatibleAdapter()
        adapter.generate(_connection(fake_provider), "fake-image-1", "x", {})
        auth = [r[3] for r in fake_provider.requests if r[0] == "POST"]
        assert auth and auth[-1] == "Bearer sk-fake-123456789"

    def test_fake_provider_video_generation_returns_a_job_id(self, fake_provider):
        adapter = OpenAICompatibleAdapter()
        result = adapter.generate(_connection(fake_provider), "fake-video-t2v",
                                  "a shot", {"duration": 4})
        assert result.provider_job_id == "fake-job-1"
        assert result.pending is True

    def test_fake_provider_poll_reports_the_output(self, fake_provider):
        adapter = OpenAICompatibleAdapter()
        result = adapter.poll(_connection(fake_provider), "fake-job-1")
        assert result.outputs
        assert result.pending is False

    def test_fake_provider_chat_returns_the_message(self, fake_provider):
        adapter = OpenAICompatibleAdapter()
        answer = adapter.chat(_connection(fake_provider), "fake-chat-1",
                              [{"role": "user", "content": "hi"}])
        assert answer == "a concrete prompt suggestion"

    def test_fake_provider_unsupported_param_is_refused_before_sending(self, fake_provider):
        """The request is refused locally: nothing is POSTed to the provider."""
        adapter = OpenAICompatibleAdapter()
        # The capabilities lookup reads /models, so count only the POSTs.
        posts_before = len([r for r in fake_provider.requests if r[0] == "POST"])
        with pytest.raises(ApiError) as exc:
            adapter.generate(_connection(fake_provider), "fake-image-1", "x",
                             {"warp_speed": 9})
        assert exc.value.code == "unsupported_parameter"
        posts_after = len([r for r in fake_provider.requests if r[0] == "POST"])
        assert posts_after == posts_before, "nothing should have been sent"


class TestFakeProviderErrors:
    """The provider's failure modes, mapped onto our vocabulary (spec §19)."""

    @pytest.mark.parametrize("status,code", [
        (401, "invalid_api_key"),
        (403, "invalid_api_key"),
        (402, "insufficient_balance"),
        (404, "invalid_model_id"),
        (429, "provider_rate_limited"),
        (500, "provider_outage"),
        (502, "provider_outage"),
        (503, "provider_outage"),
    ])
    def test_fake_provider_status_maps_to_code(self, fake_provider, status, code):
        adapter = OpenAICompatibleAdapter()
        fake_provider.override["/v1/models"] = (status, {"error": {"message": "nope"}})
        with pytest.raises(ApiError) as exc:
            adapter.list_models(_connection(fake_provider))
        assert exc.value.code == code, (status, exc.value.code)

    def test_fake_provider_malformed_json_is_reported(self, fake_provider):
        adapter = OpenAICompatibleAdapter()
        fake_provider.override["/v1/images/generations"] = (200, "not-a-dict-at-all")
        with pytest.raises(ApiError) as exc:
            adapter.generate(_connection(fake_provider), "fake-image-1", "x", {})
        assert exc.value.code == "invalid_provider_response"

    def test_fake_provider_empty_reply_is_reported(self, fake_provider):
        adapter = OpenAICompatibleAdapter()
        fake_provider.override["/v1/images/generations"] = (200, {"data": []})
        with pytest.raises(ApiError) as exc:
            adapter.generate(_connection(fake_provider), "fake-image-1", "x", {})
        assert exc.value.code == "invalid_provider_response"

    def test_fake_provider_a_credential_is_not_echoed_in_the_error(self):
        """A provider error that mentions a key is not repeated to the caller."""
        from app.errors import classify_upstream

        error = classify_upstream(401, "Invalid key sk-live-abcdef123456")
        assert "sk-live-abcdef123456" not in json.dumps(error.to_dict())

    def test_fake_provider_unreachable_host_is_connection_failed(self):
        adapter = OpenAICompatibleAdapter()
        dead = Connection(base_url="http://127.0.0.1:9/v1", secret="sk-x-000000000")
        with pytest.raises(ApiError) as exc:
            adapter.list_models(dead)
        assert exc.value.code in ("connection_failed", "provider_timeout")


class TestThroughTheEnvironment:
    """The same fake provider, reached the way the app now reaches one.

    Under BYOK these tests created a connection and pressed "test". There is no such
    button and no stored key: a model is reached because the environment names its key,
    model id and URL. The point of the test is unchanged — a real request goes out and a
    bad key is reported as `invalid_api_key`, not swallowed.
    """

    def test_a_configured_model_reaches_the_provider(self, client, fake_provider, monkeypatch):
        from app.services import connections as connection_service
        from app.services import sync

        monkeypatch.setenv("GPT_IMAGE_2_5_API_KEY", "test-key")
        monkeypatch.setenv("GPT_IMAGE_2_5_API_URL", fake_provider.base_url)
        monkeypatch.delenv("GPT_IMAGE_2_5_MODEL", raising=False)
        monkeypatch.setenv("ALLOW_PRIVATE_UPSTREAM", "1")
        sync.sync_from_env()

        model = connection_service.get_model_by_env_prefix("GPT_IMAGE_2_5")
        adapter, conn, provider = connection_service.adapter_and_connection(model)
        assert conn.base_url == fake_provider.base_url
        assert conn.secret == "test-key"

        # A real round trip: the adapter asks the provider what it has.
        listed = adapter.list_models(conn)
        assert len(listed) >= 3
        # And the provider row is a real endpoint, not a placeholder.
        assert provider["base_url"] == fake_provider.base_url

    def test_a_bad_key_is_reported_as_an_auth_failure(self, client, fake_provider,
                                                      monkeypatch):
        from app.errors import ApiError
        from app.services import connections as connection_service
        from app.services import sync

        monkeypatch.setenv("NANO_BANANA_2_API_KEY", "bad-key")
        monkeypatch.setenv("NANO_BANANA_2_API_URL", fake_provider.base_url)
        monkeypatch.delenv("NANO_BANANA_2_MODEL", raising=False)
        monkeypatch.setenv("ALLOW_PRIVATE_UPSTREAM", "1")
        # The adapter appends "/models" to the base URL, so with a base of .../v1 the
        # request path is /v1/models. Override on that exact path.
        fake_provider.override["/v1/models"] = (401, {"error": {"message": "bad key"}})
        sync.sync_from_env()

        model = connection_service.get_model_by_env_prefix("NANO_BANANA_2")
        adapter, conn, _provider = connection_service.adapter_and_connection(model)
        # Assert the credential is actually attached before asking: without it the
        # request would be an unauthenticated call, and the 401 would prove nothing.
        assert conn.secret == "bad-key", "the key was not read from the environment"
        with pytest.raises(ApiError) as exc:
            adapter.list_models(conn)
        assert exc.value.code == "invalid_api_key", exc.value.code

    def test_a_model_without_a_key_is_not_reachable(self, client, monkeypatch):
        """No env, no model: resolving it must fail rather than call out with nothing."""
        from app.errors import ApiError
        from app.services import connections as connection_service
        from app.services import sync

        monkeypatch.delenv("SOUL_API_KEY", raising=False)
        monkeypatch.delenv("SOUL_API_URL", raising=False)
        sync.sync_from_env()
        # The row is disabled by the sync, so it is not in the enabled list at all.
        enabled = [m["model_id"] for m in connection_service.list_models()]
        assert "text2image_soul_v2" not in enabled
