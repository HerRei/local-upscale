import io
import os
import struct
import subprocess
import sys
import tempfile
import threading
import warnings
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import torch
from PIL import Image, ImageCms, ImageOps, TiffImagePlugin, UnidentifiedImageError

from localsr.core.image_formats import (
    is_raw_input,
    is_system_decoded,
    require_ordinary_source,
)
from localsr.core.output_publish import owned_temporary, publish_output

# The most pixels a source image may have: what Pillow accepts before it refuses an image
# as a decompression bomb (twice its 89 megapixel warning threshold). Everything up to
# here opens, medium-format and stitched files included; the warning for the upper half
# is silenced because accepting that range is intended.
MAX_INPUT_PIXELS = 2 * 89_478_485
TOO_LARGE_MESSAGE = (
    f"This image has more than {round(MAX_INPUT_PIXELS / 1_000_000)} megapixels, which is more "
    "than LocalSR opens. Reduce its size first."
)


class UnreadableImageError(OSError):
    """A source LocalSR cannot read; the message is a sentence for the person who added it."""


def _unreadable(path: str | Path) -> UnreadableImageError:
    return UnreadableImageError(
        f"{Path(path).name} is damaged or is not a picture format LocalSR can read."
    )


def _is_decode_failure(error: BaseException) -> bool:
    """Whether a library raised ``error`` for bytes it could not make sense of.

    A real input/output failure (no permission, no such file, a full disk) carries an
    errno and keeps its own message; the libraries' decode failures carry none.
    """
    if isinstance(error, OSError):
        return error.errno is None and not isinstance(error, UnreadableImageError)
    return isinstance(error, (SyntaxError, EOFError, zlib.error, struct.error))


SAFE_EXIF_TAGS = frozenset({306, 315, 33432})
# Formats whose extra frames are animation frames or pages, not another view of the picture
# (a phone's JPEG also lists its depth map or gain map as a second frame).
ANIMATED_OR_PAGED_FORMATS = frozenset({"GIF", "WEBP", "PNG", "TIFF"})
# What a JPEG's single EXIF segment can hold, header included.
JPEG_EXIF_LIMIT = 65_533
_EXIF_TAG_NAMES = {306: "date", 315: "artist", 33432: "copyright"}
# 16-bit grey PNG and TIFF scans open as I;16, and some files as 32-bit I. Pillow
# converts those to 8 bits by clipping, not scaling.
WIDE_GREY_MODES = frozenset({"I;16", "I;16L", "I;16B", "I;16N", "I"})
# Pillow modes without colour.
GREY_MODES = frozenset({"1", "L", "LA", "La"}) | WIDE_GREY_MODES


def _narrow_wide_grey(image: Image.Image, sample_bits: int = 16) -> Image.Image:
    """Scale 16- and 32-bit grey to 8 bits.

    Pillow's own conversion clips every value above 255, so a 16-bit scan turned
    white apart from its blackest pixels. A 32-bit I image holding only 8-bit
    values is taken as it is.
    """
    values = np.asarray(image).astype(np.float64)
    if image.mode != "I" or values.max() > 255:
        values = values * (255.0 / ((1 << sample_bits) - 1))
    narrowed = Image.fromarray(np.clip(np.rint(values), 0, 255).astype(np.uint8))
    narrowed.info.update(image.info)
    return narrowed


_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
# Adam7 passes: first column, first row, column step, row step.
_ADAM7 = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)


