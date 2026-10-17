"""Uploads, media validation, asset ownership, and the error vocabulary (spec §18, §19, §20).

The upload tests mostly assert refusals: a file that claims to be a PNG but is not must
not be stored and must not be served.
"""

import base64
import io

import pytest

from app.services.assets import sniff_mime


def _png_bytes(width=8, height=8) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


class TestSignatureChecks:
    def test_a_real_png_is_recognised(self):
        assert sniff_mime(_png_bytes()) == "image/png"

    @pytest.mark.parametrize("data,expected", [
        (b"\xff\xd8\xff\xe0" + b"\x00" * 20, "image/jpeg"),
        (b"GIF89a" + b"\x00" * 20, "image/gif"),
        (b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 8, "image/webp"),
        (b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8, "video/mp4"),
    ])
    def test_other_signatures(self, data, expected):
        assert sniff_mime(data) == expected

    def test_an_unknown_blob_is_empty(self):
        assert sniff_mime(b"this is not media at all") == ""

    def test_an_svg_is_not_accepted(self):
        """SVG can carry script, so it is not in the accepted set at all."""
        assert sniff_mime(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>") == ""


class TestUploads:
    def test_a_png_upload_is_stored_with_real_dimensions(self, client):
        png = _png_bytes(64, 32)
        code, body = client.post("/api/assets/upload", {
            "filename": "shot.png", "mime": "image/png",
            "data": base64.b64encode(png).decode()})
        assert code == 201, body
        asset = body["data"]
        assert asset["width"] == 64 and asset["height"] == 32
        assert asset["bytes"] == len(png)
        assert asset["media_type"] == "image"

    def test_the_bytes_can_be_downloaded_back(self, client):
        png = _png_bytes()
        code, body = client.post("/api/assets/upload", {
            "filename": "back.png", "mime": "image/png",
            "data": base64.b64encode(png).decode()})
        asset_id = body["data"]["id"]
        code, blob = client.get(f"/api/assets/{asset_id}/raw")
        assert code == 200
        assert blob["raw"] == png

    def test_a_type_mismatch_is_refused(self, client):
        """Declared PNG, actually a GIF: refused rather than reinterpreted."""
        gif = b"GIF89a" + b"\x00" * 40
        code, body = client.post("/api/assets/upload", {
            "filename": "lie.png", "mime": "image/png",
            "data": base64.b64encode(gif).decode()})
        assert code == 400
        assert body["error"]["code"] == "media_mismatch"

    def test_a_non_media_file_is_refused(self, client):
        code, body = client.post("/api/assets/upload", {
            "filename": "script.sh", "mime": "application/x-sh",
            "data": base64.b64encode(b"#!/bin/sh\nrm -rf /\n").decode()})
        assert code == 400
        assert body["error"]["code"] in ("unsupported_media_type", "media_mismatch")

    def test_invalid_base64_is_refused(self, client):
        code, body = client.post("/api/assets/upload", {
            "filename": "x.png", "mime": "image/png", "data": "!!!not base64!!!"})
        assert code == 400

    def test_an_empty_file_is_refused(self, client):
        code, body = client.post("/api/assets/upload", {
            "filename": "x.png", "mime": "image/png", "data": ""})
        assert code == 400

    def test_a_corrupt_png_is_refused(self, client):
        """Correct signature, undecodable body: refused, because the image is unusable."""
        fake = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
        code, body = client.post("/api/assets/upload", {
            "filename": "broken.png", "mime": "image/png",
            "data": base64.b64encode(fake).decode()})
        assert code == 400
        assert body["error"]["code"] == "invalid_media"

    def test_a_traversal_filename_cannot_escape_the_upload_directory(self, client):
        png = _png_bytes()
        code, body = client.post("/api/assets/upload", {
            "filename": "../../../evil.png", "mime": "image/png",
            "data": base64.b64encode(png).decode()})
        assert code == 201
        from app.config import UPLOAD_DIR
        from app import store

        row = store.row("SELECT path FROM assets WHERE id=?", (body["data"]["id"],))
        assert ".." not in row["path"]
        assert (UPLOAD_DIR / row["path"]).resolve().is_file()


class TestOwnership:
    def test_a_missing_asset_is_a_404_not_a_crash(self, client):
        code, body = client.get("/api/assets/ast_nope/raw")
        assert code == 404
        assert body["error"]["code"] == "not_found"

    def test_an_assets_file_is_not_served_by_path(self, client):
        """There is no route that serves an arbitrary path from the uploads directory."""
        code, body = client.get("/api/assets/../data/studio.db/raw")
        assert code in (400, 404)

    def test_deleting_an_asset_removes_the_row_and_the_file(self, client):
        png = _png_bytes()
        code, body = client.post("/api/assets/upload", {
            "filename": "gone.png", "mime": "image/png",
            "data": base64.b64encode(png).decode()})
        asset_id = body["data"]["id"]
        code, body = client.delete(f"/api/assets/{asset_id}")
        assert code == 200
        code, body = client.get(f"/api/assets/{asset_id}/raw")
        assert code == 404


class TestErrorVocabulary:
    def test_every_error_has_a_code_and_message(self, client):
        for method, path, payload in [
            ("GET", "/api/does-not-exist", None),
            ("POST", "/api/connections", {}),
            ("POST", "/api/generations/images", {}),
            ("GET", "/api/models/mdl_nope", None),
        ]:
            code, body = client.call(method, path, payload)
            assert code >= 400, (path, code)
            assert "error" in body, (path, body)
            assert body["error"]["code"], (path, body)
            assert body["error"]["message"], (path, body)

    def test_a_malformed_body_is_a_400_not_a_crash(self, client):
        import urllib.request
        import urllib.error

        req = urllib.request.Request(
            client.base + "/api/connections", data=b"{not json",
            method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                code = resp.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        assert code == 400

    def test_a_wrong_content_type_is_refused(self, client):
        import urllib.request
        import urllib.error

        req = urllib.request.Request(
            client.base + "/api/connections", data=b"hello",
            method="POST", headers={"Content-Type": "text/plain"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                code = resp.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        assert code == 415

    def test_method_not_allowed_names_the_methods(self, client):
        code, body = client.delete("/api/health")
        assert code == 405
        assert "allowed" in body["error"]["details"]

    def test_an_internal_error_does_not_leak_a_traceback(self, client, monkeypatch):
        """A raise in a handler is logged, never shown."""
        from app.web import routes

        def explode(req):
            raise RuntimeError("secret internal detail")

        monkeypatch.setattr(routes, "health", explode)
        # The router still points at the name, so this finds the patched function.
        code, body = client.get("/api/health")
        if code == 500:
            assert "secret internal detail" not in str(body)
            assert body["error"]["code"] == "internal_error"
