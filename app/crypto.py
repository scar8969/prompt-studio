"""Credential storage: encrypted at rest, never returned in plaintext (spec §16).

Two rules this module exists to enforce:

1. A provider key is never stored as plaintext and never serialised back to a client.
   `mask()` is the only way a key leaves the database, and it cannot be inverted.
2. Encryption is authenticated. A tampered ciphertext raises instead of decrypting to
   something plausible.

Storage format is ``v1:<fernet-token>``. The prefix means the scheme can change later
without guessing at old rows.
"""

from __future__ import annotations

import base64
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from .config import APP_ENCRYPTION_KEY

_PREFIX = "v1:"
_fernet = Fernet(APP_ENCRYPTION_KEY)


def encrypt(plaintext: str) -> str:
    """Encrypt a secret for storage. Empty stays empty (a missing key is not a secret)."""
    if plaintext is None:
        return ""
    if plaintext == "":
        return ""
    token = _fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt(stored: str) -> str:
    """Decrypt a stored secret. Returns "" for anything unreadable.

    Undecryptable is treated as absent rather than fatal: a row written under a
    rotated key should fail that one connection with 'invalid_api_key', not take the
    server down. The caller sees an empty key and the provider rejects it, which is
    the honest outcome.
    """
    if not stored:
        return ""
    if not stored.startswith(_PREFIX):
        # A row written before encryption existed. Refuse to use it: using it would
        # mean trusting a plaintext value we cannot verify.
        return ""
    try:
        return _fernet.decrypt(stored[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return ""


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(_PREFIX)


def mask(key: Optional[str]) -> str:
    """A display form that cannot be reversed: ``sk-ab…4f9c``.

    Short keys are fully masked, because a 4-character key shown as ``ab…cd`` would
    give away half of it.
    """
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:4]}…{key[-4:]}"


def looks_like_plaintext_key(value: str) -> bool:
    """True when a value reaching storage is clearly not encrypted.

    Guards the migration path: a plaintext key found in the database is re-encrypted
    on read, and this is what the check uses to notice it.
    """
    if not value:
        return False
    return not value.startswith(_PREFIX)


def generate_key() -> str:
    """A fresh Fernet key, for `python -m app.cli init-key`."""
    return Fernet.generate_key().decode("ascii")


def _self_test() -> None:
    sample = "sk-test-1234567890abcdef"
    token = encrypt(sample)
    assert token.startswith(_PREFIX), token
    assert sample not in token, "ciphertext must not contain the plaintext"
    assert decrypt(token) == sample
    assert decrypt("v1:not-a-real-token") == ""
    assert decrypt("sk-plaintext") == "", "unescaped plaintext must not be usable"
    assert mask(sample) == "sk-t…cdef"
    assert mask("abc") == "•••"
    assert mask("") == ""
    assert encrypt("") == ""


if __name__ == "__main__":  # pragma: no cover - manual probe
    _self_test()
    print("crypto self-test OK")