def _png_scanline_bytes(path: str | Path) -> tuple[int, int] | None:
    """The bytes of scanline data a PNG's header promises, and what its IDAT chunks hold.

    Pillow reads a PNG whose compressed data ends after too few rows without an error and
    leaves the missing rows black. Returns None when the file is not one this can follow;
    the decoder then reports its own problem.
    """
    try:
        with open(path, "rb") as file:
            if file.read(8) != b"\x89PNG\r\n\x1a\n":
                return None
            length, kind = struct.unpack(">I4s", file.read(8))
            if kind != b"IHDR" or length < 13:
                return None
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", file.read(13))
            file.seek(length - 13 + 4, os.SEEK_CUR)
            channels = _PNG_CHANNELS.get(colour)
            if channels is None or interlace not in (0, 1):
                return None
            bits = channels * depth
            passes = _ADAM7 if interlace else ((0, 0, 1, 1),)
            expected = 0
            for x0, y0, dx, dy in passes:
                columns, rows = -(-(width - x0) // dx), -(-(height - y0) // dy)
                if columns > 0 and rows > 0:
                    expected += rows * (1 + -(-columns * bits // 8))
            size = os.fstat(file.fileno()).st_size
            inflated, decoder = 0, zlib.decompressobj()
            while inflated < expected:
                header = file.read(8)
                if len(header) < 8:
                    break
                length, kind = struct.unpack(">I4s", header)
                if kind == b"IEND":
                    break
                if kind != b"IDAT":
                    file.seek(length + 4, os.SEEK_CUR)
                    continue
                # The length comes from the file: read it in pieces, and never past its end.
                if length + 4 > size - file.tell():
                    break
                left = length
                while left and inflated < expected:
                    pending = file.read(min(left, 1 << 20))
                    if not pending:
                        break
                    left -= len(pending)
                    while pending and inflated < expected:
                        inflated += len(decoder.decompress(pending, 1 << 20))
                        pending = decoder.unconsumed_tail
                file.seek(left + 4, os.SEEK_CUR)
            return expected, inflated
    except (OSError, zlib.error, struct.error):
        return None


def require_pixel_budget(width: int, height: int) -> None:
    """Refuse an image that has more pixels than LocalSR reads, before decoding any."""
    if width * height > MAX_INPUT_PIXELS:
        raise ValueError(TOO_LARGE_MESSAGE)


def _float_grey_to_16_bit(image: Image.Image) -> tuple[Image.Image, int | None]:
    """Take a 32-bit float grey image to 16 bits, and say which range it was read as.

    Pillow's own conversion keeps float values as they are and clips them to 0..255, so
    every pixel of a 0..1 image became black. The three common conventions are told apart
    by the largest value: up to 1 is 0..1, up to 255 is 0..255, up to 65535 is 0..65535.
    The second element is the upper end of the range when it is not 0..1, else None.
    Anything beyond that, below zero or not a number has no certain meaning and is refused.
    """
    values = np.asarray(image, dtype=np.float32)
    if not np.isfinite(values).all() or values.min() < -0.001 or values.max() > 65535.0 + 0.001:
        raise ValueError(
            "This floating-point image holds negative values, values above 65535 or "
            "not-a-number values. Only floating-point images whose values run from 0 to 1, "
            "0 to 255 or 0 to 65535 are supported; save an 8-bit or 16-bit copy first."
        )
    peak = float(values.max())
    scale = None if peak <= 1.001 else 255 if peak <= 255.0 + 0.001 else 65535
    wide = Image.fromarray(
        np.rint(np.clip(values / (scale or 1), 0.0, 1.0) * 65535.0).astype(np.uint16)
    )
    wide.info.update(image.info)
    return wide, scale


def _is_float_tiff(path: str | Path) -> bool:
    """Whether a file Pillow cannot identify is a TIFF with floating-point samples."""
    try:
        with open(path, "rb") as file:
            header = file.read(8)
            if header[:4] not in (b"II*\x00", b"MM\x00*"):
                return False
            directory = TiffImagePlugin.ImageFileDirectory_v2(header)
            file.seek(directory.next)
            directory.load(file)
        return 3 in tuple(directory.get(339) or ())  # SampleFormat: IEEE floating point
    except (OSError, SyntaxError, ValueError, TypeError, EOFError):
        return False


def _to_srgb(
    image: Image.Image, source: ImageCms.ImageCmsProfile, srgb: ImageCms.ImageCmsProfile
) -> Image.Image:
    """Convert pixels through their embedded profile to sRGB.

    profileToProfile keeps the input mode unless told otherwise, and a grey,
    palette or CMYK image cannot hold sRGB: those transforms failed, and their
    profile was ignored. Such images now convert to RGB, keeping any alpha.
    """
    if image.mode in ("RGB", "RGBA"):
        return ImageCms.profileToProfile(image, source, srgb)
    alpha = None
    if image.mode in ("LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        with_alpha = image.convert("LA" if image.mode == "LA" else "RGBA")
        alpha = with_alpha.getchannel("A")
        image = with_alpha.convert("L" if image.mode == "LA" else "RGB")
    elif image.mode == "P":
        image = image.convert("RGB")
    elif image.mode == "1":
        image = image.convert("L")
    converted = ImageCms.profileToProfile(image, source, srgb, outputMode="RGB")
    if alpha is not None:
        converted.putalpha(alpha)
    return converted


def _decode_with_macos(path: str) -> Image.Image:
    """Decode HEIC/HEIF or JPEG XL with macOS (ImageIO, through /usr/bin/sips).

    LocalSR ships no HEVC decoder: Apple licenses its own for apps that use the
    system API. sips applies the orientation to the pixels and keeps the colour
    profile and the descriptive EXIF tags; a 10-bit photo arrives as a 16-bit
    TIFF that Pillow reads as 8-bit RGBA.
    """
    with tempfile.TemporaryDirectory(prefix="localsr-decode-") as work:
        target = Path(work) / "decoded.tiff"
        try:
            result = subprocess.run(
                ["/usr/bin/sips", "-s", "format", "tiff", str(path), "--out", str(target)],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise UnreadableImageError(
                f"macOS could not decode {Path(path).name}: {error}"
            ) from error
        if result.returncode != 0 or not target.is_file():
            # sips ends an error with a pointer to its manual, which says nothing.
            detail = [
                line
                for line in (result.stderr or result.stdout).strip().splitlines()
                if not line.startswith("Try '")
            ]
            raise UnreadableImageError(
                f"macOS could not decode {Path(path).name}"
                + (f": {detail[-1][:200]}" if detail else ".")
            )
        try:
            image = Image.open(io.BytesIO(target.read_bytes()))
            image.load()
        except (OSError, SyntaxError, EOFError, ValueError) as error:
            # sips reports success for some truncated files and writes a broken TIFF.
            raise UnreadableImageError(
                f"macOS could not decode {Path(path).name}: the file looks damaged."
            ) from error
    if image.mode == "RGBA" and image.getchannel("A").getextrema() == (255, 255):
        # sips always writes an alpha channel; an opaque one is not transparency.
        icc, exif = image.info.get("icc_profile"), image.getexif()
        image = image.convert("RGB")
        if icc:
            image.info["icc_profile"] = icc
        image.getexif().update(exif)
    return image


def require_output_format(format: str) -> None:
    """Fail before any inference when this computer cannot write ``format``."""
    if format.lower() == "heic" and sys.platform != "darwin":
        raise ValueError("HEIC output needs macOS, which provides the HEIC encoder.")


def _encode_with_macos(image: Image.Image, destination: Path, quality: int, **kwargs: Any) -> None:
    """Write HEIC with macOS (ImageIO, through /usr/bin/sips), the mirror of the decoder.

    LocalSR ships no HEVC encoder: Apple licenses its own for apps that use the system
    API. Pillow writes a lossless TIFF that carries the colour profile, the allow-listed
    EXIF tags and any transparency; sips turns it into HEIC at the chosen quality.
    """
    require_output_format("heic")
    with tempfile.TemporaryDirectory(prefix="localsr-encode-", dir=destination.parent) as work:
        source = Path(work) / "source.tiff"
        image.save(source, format="TIFF", compression="tiff_deflate", **kwargs)
        try:
            result = subprocess.run(
                [
                    "/usr/bin/sips",
                    "-s",
                    "format",
                    "heic",
                    "-s",
                    "formatOptions",
                    str(max(1, min(100, int(quality)))),
                    str(source),
                    "--out",
                    str(destination),
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OSError(f"macOS could not write the HEIC image: {error}") from error
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise OSError(
            "macOS could not write the HEIC image" + (f": {detail[-1][:200]}" if detail else ".")
        )


def open_image(path: str | Path) -> Image.Image:
    """Open a still image: through macOS for HEIC/HEIF and JPEG XL, else Pillow."""
    if is_system_decoded(path):
        return _decode_with_macos(str(path))
    return Image.open(path)


def open_photo(path: str | Path) -> Image.Image:
    """Open any supported still image as pixels: camera RAW is developed by LibRaw."""
    if is_raw_input(path):
        return Image.fromarray(_develop_raw(str(path)))
    return open_image(path)


def _develop_raw(path: str) -> np.ndarray:
    # Keep LibRaw out of the GUI process. ImageManager is instantiated by the
    # isolated worker for real jobs, and the import occurs only for RAW input.
    import rawpy

    try:
        with rawpy.imread(path) as raw:
            return raw.postprocess(
                use_camera_wb=True,
                use_auto_wb=False,
                output_color=rawpy.ColorSpace.sRGB,
                output_bps=8,
            )
    except Exception as error:
        # LibRaw's own errors are bytes such as b'Input/output error'.
        if isinstance(error, getattr(rawpy, "LibRawError", ())):
            raise _unreadable(path) from error
        raise


class ImageManager:
    def __init__(self):
        self.original_mode = "RGB"
        # What loading and saving left out or changed that the user should know about.
        self.notes: list[str] = []
        self.alpha_channel = None
        # A grey source has no colour, so save() keeps only the luma a model returns.
        self.grayscale = False

    def load(self, path: str) -> dict:
        """
        Loads an image safely.
        - Applies EXIF orientation.
        - Checks ICC profile. If present, converts to sRGB.
        - If RGBA, extracts alpha channel.
        - Returns a dict containing the tensor and metadata.
        A file that cannot be decoded raises UnreadableImageError, whatever library it came
        from; the library's own message stays as the chained cause.
        """
        try:
            return self._load(path)
        except Exception as error:
            if _is_decode_failure(error):
                raise _unreadable(path) from error
            raise

    def _load(self, path: str) -> dict:
        require_ordinary_source(path)
        self.notes = []
        self.alpha_channel = None
        self.grayscale = False
        is_raw = is_raw_input(path)

        # Pillow can read metadata from many DNG containers even though it cannot
        # reliably develop their sensor data. Metadata extraction therefore stays
        # separate from the LibRaw pixel decode below.
        metadata_img = None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                metadata_img = open_image(path)
            # The size comes from the header; nothing has been decoded yet.
            if not is_raw:
                require_pixel_budget(metadata_img.width, metadata_img.height)
        except Image.DecompressionBombError as error:
            raise ValueError(TOO_LARGE_MESSAGE) from error
        except (OSError, SyntaxError, ValueError) as error:
            if not is_raw:
                if isinstance(error, UnidentifiedImageError) and _is_float_tiff(path):
                    raise UnreadableImageError(
                        f"{Path(path).name} is a floating-point colour TIFF, which LocalSR cannot "
                        "read. Save an 8-bit or 16-bit copy and add that instead."
                    ) from error
                raise
            # Pillow reads DNG's TIFF structure but not most other RAW containers;
            # their pixels still come from LibRaw below.
            warnings.warn(
                f"Could not read RAW metadata: {error}",
                RuntimeWarning,
                stacklevel=2,
            )

        if metadata_img is not None and metadata_img.format == "PNG":
            counted = _png_scanline_bytes(path)
            if counted is not None and counted[1] < counted[0]:
                raise _unreadable(path)

        frames = getattr(metadata_img, "n_frames", 1)
        if frames > 1 and metadata_img.format in ANIMATED_OR_PAGED_FORMATS:
            self.notes.append(
                f"{Path(path).name} has {frames} frames or pages; only the first one was used."
            )

        # Copy a deliberately small privacy allow-list. In particular, GPS,
        # camera serial/device identity, embedded thumbnails, software, and host
        # names never cross into an output file.
        safe_exif = {}
        try:
            exif_data = metadata_img.getexif() if metadata_img is not None else None
            if exif_data is not None:
                # 274 is Orientation.
                # Descriptive authorship/time tags only. Dimension and
                # orientation tags are regenerated from the actual output.
                for k, v in exif_data.items():
                    if k in SAFE_EXIF_TAGS:
                        safe_exif[k] = v
        except (OSError, SyntaxError, TypeError, ValueError) as error:
            warnings.warn(
                f"Could not read image metadata: {error}",
                RuntimeWarning,
                stacklevel=2,
            )

        sample_bits = 16
        if metadata_img is not None:
            bits = getattr(metadata_img, "tag_v2", {}).get(258)
            if isinstance(bits, tuple) and len(bits) == 1:
                bits = bits[0]
            if isinstance(bits, int) and 8 < bits <= 16:
                sample_bits = bits

        if is_raw:
            try:
                rgb = _develop_raw(path)
                img = Image.fromarray(rgb)
                converted_icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
            finally:
                if metadata_img is not None:
                    metadata_img.close()
        else:
            if metadata_img is None:  # pragma: no cover - guarded by the exception above
                raise OSError(f"Could not open image: {path}")
            img = ImageOps.exif_transpose(metadata_img)
            if img.mode == "F":
                img, float_range = _float_grey_to_16_bit(img)
                if float_range is not None:
                    self.notes.append(
                        f"{Path(path).name} holds floating-point values up to "
                        f"{float_range}; they were read as a range from 0 to {float_range}."
                    )
            converted_icc = None

        self.original_mode = img.mode
        grey_source = img.mode in GREY_MODES
        icc_profile_bytes = None if is_raw else img.info.get("icc_profile")
        has_invalid_icc = False

        if icc_profile_bytes:
            profile_image = img
            try:
                f = io.BytesIO(icc_profile_bytes)
                src_profile = ImageCms.ImageCmsProfile(f)
                srgb_profile = ImageCms.createProfile("sRGB")
                if img.mode in WIDE_GREY_MODES:
                    values = np.asarray(img).astype(np.float64)
                    maximum = (1 << sample_bits) - 1
                    if img.mode == "I" and values.max() <= 255:
                        maximum = 255
                    values = np.clip(np.rint(values * (65535 / maximum)), 0, 65535)
                    img = Image.fromarray(values.astype(np.uint16))
                img = _to_srgb(img, src_profile, srgb_profile)

                # Embed the output sRGB profile when saving.
                converted_icc = ImageCms.ImageCmsProfile(srgb_profile).tobytes()
            except (ImageCms.PyCMSError, OSError, ValueError) as e:
                img = profile_image
                has_invalid_icc = True
                warnings.warn(f"Invalid ICC profile: {e}", RuntimeWarning, stacklevel=2)

        if img.mode in WIDE_GREY_MODES:
            img = _narrow_wide_grey(img, sample_bits)

        # Handle Alpha. Palette and grey images (GIF, PNG) keep their transparency.
        if img.mode in ("LA", "PA") or (img.mode == "P" and "transparency" in img.info):
            img = img.convert("RGBA")
        if img.mode == "RGBA":
            self.alpha_channel = img.getchannel("A")
            img = img.convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")

        pixels = np.array(img)
        # Grey modes, grey palettes and RGB files whose channels are all equal.
        self.grayscale = grey_source or bool(
            (pixels[..., 0] == pixels[..., 1]).all() and (pixels[..., 1] == pixels[..., 2]).all()
        )
        arr = pixels.astype(np.float32) / 255.0
        tensor = torch.from_numpy(arr).permute(2, 0, 1)

        return {
            "tensor": tensor,
            "icc_profile": converted_icc,
            "safe_exif": safe_exif,
            "has_invalid_icc": has_invalid_icc,
            "original_mode": self.original_mode,
            "alpha_image": self.alpha_channel,
            "grayscale": self.grayscale,
        }

    def save(
        self,
        output_writer: Any,
        destination_path: str,
        format: str,
        quality: int = 98,
        preserve_metadata: bool = True,
        icc_profile: bytes | None = None,
        safe_exif: dict | None = None,
        scale: int = 1,
        output_scale: int | None = None,
        temporary_directory: str | os.PathLike | None = None,
        cancel_event: threading.Event | None = None,
    ):
        """
        Saves the memory-mapped numpy array to the final destination.
        Applies Alpha channel scaling if needed.
        Delegates to save_from_writer.
        """
        if safe_exif is None:
            safe_exif = {}
        writer_mmap = (
            output_writer.get_array() if hasattr(output_writer, "get_array") else output_writer
        )
        self.save_from_writer(
            writer_mmap=writer_mmap,
            destination_path=destination_path,
            format=format,
            quality=quality,
            preserve_metadata=preserve_metadata,
            icc_profile=icc_profile,
            safe_exif=safe_exif,
            scale=scale,
            output_scale=output_scale,
            temporary_directory=temporary_directory,
            cancel_event=cancel_event,
        )

    def _fit_jpeg_exif(self, exif: Image.Exif, copied: dict) -> None:
        """Leave the largest copied tags out until the EXIF block fits a JPEG.

        Encoding fails for a block over 64 KB, and a source tag can be that long. The
        tags LocalSR generates are small, so only copied ones are dropped.
        """
        dropped = []
        for tag in sorted(copied, key=lambda tag: len(str(copied[tag])), reverse=True):
            if len(exif.tobytes()) <= JPEG_EXIF_LIMIT:
                break
            del exif[tag]
            dropped.append(_EXIF_TAG_NAMES.get(tag, f"tag {tag}"))
        if dropped:
            self.notes.append(
                f"The photo's {', '.join(dropped)} text was too long to fit a JPEG file and "
                "was left out."
            )

    def save_from_writer(
        self,
        writer_mmap: np.ndarray,
        destination_path: str,
        format: str,
        quality: int,
        preserve_metadata: bool,
        icc_profile: bytes | None,
        safe_exif: dict,
        scale: int,
        output_scale: int | None = None,
        temporary_directory: str | os.PathLike | None = None,
        cancel_event: threading.Event | None = None,
    ):

        arr = np.transpose(writer_mmap, (1, 2, 0))
        out_img = Image.fromarray(arr, mode="RGB")

        final_scale = scale if output_scale is None else int(output_scale)
        if final_scale < 1 or final_scale > scale:
            raise ValueError(
                f"Output scale must be between 1 and the model's native {scale}× scale."
            )
        if final_scale != scale:
            target_size = (
                round(out_img.width * final_scale / scale),
                round(out_img.height * final_scale / scale),
            )
            out_img = out_img.resize(target_size, Image.Resampling.LANCZOS)

        if self.grayscale:
            # Colour-trained models add chroma to grey pages; a grey source
            # has none, so keep the model's luma only.
            luma = out_img.convert("L")
            out_img = Image.merge("RGB", (luma, luma, luma))

        if self.alpha_channel is not None and format.lower() in [
            "png",
            "tif",
            "tiff",
            "webp",
            "heic",
        ]:
            new_size = (
                self.alpha_channel.width * final_scale,
                self.alpha_channel.height * final_scale,
            )
            scaled_alpha = self.alpha_channel.resize(new_size, Image.Resampling.LANCZOS)
            out_img.putalpha(scaled_alpha)

        kwargs = {}
        if preserve_metadata:
            if icc_profile:
                kwargs["icc_profile"] = icc_profile

            exif = out_img.getexif()
            for k, v in safe_exif.items():
                exif[k] = v
            # Enforce orientation and output dimensions even when the source
            # carried no other safe fields. These generated structural tags do
            # not reintroduce private camera or location metadata.
            exif[274] = 1
            exif[40962] = out_img.width
            exif[40963] = out_img.height
            if format.lower() in ("jpg", "jpeg"):
                self._fit_jpeg_exif(exif, safe_exif)
            kwargs["exif"] = exif

        fmt = format.lower()
        destination = Path(destination_path)
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Cancelled before image export.")
        # The native host provides a per-job directory on the output's
        # filesystem, so even a force-stopped encode can be cleaned up.
        tmp_path = owned_temporary(temporary_directory or destination.parent, "image")
        try:
            if fmt in ["jpg", "jpeg"]:
                out_img.save(tmp_path, format="JPEG", quality=quality, subsampling=0, **kwargs)
            elif fmt == "png":
                out_img.save(tmp_path, format="PNG", **kwargs)
            elif fmt in ["tif", "tiff"]:
                out_img.save(tmp_path, format="TIFF", compression="tiff_deflate", **kwargs)
            elif fmt == "webp":
                out_img.save(tmp_path, format="WEBP", quality=quality, method=6, **kwargs)
            elif fmt == "heic":
                _encode_with_macos(out_img, tmp_path, quality, **kwargs)
            else:
                out_img.save(tmp_path, format=fmt, **kwargs)

            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Cancelled during image export.")
            publish_output(tmp_path, destination)
        except Exception:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
            raise
