"""OpenAI-compatible adapter: image, video and chat (spec §24.5).

Covers every endpoint that speaks the OpenAI wire format — `/models`,
`/images/generations`, `/images/edits`, `/chat/completions`, and the video
generations shape several gateways expose. The real gateway used during development
(`asiasouth.up.railway.app`) is one of these, which is why this is the first adapter
(spec §23, Phase 2 "initial provider adapter").

Capabilities are discovered, not assumed:

* `/models` is read for the ids. Where a gateway annotates modalities, that is used.
* An id whose name or metadata says `video` reports video capabilities; `edit` in the
  name reports editing; everything else reports image.
* If the listing says nothing at all, the model still gets `image.generate` — the one
  capability that every one of these endpoints has by definition — and nothing else.
  Guessing beyond that is what produces inert buttons and 404s.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..errors import ApiError
from .base import Adapter, Connection, GenerationResult, ModelInfo

IMAGE_RATIOS = {
    "1:1": "1024x1024",
    "3:4": "1024x1365",
    "4:3": "1365x1024",
    "16:9": "1536x864",
    "9:16": "864x1536",
}

# Parameter descriptors shared by every OpenAI-shaped image endpoint. Values a model
# does not accept are refused at request time rather than silently dropped, so a
# client learns its request was wrong (spec §19).
def _image_parameters() -> List[Dict[str, Any]]:
    return [
        {"name": "aspect_ratio", "type": "enum", "choices": list(IMAGE_RATIOS.keys()), "required": False},
        {"name": "size", "type": "string", "required": False},
        {"name": "count", "type": "int", "minimum": 1, "maximum": 4, "required": False},
        {"name": "seed", "type": "int", "required": False},
        {"name": "negative_prompt", "type": "string", "required": False},
        {"name": "quality", "type": "string", "required": False},
        {"name": "style", "type": "string", "required": False},
    ]


VIDEO_PARAMETERS = [
    {"name": "aspect_ratio", "type": "enum", "choices": ["16:9", "9:16", "1:1"], "required": False},
    {"name": "duration", "type": "int", "minimum": 1, "maximum": 60, "required": False},
    {"name": "resolution", "type": "string", "required": False},
    {"name": "camera_move", "type": "string", "required": False},
    {"name": "count", "type": "int", "minimum": 1, "maximum": 2, "required": False},
]


def _looks_video(model_id: str, meta: Dict[str, Any]) -> bool:
    haystack = f"{model_id} {meta.get('modality', '')} {meta.get('type', '')}".lower()
    return any(token in haystack for token in ("video", "t2v", "i2v", "kling", "veo", "sora", "wan"))


def _looks_edit(model_id: str) -> bool:
    return any(token in model_id.lower() for token in ("edit", "inpaint", "img2img"))


class OpenAICompatibleAdapter(Adapter):
    name = "openai-compatible"

    # -- discovery ----------------------------------------------------------
    def list_models(self, conn: Connection) -> List[ModelInfo]:
        resp = self.client.get_json(self._url(conn.base_url, "models"),
                                    {"Authorization": f"Bearer {conn.secret}"} if conn.secret else {},
                                    conn.pinned_ip)
        self._check(resp)
        payload = resp.json()
        if payload is None:
            raise ApiError("invalid_provider_response",
                           "The provider's /models reply was not JSON.", status=502)

        entries = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            raise ApiError("invalid_provider_response",
                           "The provider's /models reply had no model list.", status=502)

        found: List[ModelInfo] = []
        for entry in entries:
            info = self._model_from_entry(entry)
            if info:
                found.append(info)
        return found

    def _model_from_entry(self, entry: Any) -> Optional[ModelInfo]:
        if isinstance(entry, str):
            entry = {"id": entry}
        if not isinstance(entry, dict):
            return None
        model_id = entry.get("id") or entry.get("name") or entry.get("model")
        if not isinstance(model_id, str) or not model_id.strip():
            return None
        model_id = model_id.strip()

        meta = {k: v for k, v in entry.items()
                if k in ("modality", "type", "owned_by", "created", "context_length", "pricing")}
        video = _looks_video(model_id, meta)

        caps = ["video.generate"] if video else ["image.generate"]
        if video:
            caps.append("video.async")
            if any(t in model_id.lower() for t in ("i2v", "image", "frame")):
                caps.append("video.image_to_video")
        else:
            if _looks_edit(model_id):
                caps.append("image.edit")
            caps.append("image.reference")

        params = VIDEO_PARAMETERS if video else _image_parameters()
        return ModelInfo(
            model_id=model_id,
            display_name=str(entry.get("name") or model_id),
            modality="video" if video else "image",
            capabilities=caps,
            parameters=params,
            metadata={k: v for k, v in meta.items() if v not in (None, "")},
        )

    def capabilities(self, conn: Connection, model_id: str) -> List[str]:
        """Discover, and fall back to the one capability that cannot be wrong.

        A listing that omits a model must not silently grant it capabilities, so an
        unknown model gets `image.generate` only.
        """
        try:
            for info in self.list_models(conn):
                if info.model_id == model_id:
                    return list(info.capabilities)
        except ApiError:
            raise
        return ["image.generate"]

    def parameters(self, conn: Connection, model_id: str) -> List[Dict[str, Any]]:
        try:
            for info in self.list_models(conn):
                if info.model_id == model_id:
                    return list(info.parameters)
        except ApiError:
            pass
        return _image_parameters()

    # -- work ---------------------------------------------------------------
    def _validate_params(self, params: Dict[str, Any], allowed: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Refuse unsupported parameters instead of dropping them quietly."""
        known = {p["name"] for p in allowed}
        clean: Dict[str, Any] = {}
        unsupported = [k for k in params if k not in known and k not in ("prompt", "model", "size")]
        if unsupported:
            raise ApiError("unsupported_parameter",
                           f"This model does not accept: {', '.join(sorted(unsupported))}.",
                           details={"unsupported": sorted(unsupported),
                                    "supported": sorted(known)})
        for spec in allowed:
            name = spec["name"]
            if name not in params:
                continue
            value = params[name]
            if value in (None, ""):
                continue
            if spec["type"] == "int":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise ApiError("invalid_field", f"'{name}' must be a whole number.")
                if spec.get("minimum") is not None and value < spec["minimum"]:
                    raise ApiError("invalid_field", f"'{name}' must be at least {spec['minimum']}.")
                if spec.get("maximum") is not None and value > spec["maximum"]:
                    raise ApiError("invalid_field", f"'{name}' must be at most {spec['maximum']}.")
            if spec["type"] == "enum" and value not in spec["choices"]:
                raise ApiError("invalid_field",
                               f"'{name}' must be one of: {', '.join(spec['choices'])}.")
            clean[name] = value
        return clean

    def generate(self, conn: Connection, model_id: str, prompt: str, params: Dict[str, Any],
                 references: Optional[List[Dict[str, Any]]] = None) -> GenerationResult:
        caps = self.capabilities(conn, model_id)
        if "video.generate" in caps:
            return self._generate_video(conn, model_id, prompt, params, references or [])
        if "image.generate" not in caps:
            raise ApiError("unsupported_modality",
                           f"'{model_id}' cannot generate images.",
                           details={"capabilities": caps})
        return self._generate_image(conn, model_id, prompt, params, references or [])

    def _generate_image(self, conn: Connection, model_id: str, prompt: str,
                        params: Dict[str, Any], references: List[Dict[str, Any]]) -> GenerationResult:
        clean = self._validate_params(params, _image_parameters())
        ratio = clean.get("aspect_ratio") or "1:1"
        size = clean.get("size") or IMAGE_RATIOS.get(ratio, "1024x1024")
        editing = bool(references) and any(
            a.get("media_type") == "image" for a in references)

        if editing:
            # /images/edits takes multipart. If the provider shape differs, the
            # failure is reported rather than guessed at.
            raise ApiError(
                "unsupported_parameter",
                "Editing with a reference image needs the provider's multipart "
                "/images/edits shape, which this adapter does not implement yet. "
                "Generate without a reference, or connect an editing model.",
                details={"model": model_id})

        payload: Dict[str, Any] = {
            "model": model_id,
            "prompt": prompt,
            "n": int(clean.get("count", 1) or 1),
            "size": size,
        }
        if clean.get("seed") not in (None, ""):
            payload["seed"] = clean["seed"]
        for key in ("negative_prompt", "quality", "style"):
            if clean.get(key):
                payload[key] = clean[key]

        started = time.time()
        resp = self.client.post_json(self._url(conn.base_url, "images/generations"),
                                     payload, _auth(conn), conn.pinned_ip)
        self._check(resp)
        body = resp.json()
        outputs = _read_image_outputs(body)
        if not outputs:
            raise ApiError("invalid_provider_response",
                           "The provider returned no image data.", status=502)
        return GenerationResult(outputs=outputs, raw_status=resp.status,
                                usage={"latency_ms": int((time.time() - started) * 1000),
                                       "images": len(outputs)})

    def _generate_video(self, conn: Connection, model_id: str, prompt: str,
                        params: Dict[str, Any], references: List[Dict[str, Any]]) -> GenerationResult:
        clean = self._validate_params(params, VIDEO_PARAMETERS)
        payload: Dict[str, Any] = {"model": model_id, "prompt": prompt}
        for key in ("aspect_ratio", "duration", "resolution", "camera_move"):
            if clean.get(key) not in (None, ""):
                payload[key] = clean[key]
        if references:
            urls = [a.get("remote_url") or a.get("url") for a in references]
            payload["image_urls"] = [u for u in urls if u]

        resp = self.client.post_json(self._url(conn.base_url, "videos/generations"),
                                     payload, _auth(conn), conn.pinned_ip)
        self._check(resp)
        body = resp.json() if isinstance(resp.json(), dict) else {}
        job_id = _first_str(body, ("id", "job_id", "task_id", "request_id"))
        outputs = _read_video_outputs(body)
        if not outputs and not job_id:
            raise ApiError("invalid_provider_response",
                           "The provider returned neither a video nor a job id.", status=502)
        return GenerationResult(outputs=outputs, provider_job_id=job_id,
                                raw_status=resp.status, pending=not outputs)

    def poll(self, conn: Connection, provider_job_id: str) -> GenerationResult:
        resp = self.client.get_json(self._url(conn.base_url, f"videos/generations/{provider_job_id}"),
                                    _auth(conn), conn.pinned_ip)
        self._check(resp)
        body = resp.json() if isinstance(resp.json(), dict) else {}
        status = str(body.get("status", "")).lower()
        outputs = _read_video_outputs(body)
        if status in ("failed", "error"):
            raise ApiError("provider_outage",
                           str(body.get("error") or "The provider reported the job failed."),
                           status=502)
        done = bool(outputs) or status in ("succeeded", "completed", "success")
        return GenerationResult(outputs=outputs, provider_job_id=provider_job_id,
                                pending=not done, raw_status=resp.status)

    def cancel(self, conn: Connection, provider_job_id: str) -> None:
        resp = self.client.request("POST",
                                   self._url(conn.base_url, f"videos/generations/{provider_job_id}/cancel"),
                                   _auth(conn) | {"Content-Type": "application/json"}, b"{}",
                                   conn.pinned_ip)
        if resp.status not in (200, 202, 204, 404):
            self._check(resp)

    # -- chat (used by the creative assistant) ------------------------------
    def chat(self, conn: Connection, model_id: str, messages: List[Dict[str, str]],
             temperature: float = 0.7, max_tokens: int = 2048) -> str:
        payload = {"model": model_id, "messages": messages,
                   "temperature": temperature, "max_tokens": max_tokens}
        resp = self.client.post_json(self._url(conn.base_url, "chat/completions"),
                                     payload, _auth(conn), conn.pinned_ip)
        self._check(resp)
        body = resp.json()
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ApiError("invalid_provider_response",
                           "The provider's chat reply had no message.", status=502)


def _auth(conn: Connection) -> Dict[str, str]:
    return {"Authorization": f"Bearer {conn.secret}"} if conn.secret else {}


def _first_str(obj: Any, keys) -> str:
    if isinstance(obj, dict):
        for key in keys:
            value = obj.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _read_image_outputs(body: Any) -> List[Dict[str, Any]]:
    """Pull image outputs out of either response shape, ignoring anything unusable."""
    out: List[Dict[str, Any]] = []
    if not isinstance(body, dict):
        return out
    data = body.get("data")
    if data is None and isinstance(body.get("images"), list):
        data = body["images"]
    if not isinstance(data, list):
        return out
    for item in data:
        if isinstance(item, dict):
            url = item.get("url") or item.get("b64_json") and None or item.get("url")
            b64 = item.get("b64_json") or item.get("b64")
            entry: Dict[str, Any] = {}
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                entry["remote_url"] = url
            if isinstance(b64, str) and b64:
                entry["b64"] = b64
            if entry:
                out.append(entry)
        elif isinstance(item, str) and item.startswith(("http://", "https://")):
            out.append({"remote_url": item})
    return out


def _read_video_outputs(body: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(body, dict):
        return out
    candidates: List[Any] = []
    for key in ("videos", "data", "output", "outputs"):
        value = body.get(key)
        if isinstance(value, list):
            candidates.extend(value)
        elif isinstance(value, dict):
            candidates.append(value)
    url = body.get("url") or body.get("video_url")
    if isinstance(url, str):
        candidates.append({"url": url})
    for item in candidates:
        if isinstance(item, str) and item.startswith(("http://", "https://")):
            out.append({"remote_url": item})
        elif isinstance(item, dict):
            u = item.get("url") or item.get("video_url") or item.get("remote_url")
            if isinstance(u, str) and u.startswith(("http://", "https://")):
                entry = {"remote_url": u}
                if item.get("duration") is not None:
                    entry["duration"] = item["duration"]
                out.append(entry)
    return out
