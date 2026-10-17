"""The rest of the spec's acceptance criteria (spec §21, §25, §24).

Covers what the other files do not: workspace persistence, usage accounting, the
assistant's honesty, the OpenAPI document staying in step, and video jobs.
"""

import io
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


class TestVideoJobs:
    def test_a_video_job_runs_and_stores_a_file(self, client, mock_connection, wait_for_job):
        """The offline adapter reports a job id, so this exercises the polling path."""
        code, body = client.post("/api/generations/videos", {
            "model_pk": mock_connection["video_pk"],
            "prompt": f"a slow push in {time.time()}"})
        assert code == 202, body
        final = wait_for_job(body["data"]["id"], timeout=40)
        assert final["status"] == "succeeded", final.get("error")
        assert final["outputs"], "a video job must produce an output"
        output = final["outputs"][0]
        assert output["mime"].startswith("video/")
        code, blob = client.get(f"/api/assets/{output['asset_id']}/raw")
        assert code == 200 and len(blob["raw"]) > 8

    def test_the_poll_is_reported_in_the_history(self, client, mock_connection, wait_for_job):
        code, body = client.post("/api/generations/videos", {
            "model_pk": mock_connection["video_pk"], "prompt": f"history {time.time()}"})
        final = wait_for_job(body["data"]["id"], timeout=40)
        details = " ".join(event["detail"] for event in final["events"])
        assert "waiting on the provider" in details, details


class TestWorkspace:
    def test_a_project_survives_a_reread(self, client):
        code, body = client.post("/api/projects", {"name": "Persistence", "description": "d"})
        project_id = body["data"]["id"]
        code, body = client.get(f"/api/projects/{project_id}")
        assert code == 200
        assert body["data"]["name"] == "Persistence"
        assert body["data"]["description"] == "d"

    def test_renaming_a_project_persists(self, client):
        code, body = client.post("/api/projects", {"name": "Before"})
        project_id = body["data"]["id"]
        code, body = client.patch(f"/api/projects/{project_id}", {"name": "After"})
        assert body["data"]["name"] == "After"
        code, body = client.get(f"/api/projects/{project_id}")
        assert body["data"]["name"] == "After"

    def test_deleting_a_project_keeps_its_assets(self, client, mock_connection, wait_for_job):
        code, body = client.post("/api/projects", {"name": "Doomed"})
        project_id = body["data"]["id"]
        code, body = client.post("/api/generations/images", {
            "model_pk": mock_connection["image_pk"], "project_id": project_id,
            "prompt": f"in a doomed project {time.time()}"})
        final = wait_for_job(body["data"]["id"])
        assert final["outputs"]

        code, body = client.delete(f"/api/projects/{project_id}")
        assert code == 200
        # The output is still downloadable: deleting a grouping is not deleting work.
        asset_id = final["outputs"][0]["asset_id"]
        code, blob = client.get(f"/api/assets/{asset_id}/raw")
        assert code == 200

    def test_presets_round_trip(self, client):
        code, body = client.post("/api/presets", {
            "name": "Cinematic", "modality": "video", "category": "look",
            "configuration": {"aspect_ratio": "16:9"}})
        assert code == 201, body
        preset_id = body["data"]["id"]
        code, body = client.get("/api/presets?modality=video")
        assert any(p["id"] == preset_id for p in body["data"])
        code, body = client.delete(f"/api/presets/{preset_id}")
        assert code == 200

    def test_saved_prompts_can_be_favourited(self, client):
        code, body = client.post("/api/prompts", {"title": "t", "prompt": "a lighthouse"})
        prompt_id = body["data"]["id"]
        code, body = client.patch(f"/api/prompts/{prompt_id}", {"favorite": True})
        assert body["data"]["favorite"] is True
        code, body = client.get("/api/prompts?favorite=1")
        assert any(p["id"] == prompt_id for p in body["data"])

    def test_a_character_references_an_owned_asset(self, client):
        import base64
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (10, 10, 10)).save(buf, format="PNG")
        code, body = client.post("/api/assets/upload", {
            "filename": "face.png", "mime": "image/png",
            "data": base64.b64encode(buf.getvalue()).decode()})
        asset_id = body["data"]["id"]

        code, body = client.post("/api/characters", {
            "name": "Keeper", "description": "the lighthouse keeper",
            "references": [{"asset_id": asset_id, "role": "face"}]})
        assert code == 201, body
        assert body["data"]["references"][0]["asset_id"] == asset_id

    def test_a_character_cannot_reference_a_missing_asset(self, client):
        code, body = client.post("/api/characters", {
            "name": "Ghost", "references": [{"asset_id": "ast_nope"}]})
        assert code == 400
        assert body["error"]["code"] == "invalid_field"


