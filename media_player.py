from pathlib import Path
from time import monotonic, sleep
from typing import Callable

from PIL import Image, ImageOps, ImageSequence

from led_settings import BRIGHTNESS_POLL_SECONDS


GIF_EXTENSIONS = {".gif"}
VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}
MEDIA_EXTENSIONS = GIF_EXTENSIONS | VIDEO_EXTENSIONS
PLAYBACK_SLOWDOWN = 5.0 # Multiplier to slow down playback speed

DisplayFrame = Callable[[Image.Image], None]
StopPlayback = Callable[[], bool]


def wait_for_stop(seconds: float, should_stop: StopPlayback = None) -> bool:
    """Wait for a frame/display duration, polling for live controls and changes."""
    end_time = monotonic() + seconds
    while True:
        if should_stop is not None and should_stop():
            return True
        remaining = end_time - monotonic()
        if remaining <= 0:
            return False
        sleep(min(BRIGHTNESS_POLL_SECONDS, remaining) if should_stop is not None else remaining)


def is_media_file(path: Path) -> bool:
    """Return True when the file should be played frame-by-frame."""
    return path.suffix.lower() in MEDIA_EXTENSIONS


def fit_frame(frame: Image.Image, matrix_size: tuple[int, int]) -> Image.Image:
    """Convert and crop/resize a frame to fill the LED matrix."""
    return ImageOps.fit(
        frame.convert("RGB"),
        matrix_size,
        method=Image.Resampling.LANCZOS,
    )


def play_media_file(
    media_path: Path,
    display_frame: DisplayFrame,
    matrix_size: tuple[int, int],
    play_seconds: float,
    should_stop: StopPlayback = None,
) -> None:
    """Play a GIF or video file for the requested number of seconds."""
    suffix = media_path.suffix.lower()

    if suffix in GIF_EXTENSIONS:
        play_gif(media_path, display_frame, matrix_size, play_seconds, should_stop)
        return

    if suffix in VIDEO_EXTENSIONS:
        play_video(media_path, display_frame, matrix_size, play_seconds, should_stop)
        return

    raise ValueError(f"Unsupported media file: {media_path}")


def play_gif(
    gif_path: Path,
    display_frame: DisplayFrame,
    matrix_size: tuple[int, int],
    play_seconds: float,
    should_stop: StopPlayback = None,
) -> None:
    """Play an animated GIF for the requested number of seconds."""
    end_time = monotonic() + play_seconds

    with Image.open(gif_path) as source:
        while monotonic() < end_time:
            for frame in ImageSequence.Iterator(source):
                if should_stop is not None and should_stop():
                    return
                image = fit_frame(ImageOps.exif_transpose(frame), matrix_size)
                display_frame(image)

                duration_ms = frame.info.get("duration", 100)
                if wait_for_stop(
                    min(duration_ms / 1000 * PLAYBACK_SLOWDOWN, max(0, end_time - monotonic())),
                    should_stop,
                ):
                    return

                if monotonic() >= end_time:
                    break


def play_video(
    video_path: Path,
    display_frame: DisplayFrame,
    matrix_size: tuple[int, int],
    play_seconds: float,
    should_stop: StopPlayback = None,
) -> None:
    """Play a video file for the requested number of seconds."""
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "Video playback requires OpenCV. Install it with: "
            "python3 -m pip install opencv-python"
        ) from exc

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video file: {video_path}")

    fps = capture.get(cv2.CAP_PROP_FPS)
    frame_delay = (1 / fps if fps and fps > 0 else 1 / 30) * PLAYBACK_SLOWDOWN
    end_time = monotonic() + play_seconds

    try:
        while monotonic() < end_time:
            if should_stop is not None and should_stop():
                return
            ok, frame = capture.read()
            if not ok:
                capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue

            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = fit_frame(Image.fromarray(rgb_frame), matrix_size)
            display_frame(image)
            if wait_for_stop(
                min(frame_delay, max(0, end_time - monotonic())), should_stop,
            ):
                return
    finally:
        capture.release()
