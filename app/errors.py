"""Error codes and the one response shape every endpoint returns (spec §19).

Every failure carries a stable code a client can branch on, a human-readable
message, and an actionable hint where one exists. Internal detail — stack traces,
upstream URLs, credentials — is never included; it is logged instead.
"""

from typing import Any, Dict, Optional

# Stable, machine-checkable codes. The set is closed on purpose: a client should be
# able to enumerate the ways a call can fail.
CODES: Dict[str, str] = {
    # request
    "invalid_request": "The request body was not valid.",
    "missing_field": "A required field was not provided.",
    "invalid_field": "A field had a value that cannot be used.",
    "unsupported_parameter": "A parameter this model does not accept was sent.",
    "unsupported_modality": "This model cannot do what was asked of it.",
    "not_found": "No such record.",
    "forbidden": "You do not have access to this record.",
    "method_not_allowed": "That method is not supported on this route.",
    "payload_too_large": "The request exceeded the size limit.",
    # credentials and connections
    "invalid_api_key": "The provider rejected the API key.",
    "expired_credentials": "The provider says these credentials have expired or were revoked.",
    "invalid_base_url": "The base URL is not a usable http(s) endpoint.",
    "blocked_url": "That URL points somewhere this server will not call.",
    "invalid_model_id": "The provider does not know that model id.",
    "connection_failed": "Could not reach the provider.",
    "duplicate_connection": "A connection with that base URL already exists.",
    # provider behaviour
    "provider_timeout": "The provider did not answer in time.",
    "provider_rate_limited": "The provider is rate limiting this key.",
    "provider_outage": "The provider returned a server error.",
    "insufficient_balance": "The provider account has insufficient balance or quota.",
    "invalid_provider_response": "The provider replied in a shape this app cannot read.",
    "output_download_failed": "The generated output could not be downloaded.",
    "storage_failure": "The output could not be stored.",
    # media
    "unsupported_media_type": "That file type is not accepted.",
    "media_mismatch": "The file contents do not match its declared type.",
    "media_too_large": "That file is larger than the limit.",
    "invalid_media": "The media could not be read.",
    # jobs
    "job_cancelled": "The job was cancelled.",
    "job_not_cancellable": "That job has already finished.",
    "duplicate_submission": "That job was already submitted.",
    # server
    "rate_limited": "Too many requests.",
    "internal_error": "Something went wrong on the server.",
}

# A short, safe hint shown to the user for the codes where one is genuinely useful.
HINTS: Dict[str, str] = {
    "invalid_api_key": "Check the key, and whether it is for the right provider.",
    "expired_credentials": "Issue a new key with the provider and update the connection.",
    "invalid_base_url": "It should look like https://host/v1 — including the version path.",
    "blocked_url": "Private, loopback and link-local addresses are refused. Set ALLOW_PRIVATE_UPSTREAM=1 only for local development.",
    "invalid_model_id": "Use the exact model id the provider documents — GET the connection's models to list them.",
    "provider_timeout": "Raise UPSTREAM_TIMEOUT, or pick a faster model.",
    "provider_rate_limited": "Wait and retry, or use a different key.",
    "insufficient_balance": "Top up the provider account.",
    "unsupported_parameter": "Remove it, or choose a model whose capabilities include it.",
    "unsupported_modality": "Pick a model that supports this output type.",
    "connection_failed": "Check the base URL and that the provider is reachable.",
}


class ApiError(Exception):
    """A failure that is safe to show a caller."""

    def __init__(self, code: str, message: Optional[str] = None,
                 status: int = 400, details: Optional[Dict[str, Any]] = None):
        if code not in CODES:
            raise ValueError(f"unknown error code: {code}")
        self.code = code
        self.status = status
        self.message = message or CODES[code]
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {"error": {"code": self.code, "message": self.message}}
        hint = HINTS.get(self.code)
        if hint:
            body["error"]["hint"] = hint
        if self.details:
            body["error"]["details"] = self.details
        return body


def not_found(what: str = "record") -> ApiError:
    return ApiError("not_found", f"No such {what}.", status=404)


def forbidden(what: str = "record") -> ApiError:
    return ApiError("forbidden", f"You do not have access to that {what}.", status=403)


def invalid_field(field: str, why: str) -> ApiError:
    return ApiError("invalid_field", f"'{field}': {why}", details={"field": field})


def missing_field(field: str) -> ApiError:
    return ApiError("missing_field", f"'{field}' is required.", details={"field": field})


def classify_upstream(status: int, body: str = "") -> ApiError:
    """Map a provider's HTTP status onto our error vocabulary (spec §19).

    The provider's own words are kept only when they are clearly safe — a short,
    key-redacted excerpt — because the actionable part is usually theirs.
    """
    excerpt = _safe_excerpt(body)
    if status in (401, 403):
        return ApiError("invalid_api_key", status=502, details={"provider_status": status, "provider_message": excerpt})
    if status == 402:
        return ApiError("insufficient_balance", status=502, details={"provider_status": status, "provider_message": excerpt})
    if status == 404:
        return ApiError("invalid_model_id", status=502, details={"provider_status": status, "provider_message": excerpt})
    if status == 408:
        return ApiError("provider_timeout", status=504, details={"provider_status": status})
    if status == 429:
        return ApiError("provider_rate_limited", status=503, details={"provider_status": status, "provider_message": excerpt})
    if 500 <= status < 600:
        return ApiError("provider_outage", status=502, details={"provider_status": status, "provider_message": excerpt})
    return ApiError("invalid_provider_response", status=502,
                    details={"provider_status": status, "provider_message": excerpt})


_KEYISH = ("sk-", "key", "token", "authorization", "bearer")


def _safe_excerpt(body: str, limit: int = 240) -> str:
    """A short excerpt of a provider's error, with anything key-shaped removed."""
    if not body:
        return ""
    text = " ".join(str(body).split())
    lowered = text.lower()
    if any(marker in lowered for marker in _KEYISH):
        # Do not echo it at all if it looks like it may contain a credential.
        return ""
    return text[:limit]
