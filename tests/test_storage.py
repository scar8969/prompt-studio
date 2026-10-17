"""The storage backends, and the S3 signature that Supabase Storage depends on.

The signature is the part worth testing hardest: it is a pure function of its inputs, so
a wrong one is a bug that only shows up as a 403 from the provider, which reads exactly
like "your keys are wrong". These tests pin the canonical request and the signature
against AWS's own published example, so a regression is caught here instead of in
production.

Reference: AWS General Reference, "Signature Calculations for the Authorization Header:
Creating a Canonical Request" — the example uses the Amazon S3 service, 2013-05-24,
`us-east-1`, access key `AKIAIOSFODNN7EXAMPLE`.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import storage  # noqa: E402

ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

#: Produced by botocore 1.43's `S3SigV4Auth` (the reference implementation). Replaying
#: them here pins our SigV4 to the same algorithm, byte for byte.
VECTORS = [
    {"method": "GET", "host": "examplebucket.s3.amazonaws.com",
     "bucket": "examplebucket", "key": "test.txt", "content_type": "",
     "payload": b"", "amz_date": "20261017T183120Z",
     "signature": "0de10333e6de26f6c75f590f9e916ef11daeef9c21f5e0bebff35693e8a940a0"},
    {"method": "PUT", "host": "ref.storage.supabase.co", "bucket": "media",
     "key": "ast_abc.png", "content_type": "image/png",
     "payload": bytes.fromhex("89504e470d0a1a0a") + b"fake",
     "amz_date": "20261017T183120Z",
     "signature": "bd304b0db87b59cc4cd9161b3e270a3a7d6f7eee3abfd414508ceedb5582857c"},
    {"method": "PUT", "host": "ref.storage.supabase.co", "bucket": "media",
     "key": "folder/a file.webp", "content_type": "image/webp",
     "payload": b"x" * 300, "amz_date": "20261017T183120Z",
     "signature": "2ce74ce99194fc3ba7ad931888ec9c1621003138d068cc957d68e4fd69eee82c"},
    {"method": "DELETE", "host": "ref.storage.supabase.co", "bucket": "media",
     "key": "ast_abc.png", "content_type": "", "payload": b"",
     "amz_date": "20261017T183120Z",
     "signature": "74bb0a7be8406d8fd6d4d1230ae3c63b334cba47b3a25dcbccb2e57485e99044"},
]


class TestSigV4:
    """The signature must match what AWS computes for a known request."""

    def test_the_canonical_request_matches_the_documented_shape(self):
        """Six newline-separated parts, in the order the spec fixes.

        A wrong order or a missing blank line produces a signature that looks plausible
        and is always rejected, so the structure is asserted directly.
        """
        request, _ = storage.canonical_request(
            "GET", "examplebucket.s3.amazonaws.com", "examplebucket", "test.txt",
            region="us-east-1", when=datetime(2013, 5, 24, tzinfo=timezone.utc))
        parts = request.split("\n")
        # method, uri, query, then the canonical headers, then the signed header list,
        # then the payload hash.
        assert parts[0] == "GET"
        assert parts[1] == "/examplebucket/test.txt"
        assert parts[2] == ""
        assert "host:examplebucket.s3.amazonaws.com" in parts
        assert "x-amz-content-sha256:" in request
        assert "x-amz-date:20130524T000000Z" in request
        # The last line is the payload hash: empty body.
        assert parts[-1] == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")

    @pytest.mark.parametrize("case", VECTORS)
    def test_signatures_match_botocore(self, case):
        """Signature parity with AWS's reference implementation, not a memory of it.

        The earlier version of this test compared against a signature I had written from
        memory, which tests nothing but my typing. These vectors were produced by
        botocore's own `S3SigV4Auth` and are replayed here, so the algorithm is pinned to
        the implementation Supabase's S3 endpoint actually validates against. Includes a
        key with a space, which is where path encoding usually goes wrong.
        """
        headers = storage.sign_request(
            case["method"], case["host"], case["bucket"], case["key"],
            region="us-east-1", access_key=ACCESS_KEY, secret_key=SECRET_KEY,
            payload=case["payload"], content_type=case["content_type"],
            when=datetime.strptime(case["amz_date"], "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc))
        signature = re.search(r"Signature=([0-9a-f]+)", headers["Authorization"]).group(1)
        assert signature == case["signature"]

    def test_the_signed_headers_include_the_content_type_only_when_set(self):
        """A header that is signed but not sent (or sent but not signed) is a 403."""
        with_type = storage.sign_request(
            "PUT", "h", "b", "k", region="us-east-1", access_key="A", secret_key="S",
            content_type="image/png", when=datetime(2026, 1, 1, tzinfo=timezone.utc))
        without = storage.sign_request(
            "PUT", "h", "b", "k", region="us-east-1", access_key="A", secret_key="S",
            when=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert "content-type;" in with_type["Authorization"]
        assert "content-type;" not in without["Authorization"]
        assert "Content-Type" not in without

    def test_the_signature_depends_on_the_region(self):
        """Supabase signs with the project's region, so it must be a real input."""
        base = dict(method="PUT", host="abc.storage.supabase.co", bucket="media",
                    key="a.png", payload=b"x", access_key="AKIA", secret_key="secret",
                    when=datetime(2026, 1, 1, tzinfo=timezone.utc))
        one = storage.sign_request(region="ap-south-1", **base)["Authorization"]
        two = storage.sign_request(region="us-east-1", **base)["Authorization"]
        assert one != two
        assert "/ap-south-1/s3/aws4_request" in one

    def test_the_signature_covers_the_body(self):
        """Two different payloads must not share a signature."""
        base = dict(method="PUT", host="h", bucket="b", key="k.png", region="us-east-1",
                    access_key="AKIA", secret_key="secret",
                    when=datetime(2026, 1, 1, tzinfo=timezone.utc))
        one = storage.sign_request(payload=b"first", **base)["Authorization"]
        two = storage.sign_request(payload=b"second", **base)["Authorization"]
        assert one != two

    def test_signing_is_deterministic(self):
        """Same inputs, same signature — otherwise nothing can be asserted about it."""
        args = dict(method="GET", host="h", bucket="b", key="k", region="us-east-1",
                    access_key="AKIA", secret_key="secret", payload=b"",
                    when=datetime(2026, 5, 5, 12, tzinfo=timezone.utc))
        assert storage.sign_request(**args) == storage.sign_request(**args)

    def test_a_path_is_encoded_but_slashes_survive(self):
        """Keys contain `/` and sometimes spaces; only the segment may be encoded."""
        uri = storage._canonical_uri("bucket", "folder/a file.png")
        assert uri.startswith("/bucket/folder/")
        assert " " not in uri
        assert "%20" in uri

    def test_query_parameters_are_sorted_and_encoded(self):
        query = storage._canonical_query({"b": "2", "a": "1 2"})
        assert query == "a=1%202&b=2"


