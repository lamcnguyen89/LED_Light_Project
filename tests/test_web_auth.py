"""Verify password-only sign-in, signed sessions, and protected controls."""
from io import BytesIO
from pathlib import Path
import tempfile
from time import time
import unittest
from unittest.mock import patch

from PIL import Image

from shared.settings import SETTINGS_FILENAME, read_brightness, read_display_seconds, read_paused_files
from web_server.app import create_app


def unlock(client, password="test-password"):
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    return client.post("/login", data={"password": password, "csrf_token": csrf})


class WebAuthenticationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name) / "Input"
        self.app = create_app(self.folder, {
            "TESTING": True, "SECRET_KEY": "test-secret", "WEB_PASSWORD": "test-password",
        })
        self.client = self.app.test_client()
        Image.new("RGB", (20, 15), "red").save(self.folder / "existing.png")

    def login(self):
        response = unlock(self.client)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/")
        self.assertIn(b'id="gallery"', self.client.get("/").data)
        with self.client.session_transaction() as session:
            self.assertEqual(session["user"], "admin")
            self.assertNotIn("test-password", session.values())
            return {"X-CSRF-Token": session["csrf_token"]}

    def test_locked_page_has_only_password_dialog_and_public_assets(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="login-dialog"', response.data)
        self.assertIn(b'type="password"', response.data)
        self.assertNotIn(b'name="username"', response.data)
        self.assertNotIn(b'id="gallery"', response.data)
        self.assertNotIn(b"existing.png", response.data)
        self.assertNotIn(b"test-password", response.data)
        self.assertNotIn("WWW-Authenticate", response.headers)
        for path in ("/static/login.js", "/static/web_ui.css", "/static/web_ui.js"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b"test-password", response.data)
            response.close()

    def test_previews_and_api_routes_require_an_authenticated_session(self):
        routes = [
            ("GET", "/preview/existing.png"), ("GET", "/preview/existing.png?matrix=1"),
            ("GET", "/api/files"), ("GET", "/api/brightness"),
            ("GET", "/api/display-seconds"), ("POST", "/api/brightness"),
            ("POST", "/api/display-seconds"), ("POST", "/api/upload"),
            ("POST", "/api/files/existing.png/pause"), ("DELETE", "/api/files/existing.png"),
        ]
        for method, path in routes:
            with self.subTest(method=method, path=path):
                response = self.client.open(path, method=method)
                self.assertEqual(response.status_code, 401)
                self.assertNotIn("WWW-Authenticate", response.headers)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertNotIn(b"test-password", response.data)
        # Previously cached Basic credentials must not bypass the session gate.
        response = self.client.get("/api/files", headers={"Authorization": "Basic YWRtaW46dGVzdC1wYXNzd29yZA=="})
        self.assertEqual(response.status_code, 401)
        self.assertTrue((self.folder / "existing.png").exists())
        self.assertFalse((self.folder / SETTINGS_FILENAME).exists())

    def test_wrong_password_and_missing_login_csrf_do_not_authenticate(self):
        for password in ("wrong", ""):
            response = unlock(self.client, password)
            self.assertEqual(response.status_code, 401)
            self.assertIn(b"Incorrect password", response.data)
            self.assertNotIn(b"test-password", response.data)
            self.assertNotIn("WWW-Authenticate", response.headers)
            self.assertEqual(self.client.get("/api/files").status_code, 401)
        response = self.client.post("/login", data={"password": "test-password"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/api/files").status_code, 401)

    def test_login_rotates_csrf_and_valid_session_still_requires_it_for_changes(self):
        self.client.get("/")
        with self.client.session_transaction() as session:
            previous_csrf = session["csrf_token"]
        headers = self.login()
        self.assertNotEqual(headers["X-CSRF-Token"], previous_csrf)
        for invalid_headers in ({}, {"X-CSRF-Token": previous_csrf}):
            response = self.client.post("/api/brightness", json={"brightness": 10}, headers=invalid_headers)
            self.assertEqual(response.status_code, 403)
        self.assertFalse((self.folder / SETTINGS_FILENAME).exists())

    def test_authenticated_controls_upload_and_delete_work(self):
        headers = self.login()
        for path, payload in (("/api/brightness", {"brightness": 25}),
                              ("/api/display-seconds", {"display_seconds": 30}),
                              ("/api/files/existing.png/pause", {"paused": True})):
            self.assertEqual(self.client.post(path, json=payload, headers=headers).status_code, 200)
        self.assertEqual(read_brightness(self.folder / SETTINGS_FILENAME), 25)
        self.assertEqual(read_display_seconds(self.folder / SETTINGS_FILENAME), 30)
        self.assertEqual(read_paused_files(self.folder / SETTINGS_FILENAME), {"existing.png"})
        image = BytesIO()
        Image.new("RGB", (20, 15), "blue").save(image, format="PNG")
        image.seek(0)
        response = self.client.post("/api/upload", headers=headers, data={"files": (image, "new.png")})
        self.assertEqual(response.status_code, 201)
        self.assertTrue((self.folder / "new.png").exists())
        self.assertEqual(self.client.delete("/api/files/new.png", headers=headers).status_code, 200)
        self.assertFalse((self.folder / "new.png").exists())
        for path in ("/", "/api/files", "/preview/existing.png"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertNotIn(b"test-password", response.data)
            response.close()

    def test_lock_clears_authentication_and_requires_csrf(self):
        headers = self.login()
        self.assertEqual(self.client.post("/logout").status_code, 403)
        self.assertEqual(self.client.get("/api/files").status_code, 200)
        response = self.client.post("/logout", data={"csrf_token": headers["X-CSRF-Token"]})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.client.get("/api/files").status_code, 401)
        self.assertIn(b'id="login-dialog"', self.client.get("/").data)

    def test_changed_password_invalidates_the_old_session(self):
        self.login()
        self.app.config["WEB_PASSWORD"] = "new-password"
        self.assertEqual(self.client.get("/api/files").status_code, 401)
        self.assertEqual(unlock(self.client).status_code, 401)
        self.assertEqual(unlock(self.client, "new-password").status_code, 303)
        self.assertEqual(self.client.get("/api/files").status_code, 200)

    def test_expired_and_tampered_session_cookies_do_not_unlock_the_app(self):
        self.login()
        cookie = self.client.get_cookie("session").value
        self.client.set_cookie("session", cookie + "x")
        self.assertEqual(self.client.get("/api/files").status_code, 401)
        self.client.set_cookie("session", cookie)
        with patch("itsdangerous.timed.TimestampSigner.get_timestamp", return_value=int(time()) + 13 * 3600):
            self.assertEqual(self.client.get("/api/files").status_code, 401)

    def test_password_comes_from_root_env_and_supports_unicode_and_punctuation(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("shared.config.PROJECT_ROOT", Path(directory)), \
                patch.dict("os.environ", {}, clear=True):
            password = "Lämp: # matrix 🔒"
            (Path(directory) / ".env").write_text(f'LEDART_WEB_PASSWORD="{password}"\n', encoding="utf-8")
            client = create_app(self.folder, {"TESTING": True}).test_client()
            self.assertEqual(client.get("/api/files").status_code, 401)
            self.assertEqual(unlock(client, password).status_code, 303)
            self.assertEqual(client.get("/api/files").status_code, 200)
            with patch.dict("os.environ", {"LEDART_WEB_PASSWORD": "override"}):
                overridden = create_app(self.folder, {"TESTING": True}).test_client()
                self.assertEqual(unlock(overridden, password).status_code, 401)
                self.assertEqual(unlock(overridden, "override").status_code, 303)

    def test_blank_or_missing_password_keeps_authentication_optional(self):
        for environment in ({}, {"LEDART_WEB_PASSWORD": ""}):
            with self.subTest(environment=environment), \
                    patch("web_server.app.read_environment", return_value=environment):
                client = create_app(self.folder, {"TESTING": True}).test_client()
                response = client.get("/")
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b'id="login-dialog"', response.data)
                self.assertNotIn(b'id="logout-form"', response.data)
                self.assertEqual(client.get("/api/files").status_code, 200)


if __name__ == "__main__":
    unittest.main()
