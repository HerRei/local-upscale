from pathlib import Path

import tifffile
from PIL import Image

RAW_INPUT_EXTENSIONS = frozenset({".dng"})
RASTER_INPUT_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"})
VIDEO_INPUT_EXTENSIONS = frozenset({".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"})
SUPPORTED_INPUT_EXTENSIONS = RAW_INPUT_EXTENSIONS | RASTER_INPUT_EXTENSIONS | VIDEO_INPUT_EXTENSIONS
IMAGE_FILE_DIALOG_FILTER = (
    "Images and camera RAW (*.png *.jpg *.jpeg *.tif *.tiff *.webp *.dng *.DNG)"
)


def is_raw_input(path: str | Path) -> bool:
    return Path(path).suffix.lower() in RAW_INPUT_EXTENSIONS


def is_video_input(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_INPUT_EXTENSIONS


def probe_image_size(path: str | Path) -> tuple[int, int]:
    """Read display-oriented dimensions without decoding the full image."""
    path = str(path)
    if is_raw_input(path):
        # DNG is a TIFF container. Reading its tags keeps the native LibRaw
        # extension out of the GUI process; full RAW development stays isolated
        # in the inference worker.
        with tifffile.TiffFile(path) as image:
            page = max(
                image.pages,
                key=lambda candidate: int(candidate.imagewidth) * int(candidate.imagelength),
            )
            width = int(page.imagewidth)
            height = int(page.imagelength)
            orientation_tag = page.tags.get("Orientation")
            orientation = int(orientation_tag.value) if orientation_tag is not None else 1
            if orientation in {5, 6, 7, 8}:
                width, height = height, width
    else:
        with Image.open(path) as image:
            width, height = image.size
            orientation = int(image.getexif().get(274, 1))
            if orientation in {5, 6, 7, 8}:
                width, height = height, width

    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive.")
    return width, height