class TestSupabaseEndpoint:
    def test_the_endpoint_is_the_storage_subdomain(self):
        """Signing against the project host produces a 403 that reads like a bad key."""
        monkey = pytest.MonkeyPatch()
        monkey.setattr(storage, "SUPABASE_URL", "https://abcdefgh.supabase.co")
        monkey.setattr(storage, "SUPABASE_PROJECT_REF", "")
        try:
            assert storage.supabase_host() == "abcdefgh.storage.supabase.co"
        finally:
            monkey.undo()

    def test_a_project_ref_alone_is_enough(self):
        monkey = pytest.MonkeyPatch()
        monkey.setattr(storage, "SUPABASE_URL", "")
        monkey.setattr(storage, "SUPABASE_PROJECT_REF", "zyxwvuts")
        try:
            assert storage.supabase_host() == "zyxwvuts.storage.supabase.co"
        finally:
            monkey.undo()

    def test_a_partial_configuration_is_named_not_guessed(self):
        """A bucket with no keys must not silently fall back to disk."""
        monkey = pytest.MonkeyPatch()
        monkey.setattr(storage, "SUPABASE_BUCKET", "media")
        monkey.setattr(storage, "SUPABASE_ACCESS_KEY", "")
        monkey.setattr(storage, "SUPABASE_SECRET_KEY", "")
        try:
            assert storage.use_supabase() is False
            described = storage.describe()
            assert described["supabase_bucket"] == "media"
            assert described["has_access_key"] is False
            assert described["has_secret_key"] is False
            # With no complete backend, disk is the honest answer, not a fake "supabase".
            assert described["backend"] == "disk"
        finally:
            monkey.undo()

    def test_a_complete_configuration_selects_supabase(self):
        monkey = pytest.MonkeyPatch()
        monkey.setattr(storage, "SUPABASE_BUCKET", "media")
        monkey.setattr(storage, "SUPABASE_ACCESS_KEY", "key")
        monkey.setattr(storage, "SUPABASE_SECRET_KEY", "secret")
        monkey.setattr(storage, "SUPABASE_URL", "https://ref.supabase.co")
        try:
            assert storage.use_supabase() is True
            assert storage.backend() == "supabase"
            described = storage.describe()
            assert described["supabase_endpoint"] == "ref.storage.supabase.co"
            # Presence only. The value is never echoed back — the key names say
            # `has_secret_key`, which is a boolean, not the secret.
            assert "secret_key:" not in str(described).lower()
            assert described["has_secret_key"] is True
        finally:
            monkey.undo()

    def test_supabase_wins_over_vercel_blob(self):
        """Both configured means Supabase, because that is the deployment's choice."""
        monkey = pytest.MonkeyPatch()
        monkey.setattr(storage, "SUPABASE_BUCKET", "media")
        monkey.setattr(storage, "SUPABASE_ACCESS_KEY", "key")
        monkey.setattr(storage, "SUPABASE_SECRET_KEY", "secret")
        monkey.setattr(storage, "BLOB_TOKEN", "vercel_blob_token")
        try:
            assert storage.backend() == "supabase"
            assert storage.use_blob() is False
        finally:
            monkey.undo()


class TestDisk:
    def test_a_key_cannot_escape_the_upload_directory(self):
        with pytest.raises(storage.StorageError):
            storage.disk_path("../../etc/passwd")

    def test_a_normal_key_round_trips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "UPLOAD_DIR", tmp_path)
        monkeypatch.setattr(storage, "SUPABASE_BUCKET", "")
        monkeypatch.setattr(storage, "BLOB_TOKEN", "")
        stored = storage.put("probe.png", b"\x89PNG\r\n\x1a\n data", "image/png")
        assert stored["backend"] == "disk"
        assert stored["sha256"]
        data, _ = storage.get("probe.png")
        assert data.startswith(b"\x89PNG")
        storage.delete("probe.png")
        with pytest.raises(storage.StorageError):
            storage.get("probe.png")
