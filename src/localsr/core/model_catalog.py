import hashlib
import os
import shutil
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import BinaryIO

CATALOG_REVISION = "867e7c0ad519aba5d36b5bb4ef4a4a91781914c4"
CATALOG_BASE_URL = (
    f"https://huggingface.co/jaideepsingh/upscale_models/resolve/{CATALOG_REVISION}/HAT"
)
HAT_SOURCE_URL = "https://github.com/XPixelGroup/HAT"
PROJECT_URL = "https://github.com/HerRei/local-upscale"


class ModelPurpose(StrEnum):
    GENERAL = "general"
    PHOTO = "photo"
    ILLUSTRATION = "illustration"
    DENOISE = "denoise"


class QualityTier(IntEnum):
    STANDARD = 1
    HIGH = 2
    MAXIMUM = 3


class SpeedTier(IntEnum):
    SLOW = 1
    MEDIUM = 2
    FAST = 3


@dataclass(frozen=True)
class CatalogModel:
    model_id: str
    name: str
    filename: str
    description: str
    size_bytes: int
    sha256: str
    download_url: str
    architecture: str = "Unknown"
    native_scale: int = 4
    purposes: tuple[ModelPurpose, ...] = (ModelPurpose.GENERAL,)
    quality_tier: QualityTier = QualityTier.STANDARD
    speed_tier: SpeedTier = SpeedTier.MEDIUM
    recommended_halo: int = 16
    source_url: str = HAT_SOURCE_URL
    license_name: str = "Apache-2.0"
    author: str = ""
    memory_factor: float = 1.0
    time_factor: float = 1.0

    @property
    def size_megabytes(self) -> float:
        return self.size_bytes / 1_000_000


MODEL_CATALOG = (
    CatalogModel(
        model_id="span_x4_official",
        name="SPAN ×4 — Ultra Fast",
        filename="4x-spanx4-ch48.pth",
        description=(
            "The official lightweight SPAN ×4 checkpoint. Fastest recommended choice for "
            "clean images and everyday upscaling."
        ),
        size_bytes=9_004_922,
        sha256="c79e716b8eb24182c1d7fcc74fa10ae074bdb34fee7c6e67c73053ff5498c667",
        download_url=(
            "https://objectstorage.us-phoenix-1.oraclecloud.com/n/ax6ygfvpvzka/b/"
            "open-modeldb-files/o/4x-spanx4-ch48.pth"
        ),
        architecture="SPAN",
        native_scale=4,
        purposes=(ModelPurpose.GENERAL, ModelPurpose.PHOTO, ModelPurpose.ILLUSTRATION),
        quality_tier=QualityTier.STANDARD,
        speed_tier=SpeedTier.FAST,
        recommended_halo=16,
        source_url="https://github.com/hongyuanyu/SPAN",
        license_name="Apache-2.0",
        author="Hongyuan Yu and SPAN contributors",
        memory_factor=0.18,
        time_factor=0.12,
    ),
    CatalogModel(
        model_id="nomos_web_photo_realplksr_x4",
        name="Nomos Web Photo ×4 — Fast Photo",
        filename="4xNomosWebPhoto_RealPLKSR.pth",
        description=(
            "A compact RealPLKSR model trained for photographs with realistic blur, noise, "
            "JPEG and WebP degradation."
        ),
        size_bytes=29_683_482,
        sha256="a9db66c9b674c6a5025b6ef3bee71a57c33b8605d8a2de0980470f89002efbbe",
        download_url=(
            "https://github.com/Phhofm/models/releases/download/"
            "4xNomosWebPhoto_RealPLKSR/4xNomosWebPhoto_RealPLKSR.pth"
        ),
        architecture="RealPLKSR",
        native_scale=4,
        purposes=(ModelPurpose.PHOTO,),
        quality_tier=QualityTier.HIGH,
        speed_tier=SpeedTier.FAST,
        recommended_halo=16,
        source_url=("https://github.com/Phhofm/models/releases/tag/4xNomosWebPhoto_RealPLKSR"),
        license_name="CC-BY-4.0",
        author="Philip Hofmann",
        memory_factor=0.38,
        time_factor=0.25,
    ),
    CatalogModel(
        model_id="hat_s_x4",
        name="HAT-S ×4 — Fast",
        filename="HAT-S_SRx4.pth",
        description="The lightest official HAT variant. Best default for laptops and smaller GPUs.",
        size_bytes=81_089_561,
        sha256="a92f81bd2c0c1aaa371a6e4d6cac69e749fde2e36196885ee47a4a3667542c9a",
        download_url=f"{CATALOG_BASE_URL}/HAT-S_SRx4.pth?download=true",
        architecture="HAT",
        native_scale=4,
        purposes=(ModelPurpose.GENERAL, ModelPurpose.PHOTO),
        quality_tier=QualityTier.HIGH,
        speed_tier=SpeedTier.MEDIUM,
        recommended_halo=16,
        author="XPixel Group",
        memory_factor=0.65,
        time_factor=0.65,
    ),
    CatalogModel(
        model_id="hat_x4_imagenet",
        name="HAT ×4 ImageNet — Balanced",
        filename="HAT_SRx4_ImageNet-pretrain.pth",
        description="The standard ImageNet-pretrained HAT model: strong fidelity at moderate cost.",
        size_bytes=85_137_601,
        sha256="4ee053c42461187846dc0e93aa5abd34591c0725a8e044a59000e92ee215e833",
        download_url=f"{CATALOG_BASE_URL}/HAT_SRx4_ImageNet-pretrain.pth?download=true",
        architecture="HAT",
        native_scale=4,
        purposes=(ModelPurpose.GENERAL, ModelPurpose.PHOTO),
        quality_tier=QualityTier.HIGH,
        speed_tier=SpeedTier.SLOW,
        recommended_halo=16,
        author="XPixel Group",
        memory_factor=1.0,
        time_factor=1.0,
    ),
    CatalogModel(
        model_id="hat_l_x4_imagenet",
        name="HAT-L ×4 ImageNet — Maximum",
        filename="HAT-L_SRx4_ImageNet-pretrain.pth",
        description="The largest official HAT variant. Highest cost; intended for capable hardware.",
        size_bytes=165_774_123,
        sha256="5992bd38522f2b8faf11ea4bd8ee08de92465bb66892166576999afc36d60043",
        download_url=f"{CATALOG_BASE_URL}/HAT-L_SRx4_ImageNet-pretrain.pth?download=true",
        architecture="HAT",
        native_scale=4,
        purposes=(ModelPurpose.GENERAL, ModelPurpose.PHOTO),
        quality_tier=QualityTier.MAXIMUM,
        speed_tier=SpeedTier.SLOW,
        recommended_halo=16,
        author="XPixel Group",
        memory_factor=1.8,
        time_factor=1.8,
    ),
    CatalogModel(
        model_id="scunet_color_real_psnr",
        name="SCUNet Real Denoise",
        filename="scunet_color_real_psnr.pth",
        description="A powerful state-of-the-art denoising model that excels at removing noise from real-world photos without losing fine details.",
        size_bytes=71_982_841,
        sha256="fa78899ba2caec9d235a900e91d96c689da71c42029230c2028b00f09f809c2e",
        download_url="https://github.com/cszn/KAIR/releases/download/v1.0/scunet_color_real_psnr.pth",
        architecture="SCUNet",
        native_scale=1,
        purposes=(ModelPurpose.DENOISE, ModelPurpose.PHOTO),
        quality_tier=QualityTier.MAXIMUM,
        speed_tier=SpeedTier.SLOW,
        source_url="https://github.com/cszn/KAIR",
        time_factor=1.5,
    ),
    CatalogModel(
        model_id="denoise_realplksr_1x",
        name="RealPLKSR Denoise (Fast)",
        filename="1xDeNoise_realplksr_otf.pth",
        description="A very fast, lightweight denoising model trained on Nomosv2 to rapidly clean up noisy photos.",
        size_bytes=29_559_554,
        sha256="f4774fbe13ceaa9df390343c2baaa980061458ef27292fa6aca6740d87608a8e",
        download_url="https://github.com/Phhofm/models/releases/download/1xDeNoise_realplksr_otf/1xDeNoise_realplksr_otf.pth",
        architecture="RealPLKSR",
        native_scale=1,
        purposes=(ModelPurpose.DENOISE, ModelPurpose.PHOTO),
        quality_tier=QualityTier.HIGH,
        speed_tier=SpeedTier.FAST,
        recommended_halo=8,
        source_url="https://github.com/Phhofm/models",
        license_name="CC-BY-4.0",
        author="Philip Hofmann",
        memory_factor=0.38,
        time_factor=0.25,
    ),
)

CATALOG_BY_ID = {model.model_id: model for model in MODEL_CATALOG}


def default_model_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "LocalSR" / "models"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "LocalSR" / "models"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "LocalSR" / "models"


class ModelStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else default_model_directory()

    def path_for(self, model: CatalogModel) -> Path:
        return self.root / model.filename

    def is_installed(self, model: CatalogModel) -> bool:
        path = self.path_for(model)
        try:
            return path.is_file() and path.stat().st_size == model.size_bytes
        except OSError:
            return False


class ModelDownloadError(RuntimeError):
    pass


class ModelDownloadCancelled(ModelDownloadError):
    pass


def _open_download(request: urllib.request.Request, timeout: float) -> BinaryIO:
    return urllib.request.urlopen(request, timeout=timeout)


def download_model(
    model: CatalogModel,
    destination: str | Path,
    progress_callback=None,
    cancel_event: threading.Event | None = None,
    opener=None,
    chunk_size: int = 1024 * 1024,
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    required_disk = int(model.size_bytes * 1.15)
    available_disk = shutil.disk_usage(destination.parent).free
    if available_disk < required_disk:
        raise ModelDownloadError(
            f"Not enough disk space. Need about {required_disk / 1_000_000:.0f} MB, "
            f"but only {available_disk / 1_000_000:.0f} MB is free."
        )

    partial_path = destination.with_name(f"{destination.name}.part")
    hasher = hashlib.sha256()
    downloaded = 0
    success = False
    request = urllib.request.Request(
        model.download_url,
        headers={"User-Agent": f"LocalSR/0.3 (+{PROJECT_URL})"},
    )
    open_request = opener or _open_download

    try:
        with open_request(request, timeout=15.0) as response, partial_path.open("wb") as output:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise ModelDownloadCancelled("Model download cancelled.")
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                output.write(chunk)
                hasher.update(chunk)
                downloaded += len(chunk)
                if progress_callback is not None:
                    progress_callback(downloaded, model.size_bytes)

        if downloaded != model.size_bytes:
            raise ModelDownloadError(
                f"Incomplete model download: received {downloaded:,} of {model.size_bytes:,} bytes."
            )
        digest = hasher.hexdigest()
        if digest != model.sha256:
            raise ModelDownloadError(
                "Downloaded model failed SHA-256 verification. The untrusted file was removed."
            )

        os.replace(partial_path, destination)
        success = True
        return destination
    except ModelDownloadError:
        raise
    except (OSError, urllib.error.URLError) as error:
        raise ModelDownloadError(f"Could not download model: {error}") from error
    finally:
        if not success:
            try:
                partial_path.unlink()
            except FileNotFoundError:
                pass
