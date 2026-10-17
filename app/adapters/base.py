"""The unified model adapter interface (spec §24.5).

One interface, several dialects. A provider is reached through an `Adapter`, which
knows how to (a) list models, (b) turn a capability-checked request into that
provider's wire format, and (c) read the reply back into a common shape. Nothing
above this layer knows which provider is in use, and nothing below it knows what a
job or a project is.

Two rules that keep this honest:

* `capabilities()` reports what the provider actually documents, not what would be
  nice. The registry stores the result; the UI reads it. A model without
  `image.edit` gets no edit button (spec §25).
* `estimate_cost()` returns ``None`` when the price is unknown, and the caller stores
  ``cost_source='unknown'``. A fabricated cost is worse than no cost (spec §25).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..errors import ApiError, classify_upstream
from ..http_client import UpstreamClient, UpstreamResponse


@dataclass
class ModelInfo:
    """One model as its provider describes it."""
    model_id: str
    display_name: str = ""
    modality: str = "image"
    capabilities: List[str] = field(default_factory=list)
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationResult:
    """What an adapter returns for a generation attempt."""
    outputs: List[Dict[str, Any]] = field(default_factory=list)
    provider_job_id: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)
    raw_status: Optional[int] = None
    pending: bool = False          # True when the provider is still working


@dataclass
class Connection:
    """Resolved connection details. `secret` is plaintext only in memory."""
    base_url: str
    secret: str = ""
    provider: str = ""
    pinned_ip: str = ""


class Adapter:
    """Base class. Subclasses implement the provider-specific parts."""

    name = "base"
    #: True for the offline adapter, which never touches the network.
    offline = False
    #: Cost is per image/video/second. ``None`` means the price is not published.
    cost_per_output: Optional[float] = None
    cost_currency = "USD"

    def __init__(self, client: Optional[UpstreamClient] = None):
        self.client = client or UpstreamClient()

    # -- capabilities -------------------------------------------------------
    def list_models(self, conn: Connection) -> List[ModelInfo]:
        raise NotImplementedError

    def capabilities(self, conn: Connection, model_id: str) -> List[str]:
        """Capabilities for one model, from the provider's own listing.

        Returns [] when the provider does not say — never a guess, because the whole
        point of the capability gate is that it reflects real support.
        """
        for info in self.list_models(conn):
            if info.model_id == model_id:
                return list(info.capabilities)
        return []

    def parameters(self, conn: Connection, model_id: str) -> List[Dict[str, Any]]:
        for info in self.list_models(conn):
            if info.model_id == model_id:
                return list(info.parameters)
        return []

    # -- work ---------------------------------------------------------------
    def generate(self, conn: Connection, model_id: str, prompt: str,
                 params: Dict[str, Any], references: Optional[List[Dict[str, Any]]] = None) -> GenerationResult:
        """Run a generation, or start one. Raises ApiError on a provider failure."""
        raise NotImplementedError

    def poll(self, conn: Connection, provider_job_id: str) -> GenerationResult:
        """Check on an asynchronous provider job."""
        raise ApiError("unsupported_modality", "This provider does not report job progress.", status=400)

    def cancel(self, conn: Connection, provider_job_id: str) -> None:
        raise ApiError("job_not_cancellable", "This provider does not support cancelling.", status=400)

    # -- cost ---------------------------------------------------------------
    def estimate_cost(self, model_id: str, params: Dict[str, Any]) -> Optional[float]:
        """Cost in `cost_currency`, or None when it is not published.

        Deliberately not guessed: an invented number in a usage record is a lie the
        user would make spending decisions on.
        """
        if self.cost_per_output is None:
            return None
        count = 1
        try:
            count = max(1, int(params.get("count", 1)))
        except (TypeError, ValueError):
            count = 1
        return round(self.cost_per_output * count, 6)

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _check(resp: UpstreamResponse) -> UpstreamResponse:
        """Turn a non-2xx upstream response into our error vocabulary."""
        if resp.ok:
            return resp
        raise classify_upstream(resp.status, resp.text())

    @staticmethod
    def _url(base_url: str, path: str) -> str:
        return base_url.rstrip("/") + "/" + path.lstrip("/")
