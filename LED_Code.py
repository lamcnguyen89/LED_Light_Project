from pathlib import Path
from time import monotonic, sleep

from PIL import Image, ImageOps
from rpi_ws281x import Color, PixelStrip

from media_player import MEDIA_EXTENSIONS, is_media_file, play_media_file, wait_for_stop
from led_settings import (
    DEFAULT_BRIGHTNESS, SETTINGS_FILENAME,
    BrightnessController, read_brightness,
)


# LED matrix configuration
MATRIX_WIDTH = 20
MATRIX_HEIGHT = 15
LED_COUNT = MATRIX_WIDTH * MATRIX_HEIGHT

# LED strip configuration
LED_PIN = 18             # GPIO18
LED_FREQ_HZ = 800000     # WS2812 signal frequency
LED_DMA = 10             # DMA channel
LED_BRIGHTNESS = DEFAULT_BRIGHTNESS  # 0-255; used when no saved setting exists
LED_INVERT = False
LED_CHANNEL = 0

INPUT_FOLDER = Path(__file__).resolve().parent / "Input"
IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | MEDIA_EXTENSIONS
DISPLAY_SECONDS = 600
INPUT_POLL_SECONDS = 1.0


def find_display_files(folder: Path) -> list[Path]:
    """Return all supported images and videos in the folder, sorted by filename."""
    if not folder.is_dir():
        raise FileNotFoundError(f"Input folder does not exist: {folder}")

    display_files = sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        ),
        key=lambda path: path.name.lower(),
    )

    return display_files


def scan_display_files(folder: Path) -> dict[Path, tuple[int, int]]:
    """Snapshot filenames, modification times and sizes without loading images."""
    snapshot = {}
    for path in find_display_files(folder):
        try:
            stat = path.stat()
        except FileNotFoundError:
            # A file may be removed between listing the folder and reading its stat.
            continue
        snapshot[path] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def xy_to_led_index(x: int, y: int) -> int:
    """Map an image coordinate to the vertically snaked physical LED index.

    LED 0 is the top-right pixel. The rightmost column travels downward, the
    next column travels upward, and the direction alternates thereafter.
    """
    column_from_right = MATRIX_WIDTH - 1 - x
    column_start = column_from_right * MATRIX_HEIGHT

    if column_from_right % 2 == 0:
        return column_start + y
    return column_start + (MATRIX_HEIGHT - 1 - y)

def load_image(image_path: Path) -> Image.Image:
    """Load an image and crop/resize it to fill the LED matrix."""
    with Image.open(image_path) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
        return ImageOps.fit(
            source,
            (MATRIX_WIDTH, MATRIX_HEIGHT),
            method=Image.Resampling.LANCZOS,
        )


def display_image(strip: PixelStrip, image: Image.Image) -> None:
    pixels = image.load()
    for y in range(MATRIX_HEIGHT):
        for x in range(MATRIX_WIDTH):
            red, green, blue = pixels[x, y]
            strip.setPixelColor(xy_to_led_index(x, y), Color(red, green, blue))
    strip.show()

def main() -> None:
    INPUT_FOLDER.mkdir(parents=True, exist_ok=True)
    settings_path = INPUT_FOLDER / SETTINGS_FILENAME
    initial_brightness = read_brightness(settings_path, LED_BRIGHTNESS)
    strip = PixelStrip(
        LED_COUNT,
        LED_PIN,
        LED_FREQ_HZ,
        LED_DMA,
        LED_INVERT,
        initial_brightness,
        LED_CHANNEL,
    )
    
    strip.begin()
    brightness = BrightnessController(strip, settings_path, initial_brightness)

    print(f"Watching {INPUT_FOLDER}. Press Ctrl+C to stop.")
    previous_snapshot = {}
    while True:
        brightness.update()
        snapshot = scan_display_files(INPUT_FOLDER)
        if not snapshot:
            previous_snapshot = snapshot
            # Keep brightness responsive without scanning an empty library 20 times/second.
            wait_for_stop(INPUT_POLL_SECONDS, lambda: brightness.update() or False)
            continue

        # Show newly added or replaced files first, then resume the slideshow.
        changed_paths = [
            path for path in snapshot
            if previous_snapshot.get(path) != snapshot[path]
        ]
        display_paths = changed_paths + [
            path for path in snapshot if path not in changed_paths
        ]
        previous_snapshot = snapshot
        next_scan = 0.0

        def input_changed() -> bool:
            nonlocal next_scan
            brightness.update()
            now = monotonic()
            if now < next_scan:
                return False
            next_scan = now + INPUT_POLL_SECONDS
            return scan_display_files(INPUT_FOLDER) != snapshot

        for display_path in display_paths:
            if input_changed():
                break
            print(f"Displaying {display_path.name} on the LED matrix")
            try:
                if is_media_file(display_path):
                    play_media_file(
                        display_path,
                        lambda image: display_image(strip, image),
                        (MATRIX_WIDTH, MATRIX_HEIGHT),
                        DISPLAY_SECONDS,
                        should_stop=input_changed,
                    )
                else:
                    image = load_image(display_path)
                    display_image(strip, image)
                    wait_for_stop(DISPLAY_SECONDS, input_changed)
            except (OSError, ValueError, RuntimeError) as exc:
                # Uploads may be incomplete or disappear while being opened.
                print(f"Skipping {display_path.name}: {exc}")
                wait_for_stop(INPUT_POLL_SECONDS, lambda: brightness.update() or False)
            # Check directly so a cached poll cannot hide a detected change.
            if scan_display_files(INPUT_FOLDER) != snapshot:
                break


if __name__ == "__main__":
    main()
