"""Test configuration and alias lifecycle without a Pi or a live mDNS network."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from web_server.network_alias import get_ipv4_address, main, normalize_alias, publish_alias, stop_publisher
from shared.config import read_environment
from web_server import app as web_app


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        root_patch = patch("shared.config.PROJECT_ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        env_patch = patch.dict("os.environ", {}, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def test_reads_project_env_and_preserves_explicit_environment(self):
        (self.root / ".env").write_text(
            '# comments and quoted values are supported\n'
            'LEDART_WEB_ALIAS="matrix"\nLEDART_WEB_PORT=8081\nUNSET\n',
        )
        with patch.dict("os.environ", {"LEDART_WEB_PORT": "9090"}):
            environment = read_environment()
        self.assertEqual(environment["LEDART_WEB_ALIAS"], "matrix")
        self.assertEqual(environment["LEDART_WEB_PORT"], "9090")
        self.assertNotIn("UNSET", environment)

    def test_missing_env_file_is_optional_and_empty_alias_is_preserved(self):
        self.assertEqual(read_environment(), {})
        (self.root / ".env").write_text("LEDART_WEB_ALIAS=\n")
        self.assertEqual(read_environment()["LEDART_WEB_ALIAS"], "")

    def test_web_startup_uses_env_file_and_cli_can_override(self):
        (self.root / ".env").write_text("LEDART_WEB_HOST=127.0.0.1\nLEDART_WEB_PORT=8081\n")
        for arguments, expected_port in (([], 8081), (["--port", "9090"], 9090)):
            with self.subTest(arguments=arguments), \
                    patch("web_server.app.check_upload_storage"), \
                    patch("web_server.app.create_app") as create_app, \
                    patch("waitress.serve") as serve:
                create_app.return_value.config = {"MAX_CONTENT_LENGTH": 1024}
                web_app.main(arguments)
                self.assertEqual(serve.call_args.kwargs["host"], "127.0.0.1")
                self.assertEqual(serve.call_args.kwargs["port"], expected_port)


class NetworkAliasTests(unittest.TestCase):
    def test_alias_validation(self):
        for value in ("matrix", "Matrix.local", " matrix.local. "):
            self.assertEqual(normalize_alias(value), "matrix.local")
        self.assertEqual(normalize_alias(""), "")
        self.assertEqual(normalize_alias("  "), "")
        self.assertEqual(normalize_alias("x" * 63), "x" * 63 + ".local")
        for value in ("-matrix", "matrix-", "x" * 64, "a.b", "foo.com", "my light",
                      "http://matrix", "matrix:8080", "$(hostname)", "é", ".local"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_alias(value)

    def test_ip_selection_waits_for_an_active_lan_address(self):
        interfaces = [
            {"operstate": "DOWN", "addr_info": [
                {"family": "inet", "scope": "global", "local": "192.168.1.10"},
            ]},
            {"operstate": "UP", "addr_info": [
                {"family": "inet", "scope": "global", "local": "127.0.0.1"},
                {"family": "inet", "scope": "link", "local": "169.254.1.1"},
                {"family": "inet", "scope": "global", "local": "192.168.1.20"},
            ]},
        ]
        with patch("web_server.network_alias.subprocess.run", return_value=Mock(stdout=json.dumps(interfaces))):
            self.assertEqual(get_ipv4_address(), "192.168.1.20")
        with patch("web_server.network_alias.subprocess.run", return_value=Mock(stdout="[]")):
            self.assertIsNone(get_ipv4_address())

    def test_republishes_after_dhcp_change_and_network_reconnect_and_cleans_up(self):
        stopped = Mock()
        stopped.is_set.side_effect = [False] * 6 + [True]
        addresses = [None, "192.168.1.20", "192.168.1.20", "192.168.1.21", None, "192.168.1.22"]
        publishers = [Mock(), Mock(), Mock()]
        for publisher in publishers:
            publisher.poll.return_value = None
        with patch("web_server.network_alias.get_ipv4_address", side_effect=addresses), \
                patch("web_server.network_alias.subprocess.Popen", side_effect=publishers) as popen:
            publish_alias("matrix.local", stopped)
        self.assertEqual(popen.call_args_list, [
            call(["avahi-publish-address", "--no-reverse", "--no-fail", "matrix.local", address])
            for address in ("192.168.1.20", "192.168.1.21", "192.168.1.22")
        ])
        for publisher in publishers:
            publisher.terminate.assert_called_once()
            publisher.wait.assert_called_once_with(timeout=5)

    def test_publisher_failure_is_reported_for_systemd_to_retry(self):
        stopped = Mock()
        stopped.is_set.return_value = False
        publisher = Mock(returncode=1)
        publisher.poll.return_value = 1
        with patch("web_server.network_alias.get_ipv4_address", return_value="192.168.1.20"), \
                patch("web_server.network_alias.subprocess.Popen", return_value=publisher), \
                self.assertRaisesRegex(RuntimeError, "unique LEDART_WEB_ALIAS"):
            publish_alias("matrix.local", stopped)

    def test_network_query_failure_still_withdraws_published_alias(self):
        stopped = Mock()
        stopped.is_set.return_value = False
        publisher = Mock()
        publisher.poll.return_value = None
        failure = subprocess.CalledProcessError(1, "ip")
        with patch("web_server.network_alias.get_ipv4_address", side_effect=["192.168.1.20", failure]), \
                patch("web_server.network_alias.subprocess.Popen", return_value=publisher), \
                self.assertRaises(subprocess.CalledProcessError):
            publish_alias("matrix.local", stopped)
        publisher.terminate.assert_called_once()

    def test_shutdown_kills_a_stuck_publisher(self):
        publisher = Mock()
        publisher.poll.return_value = None
        publisher.wait.side_effect = [subprocess.TimeoutExpired("avahi-publish-address", 5), 0]
        stop_publisher(publisher)
        publisher.terminate.assert_called_once()
        publisher.kill.assert_called_once()

    def test_disabled_alias_and_config_check_never_start_publisher(self):
        for environment, arguments in (({}, []), ({"LEDART_WEB_ALIAS": "matrix"}, ["--check-config"])):
            with self.subTest(environment=environment), \
                    patch("web_server.network_alias.read_environment", return_value=environment), \
                    patch("web_server.network_alias.publish_alias") as publish:
                main(arguments)
                publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
