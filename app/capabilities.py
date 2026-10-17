"""The capability vocabulary (spec §25).

A closed set, because these strings are what the UI gates on. An unknown capability is
refused rather than stored, so a typo cannot become a button that fails at click time.
"""

from __future__ import annotations

from typing import Iterable, List

CAPABILITIES = {
    # image
    "image.generate": "Generate an image from text.",
    "image.edit": "Edit a supplied image (inpaint, replace, restyle).",
    "image.reference": "Accept a reference image to guide the result.",
    "image.variations": "Produce variations of a supplied image.",
    "image.upscale": "Increase an image's resolution.",
    # video
    "video.generate": "Generate video from text.",
    "video.image_to_video": "Animate a supplied image.",
    "video.extend": "Continue an existing clip.",
    "video.transform": "Restyle or transform an existing clip.",
    "video.async": "Runs as a provider-side job that is polled for completion.",
    # chat
    "chat.complete": "Conversational completion, used by the assistant.",
}

MODALITY_BY_CAPABILITY = {
    "image": ("image.generate", "image.edit", "image.reference", "image.variations",
              "image.upscale"),
    "video": ("video.generate", "video.image_to_video", "video.extend",
              "video.transform", "video.async"),
    "chat": ("chat.complete",),
}

#: An explicit edit/reference capability implies the base one.
IMPLIED = {
    "image.edit": ("image.generate",),
    "image.variations": ("image.generate",),
    "image.upscale": ("image.generate",),
    "image.reference": ("image.generate",),
    "video.image_to_video": ("video.generate",),
    "video.extend": ("video.generate",),
    "video.transform": ("video.generate",),
    "video.async": ("video.generate",),
}

#: Modality implied by a capability with no explicit modality.
MODALITY_OF = {
    "image.generate": "image", "image.edit": "image", "image.reference": "image",
    "image.variations": "image", "image.upscale": "image",
    "video.generate": "video", "video.image_to_video": "video", "video.extend": "video",
    "video.transform": "video", "video.async": "video",
    "chat.complete": "chat",
}


def validate_capabilities(values: Iterable[str]) -> List[str]:
    """Drop nothing silently: an unknown capability is an error."""
    from .errors import ApiError

    if isinstance(values, str):
        raise ApiError("invalid_field", "'capabilities' must be a list, not a string.",
                       details={"field": "capabilities"})
    out: List[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ApiError("invalid_field", "Each capability must be a string.",
                           details={"field": "capabilities"})
        key = value.strip().lower()
        if key not in CAPABILITIES:
            raise ApiError("invalid_field",
                           f"Unknown capability '{value}'.",
                           details={"field": "capabilities",
                                    "known": sorted(CAPABILITIES)})
        # Add what it implies, so a capability check never has to reason about it.
        for implied in IMPLIED.get(key, ()):
            if implied not in out:
                out.append(implied)
        if key not in out:
            out.append(key)
    return out


def modality_for(capabilities: Iterable[str]) -> str:
    caps = list(capabilities)
    for modality, members in MODALITY_BY_CAPABILITY.items():
        if any(c in members for c in caps):
            return modality
    return "image"


def has(capabilities: Iterable[str], capability: str) -> bool:
    return capability in set(capabilities)


def requires(capabilities: Iterable[str], capability: str, model_label: str) -> None:
    """Raise unless the model has the capability the request needs."""
    from .errors import ApiError

    if has(capabilities, capability):
        return
    raise ApiError(
        "unsupported_modality",
        f"{model_label} cannot do that: it does not report '{capability}'.",
        details={"required": capability, "capabilities": sorted(set(capabilities))})
