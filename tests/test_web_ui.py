"""Run: python -B -m unittest discover -s tests -v"""
import errno
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

from PIL import Image

from web_ui import check_upload_storage, create_app


def image_bytes(format="PNG", animated=False):
    stream = BytesIO()
    first = Image.new("RGB", (40, 30), "red")
    if animated:
        first.save(
            stream, format=format, save_all=True,
            append_images=[Image.new("RGB", (40, 30), "blue")],
            duration=[100, 200], loop=0,
        )
    else:
        first.save(stream, format=format)
    stream.seek(0)
    return stream


class WebUITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name) / "Input"
        self.app = create_app(self.folder, {"TESTING": True, "SECRET_KEY": "test-secret"})
        self.client = self.app.test_client()
        self.client.get("/")
        with self.client.session_transaction() as session:
            self.headers = {"X-CSRF-Token": session["csrf_token"]}

    def upload(self, name="test.png", contents=None):
        return self.client.post(
            "/api/upload", headers=self.headers,
            data={"files": (contents or image_bytes(), name)},
        )

    def test_storage_check_preserves_media_and_cleans_scratch_files(self):
        media = self.folder / "existing.gif"
        contents = image_bytes("GIF", animated=True).getvalue()
        media.write_bytes(contents)
        check_upload_storage(self.folder)
        self.assertEqual(media.read_bytes(), contents)
        self.assertEqual(list(self.folder.iterdir()), [media])

    def test_storage_check_reports_read_only_filesystem(self):
        with patch("web_ui.tempfile.TemporaryDirectory", side_effect=OSError(errno.EROFS, "Read-only file system")):
            with self.assertRaisesRegex(RuntimeError, "Upload storage check failed.*install_ledart"):
                check_upload_storage(self.folder)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_storage_check_cleans_up_after_hard_link_failure(self):
        with patch("web_ui.os.link", side_effect=PermissionError(errno.EACCES, "Permission denied")):
            with self.assertRaisesRegex(RuntimeError, "Upload storage check failed"):
                check_upload_storage(self.folder)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_page_assets_and_empty_gallery(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"csrf-token", response.data)
        self.assertEqual(self.client.get("/api/files").json["files"], [])
        for name in ("web_ui.css", "web_ui.js"):
            response = self.client.get("/static/" + name)
            self.assertEqual(response.status_code, 200)
            response.close()

    def test_upload_list_preview_and_delete(self):
        self.assertEqual(self.upload().status_code, 201)
        items = self.client.get("/api/files").json["files"]
        self.assertEqual([item["name"] for item in items], ["test.png"])
        self.assertEqual((items[0]["width"], items[0]["height"]), (40, 30))
        response = self.client.get("/preview/test.png?matrix=1")
        self.assertEqual(response.status_code, 200)
        with Image.open(BytesIO(response.data)) as image:
            self.assertEqual(image.size, (20, 15))
        self.assertEqual(self.client.delete("/api/files/test.png", headers=self.headers).status_code, 200)
        self.assertFalse((self.folder / "test.png").exists())
        self.assertEqual(self.client.get("/preview/test.png").status_code, 404)

    def test_gif_remains_animated_and_original_bytes_are_preserved(self):
        contents = image_bytes("GIF", animated=True)
        original = contents.getvalue()
        self.assertEqual(self.upload("animated.gif", contents).status_code, 201)
        response = self.client.get("/preview/animated.gif")
        self.assertEqual(response.mimetype, "image/gif")
        self.assertEqual(response.data, original)
        response.close()
        with Image.open(BytesIO(original)) as image:
            self.assertEqual(image.n_frames, 2)
        matrix = self.client.get("/preview/animated.gif?matrix=1")
        self.assertEqual(matrix.mimetype, "image/png")

    def test_all_supported_still_formats_have_browser_previews(self):
        for extension, format in (
            ("bmp", "BMP"), ("jpg", "JPEG"), ("jpeg", "JPEG"), ("png", "PNG"),
            ("tif", "TIFF"), ("tiff", "TIFF"), ("webp", "WEBP"),
        ):
            with self.subTest(extension=extension):
                name = "picture." + extension
                self.assertEqual(self.upload(name, image_bytes(format)).status_code, 201)
                response = self.client.get("/preview/" + name)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "image/png")

    def test_duplicate_names_do_not_overwrite(self):
        self.upload()
        original = (self.folder / "test.png").read_bytes()
        result = self.upload()
        self.assertEqual(result.json["saved"], ["test_1.png"])
        self.assertEqual((self.folder / "test.png").read_bytes(), original)
        self.assertFalse(any(path.name.startswith(".upload-") for path in self.folder.iterdir()))

    def test_mixed_batch_reports_valid_and_rejected_files(self):
        result = self.client.post("/api/upload", headers=self.headers, data={
            "files": [(image_bytes(), "one.png"), (BytesIO(b"bad"), "bad.gif")],
        })
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json["saved"], ["one.png"])
        self.assertEqual(result.json["errors"][0]["name"], "bad.gif")
        self.assertEqual([path.name for path in self.folder.iterdir()], ["one.png"])

    def test_invalid_contents_extensions_and_format_mismatch(self):
        for name, contents in (
            ("bad.png", b"not an image"), ("script.html", b"<script>"),
            ("wrong.gif", image_bytes().getvalue()), ("video.mp4", b"video"),
        ):
            with self.subTest(name=name):
                self.assertEqual(self.upload(name, BytesIO(contents)).status_code, 400)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_mutations_require_csrf_and_get_never_deletes(self):
        self.upload()
        self.assertEqual(self.client.delete("/api/files/test.png").status_code, 403)
        self.assertEqual(self.client.post("/api/upload", data={"files": (image_bytes(), "bad.png")}).status_code, 403)
        self.assertEqual(self.client.get("/api/files/test.png").status_code, 405)
        self.assertTrue((self.folder / "test.png").exists())

    def test_request_size_and_image_limits(self):
        self.app.config["MAX_CONTENT_LENGTH"] = 100
        self.assertEqual(self.upload().status_code, 413)
        self.app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
        with patch("web_ui.MAX_PIXELS", 10):
            self.assertEqual(self.upload().status_code, 400)
        with patch("web_ui.MAX_FRAMES", 1):
            self.assertEqual(self.upload("frames.gif", image_bytes("GIF", True)).status_code, 400)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_traversal_upload_is_sanitized_and_delete_cannot_escape(self):
        self.assertEqual(self.upload("../../escape.png").status_code, 201)
        self.assertTrue((self.folder / "escape.png").is_file())
        self.assertFalse((Path(self.temporary.name) / "escape.png").exists())
        outside = Path(self.temporary.name) / "outside.png"
        outside.write_bytes(image_bytes().getvalue())
        for name in ("../outside.png", r"..\outside.png"):
            self.assertEqual(self.client.delete(
                "/api/files/" + quote(name, safe=""), headers=self.headers,
            ).status_code, 404)
        self.assertTrue(outside.exists())

    def test_symlinks_are_not_listed_served_or_deleted(self):
        outside = Path(self.temporary.name) / "outside.png"
        outside.write_bytes(image_bytes().getvalue())
        link = self.folder / "link.png"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("Creating symlinks requires privileges on this machine.")
        self.assertEqual(self.client.get("/api/files").json["files"], [])
        self.assertEqual(self.client.get("/preview/link.png").status_code, 404)
        self.assertEqual(self.client.delete("/api/files/link.png", headers=self.headers).status_code, 404)
        self.assertTrue(outside.exists())

    def test_existing_corrupt_image_can_be_deleted_and_videos_are_untouched(self):
        (self.folder / "broken.png").write_bytes(b"broken")
        (self.folder / "movie.mp4").write_bytes(b"existing video")
        items = self.client.get("/api/files").json["files"]
        self.assertEqual([item["name"] for item in items], ["broken.png"])
        self.assertIn("error", items[0])
        self.assertEqual(self.client.get("/preview/broken.png").status_code, 415)
        self.assertEqual(self.client.delete("/api/files/broken.png", headers=self.headers).status_code, 200)
        self.assertTrue((self.folder / "movie.mp4").exists())

    def test_delete_missing_file_and_empty_upload(self):
        self.assertEqual(self.client.delete("/api/files/missing.png", headers=self.headers).status_code, 404)
        self.assertEqual(self.client.post("/api/upload", headers=self.headers).status_code, 400)


if __name__ == "__main__":
    unittest.main()
