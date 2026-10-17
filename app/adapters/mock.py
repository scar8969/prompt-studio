"""The offline adapter (spec §26).

Spec §26 permits a mock adapter "for development" and requires the distinction to be
explicit. This adapter is that, and it is built so it cannot be mistaken for a real
provider:

* Every output it produces is a generated placeholder image, and the job records
  ``provider='mock'`` with ``cost_source='unknown'`` — never a real-looking cost.
* The PNG it renders carries a visible banner saying it is a demo image, so a
  mislabelled screenshot is self-evidently a demo.
* It is selected only by an explicit connection of kind ``mock``; it is never a
  fallback for a failing real provider.
"""

from __future__ import annotations

import io
import time
from typing import Any, Dict, List, Optional

from .base import Adapter, Connection, GenerationResult, ModelInfo

DEMO_MODELS = [
    ModelInfo(
        model_id="demo-image-1",
        display_name="Demo Image (offline)",
        modality="image",
        capabilities=["image.generate", "image.reference"],
        parameters=[
            {"name": "aspect_ratio", "type": "enum", "choices": ["1:1", "3:4", "4:3", "16:9", "9:16"], "required": False},
            {"name": "count", "type": "int", "minimum": 1, "maximum": 4, "required": False},
            {"name": "seed", "type": "int", "required": False},
            {"name": "negative_prompt", "type": "string", "required": False},
        ],
        metadata={"offline": True},
    ),
    ModelInfo(
        model_id="demo-video-1",
        display_name="Demo Video (offline)",
        modality="video",
        capabilities=["video.generate", "video.async", "video.image_to_video"],
        parameters=[
            {"name": "aspect_ratio", "type": "enum", "choices": ["16:9", "9:16", "1:1"], "required": False},
            {"name": "duration", "type": "int", "minimum": 1, "maximum": 30, "required": False},
            {"name": "count", "type": "int", "minimum": 1, "maximum": 2, "required": False},
        ],
        metadata={"offline": True},
    ),
]


class MockAdapter(Adapter):
    name = "mock"
    offline = True
    cost_per_output = None          # unknown on purpose: no invented prices

    def list_models(self, conn: Connection) -> List[ModelInfo]:
        return list(DEMO_MODELS)

    def generate(self, conn: Connection, model_id: str, prompt: str, params: Dict[str, Any],
                 references: Optional[List[Dict[str, Any]]] = None) -> GenerationResult:
        known = {m.model_id: m for m in DEMO_MODELS}
        info = known.get(model_id)
        if info is None:
            from ..errors import ApiError
            raise ApiError("invalid_model_id",
                           f"'{model_id}' is not one of the demo models: "
                           f"{', '.join(known)}.")
        count = 1
        try:
            count = max(1, min(4, int(params.get("count", 1))))
        except (TypeError, ValueError):
            count = 1

        if "video.generate" in info.capabilities:
            # Reported as pending with a provider job id, so the async path is
            # exercised exactly as a real provider would exercise it.
            return GenerationResult(provider_job_id=f"demo-{int(time.time())}",
                                    pending=True, raw_status=200)

        outputs = []
        for index in range(count):
            outputs.append({"png": render_demo_png(prompt, index)})
        return GenerationResult(outputs=outputs, raw_status=200,
                                usage={"images": count, "offline": True})

    def poll(self, conn: Connection, provider_job_id: str) -> GenerationResult:
        # A demo video completes after a short, real delay so the polling UI is
        # genuinely exercised rather than instantly done.
        try:
            submitted = float(provider_job_id.split("-")[-1])
        except (ValueError, IndexError):
            submitted = time.time()
        if time.time() - submitted < 4:
            return GenerationResult(provider_job_id=provider_job_id, pending=True, raw_status=200)
        return GenerationResult(
            outputs=[{"video_placeholder": True, "duration": 4}],
            provider_job_id=provider_job_id, raw_status=200)

    def estimate_cost(self, model_id: str, params: Dict[str, Any]) -> Optional[float]:
        return None      # the demo does not know what anything costs


def render_demo_png(prompt: str, index: int = 0, size: int = 512) -> bytes:
    """A placeholder PNG with a banner, so it can never pass as a real generation."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (size, size), (18, 18, 20))
    draw = ImageDraw.Draw(img)
    for y in range(size):
        shade = 18 + int(40 * (y / size))
        draw.line([(0, y), (size, y)], fill=(shade, shade, shade + 6))

    band = int(size * 0.16)
    draw.rectangle([0, 0, size, band], fill=(255, 243, 214))
    draw.text((10, band // 3), "DEMO IMAGE - NOT A REAL GENERATION",
              fill=(138, 90, 0))

    words = (prompt or "").split()
    lines, line = [], ""
    for word in words:
        if len(line) + len(word) + 1 > 44:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)

    y = band + 18
    for text in lines[:10]:
        draw.text((12, y), text, fill=(230, 230, 235))
        y += 18
    draw.text((12, size - 26), f"mock adapter · #{index + 1}", fill=(150, 150, 158))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
