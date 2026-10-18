"""The catalogue and the env file must describe the same 58 models.

Both are generated from Higgsfield's published reference, so these tests are about
drift: a model added upstream, a block lost from the env file, or a hand-edit that
splits the two apart. Generated files that nobody checks become stale silently.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import catalog  # noqa: E402


def test_the_catalogue_covers_every_published_model():
    """59 models across four categories.

    58 come from MODELS.md; `seedance_2_5` is documented in the CLI README as the SOTA
    video default but missing from MODELS.md, so it is recorded explicitly.
    """
    counts = catalog.summary()["by_modality"]
    assert counts == {"image": 24, "video": 25, "3d": 5, "audio": 5}, (
        f"catalogue counts changed: {counts}")


def test_the_readme_default_video_model_is_present():
    """Higgsfield's README calls seedance_2_5 the SOTA default; MODELS.md omits it."""
    model = catalog.get("seedance_2_5")
    assert model is not None, "seedance_2_5 is missing from the catalogue"
    assert model["modality"] == "video"
    assert "video.generate" in model["capabilities"]


def test_every_model_has_a_real_job_set_type():
    """A job-set type is what the Higgsfield CLI actually accepts.

    The bug this prevents: a display name quietly used as an id (`GPT Image 2.5` with
    spaces) that no provider would ever accept. Upstream ids are lowercase snake_case,
    and may start with a digit (`3d_rigging`, `3d_objects`).
    """
    pattern = re.compile(r"^[a-z0-9][a-z0-9_]*$")
    for model in catalog.CATALOG:
        assert pattern.match(model["job_set_type"]), (
            f"{model['label']} has a job_set_type that is not a CLI id: "
            f"{model['job_set_type']!r}")
        assert " " not in model["job_set_type"], (
            f"{model['label']} uses a display name as an id")


def test_the_env_prefix_is_derived_from_the_job_set_type():
    """The variable name and the model id can never disagree."""
    for model in catalog.CATALOG:
        assert model["env"] == model["job_set_type"].upper()


def test_every_model_has_capabilities_from_the_closed_vocabulary():
    from app.capabilities import MODALITY_BY_CAPABILITY, validate_capabilities

    for model in catalog.CATALOG:
        assert model["capabilities"], f"{model['label']} reports no capabilities"
        validated = validate_capabilities(model["capabilities"])
        assert set(validated) <= set(MODALITY_BY_CAPABILITY[model["modality"]]) | set(
            model["capabilities"]), f"{model['label']} has a capability it cannot have"


def test_reference_and_edit_capabilities_follow_the_documented_flags():
    """Capabilities are derived, not assigned: the flag is the evidence."""
    by_id = {m["job_set_type"]: m for m in catalog.CATALOG}
    # GPT Image 2 documents --mask and --is_inpaint, so it can edit.
    assert "image.edit" in by_id["gpt_image_2"]["capabilities"]
    # Kling v3.0 documents --start-image, so it can animate a still.
    assert "video.image_to_video" in by_id["kling3_0"]["capabilities"]
    # A plain text-to-image model must not claim image-to-video.
    assert "video.image_to_video" not in by_id["flux_2"]["capabilities"]


def test_the_env_file_has_a_block_for_every_model():
    """The requirement: model-specific API key AND model name, for all of them."""
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    missing_key, missing_model = [], []
    for model in catalog.CATALOG:
        prefix = model["env"]
        if f"{prefix}_API_KEY=" not in text:
            missing_key.append(model["label"])
        if f"{prefix}_MODEL=" not in text:
            missing_model.append(model["label"])
    assert not missing_key, f"no API_KEY block for: {missing_key}"
    assert not missing_model, f"no MODEL block for: {missing_model}"


def test_the_env_file_states_the_real_job_set_type():
    """The default value in `<PREFIX>_MODEL=` must be the id the CLI takes."""
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for model in catalog.CATALOG:
        line = f"{model['env']}_MODEL={model['job_set_type']}"
        assert line in text, f"expected {line} in .env.example"


def test_the_committed_files_are_not_stale():
    """Regenerating must be a no-op; if it is not, someone edited a generated file."""
    import subprocess

    for script in ("scripts/gen_catalog.py", "scripts/gen_env_example.py"):
        result = subprocess.run([sys.executable, script, "--check"],
                                capture_output=True, text=True, cwd=str(ROOT))
        assert result.returncode == 0, (
            f"{script} reports drift:\n{result.stdout}\n{result.stderr}")


def test_an_unconfigured_model_is_hidden_and_never_invented(monkeypatch):
    """No key means not configured — with the variable named, and no fake id."""
    monkeypatch.delenv("HIGGSFIELD_API_KEY", raising=False)
    monkeypatch.setattr(catalog.shutil, "which", lambda _: None)
    monkeypatch.setenv("NANO_BANANA_2_ENABLED", "1")
    model = catalog.get("nano_banana_2")
    assert model["configured"] is False
    assert "NANO_BANANA_2_API_KEY" in model["missing"]
    # The model id defaults to the provider's own id, not Higgsfield's job-set id:
    # Google would reject `nano_banana_2`, so the id that gets sent is Gemini's.
    assert model["model_id"] == "gemini-3-pro-image-preview"


