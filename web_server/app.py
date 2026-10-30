"""LAN media manager for the LED matrix. Start with: python main.py --service web."""
import argparse
from datetime import timedelta
import hmac
from io import BytesIO
import os
from pathlib import Path
import secrets
import tempfile
from threading import Lock
import warnings

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename
from led_backend.media_player import VIDEO_EXTENSIONS
from shared.config import INPUT_FOLDER, read_environment

from shared.settings import (
    SETTINGS_FILENAME, read_brightness, validate_brightness, write_brightness,
    read_display_seconds, validate_display_seconds, write_display_seconds,
    read_paused_files, write_media_paused,
)

# These image extensions match led_backend/player.py; videos remain managed separately.
IMAGE_FORMATS = {
    ".bmp": "BMP", ".jpeg": "JPEG", ".jpg": "JPEG", ".png": "PNG",
    ".tif": "TIFF", ".tiff": "TIFF", ".webp": "WEBP", ".gif": "GIF",
}
MAX_PIXELS = 16_000_000
MAX_FRAMES = 500
MAX_TOTAL_PIXELS = 100_000_000
MATRIX_SIZE = (20, 15)


def inspect_image(path: Path, validate: bool = False) -> dict:
    """Read metadata and, for uploads, decode frames within Pi-friendly limits."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            expected = IMAGE_FORMATS.get(path.suffix.lower())
            # Temporary upload paths end in .tmp; their extension is checked separately.
            if expected and image.format != expected:
                raise ValueError("Image contents do not match the filename extension.")
            width, height = image.size
            if width * height > MAX_PIXELS:
                raise ValueError("Images must contain no more than 16 million pixels.")
            frames = getattr(image, "n_frames", 1)
            if frames > MAX_FRAMES or width * height * frames > MAX_TOTAL_PIXELS:
                raise ValueError("Animation is too large (500 frames / 100 million frame pixels maximum).")
            details = dict(width=width, height=height, frames=frames, format=image.format)
            if validate:
                # Animated WebP and TIFF are displayed as stills by the LED player.
                for index in range(frames if image.format == "GIF" else 1):
                    image.seek(index)
                    image.load()
                image.seek(0)
        if validate:
            with Image.open(path) as image:
                image.verify()
    return details


def check_upload_storage(folder: Path) -> None:
    """Exercise upload writes, permissions, hard links, and deletion before serving."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        # Hidden scratch files cannot enter the LED player's media library.
        with tempfile.TemporaryDirectory(dir=folder, prefix=".storage-check-") as scratch:
            source = Path(scratch) / "upload.tmp"
            source.write_bytes(b"LED media storage check")
            os.chmod(source, 0o644)
            published = Path(scratch) / "published.tmp"
            os.link(source, published)
            source.unlink()
            published.unlink()
    except OSError as exc:
        raise RuntimeError(
            f"Upload storage check failed for {folder}: {exc}. "
            "Run sudo bash systemd/install_ledart.sh to update the service and Input permissions. "
            "If it still fails, inspect sudo systemctl cat ledart-web and the filesystem mount."
        ) from exc


