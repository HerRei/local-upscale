# The most pixels a source image may have: what Pillow accepts before it refuses an image
# as a decompression bomb (twice its 89 megapixel warning threshold). Everything up to
# here opens, medium-format and stitched files included; the warning for the upper half
# is silenced because accepting that range is intended.
MAX_INPUT_PIXELS = 2 * 89_478_485
TOO_LARGE_MESSAGE = (
    f"This image has more than {round(MAX_INPUT_PIXELS / 1_000_000)} megapixels, which is more "
    "than LocalSR opens. Reduce its size first."
)


def require_pixel_budget(width: int, height: int) -> None:
    """Refuse an image that has more pixels than LocalSR reads, before decoding any."""
    if width * height > MAX_INPUT_PIXELS:
        raise ValueError(TOO_LARGE_MESSAGE)