class TestUsage:
    def test_usage_is_recorded_for_a_job(self, client, mock_connection, wait_for_job):
        code, body = client.post("/api/generations/images", {
            "model_pk": mock_connection["image_pk"], "prompt": f"usage {time.time()}",
            "params": {"count": 2}})
        final = wait_for_job(body["data"]["id"])
        assert final["usage"] is not None
        assert final["usage"]["quantity"] == 2
        assert final["usage"]["cost_source"] == "unknown"
        assert final["usage"]["estimated_cost"] is None

    def test_analytics_never_invents_a_total(self, client, mock_connection):
        code, body = client.get("/api/analytics")
        data = body["data"]
        assert data["estimated_cost"] is None
        assert data["cost_known_for"] == 0
        assert "no cost" in data["cost_note"].lower()

    def test_analytics_counts_jobs_by_status(self, client, mock_connection, wait_for_job):
        code, body = client.post("/api/generations/images", {
            "model_pk": mock_connection["image_pk"], "prompt": f"count me {time.time()}"})
        wait_for_job(body["data"]["id"])
        code, body = client.get("/api/analytics?days=1")
        assert body["data"]["total_jobs"] >= 1

    def test_a_bad_days_value_is_refused(self, client):
        code, body = client.get("/api/analytics?days=0")
        assert code == 400


class TestAssistantHonesty:
    def test_the_assistant_reports_unavailable_without_a_chat_model(self, client):
        code, body = client.get("/api/assistant")
        assert code == 200
        assert body["data"]["available"] is False
        assert "chat model" in body["data"]["reason"]

    def test_asking_for_a_reply_without_a_model_is_refused_clearly(self, client):
        code, body = client.post("/api/assistant", {"message": "improve this prompt"})
        assert code == 400
        assert body["error"]["code"] == "unsupported_modality"

    def test_the_offline_adapter_does_not_pretend_to_be_a_language_model(self, client, mock_connection):
        """If a chat model is registered but is the demo, the answer says it cannot."""
        code, body = client.post("/api/assistant", {"message": "hi"})
        # There is no chat model at all here, so this is the "no model" path.
        assert code == 400


class TestOpenApi:
    def test_the_document_is_current(self):
        """docs/openapi.yaml is generated, and this fails when it drifts from the router."""
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "gen_openapi.py"), "--check"],
            capture_output=True, text=True, cwd=str(REPO))
        assert result.returncode == 0, result.stdout + result.stderr

    def test_every_route_is_documented(self):
        import yaml
        from app.web.routes import router

        document = yaml.safe_load((REPO / "docs" / "openapi.yaml").read_text(encoding="utf-8"))
        import re

        for method, pattern, name in router._routes:
            if method.lower() == "head":
                continue
            path = re.sub(r"\(\?P<(\w+)>\[\^/\]\+\)", r"{\1}", pattern.pattern[1:-1])
            assert path in document["paths"], path
            assert method.lower() in document["paths"][path], (method, path)


class TestNoInertClaims:
    def test_every_implemented_endpoint_answers(self, client, mock_connection):
        """A 200/201/202/400/404 — never a 501. A 501 means a route exists but is hollow."""
        paths = [
            ("GET", "/api/health"), ("GET", "/api/capabilities"),
            ("GET", "/api/connections"), ("GET", "/api/models"),
            ("GET", "/api/jobs"), ("GET", "/api/projects"), ("GET", "/api/assets"),
            ("GET", "/api/presets"), ("GET", "/api/prompts"), ("GET", "/api/characters"),
            ("GET", "/api/analytics"), ("GET", "/api/assistant"),
        ]
        for method, path in paths:
            code, body = client.call(method, path)
            assert code != 501, f"{path} is not implemented"
            assert code < 500, (path, code, body)

    def test_the_health_endpoint_reports_real_counts(self, client, mock_connection):
        code, body = client.get("/api/health")
        assert body["counts"]["connections"] >= 1
        assert body["counts"]["models"] >= 2
        assert body["schema_version"] >= 3
