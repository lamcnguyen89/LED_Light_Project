"""Shared, persistent brightness settings without importing LED hardware."""
import json
import os
from pathlib import Path
import tempfile
from time import monotonic

DEFAULT_BRIGHTNESS = 255
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


def write_brightness(path, value):
    """Publish a complete setting atomically so the player never reads a partial file."""
    value = validate_brightness(value)
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".led-settings-", suffix=".tmp",
            mode="w", encoding="utf-8", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump({"brightness": value}, stream)
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