def create_app(input_folder=None, config=None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secrets.token_hex(32),
        WEB_PASSWORD=read_environment().get("LEDART_WEB_PASSWORD", ""),
        MAX_CONTENT_LENGTH=32 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    if config:
        app.config.update(config)
    folder = Path(input_folder or INPUT_FOLDER).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    app.config["INPUT_FOLDER"] = folder
    upload_lock = Lock()

    def media_path(name):
        if (
            not name or name.startswith(".") or "/" in name or "\\" in name
            or Path(name).name != name
            or Path(name).suffix.lower() not in set(IMAGE_FORMATS) | VIDEO_EXTENSIONS
        ):
            abort(404)
        path = folder / name
        if path.is_symlink() or not path.is_file():
            abort(404)
        return path

    def authentication_marker():
        # Bind the session to the current password without storing the password
        # or an unkeyed password hash in the browser's signed session cookie.
        key = app.secret_key
        if isinstance(key, str):
            key = key.encode("utf-8")
        return hmac.new(key, app.config["WEB_PASSWORD"].encode("utf-8"), "sha256").hexdigest()

    def authenticated():
        return session.get("user") == "admin" and secrets.compare_digest(
            session.get("auth_token", ""), authentication_marker(),
        )

    def login_page(error=None):
        session.setdefault("csrf_token", secrets.token_hex(32))
        return render_template("login.html", csrf_token=session["csrf_token"], error=error)

    @app.before_request
    def require_password():
        if not app.config["WEB_PASSWORD"] or authenticated():
            return
        # The locked index serves only the password dialog. Static assets contain
        # no private data and supply the dialog's stylesheet and script.
        if request.endpoint in {"index", "login", "static"}:
            return
        return jsonify(error="Enter the password to unlock the LED media manager."), 401

    @app.before_request
    def protect_changes():
        if request.method in {"POST", "DELETE", "PUT", "PATCH"}:
            token = request.headers.get("X-CSRF-Token", "")
            if not token and request.endpoint in {"login", "logout"}:
                token = request.form.get("csrf_token", "")
            expected = session.get("csrf_token", "")
            if not expected or not secrets.compare_digest(token, expected):
                abort(403, description="Refresh the page before making changes.")

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self'; script-src 'self'; "
            "style-src 'self'; object-src 'none'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'"
        )
        if app.config["WEB_PASSWORD"] or request.path == "/" or request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        message = error.description
        if error.code == 413:
            message = "The upload exceeds the 32 MiB request limit. Upload fewer or smaller files."
        return jsonify(error=message), error.code

    @app.get("/")
    def index():
        if app.config["WEB_PASSWORD"] and not authenticated():
            return login_page()
        session.setdefault("csrf_token", secrets.token_hex(32))
        return render_template(
            "index.html", csrf_token=session["csrf_token"],
            extensions=", ".join(sorted(IMAGE_FORMATS)),
            accept=",".join(sorted(IMAGE_FORMATS)),
            password_enabled=bool(app.config["WEB_PASSWORD"]),
        )

    @app.post("/login")
    def login():
        if not app.config["WEB_PASSWORD"]:
            return redirect(url_for("index"), code=303)
        password = request.form.get("password", "")
        if not secrets.compare_digest(password.encode("utf-8"), app.config["WEB_PASSWORD"].encode("utf-8")):
            return login_page("Incorrect password. Please try again."), 401
        session.clear()
        session["user"] = "admin"
        session["auth_token"] = authentication_marker()
        session["csrf_token"] = secrets.token_hex(32)
        return redirect(url_for("index"), code=303)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"), code=303)

    @app.get("/api/display-seconds")
    def get_display_seconds():
        return jsonify(display_seconds=read_display_seconds(folder / SETTINGS_FILENAME))

    @app.post("/api/display-seconds")
    def set_display_seconds():
        payload = request.get_json(silent=True)
        try:
            value = validate_display_seconds(
                payload.get("display_seconds") if isinstance(payload, dict) else None
            )
        except ValueError as exc:
            abort(400, description=str(exc))
        try:
            with upload_lock:
                write_display_seconds(folder / SETTINGS_FILENAME, value)
        except OSError:
            abort(503, description="Could not save display duration. Check Input folder permissions.")
        return jsonify(display_seconds=value)

    @app.get("/api/brightness")
    def get_brightness():
        return jsonify(brightness=read_brightness(folder / SETTINGS_FILENAME))

    @app.post("/api/brightness")
    def set_brightness():
        payload = request.get_json(silent=True)
        try:
            value = validate_brightness(
                payload.get("brightness") if isinstance(payload, dict) else None
            )
        except ValueError as exc:
            abort(400, description=str(exc))
        try:
            with upload_lock:
                write_brightness(folder / SETTINGS_FILENAME, value)
        except OSError:
            abort(503, description="Could not save brightness. Check Input folder permissions.")
        return jsonify(brightness=value)

    @app.get("/api/files")
    def list_files():
        items = []
        paused = read_paused_files(folder / SETTINGS_FILENAME)
        for path in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            if path.name.startswith(".") or path.suffix.lower() not in set(IMAGE_FORMATS) | VIDEO_EXTENSIONS or path.is_symlink():
                continue
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
                item = dict(name=path.name, size=stat.st_size, modified=stat.st_mtime_ns,
                            paused=path.name in paused, video=path.suffix.lower() in VIDEO_EXTENSIONS)
                if item["video"]:
                    items.append(item)
                    continue
                try:
                    item.update(inspect_image(path))
                except (OSError, ValueError, UnidentifiedImageError,
                        Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                    item["error"] = str(exc) or "Cannot read this image."
                items.append(item)
            except FileNotFoundError:
                continue
        return jsonify(files=items)

    @app.get("/preview/<name>")
    def preview(name):
        path = media_path(name)
        if path.suffix.lower() in VIDEO_EXTENSIONS:
            return send_file(path, conditional=True, max_age=0)
        try:
            details = inspect_image(path)
            matrix = request.args.get("matrix") == "1"
            if details["format"] == "GIF" and not matrix:
                return send_file(path, mimetype="image/gif", conditional=True, max_age=0)
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                if matrix:
                    image = ImageOps.fit(
                        image.convert("RGB"), MATRIX_SIZE, method=Image.Resampling.LANCZOS,
                    )
                else:
                    image = image.convert("RGBA")
                    image.thumbnail((640, 480), Image.Resampling.LANCZOS)
                output = BytesIO()
                image.save(output, format="PNG")
            output.seek(0)
            return send_file(output, mimetype="image/png", max_age=0)
        except FileNotFoundError:
            abort(404)
        except (OSError, ValueError, UnidentifiedImageError,
                Image.DecompressionBombError, Image.DecompressionBombWarning):
            abort(415, description="This file cannot be previewed.")

    @app.post("/api/upload")
    def upload():
        uploads = request.files.getlist("files")
        if not uploads or not any(part.filename for part in uploads):
            abort(400, description="Choose at least one image or GIF.")
        saved, errors = [], []
        for part in uploads:
            original = part.filename or ""
            name = secure_filename(original)
            suffix = Path(name).suffix.lower()
            if not name or suffix not in IMAGE_FORMATS:
                errors.append(dict(name=original, error="Unsupported file extension."))
                continue
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=folder, prefix=".upload-", suffix=".tmp", delete=False) as stream:
                    temporary = Path(stream.name)
                    part.save(stream)
                details = inspect_image(temporary, validate=True)
                if details["format"] != IMAGE_FORMATS[suffix]:
                    raise ValueError("Image contents do not match the filename extension.")
                os.chmod(temporary, 0o644)
                # A hard link publishes the completed upload atomically and never overwrites.
                with upload_lock:
                    stem = Path(name).stem[:160]
                    candidate = folder / (stem + suffix)
                    number = 1
                    while True:
                        try:
                            os.link(temporary, candidate)
                            break
                        except FileExistsError:
                            candidate = folder / f"{stem}_{number}{suffix}"
                            number += 1
                saved.append(candidate.name)
            except (OSError, ValueError, UnidentifiedImageError,
                    Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                errors.append(dict(name=original, error=str(exc) or "Invalid image."))
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return jsonify(saved=saved, errors=errors), (201 if saved else 400)

    @app.post("/api/files/<name>/pause")
    def pause(name):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or type(payload.get("paused")) is not bool:
            abort(400, description="Paused must be true or false.")
        try:
            with upload_lock:
                media_path(name)
                write_media_paused(folder / SETTINGS_FILENAME, name, payload["paused"])
        except OSError:
            abort(503, description="Could not save pause state. Check Input folder permissions.")
        return jsonify(name=name, paused=payload["paused"])

    @app.delete("/api/files/<name>")
    def delete(name):
        path = media_path(name)
        try:
            with upload_lock:
                write_media_paused(folder / SETTINGS_FILENAME, name, False)
                path.unlink()
        except FileNotFoundError:
            abort(404)
        except OSError:
            abort(409, description="Could not delete this file. Check Input folder permissions.")
        return jsonify(deleted=name)

    return app


def main(argv=None):
    environment = read_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=environment.get("LEDART_WEB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=environment.get("LEDART_WEB_PORT", "8080"))
    parser.add_argument(
        "--check-storage", action="store_true",
        help="Verify upload storage access and exit without starting the web server.",
    )
    args = parser.parse_args(argv)
    folder = INPUT_FOLDER
    try:
        check_upload_storage(folder)
    except RuntimeError as exc:
        parser.exit(1, f"{exc}\n")
    if args.check_storage:
        print(f"Upload storage check passed: {folder}", flush=True)
        return

    from waitress import serve

    app = create_app(folder)
    print(f"LED media manager listening on {args.host}:{args.port}", flush=True)
    serve(
        app, host=args.host, port=args.port, threads=2,
        max_request_body_size=app.config["MAX_CONTENT_LENGTH"],
    )


if __name__ == "__main__":
    main()
