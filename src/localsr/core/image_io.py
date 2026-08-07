import io
import os
import warnings
from typing import Any

import numpy as np
import torch
from PIL import Image, ImageCms, ImageOps

from localsr.core.image_formats import is_raw_input


def _develop_dng(path: str) -> np.ndarray:
    # Keep LibRaw out of the GUI process. ImageManager is instantiated by the
    # isolated worker for real jobs, and the import occurs only for RAW input.
    import rawpy

    with rawpy.imread(path) as raw:
        return raw.postprocess(
            use_camera_wb=True,
            use_auto_wb=False,
            output_color=rawpy.ColorSpace.sRGB,
            output_bps=8,
        )


class ImageManager:
    def __init__(self):
        self.original_mode = "RGB"
        self.alpha_channel = None

    def load(self, path: str) -> dict:
        """
        Loads an image safely.
        - Applies EXIF orientation.
        - Checks ICC profile. If present, converts to sRGB.
        - If RGBA, extracts alpha channel.
        - Returns a dict containing the tensor and metadata.
        """
        self.alpha_channel = None
        is_raw = is_raw_input(path)

        # Pillow can read metadata from many DNG containers even though it cannot
        # reliably develop their sensor data. Metadata extraction therefore stays
        # separate from the LibRaw pixel decode below.
        metadata_img = None
        try:
            metadata_img = Image.open(path)
        except (OSError, SyntaxError, ValueError) as error:
            if not is_raw:
                raise
            warnings.warn(
                f"Could not read DNG metadata: {error}",
                RuntimeWarning,
                stacklevel=2,
            )

        # 1. Strip raw EXIF dimensions/thumbnails by just extracting what we need or resetting.
        # But for now, we'll just not copy EXIF natively. We will only copy safe tags.
        safe_exif = {}
        try:
            exif_data = metadata_img.getexif() if metadata_img is not None else None
            if exif_data is not None:
                # 274 is Orientation.
                # Safe tags: Copyright, DateTime, GPS, Make, Model, Software
                safe_tags = {
                    306: "DateTime",
                    315: "Artist",
                    316: "HostComputer",
                    33432: "Copyright",
                    34853: "GPSInfo",
                }
                for k, v in exif_data.items():
                    if k in safe_tags:
                        safe_exif[k] = v
        except (OSError, SyntaxError, TypeError, ValueError) as error:
            warnings.warn(
                f"Could not read image metadata: {error}",
                RuntimeWarning,
                stacklevel=2,
            )

        if is_raw:
            try:
                rgb = _develop_dng(path)
                img = Image.fromarray(rgb)
                converted_icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
            finally:
                if metadata_img is not None:
                    metadata_img.close()
        else:
            if metadata_img is None:  # pragma: no cover - guarded by the exception above
                raise OSError(f"Could not open image: {path}")
            img = ImageOps.exif_transpose(metadata_img)
            converted_icc = None

        # ICC Profile handling
        icc_profile_bytes = None if is_raw else img.info.get("icc_profile")
        has_invalid_icc = False

        if icc_profile_bytes:
            try:
                f = io.BytesIO(icc_profile_bytes)
                src_profile = ImageCms.ImageCmsProfile(f)
                srgb_profile = ImageCms.createProfile("sRGB")
                img = ImageCms.profileToProfile(img, src_profile, srgb_profile)

                # After conversion, the image is sRGB. We'll embed standard sRGB profile on save.
                # Pillow's srgb profile bytes:
                converted_icc = ImageCms.ImageCmsProfile(srgb_profile).tobytes()
            except (ImageCms.PyCMSError, OSError, ValueError) as e:
                has_invalid_icc = True
                warnings.warn(f"Invalid ICC profile: {e}", RuntimeWarning, stacklevel=2)

        self.original_mode = img.mode

        # Handle Alpha
        if img.mode == "RGBA":
            self.alpha_channel = img.getchannel("A")
            img = img.convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")

        arr = np.array(img).astype(np.float32) / 255.0
        tensor = torch.from_numpy(arr).permute(2, 0, 1)

        return {
            "tensor": tensor,
            "icc_profile": converted_icc,
            "safe_exif": safe_exif,
            "has_invalid_icc": has_invalid_icc,
            "original_mode": self.original_mode,
            "alpha_image": self.alpha_channel,
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

        # Handle Alpha scaling
        if self.alpha_channel is not None and format.lower() in ["png", "tif", "tiff"]:
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

            if safe_exif:
                exif = out_img.getexif()
                for k, v in safe_exif.items():
                    exif[k] = v
                # Enforce orientation 1
                exif[274] = 1
                kwargs["exif"] = exif

        fmt = format.lower()
        tmp_path = destination_path + ".tmp"
        try:
            if fmt in ["jpg", "jpeg"]:
                # Strip alpha if we were going to save as JPEG
                if out_img.mode == "RGBA":
                    out_img = out_img.convert("RGB")
                out_img.save(tmp_path, format="JPEG", quality=quality, subsampling=0, **kwargs)
            elif fmt == "png":
                out_img.save(tmp_path, format="PNG", **kwargs)
            elif fmt in ["tif", "tiff"]:
                # BigTIFF might be needed if extremely large, but PIL handles it if installed correctly with libtiff
                out_img.save(tmp_path, format="TIFF", compression="tiff_deflate", **kwargs)
            else:
                out_img.save(tmp_path, format=fmt, **kwargs)

            os.replace(tmp_path, destination_path)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise
