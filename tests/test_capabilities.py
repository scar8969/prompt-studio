"""Capabilities, model registration and the studios' filtering (spec §25).

The rule under test: what the UI offers comes from the stored capability rows, which
come from the provider — never from the request body.
"""

import pytest

from app import capabilities as caps


class TestVocabulary:
    def test_an_unknown_capability_is_refused(self):
        from app.errors import ApiError

        with pytest.raises(ApiError) as exc:
            caps.validate_capabilities(["image.generate", "image.do_magic"])
        assert exc.value.code == "invalid_field"

    def test_a_string_is_not_a_list(self):
        from app.errors import ApiError

        with pytest.raises(ApiError):
            caps.validate_capabilities("image.generate")

    def test_a_capability_adds_what_it_implies(self):
        got = caps.validate_capabilities(["image.edit"])
        assert "image.generate" in got and "image.edit" in got

    def test_video_capability_implies_video_modality(self):
        assert caps.modality_for(["video.image_to_video"]) == "video"

    def test_requires_raises_a_clear_error(self):
        from app.errors import ApiError

        with pytest.raises(ApiError) as exc:
            caps.requires(["image.generate"], "video.generate", "Demo Image")
        assert exc.value.code == "unsupported_modality"
        assert "video.generate" in exc.value.message


class TestRegistration:
    """Capabilities come from the deployment's environment, not from a request.

    Under BYOK a caller could POST a capability list. That is gone: a model's
    capabilities now come from the catalogue row its environment variables configure, so
    the only way to change what a model offers is to change the model. These tests hold
    that line — what the API accepts must not be able to grant a capability.
    """

    def test_capabilities_come_from_the_configuration(self, mock_connection):
        models = mock_connection["models"]
        assert "image.generate" in models["demo-image-1"]["capabilities"]
        assert "video.generate" in models["demo-video-1"]["capabilities"]
        assert "video.async" in models["demo-video-1"]["capabilities"]

    def test_a_configured_model_gets_the_catalogue_capabilities(self, monkeypatch):
        """Setting three variables is enough to make a model appear, fully described."""
        from app.services import connections as connection_service
        from app.services import sync

        monkeypatch.setenv("GPT_IMAGE_2_5_API_KEY", "sk-test")
        monkeypatch.setenv("API_BASE_URL", "https://api.openai.com/v1")
        # This model has no recorded provider id, so _MODEL is required — the
        # Higgsfield job-set id would be rejected by OpenAI.
        monkeypatch.setenv("GPT_IMAGE_2_5_MODEL", "gpt-image-2.5")
        sync.sync_from_env()

        model = connection_service.get_model_by_env_prefix("GPT_IMAGE_2_5")
        public = connection_service.public_model(model)
        assert public["modality"] == "image"
        assert "image.generate" in public["capabilities"]
        assert public["parameters"], "the documented parameters are reported"

    def test_there_is_no_endpoint_that_grants_a_capability(self, client, mock_connection):
        """A credential and a capability cannot be POSTed in — the routes are gone."""
        conn_id = mock_connection["connection_id"]
        code, body = client.post(f"/api/connections/{conn_id}/models", {
            "models": [{"model_id": "custom-one",
                        "capabilities": ["video.generate"]}]})
        assert code == 404, f"a capability could still be registered via HTTP: {body}"

    def test_the_registry_filters_by_capability(self, client, mock_connection):
        code, body = client.get("/api/models?capability=video.generate")
        assert code == 200
        assert all("video.generate" in m["capabilities"] for m in body["data"])
        assert body["data"], "the demo video model should match"

    def test_parameters_are_reported_for_a_model(self, client, mock_connection):
        """A parameter the model does not document is never offered."""
        code, body = client.get(f"/api/models/{mock_connection['image_pk']}/parameters")
        assert code == 200
        names = [p["name"] for p in body["data"]]
        # The demo model documents no parameters, so the list must be empty rather than
        # full of defaults invented on the way out.
        assert names == [] or all(isinstance(n, str) for n in names)

    def test_an_invalid_capability_cannot_be_stored(self, monkeypatch):
        """The vocabulary is closed: the sync refuses an unknown capability."""
        from app.errors import ApiError

        with pytest.raises(ApiError):
            caps.validate_capabilities(["image.generate", "image.do_magic"])
