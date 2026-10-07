"""Display duration API and shared settings regression checks."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from led_settings import (
    DEFAULT_DISPLAY_SECONDS, SETTINGS_FILENAME,
    read_brightness, read_display_seconds, write_brightness, write_display_seconds,
)
from web_ui import create_app


class DurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.path = self.folder / SETTINGS_FILENAME
        self.client = create_app(self.folder, {"TESTING": True}).test_client()
        self.client.get("/")
        with self.client.session_transaction() as session:
            self.headers = {"X-CSRF-Token": session["csrf_token"]}

    def save(self, value):
        return self.client.post(
            "/api/display-seconds", json={"display_seconds": value}, headers=self.headers,
        )

    def test_duration_persists_and_brightness_updates_preserve_it(self):
        self.assertEqual(self.client.get("/api/display-seconds").json,
                         {"display_seconds": DEFAULT_DISPLAY_SECONDS})
        write_brightness(self.path, 90)
        for value in (1, 30, 86400):
            self.assertEqual(self.save(value).json, {"display_seconds": value})
            self.assertEqual(read_display_seconds(self.path), value)
            self.assertEqual(read_brightness(self.path), 90)
        self.client.post("/api/brightness", json={"brightness": 40}, headers=self.headers)
        self.assertEqual(read_display_seconds(self.path), 86400)
        other = create_app(self.folder, {"TESTING": True}).test_client()
        self.assertEqual(other.get("/api/display-seconds").json["display_seconds"], 86400)
        self.assertEqual(self.client.get("/api/files").json["files"], [])

    def test_invalid_requests_preserve_saved_duration(self):
        self.save(30)
        for value in (0, -1, 86401, True, False, 1.5, "30", None, [], {}):
            with self.subTest(value=value):
                self.assertEqual(self.save(value).status_code, 400)
                self.assertEqual(read_display_seconds(self.path), 30)
        for payload in (None, [], 100, {}):
            self.assertEqual(self.client.post(
                "/api/display-seconds", json=payload, headers=self.headers,
            ).status_code, 400)

    def test_csrf_and_storage_errors(self):
        self.assertEqual(self.client.post(
            "/api/display-seconds", json={"display_seconds": 30},
        ).status_code, 403)
        with patch("web_ui.write_display_seconds", side_effect=PermissionError):
            self.assertEqual(self.save(30).status_code, 503)

    def test_invalid_settings_fall_back_and_writes_repair_them(self):
        self.assertEqual(read_display_seconds(self.path, 60), 60)
        for contents in ('broken', '[]', '{"display_seconds":0}', '{"display_seconds":true}'):
            self.path.write_text(contents)
            self.assertEqual(read_display_seconds(self.path, 60), 60)
            write_display_seconds(self.path, 30)
            self.assertEqual(read_display_seconds(self.path), 30)

    def test_failed_replace_preserves_both_settings(self):
        write_brightness(self.path, 90)
        write_display_seconds(self.path, 30)
        with patch("led_settings.os.replace", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                write_display_seconds(self.path, 60)
        self.assertEqual(read_display_seconds(self.path), 30)
        self.assertEqual(read_brightness(self.path), 90)
        self.assertEqual(list(self.folder.iterdir()), [self.path])


if __name__ == "__main__":
    unittest.main()
