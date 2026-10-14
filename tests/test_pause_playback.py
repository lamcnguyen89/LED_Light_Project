"""Verify pause filtering and panel clearing without Raspberry Pi hardware."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from led_settings import SETTINGS_FILENAME, write_media_paused


class PausePlaybackTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "pause_test_player", Path(__file__).resolve().parents[1] / "LED_Code.py",
        )
        self.player = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"rpi_ws281x": Mock()}):
            spec.loader.exec_module(self.player)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def test_pause_removes_all_media_types_from_live_snapshot_and_unpause_restores(self):
        for name in ("still.png", "animation.gif", "movie.mp4"):
            path = self.folder / name
            path.write_bytes(b"media")
            before = self.player.scan_display_files(self.folder)
            self.assertIn(path, before)
            write_media_paused(self.folder / SETTINGS_FILENAME, name, True)
            self.assertNotIn(path, self.player.scan_display_files(self.folder))
            self.assertTrue(path.exists())
            write_media_paused(self.folder / SETTINGS_FILENAME, name, False)
            self.assertEqual(self.player.scan_display_files(self.folder), before)

    def test_all_paused_clears_panel_instead_of_leaving_last_frame(self):
        (self.folder / "still.png").write_bytes(b"media")
        write_media_paused(self.folder / SETTINGS_FILENAME, "still.png", True)
        with patch.object(self.player, "INPUT_FOLDER", self.folder), \
                patch.object(self.player, "display_image") as display, \
                patch.object(self.player, "wait_for_stop", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.player.main()
        image = display.call_args.args[1]
        self.assertEqual(image.size, (20, 15))
        self.assertIsNone(image.getbbox())
