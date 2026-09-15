from pathlib import Path

RAW_INPUT_EXTENSIONS = frozenset({".dng"})
RASTER_INPUT_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"})
VIDEO_INPUT_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".mov",
        ".m4v",
        ".mkv",
        ".webm",
        ".avi",
        ".mpg",
        ".mpeg",
        ".mpe",
        ".vob",
        ".ts",
        ".mts",
        ".m2ts",
        ".wmv",
        ".asf",
        ".flv",
        ".f4v",
        ".3gp",
        ".3g2",
        ".ogv",
        ".divx",
    }
)
SUPPORTED_INPUT_EXTENSIONS = RAW_INPUT_EXTENSIONS | RASTER_INPUT_EXTENSIONS | VIDEO_INPUT_EXTENSIONS


def is_raw_input(path: str | Path) -> bool:
    return Path(path).suffix.lower() in RAW_INPUT_EXTENSIONS


def is_video_input(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_INPUT_EXTENSIONS
