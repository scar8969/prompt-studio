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
        entry = catalog.get("gpt_image_2_5")
        assert not entry["configured"]
        assert "GPT_IMAGE_2_5_MODEL" in entry["missing"]

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