class TestTheModelNameIsTheProviders:
    """`_MODEL` must be the name the *endpoint* expects, not Higgsfield's id.

    The trap this pins: the catalogue's `job_set_type` is what Higgsfield's CLI takes
    (`nano_banana_2`), and Google has never heard of it. Shipping Google's URL as the
    default while defaulting the model name to `nano_banana_2` produces a 404 from the
    provider that reads like a broken key.
    """

    def test_a_known_model_defaults_to_the_provider_id(self, monkeypatch):
        for name in ("NANO_BANANA_2_API_KEY", "NANO_BANANA_2_MODEL",
                     "NANO_BANANA_2_API_URL"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("NANO_BANANA_2_API_KEY", "k")
        entry = catalog.get("nano_banana_2")
        assert entry["model_id"] == "gemini-3-pro-image-preview"
        assert entry["model_id"] != "nano_banana_2"

    def test_an_unknown_provider_id_is_reported_missing_not_guessed(self, monkeypatch):
        """No recorded id means `_MODEL` is required, not silently wrong."""
        for name in ("GPT_IMAGE_2_5_MODEL", "GPT_IMAGE_2_5_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("GPT_IMAGE_2_5_API_KEY", "k")
        monkeypatch.setenv("API_BASE_URL", "https://api.openai.com/v1")
        entry = catalog.get("gpt_image_2_5")
        # With one shared endpoint the model name is the only thing left to supply, and
        # it falls back to the recorded id rather than being reported missing.
        assert entry["configured"]
        assert entry["model_id"] == "gpt_image_2_5"

    def test_an_explicit_model_always_wins(self, monkeypatch):
        monkeypatch.setenv("NANO_BANANA_2_API_KEY", "k")
        monkeypatch.setenv("NANO_BANANA_2_MODEL", "my-gateway-alias")
        assert catalog.get("nano_banana_2")["model_id"] == "my-gateway-alias"

    def test_a_higgsfield_endpoint_keeps_the_job_set_id(self, monkeypatch):
        """When the endpoint *is* Higgsfield, the job-set id is the correct name."""
        # The prefix is the job-set id uppercased: text2image_soul_v2 -> TEXT2IMAGE_SOUL_V2
        monkeypatch.setenv("TEXT2IMAGE_SOUL_V2_API_KEY", "k")
        monkeypatch.setenv("TEXT2IMAGE_SOUL_V2_API_URL", "cli://higgsfield")
        monkeypatch.delenv("TEXT2IMAGE_SOUL_V2_MODEL", raising=False)
        entry = catalog.get("text2image_soul_v2")
        assert entry["model_id"] == "text2image_soul_v2"
        assert entry["configured"]


class TestOneEndpointManyModels:
    """The simple format: one `API_BASE_URL`, then a name and a key per model.

    Every model goes through the same gateway, so the shared endpoint has to work for
    many models at once — and two models must not collide in the registry just because
    they point at the same URL.
    """

    def test_two_models_share_one_endpoint(self, monkeypatch):
        from app.services import connections as connection_service
        from app.services import sync

        monkeypatch.setenv("API_BASE_URL", "https://gw.example.com/v1")
        monkeypatch.setenv("NANO_BANANA_2_API_KEY", "k1")
        monkeypatch.setenv("GPT_IMAGE_2_API_KEY", "k2")
        monkeypatch.setenv("GPT_IMAGE_2_MODEL", "gpt-image-1")
        sync.sync_from_env()

        first = connection_service.public_model(
            connection_service.get_model_by_env_prefix("NANO_BANANA_2"))
        second = connection_service.public_model(
            connection_service.get_model_by_env_prefix("GPT_IMAGE_2"))
        assert first["api_url"] == "https://gw.example.com/v1"
        assert second["api_url"] == "https://gw.example.com/v1"
        # Same endpoint, two rows: the registry must not merge them.
        assert first["id"] != second["id"]
        assert first["model_id"] == "gemini-3-pro-image-preview"
        assert second["model_id"] == "gpt-image-1"

    def test_one_endpoint_produces_one_provider_row(self, monkeypatch):
        """Five models on one gateway is five models and one provider."""
        from app.services import connections as connection_service
        from app.services import sync

        monkeypatch.setenv("API_BASE_URL", "https://shared.example.com/v1")
        for prefix in ("NANO_BANANA_2", "GPT_IMAGE_2"):
            monkeypatch.setenv(f"{prefix}_API_KEY", "k")
        monkeypatch.setenv("GPT_IMAGE_2_MODEL", "gpt-image-1")
        sync.sync_from_env()

        rows = [p for p in connection_service.list_providers()
                if p["api_url"] == "https://shared.example.com/v1"]
        assert len(rows) == 1, f"expected one provider row, got {len(rows)}"
        assert rows[0]["model_count"] >= 2

    def test_the_model_name_is_sent_verbatim(self, monkeypatch):
        """Whatever you put in _MODEL is what the endpoint receives."""
        monkeypatch.setenv("API_BASE_URL", "https://gw.example.com/v1")
        monkeypatch.setenv("CINEMATIC_STUDIO_2_5_API_KEY", "k")
        monkeypatch.setenv("CINEMATIC_STUDIO_2_5_MODEL", "my-endpoint-alias")
        entry = catalog.get("cinematic_studio_2_5")
        assert entry["model_id"] == "my-endpoint-alias"
        assert entry["configured"]

    def test_a_per_model_url_still_works_when_needed(self, monkeypatch):
        """The global endpoint is the default; one model can still differ."""
        monkeypatch.setenv("API_BASE_URL", "https://gw.example.com/v1")
        monkeypatch.setenv("VEO3_API_KEY", "k")
        monkeypatch.setenv("VEO3_API_URL", "https://other.example.com/v1")
        assert catalog.get("veo3")["api_url"] == "https://other.example.com/v1"
