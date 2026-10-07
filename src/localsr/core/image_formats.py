import sys
from pathlib import Path

# Camera RAW files, developed by LibRaw (rawpy), which LocalSR ships with its
# corresponding source.
RAW_INPUT_EXTENSIONS = frozenset(
    {
        ".dng",  # Adobe, Apple ProRAW, Leica, Pentax, Ricoh and many phones
        ".cr2",
        ".cr3",  # Canon
        ".nef",
        ".nrw",  # Nikon
        ".arw",
        ".srf",
        ".sr2",  # Sony
        ".raf",  # Fujifilm
        ".orf",  # OM System / Olympus
        ".rw2",  # Panasonic
        ".pef",  # Pentax
        ".srw",  # Samsung
        ".3fr",  # Hasselblad
        ".iiq",  # Phase One
        ".erf",  # Epson
        ".rwl",  # Leica
        ".mrw",  # Konica Minolta
        ".mef",  # Mamiya
        ".mos",  # Leaf
        ".dcr",
        ".kdc",  # Kodak
    }
)
# Decoded by Pillow, whose AVIF (libavif) and JPEG 2000 (OpenJPEG) decoders are
# already part of the app. None of these formats needs a patent licence: AV1 is
# royalty-free, JPEG 2000 Part 1 is royalty-free and GIF's LZW patents expired
# in 2004.
RASTER_INPUT_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".gif", ".avif", ".jp2", ".j2k"}
)
# Decoded by macOS (ImageIO): HEIC/HEIF carry HEVC, a patent-pooled codec that
# Apple licenses for apps using its public API, so LocalSR ships no decoder of
# its own and offers these formats on macOS only (as it does HEVC video).
# JPEG XL is royalty-free; macOS 14 and later decode it. .hif is the HEIF
# extension Fujifilm and Canon cameras use.
SYSTEM_IMAGE_EXTENSIONS = (
    frozenset({".heic", ".heif", ".hif", ".jxl"}) if sys.platform == "darwin" else frozenset()
)
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
SUPPORTED_INPUT_EXTENSIONS = (
    RAW_INPUT_EXTENSIONS
    | RASTER_INPUT_EXTENSIONS
    | SYSTEM_IMAGE_EXTENSIONS
    | VIDEO_INPUT_EXTENSIONS
)


def is_raw_input(path: str | Path) -> bool:
    return Path(path).suffix.lower() in RAW_INPUT_EXTENSIONS


def is_system_decoded(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SYSTEM_IMAGE_EXTENSIONS


def is_video_input(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_INPUT_EXTENSIONS
