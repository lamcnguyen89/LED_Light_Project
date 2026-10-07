"""Brightness API, persistence, and hardware-independent playback checks."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from led_settings import (
    DEFAULT_BRIGHTNESS, SETTINGS_FILENAME, BrightnessController,
    read_brightness, write_brightness,
)
from media_player import wait_for_stop
from web_ui import create_app


class BrightnessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.path = self.folder / SETTINGS_FILENAME
        self.app = create_app(self.folder, {"TESTING": True, "SECRET_KEY": "test"})
        self.client = self.app.test_client()
        self.client.get("/")
        with self.client.session_transaction() as session:
            self.headers = {"X-CSRF-Token": session["csrf_token"]}

    def save(self, value):
        return self.client.post("/api/brightness", json={"brightness": value}, headers=self.headers)

    def test_default_boundaries_persistence_and_hidden_from_gallery(self):
        self.assertEqual(self.client.get("/api/brightness").json, {"brightness": DEFAULT_BRIGHTNESS})
        for value in (0, 128, 255):
            self.assertEqual(self.save(value).json, {"brightness": value})
            self.assertEqual(read_brightness(self.path), value)
        other = create_app(self.folder, {"TESTING": True}).test_client()
        self.assertEqual(other.get("/api/brightness").json["brightness"], 255)
        self.assertEqual(self.client.get("/api/files").json["files"], [])
        self.assertEqual(list(self.folder.iterdir()), [self.path])

    def test_invalid_requests_do_not_change_saved_setting(self):
        self.save(90)
        for value in (-1, 256, True, False, 10.5, "100", None, [], {}):
            with self.subTest(value=value):
                self.assertEqual(self.save(value).status_code, 400)
                self.assertEqual(read_brightness(self.path), 90)
        for payload in (None, [], 100):
            self.assertEqual(self.client.post("/api/brightness", json=payload, headers=self.headers).status_code, 400)

    def test_csrf_and_storage_error(self):
        self.assertEqual(self.client.post("/api/brightness", json={"brightness": 0}).status_code, 403)
        with patch("web_ui.write_brightness", side_effect=PermissionError):
            self.assertEqual(self.save(90).status_code, 503)

    def test_missing_or_invalid_file_keeps_current_value(self):
        self.assertEqual(read_brightness(self.path, 80), 80)
        for contents in ('broken', '{"brightness":256}', '[]', '{"brightness":true}'):
            self.path.write_text(contents)
            self.assertEqual(read_brightness(self.path, 80), 80)

    def test_failed_atomic_replace_preserves_old_setting_and_cleans_temp(self):
        write_brightness(self.path, 100)
        with patch("led_settings.os.replace", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                write_brightness(self.path, 50)
        self.assertEqual(read_brightness(self.path), 100)
        self.assertEqual(list(self.folder.iterdir()), [self.path])

    def test_playback_wait_applies_brightness_without_stopping(self):
        write_brightness(self.path, 0)
        strip = Mock()
        controller = BrightnessController(strip, self.path, 255)
        with patch("media_player.monotonic", side_effect=[0, 0, .05, .1]), patch("media_player.sleep") as sleep:
            self.assertFalse(wait_for_stop(.1, lambda: controller.update() or False))
        strip.setBrightness.assert_called_once_with(0)
        strip.show.assert_called_once_with()
        self.assertTrue(all(call.args[0] <= .05 for call in sleep.call_args_list))

    def test_poll_throttles_and_only_updates_changed_values(self):
        strip = Mock()
        controller = BrightnessController(strip, self.path, 255)
        with patch("led_settings.monotonic", side_effect=[0, .01, .06, .12]):
            controller.update()
            write_brightness(self.path, 40)
            controller.update()
            strip.setBrightness.assert_not_called()
            controller.update()
            controller.update()
        strip.setBrightness.assert_called_once_with(40)
        strip.show.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
