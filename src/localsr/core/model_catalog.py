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
    FACE = "face"
    VIDEO = "video"


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
    # For face fine-tunes: the model_id of the general model this face
    # model pairs with. Empty string for non-face models.
    pair_with: str = ""

    @property
    def size_megabytes(self) -> float:
        return self.size_bytes / 1_000_000


MODEL_CATALOG = (
    CatalogModel(
        model_id="hat_s_x4",
        name="HAT-S ×4 — Slim",
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
        pair_with="hat_s_x4_face",
    ),
    CatalogModel(
        model_id="hat_s_x4_face",
        name="HAT-S ×4 Face — Restoration",
        filename="base_95k_interp_a0p1.pth",
        description="Face-specialized HAT-S model blended for enhanced facial restoration while preserving clean fidelity.",
        size_bytes=40_484_805,
        sha256="92277daf002214307bea6f1e06b4fa745acdb7690728a0a9a619076e7bc8d7f2",
        download_url="https://github.com/HerRei/HAT/releases/download/v1.0.0-face-interp/base_95k_interp_a0p1.pth",
        architecture="HAT",
        native_scale=4,
        purposes=(ModelPurpose.FACE, ModelPurpose.PHOTO),
        quality_tier=QualityTier.HIGH,
        speed_tier=SpeedTier.MEDIUM,
        recommended_halo=16,
        source_url="https://github.com/HerRei/HAT",
        license_name="CC BY-NC-SA 4.0",
        author="HerRei / XPixel Group",
        memory_factor=0.65,
        time_factor=0.65,
        pair_with="hat_s_x4",
    ),
    CatalogModel(
        model_id="hat_l_x4_imagenet",
        name="HAT-L ×4 ImageNet — Large",
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
        model_id="denoise_realplksr_1x",
        name="RealPLKSR Denoise — Slim",
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
    CatalogModel(
        model_id="nafnet_sidd_width64",
        name="NAFNet SIDD Width64 — Large",
        filename="NAFNet-SIDD-width64.pth",
        description=(
            "The official NAFNet Width64 checkpoint trained for real camera noise in SIDD. "
            "Highest-fidelity denoising preset, but substantially larger and slower to download."
        ),
        size_bytes=464_154_961,
        sha256="cd685efaae01f7c4e9951f2deab05780079c8eb1e49ed664b72f6db04dabb445",
        download_url=(
            "https://huggingface.co/spaces/chuxiaojie/NAFNet/resolve/"
            "5964ed4955416df99210106b708e4a2df9e9eca0/"
            "NAFNet-SIDD-width64.pth?download=true"
        ),
        architecture="NAFNet",
        native_scale=1,
        purposes=(ModelPurpose.DENOISE, ModelPurpose.PHOTO),
        quality_tier=QualityTier.MAXIMUM,
        speed_tier=SpeedTier.MEDIUM,
        recommended_halo=32,
        source_url="https://github.com/megvii-research/NAFNet",
        license_name="MIT",
        author="Liangyu Chen, Xiaojie Chu, Xiangyu Zhang, and Jian Sun",
        memory_factor=1.45,
        time_factor=1.25,
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


@dataclass(frozen=True)
class ModelFile:
    """One file of a multi-file video model bundle.

    Field names deliberately match what download_model reads, so a bundle
    file rides the existing pinned-download machinery unchanged.
    """

    role: str
    filename: str
    size_bytes: int
    sha256: str
    download_url: str


@dataclass(frozen=True)
class CatalogVideoModel:
    """A temporal video restoration model: several files, one engine.

    Unlike single-image checkpoints these are not Spandrel-loadable; each
    family names vendored engine code (engine_kind) that knows how to load
    and run its bundle. Bundles install into their own directory and are
    downloaded on demand, so the application package never carries them.
    """

    model_id: str
    name: str
    description: str
    family: str
    engine_kind: str
    files: tuple[ModelFile, ...]
    license_name: str
    author: str
    source_url: str
    min_unified_memory_gb: int
    min_vram_gb: int
    temporal_window: int
    temporal_overlap: int

    @property
    def total_size_bytes(self) -> int:
        return sum(file.size_bytes for file in self.files)


# Populated when a vendored temporal engine lands; the infrastructure is
# exercised by tests against fixture bundles until then.
VIDEO_MODEL_CATALOG: tuple[CatalogVideoModel, ...] = ()
VIDEO_CATALOG_BY_ID = {model.model_id: model for model in VIDEO_MODEL_CATALOG}


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

    def bundle_dir_for(self, model: CatalogVideoModel) -> Path:
        return self.root / model.family

    def bundle_file_path(self, model: CatalogVideoModel, file: ModelFile) -> Path:
        return self.bundle_dir_for(model) / file.filename

    def is_bundle_installed(self, model: CatalogVideoModel) -> bool:
        for file in model.files:
            path = self.bundle_file_path(model, file)
            try:
                if not (path.is_file() and path.stat().st_size == file.size_bytes):
                    return False
            except OSError:
                return False
        return True


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
        headers={"User-Agent": f"LocalSR/0.0.2 (+{PROJECT_URL})"},
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


def download_bundle(
    model: CatalogVideoModel,
    store: ModelStore,
    progress_callback=None,
    cancel_event: threading.Event | None = None,
    opener=None,
) -> Path:
    """Download every file of a video model bundle with pinned verification.

    Each file goes through download_model's preflight, partial-file, and
    SHA-256 machinery. progress_callback receives (downloaded, total) in
    bytes across the whole bundle. Returns the bundle directory.
    """
    bundle_dir = store.bundle_dir_for(model)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    total = model.total_size_bytes
    completed = 0
    for file in model.files:
        destination = store.bundle_file_path(model, file)
        try:
            if destination.is_file() and destination.stat().st_size == file.size_bytes:
                completed += file.size_bytes
                if progress_callback is not None:
                    progress_callback(completed, total)
                continue
        except OSError:
            pass

        def file_progress(downloaded: int, _file_total: int, _completed: int = completed) -> None:
            if progress_callback is not None:
                progress_callback(_completed + downloaded, total)

        download_model(
            file,
            destination,
            progress_callback=file_progress,
            cancel_event=cancel_event,
            opener=opener,
        )
        completed += file.size_bytes
        if progress_callback is not None:
            progress_callback(completed, total)
    return bundle_dir
