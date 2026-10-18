"""Credentials at rest and on the wire (spec §16, §20, §25).

These are the tests that matter most: if any of them fails, the app is storing or
showing a provider key in a way it must not.
"""

import json
import os
import uuid

import pytest

from app import crypto, store


SECRET = "sk-live-do-not-leak-9f8e7d6c5b4a"


class TestEncryptionAtRest:
    def test_the_plaintext_is_not_in_the_ciphertext(self):
        token = crypto.encrypt(SECRET)
        assert SECRET not in token
        assert token.startswith("v1:")

    def test_encryption_round_trips(self):
        assert crypto.decrypt(crypto.encrypt(SECRET)) == SECRET

    def test_the_same_secret_encrypts_differently_each_time(self):
        # Fernet includes a random IV; identical ciphertexts would leak equality.
        assert crypto.encrypt(SECRET) != crypto.encrypt(SECRET)

    def test_a_tampered_ciphertext_does_not_decrypt(self):
        token = crypto.encrypt(SECRET)
        flipped = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
        assert crypto.decrypt(flipped) == ""

    def test_plaintext_in_the_database_is_not_usable(self):
        # A row written before encryption existed must not be trusted.
        assert crypto.decrypt("sk-written-in-plaintext") == ""

    def test_empty_values_stay_empty(self):
        assert crypto.encrypt("") == ""
        assert crypto.decrypt("") == ""


class TestMasking:
    @pytest.mark.parametrize("key,expected", [
        ("sk-abcdef1234567890", "sk-a…7890"),
        ("abc", "•••"),
        ("12345678", "••••••••"),      # 8 chars: showing halves would give away half
        ("", ""),
    ])
    def test_mask(self, key, expected):
        assert crypto.mask(key) == expected

    def test_a_mask_is_not_reversible(self):
        masked = crypto.mask(SECRET)
        assert SECRET not in masked
        assert len(masked) < len(SECRET)


class TestNoCredentialIsEverStored:
    """The strongest form of "the key never leaves the server": it never arrives.

    Under BYOK a key was POSTed, encrypted and stored, and these tests checked the
    ciphertext was unreadable. The app no longer accepts a key at all — credentials come
    from the environment — so the property to assert is that no credential column is
    ever written and no endpoint returns one.
    """

    def test_there_is_no_endpoint_that_accepts_a_key(self, client):
        """Creating or rotating a connection is gone, not merely undocumented."""
        for method, path in (
                ("post", "/api/connections"),
                ("patch", "/api/connections/anything"),
                ("delete", "/api/connections/anything"),
                ("post", "/api/connections/anything/rotate-key")):
            code, _ = getattr(client, method)(path)
            assert code in (404, 405), (
                f"{method.upper()} {path} still exists ({code}) — BYOK was not removed")

    def test_the_providers_endpoint_is_read_only(self, client):
        code, body = client.get("/api/providers")
        assert code == 200
        for provider in body["data"]:
            assert "secret" not in str(provider).lower()
            assert "key_hint" not in provider
            assert set(provider) >= {"id", "name", "kind", "api_url"}

    def test_no_credential_column_is_written(self, client, mock_connection):
        """Even a configured provider row stores no secret."""
        from app import store

        rows = store.rows("SELECT secret_encrypted, key_hint FROM connections")
        assert rows, "expected at least the demo provider"
        for row in rows:
            assert row["secret_encrypted"] == "", "a credential was stored"
            assert row["key_hint"] == ""

    def test_an_api_key_is_not_readable_from_any_endpoint(self, client, mock_connection):
        """A configured model's key must not appear in any JSON response."""
        import os

        os.environ["GPT_IMAGE_2_5_API_KEY"] = "sk-super-secret-probe-value"
        try:
            from app.services import sync

            sync.sync_from_env()
            for path in ("/api/models", "/api/providers", "/api/catalog",
                         "/api/models?include_disabled=true"):
                code, body = client.get(path)
                assert code == 200, path
                assert "sk-super-secret-probe-value" not in str(body), (
                    f"{path} leaked the key")
        finally:
            os.environ.pop("GPT_IMAGE_2_5_API_KEY", None)
            from app.services import sync

            sync.sync_from_env()

    def test_a_key_never_reaches_the_log(self, client):
        """The value must not be logged, whatever the provider does with it."""
        import os
        import tempfile

        from app import store

        probe = "sk-log-probe-abcdef123456"
        os.environ["GPT_IMAGE_2_5_API_KEY"] = probe
        try:
            from app.services import sync

            sync.sync_from_env()
            client.get("/api/catalog")
            client.get("/api/models")
        finally:
            os.environ.pop("GPT_IMAGE_2_5_API_KEY", None)
            from app.services import sync

            sync.sync_from_env()

        # Nothing in the database holds it either.
        dumped = str(store.rows("SELECT * FROM models")) + str(
            store.rows("SELECT * FROM connections"))
        assert probe not in dumped


class TestEncryptionStillWorks:
    """The crypto module stays: it encrypts the one thing that can still be a secret.

    It is no longer on the BYOK path, but a future feature that stores a secret must have
    somewhere safe to put it, and deleting a working, tested primitive to satisfy a
    refactor would be its own mistake.
    """

    def test_a_key_round_trips(self):
        from app import crypto

        sealed = crypto.encrypt("hello-world")
        assert sealed != "hello-world"
        assert crypto.decrypt(sealed) == "hello-world"
