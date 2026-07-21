from pathlib import Path
from time import sleep

from PIL import Image, ImageOps
from rpi_ws281x import Color, PixelStrip

from media_player import MEDIA_EXTENSIONS, is_media_file, play_media_file


# LED matrix configuration
MATRIX_WIDTH = 20
MATRIX_HEIGHT = 15
LED_COUNT = MATRIX_WIDTH * MATRIX_HEIGHT

# LED strip configuration
LED_PIN = 18             # GPIO18
LED_FREQ_HZ = 800000     # WS2812 signal frequency
LED_DMA = 10             # DMA channel
LED_BRIGHTNESS = 255     # 0-255
LED_INVERT = False
LED_CHANNEL = 0

INPUT_FOLDER = Path(__file__).resolve().parent / "Input"
IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | MEDIA_EXTENSIONS
DISPLAY_SECONDS = 600


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

    if not display_files:
        raise FileNotFoundError(f"No supported images or videos found in: {folder}")

    return display_files


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
    display_paths = find_display_files(INPUT_FOLDER)

    strip = PixelStrip(
        LED_COUNT,
        LED_PIN,
        LED_FREQ_HZ,
        LED_DMA,
        LED_INVERT,
        LED_BRIGHTNESS,
        LED_CHANNEL,
    )
    strip.begin()

    print(f"Found {len(display_paths)} display file(s). Press Ctrl+C to stop.")
    while True:
        for display_path in display_paths:
            print(f"Displaying {display_path.name} on the LED matrix")

            if is_media_file(display_path):
                play_media_file(
                    display_path,
                    lambda image: display_image(strip, image),
                    (MATRIX_WIDTH, MATRIX_HEIGHT),
                    DISPLAY_SECONDS,
                )
                continue

            image = load_image(display_path)
            display_image(strip, image)
            sleep(DISPLAY_SECONDS)


if __name__ == "__main__":
    main()
