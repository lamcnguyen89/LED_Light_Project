"""Shared, persistent display settings without importing LED hardware."""
import json
import os
from pathlib import Path
import tempfile
from time import monotonic

DEFAULT_BRIGHTNESS = 255
DEFAULT_DISPLAY_SECONDS = 600
SETTINGS_FILENAME = ".led-settings.json"
BRIGHTNESS_POLL_SECONDS = 0.05


def validate_brightness(value):
    if type(value) is not int or not 0 <= value <= 255:
        raise ValueError("Brightness must be an integer from 0 to 255.")
    return value


def read_brightness(path, default=DEFAULT_BRIGHTNESS):
    """Keep the current/default brightness when settings are missing or invalid."""
    try:
        settings = json.loads(Path(path).read_text(encoding="utf-8"))
        return validate_brightness(settings["brightness"])
    except (OSError, ValueError, KeyError, TypeError):
        return default


def validate_display_seconds(value):
    if type(value) is not int or value < 1 or value > 86400:
        raise ValueError("Display duration must be an integer from 1 to 86400 seconds.")
    return value


def read_display_seconds(path, default=DEFAULT_DISPLAY_SECONDS):
    """Use the default when the saved duration is missing or invalid."""
    try:
        settings = json.loads(Path(path).read_text(encoding="utf-8"))
        return validate_display_seconds(settings["display_seconds"])
    except (OSError, ValueError, KeyError, TypeError):
        return default


def write_display_seconds(path, value):
    write_setting(path, "display_seconds", validate_display_seconds(value))


def write_brightness(path, value):
    write_setting(path, "brightness", validate_brightness(value))


def write_setting(path, key, value):
    """Atomically update one setting while preserving the other controls."""
    path = Path(path)
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            settings = {}
    except (OSError, ValueError):
        settings = {}
    settings[key] = value
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".led-settings-", suffix=".tmp",
            mode="w", encoding="utf-8", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(settings, stream)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class BrightnessController:
    """Apply settings on the playback thread, including during frame waits."""
    def __init__(self, strip, path, initial):
        self.strip = strip
        self.path = path
        self.current = initial
        self.next_poll = 0.0

    def update(self):
        now = monotonic()
        if now < self.next_poll:
            return
        self.next_poll = now + BRIGHTNESS_POLL_SECONDS
        value = read_brightness(self.path, self.current)
        if value != self.current:
            self.strip.setBrightness(value)
            self.strip.show()
            self.current = value
