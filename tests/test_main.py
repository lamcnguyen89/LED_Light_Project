"""Exercise the common entry point, privileges, and process supervision."""
import contextlib
from io import StringIO
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, call, patch

import main as launcher
from shared.config import INPUT_FOLDER, PROJECT_ROOT
from web_server.app import create_app


class EntryPointTests(unittest.TestCase):
    def test_web_and_alias_dispatch_and_validation(self):
        with patch("web_server.app.main") as run_web:
            self.assertEqual(launcher.main(["--service", "web", "--port", "9090", "--check-storage"]), 0)
        run_web.assert_called_once_with(["--port", "9090", "--check-storage"])
        with patch("web_server.network_alias.main") as run_alias:
            self.assertEqual(launcher.main(["--service", "alias", "--check-config"]), 0)
        run_alias.assert_called_once_with(["--check-config"])
        for arguments in (["--check-storage"], ["--check-config"],
                          ["--service", "led", "--port", "9090"],
                          ["--service", "web", "--web-user", "pi"]):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(StringIO()), \
                    self.assertRaises(SystemExit) as error:
                launcher.main(arguments)
            self.assertEqual(error.exception.code, 2)

    def test_led_dispatch_does_not_start_web(self):
        player = Mock()
        with patch.dict(sys.modules, {"led_backend.player": player}):
            self.assertEqual(launcher.main(["--service", "led"]), 0)
        player.main.assert_called_once_with()

    def test_shared_paths_and_moved_assets(self):
        self.assertEqual(PROJECT_ROOT, Path(__file__).resolve().parents[1])
        self.assertEqual(INPUT_FOLDER, PROJECT_ROOT / "Input")
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, {"TESTING": True, "WEB_PASSWORD": ""})
            self.assertEqual(Path(app.root_path), PROJECT_ROOT / "web_server")
            client = app.test_client()
            self.assertEqual(client.get("/").status_code, 200)
            for path in ("/static/web_ui.css", "/static/web_ui.js"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                response.close()

    def test_all_mode_requires_gpio_privileges_before_starting_any_child(self):
        with patch("main.os.geteuid", return_value=1000), \
                patch("main.subprocess.Popen") as popen, \
                self.assertRaisesRegex(ValueError, "requires root"):
            launcher.run_all([])
        popen.assert_not_called()

    def test_root_launcher_drops_web_groups_and_uid(self):
        account = Mock(pw_uid=1000, pw_gid=100, pw_name="pi")
        with patch("main.os.geteuid", return_value=0), \
                patch("main.pwd.getpwnam", return_value=account), \
                patch("main.os.getgrouplist", return_value=[100, 200]):
            self.assertEqual(launcher.web_credentials("pi"), {
                "user": 1000, "group": 100, "extra_groups": [100, 200],
            })
        with patch("main.os.geteuid", return_value=0), \
                patch("main.pwd.getpwnam", return_value=Mock(pw_uid=0)), \
                self.assertRaisesRegex(ValueError, "unprivileged"):
            launcher.web_credentials("unsafe")


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name) / "Input"
        self.credentials = {"user": 1000, "group": 100, "extra_groups": [100]}
        self.stopped = Mock()
        self.stopped.wait.return_value = True
        patches = [
            patch("main.os.geteuid", return_value=0),
            patch("main.web_credentials", return_value=self.credentials),
            patch("shared.config.INPUT_FOLDER", self.folder),
            patch("shared.config.read_environment", return_value={"LEDART_WEB_ALIAS": "matrix"}),
            patch("main.os.chown"),
            patch("main.signal.signal"),
            patch("main.Event", return_value=self.stopped),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_starts_all_services_with_correct_privileges_and_stops_together(self):
        children = [Mock(pid=101), Mock(pid=102), Mock(pid=103)]
        with patch("main.subprocess.Popen", side_effect=children) as popen, \
                patch("main.stop_children") as stop:
            self.assertEqual(launcher.run_all(["--port", "9090"]), 0)
        self.assertEqual([args.args[0][4:] for args in popen.call_args_list], [
            ["web", "--port", "9090"], ["led"], ["alias"],
        ])
        for index, arguments in enumerate(popen.call_args_list):
            self.assertTrue(arguments.kwargs["start_new_session"])
            self.assertEqual(arguments.kwargs["cwd"], PROJECT_ROOT)
            if index != 1:
                for key, value in self.credentials.items():
                    self.assertEqual(arguments.kwargs[key], value)
            else:
                self.assertNotIn("user", arguments.kwargs)
        stop.assert_called_once_with(children)

    def test_blank_alias_skips_its_process_and_existing_folder_is_preserved(self):
        self.folder.mkdir()
        media = self.folder / "existing.png"
        media.write_bytes(b"existing")
        with patch("shared.config.read_environment", return_value={}), \
                patch("main.subprocess.Popen", side_effect=[Mock(pid=101), Mock(pid=102)]) as popen, \
                patch("main.stop_children"), patch("main.os.chown") as chown:
            self.assertEqual(launcher.run_all([]), 0)
        self.assertEqual(popen.call_count, 2)
        chown.assert_not_called()
        self.assertEqual(media.read_bytes(), b"existing")

    def test_child_failure_stops_siblings_and_returns_nonzero(self):
        self.stopped.wait.return_value = False
        web = Mock(pid=101)
        web.poll.return_value = 2
        children = [web, Mock(pid=102), Mock(pid=103)]
        with patch("main.subprocess.Popen", side_effect=children), \
                patch("main.stop_children") as stop:
            self.assertEqual(launcher.run_all([]), 2)
        stop.assert_called_once_with(children)

    def test_partial_startup_failure_cleans_up_already_started_children(self):
        web = Mock(pid=101)
        with patch("main.subprocess.Popen", side_effect=[web, OSError("startup failed")]), \
                patch("main.stop_children") as stop, self.assertRaises(OSError):
            launcher.run_all([])
        stop.assert_called_once_with([web])

    def test_shutdown_signals_process_groups_and_kills_stuck_children(self):
        first = Mock(pid=101)
        second = Mock(pid=102)
        second.wait.side_effect = [subprocess.TimeoutExpired("child", 5), 0]
        with patch("main.os.killpg") as kill:
            launcher.stop_children([first, second])
        self.assertEqual(kill.call_args_list, [
            call(101, signal.SIGTERM), call(102, signal.SIGTERM), call(102, signal.SIGKILL),
        ])


if __name__ == "__main__":
    unittest.main()
