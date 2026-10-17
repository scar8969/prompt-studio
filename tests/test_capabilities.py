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
    def test_capabilities_come_from_the_provider(self, client, mock_connection):
        models = mock_connection["models"]
        assert "image.generate" in models["demo-image-1"]["capabilities"]
        assert "video.generate" in models["demo-video-1"]["capabilities"]
        assert "video.async" in models["demo-video-1"]["capabilities"]

    def test_an_explicit_capability_list_is_stored(self, client, mock_connection):
        conn_id = mock_connection["connection_id"]
        code, body = client.post(f"/api/connections/{conn_id}/models", {
            "models": [{"model_id": "custom-one",
                        "capabilities": ["video.generate"]}]})
        assert code == 201, body
        stored = body["data"][0]
        assert stored["modality"] == "video"
        assert "video.generate" in stored["capabilities"]

    def test_an_invalid_capability_in_a_registration_is_refused(self, client, mock_connection):
        conn_id = mock_connection["connection_id"]
        code, body = client.post(f"/api/connections/{conn_id}/models", {
            "models": [{"model_id": "bad", "capabilities": ["teleport"]}]})
        assert code == 400
        assert body["error"]["code"] == "invalid_field"

    def test_registry_filters_by_modality(self, client, mock_connection):
        code, body = client.get("/api/models?modality=image")
        assert all(m["modality"] == "image" for m in body["data"])
        code, body = client.get("/api/models?modality=video")
        assert all(m["modality"] == "video" for m in body["data"])

    def test_registry_filters_by_capability(self, client, mock_connection):
        code, body = client.get("/api/models?capability=video.async")
        assert [m["model_id"] for m in body["data"]].count("demo-image-1") == 0
        assert any(m["model_id"] == "demo-video-1" for m in body["data"])

    def test_an_unknown_capability_filter_is_refused(self, client):
        code, body = client.get("/api/models?capability=make_coffee")
        assert code == 400 and body["error"]["code"] == "invalid_field"

    def test_parameters_are_reported_for_a_model(self, client, mock_connection):
        code, body = client.get(f"/api/models/{mock_connection['image_pk']}/parameters")
        assert code == 200
        names = {p["name"] for p in body["data"]}
        assert "aspect_ratio" in names

    def test_capabilities_endpoint_also_describes_them(self, client, mock_connection):
        code, body = client.get(f"/api/models/{mock_connection['image_pk']}/capabilities")
        assert code == 200
        assert body["data"]["capabilities"]
        for name in body["data"]["capabilities"]:
            assert body["data"]["described"][name]


class TestGating:
    def test_an_image_model_cannot_be_asked_for_video(self, client, mock_connection):
        code, body = client.post("/api/generations/videos",
                                 {"model_pk": mock_connection["image_pk"], "prompt": "x"})
        assert code == 400
        assert body["error"]["code"] == "unsupported_modality"

    def test_a_video_model_cannot_be_asked_for_an_image(self, client, mock_connection):
        code, body = client.post("/api/generations/images",
                                 {"model_pk": mock_connection["video_pk"], "prompt": "x"})
        assert code == 400
        assert body["error"]["code"] == "unsupported_modality"

    def test_an_unsupported_parameter_is_named(self, client, mock_connection):
        code, body = client.post("/api/generations/images", {
            "model_pk": mock_connection["image_pk"], "prompt": "x",
            "params": {"go_faster": True}})
        assert code == 400
        assert body["error"]["code"] == "unsupported_parameter"
        assert "go_faster" in body["error"]["details"]["unsupported"]

    def test_a_disabled_model_is_refused(self, client, mock_connection):
        pk = mock_connection["image_pk"]
        code, body = client.patch(f"/api/models/{pk}", {"enabled": False})
        assert code == 200 and body["data"]["enabled"] is False
        code, body = client.post("/api/generations/images", {"model_pk": pk, "prompt": "x"})
        assert code == 400
        client.patch(f"/api/models/{pk}", {"enabled": True})

    def test_a_disabled_model_disappears_from_the_registry(self, client, mock_connection):
        pk = mock_connection["image_pk"]
        client.patch(f"/api/models/{pk}", {"enabled": False})
        code, body = client.get("/api/models?modality=image")
        assert pk not in [m["id"] for m in body["data"]]
        code, body = client.get("/api/models?include_disabled=true")
        assert pk in [m["id"] for m in body["data"]]
        client.patch(f"/api/models/{pk}", {"enabled": True})
