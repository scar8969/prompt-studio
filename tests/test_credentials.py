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


class TestKeyNeverLeavesTheServer:
    def test_create_response_has_no_plaintext_key(self, client):
        code, body = client.post("/api/connections", {
            "name": "leak check", "kind": "openai-compatible",
            "base_url": f"https://api.example.com/{uuid.uuid4().hex[:8]}/v1", "api_key": SECRET})
        assert code == 201
        assert SECRET not in json.dumps(body)
        assert body["data"]["key_hint"] == "sk-l…5b4a"

    def test_read_endpoints_have_no_plaintext_key(self, client):
        code, body = client.post("/api/connections", {
            "name": "leak check 2", "kind": "openai-compatible",
            "base_url": f"https://api2.example.com/{uuid.uuid4().hex[:8]}/v1", "api_key": SECRET})
        conn_id = body["data"]["id"]
        for path in (f"/api/connections/{conn_id}", "/api/connections"):
            code, body = client.get(path)
            assert code == 200, path
            assert SECRET not in json.dumps(body), path

    def test_the_database_holds_ciphertext_only(self, client):
        code, body = client.post("/api/connections", {
            "name": "at rest", "kind": "openai-compatible",
            "base_url": f"https://api3.example.com/{uuid.uuid4().hex[:8]}/v1", "api_key": SECRET})
        conn_id = body["data"]["id"]
        row = store.row("SELECT secret_encrypted FROM connections WHERE id=?", (conn_id,))
        assert SECRET not in row["secret_encrypted"]
        assert crypto.decrypt(row["secret_encrypted"]) == SECRET

    def test_deleting_a_connection_removes_the_ciphertext(self, client):
        code, body = client.post("/api/connections", {
            "name": "delete me", "kind": "openai-compatible",
            "base_url": f"https://api4.example.com/{uuid.uuid4().hex[:8]}/v1", "api_key": SECRET})
        conn_id = body["data"]["id"]
        assert store.row("SELECT id FROM connections WHERE id=?", (conn_id,))

        code, body = client.delete(f"/api/connections/{conn_id}")
        assert code == 200 and body["data"]["secret_removed"] is True
        assert store.row("SELECT id FROM connections WHERE id=?", (conn_id,)) is None

        # Nothing anywhere in the database still holds it.
        for row in store.rows("SELECT secret_encrypted FROM connections"):
            assert SECRET not in (row["secret_encrypted"] or "")

    def test_rotating_a_key_replaces_the_stored_value(self, client):
        code, body = client.post("/api/connections", {
            "name": "rotate", "kind": "openai-compatible",
            "base_url": f"https://api5.example.com/{uuid.uuid4().hex[:8]}/v1", "api_key": "sk-old-key-000000"})
        conn_id = body["data"]["id"]
        code, body = client.post(f"/api/connections/{conn_id}/rotate-key",
                                 {"api_key": SECRET})
        assert code == 200, body
        row = store.row("SELECT secret_encrypted, key_hint, status FROM connections "
                        "WHERE id=?", (conn_id,))
        assert crypto.decrypt(row["secret_encrypted"]) == SECRET
        assert "sk-o…0000" == row["key_hint"] or row["key_hint"].endswith("5b4a")
        # A new key means the previous test result no longer applies.
        assert row["status"] == "untested"

    def test_a_key_never_reaches_the_log(self, client):
        """The request logger records the request line, never headers."""
        import logging
        from app.web import server as server_module

        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Capture()
        logger = logging.getLogger("studio.http")
        logger.addHandler(handler)
        try:
            client.post("/api/connections", {
                "name": "log check", "kind": "openai-compatible",
                "base_url": f"https://api6.example.com/{uuid.uuid4().hex[:8]}/v1", "api_key": SECRET})
        finally:
            logger.removeHandler(handler)
        joined = "\n".join(records)
        assert SECRET not in joined, "the key appeared in a log line"
        assert "Authorization" not in joined
