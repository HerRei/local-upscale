import hashlib
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlparse

CATALOG_REVISION = "867e7c0ad519aba5d36b5bb4ef4a4a91781914c4"
CATALOG_BASE_URL = (
    f"https://huggingface.co/jaideepsingh/upscale_models/resolve/{CATALOG_REVISION}/HAT"
)
HAT_SOURCE_URL = "https://github.com/XPixelGroup/HAT"
PROJECT_URL = "https://github.com/HerRei/local-upscale"


class ModelPurpose(StrEnum):
    GENERAL = "general"
    PHOTO = "photo"
    ANIME = "anime"
    ILLUSTRATION = "illustration"
    DENOISE = "denoise"
    DEBLUR = "deblur"
    RESTORATION = "restoration"
    FACE = "face"
    VIDEO = "video"


class QualityTier(IntEnum):
    FAST = 1
    STANDARD = 1
    HIGH = 2
    MAXIMUM = 3
    ULTRA = 4


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
    speed_factor: float = 1.0
    vram_estimate_mb: int = 0
    # Reciprocal companion ID for general/face model pairs. Empty for
    # models without a compatible companion.
    pair_with: str = ""
    commercial_use_status: str = "allowed"

    @property
    def size_megabytes(self) -> float:
        return self.size_bytes / 1_000_000

    @property
    def scale(self) -> int:
        return self.native_scale

    @property
    def file_size_bytes(self) -> int:
        return self.size_bytes

    @property
    def purpose(self) -> ModelPurpose:
        return self.purposes[0] if self.purposes else ModelPurpose.GENERAL


# Type alias for interface contract compliance
ModelCatalogEntry = CatalogModel


# The detector is infrastructure for the optional face-aware path rather than
# a Spandrel restoration checkpoint, so it deliberately stays out of
# MODEL_CATALOG and the user-facing model picker.  It is fetched only when a
# face-aware job is requested and is subjected to the same HTTPS, size, hash,
# partial-download, and atomic-install policy as restoration models.
FACE_DETECTOR_MODEL = CatalogModel(
    model_id="yunet_face_detector_2023mar",
    name="YuNet Face Detector (2023mar)",
    filename="face_detection_yunet_2023mar.onnx",
    description="Small CPU face detector used to build feathered restoration masks.",
    size_bytes=232_589,
    sha256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    download_url=(
        "https://github.com/opencv/opencv_zoo/raw/refs/heads/main/"
        "models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
    ),
    architecture="YuNet",
    native_scale=1,
    purposes=(ModelPurpose.FACE,),
    quality_tier=QualityTier.STANDARD,
    speed_tier=SpeedTier.FAST,
    recommended_halo=0,
    source_url=("https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet"),
    license_name="MIT",
    author="Shiqi Yu · OpenCV Zoo",
    memory_factor=0.1,
    time_factor=0.1,
    speed_factor=1.0,
    commercial_use_status="allowed",
)


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
        speed_factor=0.65,
        vram_estimate_mb=2000,
        pair_with="hat_s_x4_face",
    ),
    CatalogModel(
        model_id="hat_s_x4_face",
        name="HAT-S ×4 Face — Restoration",
        filename="base_95k_interp_a0p1.pth",
        description=(
            "Face-specialized HAT-S checkpoint with unresolved training-data/weight rights. "
            "LocalSR can verify a user-supplied copy but does not auto-download it."
        ),
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
        license_name="Checkpoint rights unverified",
        author="HerRei / XPixel Group",
        memory_factor=0.65,
        time_factor=0.65,
        speed_factor=0.65,
        vram_estimate_mb=2000,
        pair_with="hat_s_x4",
        commercial_use_status="unclear",
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
        speed_factor=0.30,
        vram_estimate_mb=6000,
        pair_with="hat_l_x4_face",
    ),
    CatalogModel(
        model_id="hat_l_x4_face",
        name="HAT-L ×4 Face — Restoration",
        filename="hat_l_x4_face_task4.pth",
        description=(
            "Face-specialized HAT-L checkpoint blended for enhanced facial restoration while "
            "preserving clean-image fidelity. LocalSR can verify a user-supplied copy but does "
            "not auto-download it."
        ),
        size_bytes=165_676_233,
        sha256="8a5548208310fcc7195abd4e1cf17ed87faaf3e5c38e2edeb63a45ac5b9c2af4",
        download_url="https://github.com/HerRei/HAT/releases/download/v1.0.1-hat-l-face/hat_l_x4_face_task4.pth",
        architecture="HAT",
        native_scale=4,
        purposes=(ModelPurpose.FACE, ModelPurpose.PHOTO),
        quality_tier=QualityTier.MAXIMUM,
        speed_tier=SpeedTier.SLOW,
        recommended_halo=16,
        source_url="https://github.com/HerRei/HAT",
        license_name="Checkpoint rights unverified",
        author="HerRei / XPixel Group",
        memory_factor=1.8,
        time_factor=1.8,
        speed_factor=0.30,
        vram_estimate_mb=6000,
        pair_with="hat_l_x4_imagenet",
        commercial_use_status="unclear",
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
        speed_factor=0.85,
        vram_estimate_mb=1000,
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
        speed_factor=0.50,
        vram_estimate_mb=4000,
    ),
    CatalogModel(
        model_id="realplksr_hfa2k_anime_x4",
        name="RealPLKSR 4x HFA2k — Anime",
        filename="4xHFA2k_ludvae_realplksr_dysample.pth",
        description="Fast RealPLKSR model trained for anime, illustrations, and clean line art.",
        size_bytes=29_715_988,
        sha256="c6e44af18fd3159787b0dbf81d432a6c1ba12c736fc1184b107ed091e49e327c",
        download_url=(
            "https://github.com/Phhofm/models/releases/download/"
            "4xHFA2k_ludvae_realplksr_dysample/4xHFA2k_ludvae_realplksr_dysample.pth"
        ),
        architecture="RealPLKSR",
        native_scale=4,
        purposes=(ModelPurpose.ILLUSTRATION, ModelPurpose.ANIME, ModelPurpose.GENERAL),
        quality_tier=QualityTier.HIGH,
        speed_tier=SpeedTier.FAST,
        recommended_halo=16,
        source_url="https://github.com/Phhofm/models",
        license_name="CC-BY-0.4 (upstream; clarify)",
        author="Philip Hofmann",
        memory_factor=0.88,
        time_factor=0.85,
        speed_factor=0.85,
        vram_estimate_mb=800,
        commercial_use_status="unclear",
    ),
    CatalogModel(
        model_id="span_photo_x4",
        name="SPAN 4x NomosUni — Quick",
        filename="4xNomosUni_span_multijpg.pth",
        description="Ultra-lightweight SPAN photo upscaler used by the Quick Start preset.",
        size_bytes=4_546_346,
        sha256="3a9037c36de90e7825c030176c8e193dfd7897ef4b5df91b8bdc59ffb6ab65ca",
        download_url=(
            "https://github.com/Phhofm/models/releases/download/"
            "4xNomosUni_span_multijpg/4xNomosUni_span_multijpg.pth"
        ),
        architecture="SPAN",
        native_scale=4,
        purposes=(ModelPurpose.PHOTO, ModelPurpose.GENERAL),
        quality_tier=QualityTier.FAST,
        speed_tier=SpeedTier.FAST,
        recommended_halo=16,
        source_url="https://github.com/Phhofm/models",
        license_name="CC BY 4.0",
        author="Philip Hofmann",
        memory_factor=0.92,
        time_factor=0.96,
        speed_factor=0.96,
        vram_estimate_mb=500,
    ),
    CatalogModel(
        model_id="realplksr_nomoswebphoto_x4",
        name="RealPLKSR 4x NomosWebPhoto — Best",
        filename="4xNomosWebPhoto_RealPLKSR.pth",
        description="High-fidelity RealPLKSR photo upscaler used by the Best Quality preset.",
        size_bytes=29_683_482,
        sha256="a9db66c9b674c6a5025b6ef3bee71a57c33b8605d8a2de0980470f89002efbbe",
        download_url=(
            "https://github.com/Phhofm/models/releases/download/"
            "4xNomosWebPhoto_RealPLKSR/4xNomosWebPhoto_RealPLKSR.pth"
        ),
        architecture="RealPLKSR",
        native_scale=4,
        purposes=(ModelPurpose.PHOTO, ModelPurpose.GENERAL),
        quality_tier=QualityTier.ULTRA,
        speed_tier=SpeedTier.MEDIUM,
        recommended_halo=16,
        source_url="https://github.com/Phhofm/models",
        license_name="CC-BY-0.4 (upstream; clarify)",
        author="Philip Hofmann",
        memory_factor=0.80,
        time_factor=0.70,
        speed_factor=0.70,
        vram_estimate_mb=1200,
        commercial_use_status="unclear",
    ),
    CatalogModel(
        model_id="realesrgan_x2plus",
        name="Real-ESRGAN ×2 — Compact",
        filename="RealESRGAN_x2plus.pth",
        description=(
            "Official native 2× Real-ESRGAN checkpoint for general photos. It avoids the "
            "extra downsampling required by 4× checkpoints when a true 2× result is wanted."
        ),
        size_bytes=67_061_725,
        sha256="49fafd45f8fd7aa8d31ab2a22d14d91b536c34494a5cfe31eb5d89c2fa266abb",
        download_url=(
            "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth"
        ),
        architecture="RealESRGAN",
        native_scale=2,
        purposes=(ModelPurpose.PHOTO, ModelPurpose.GENERAL),
        quality_tier=QualityTier.HIGH,
        speed_tier=SpeedTier.MEDIUM,
        recommended_halo=16,
        source_url="https://github.com/xinntao/Real-ESRGAN",
        license_name="BSD-3-Clause",
        author="Xintao Wang et al.",
        memory_factor=1.05,
        time_factor=1.0,
        speed_factor=0.55,
        vram_estimate_mb=1800,
    ),
    CatalogModel(
        model_id="fbcnn_color",
        name="FBCNN Color — JPEG Restoration",
        filename="fbcnn_color.pth",
        description=(
            "Official blind JPEG artifact-removal checkpoint. This is a 1× restoration model, "
            "not a super-resolution model, and can be chained before upscaling."
        ),
        size_bytes=287_755_111,
        sha256="8b0e4ef23d59cf7ac934a342cb31a17619e4fa4a0b3374a9d78c5174312387e8",
        download_url=(
            "https://github.com/jiaxi-jiang/FBCNN/releases/download/v1.0/fbcnn_color.pth"
        ),
        architecture="FBCNN",
        native_scale=1,
        purposes=(ModelPurpose.RESTORATION, ModelPurpose.PHOTO),
        quality_tier=QualityTier.HIGH,
        speed_tier=SpeedTier.MEDIUM,
        recommended_halo=32,
        source_url="https://github.com/jiaxi-jiang/FBCNN",
        license_name="Apache-2.0",
        author="Jiaxi Jiang et al.",
        memory_factor=1.25,
        time_factor=1.15,
        speed_factor=0.45,
        vram_estimate_mb=2500,
    ),
    CatalogModel(
        model_id="nafnet_gopro_deblur",
        name="NAFNet GoPro Deblur",
        filename="NAFNet-GoPro-width64.pth",
        description="Single-image camera motion deblurring model",
        size_bytes=271_778_961,
        sha256="329d3ab4077b8d6b7ff61de376e483714667960bf85be027bf4335cda701196f",
        download_url=(
            "https://huggingface.co/mikestealth/nafnet-models/resolve/"
            "9526c38b626f6e8ca0c02e4a282859ac84d240a2/"
            "NAFNet-GoPro-width64.pth?download=true"
        ),
        architecture="NAFNet",
        native_scale=1,
        purposes=(ModelPurpose.DEBLUR, ModelPurpose.RESTORATION, ModelPurpose.PHOTO),
        quality_tier=QualityTier.HIGH,
        speed_tier=SpeedTier.MEDIUM,
        recommended_halo=32,
        source_url="https://github.com/megvii-research/NAFNet",
        license_name="MIT",
        author="Liangyu Chen et al. / megvii-research",
        memory_factor=0.75,
        time_factor=0.65,
        speed_factor=0.65,
        vram_estimate_mb=1500,
    ),
)

CATALOG_BY_ID = {model.model_id: model for model in MODEL_CATALOG}
CATALOG_BY_FILENAME = {model.filename: model for model in MODEL_CATALOG}


def get_models_for_purpose(purpose: ModelPurpose | str) -> list[CatalogModel]:
    """Return all single-image catalog models supporting the specified purpose."""
    target_purpose = ModelPurpose(purpose) if isinstance(purpose, str) else purpose
    return [model for model in MODEL_CATALOG if target_purpose in model.purposes]


def get_model_by_id(model_id: str) -> CatalogModel | None:
    """Look up a single-image catalog model by its unique ID."""
    return CATALOG_BY_ID.get(model_id)


def get_all_models() -> tuple[CatalogModel, ...]:
    """Return all single-image models in the catalog."""
    return MODEL_CATALOG


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


# Immutable revision-pinned URLs: numz/SeedVR2_comfyUI at commit
# 09ced71023636e9bc8cdf9cdecfb2625d1e691e8. Sizes and SHA-256 digests match
# the upstream model registry and the HuggingFace LFS metadata.
_SEEDVR2_BASE = (
    "https://huggingface.co/numz/SeedVR2_comfyUI/resolve/09ced71023636e9bc8cdf9cdecfb2625d1e691e8"
)
_SEEDVR2_VAE = ModelFile(
    role="vae",
    filename="ema_vae_fp16.safetensors",
    size_bytes=501_324_814,
    sha256="20678548f420d98d26f11442d3528f8b8c94e57ee046ef93dbb7633da8612ca1",
    download_url=f"{_SEEDVR2_BASE}/ema_vae_fp16.safetensors?download=true",
)

VIDEO_MODEL_CATALOG: tuple[CatalogVideoModel, ...] = (
    CatalogVideoModel(
        model_id="seedvr2_3b",
        name="SeedVR2-3B — Labs",
        description=(
            "Experimental one-step diffusion video restorer with temporal consistency "
            "inside each clip window. Supports NVIDIA CUDA, AMD ROCm and Apple Metal. "
            "Needs at least 16 GB of memory; output resolution affects usage."
        ),
        family="seedvr2_3b",
        engine_kind="seedvr2",
        files=(
            ModelFile(
                role="dit",
                filename="seedvr2_ema_3b_fp16.safetensors",
                size_bytes=6_783_018_808,
                sha256=("2fd0e03a3dad24e07086750360727ca437de4ecd456f769856e960ae93e2b304"),
                download_url=(f"{_SEEDVR2_BASE}/seedvr2_ema_3b_fp16.safetensors?download=true"),
            ),
            _SEEDVR2_VAE,
        ),
        license_name="Apache-2.0",
        author="ByteDance Seed · numz",
        source_url="https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler",
        min_unified_memory_gb=16,
        min_vram_gb=16,
        temporal_window=9,
        temporal_overlap=2,
    ),
    CatalogVideoModel(
        model_id="seedvr2_3b_fp8",
        name="SeedVR2-3B FP8 — Labs",
        description=(
            "Experimental FP8 SeedVR2 for NVIDIA CUDA and AMD ROCm GPUs. "
            "Smaller model download and weight storage than FP16; working memory "
            "depends on output resolution."
        ),
        family="seedvr2_3b",
        engine_kind="seedvr2",
        files=(
            ModelFile(
                role="dit",
                filename="seedvr2_ema_3b_fp8_e4m3fn.safetensors",
                size_bytes=3_391_544_696,
                sha256=("3bf1e43ebedd570e7e7a0b1b60d6a02e105978f505c8128a241cde99a8240cff"),
                download_url=(
                    f"{_SEEDVR2_BASE}/seedvr2_ema_3b_fp8_e4m3fn.safetensors?download=true"
                ),
            ),
            _SEEDVR2_VAE,
        ),
        license_name="Apache-2.0",
        author="ByteDance Seed · numz",
        source_url="https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler",
        min_unified_memory_gb=24,
        min_vram_gb=12,
        temporal_window=9,
        temporal_overlap=2,
    ),
)
VIDEO_CATALOG_BY_ID = {model.model_id: model for model in VIDEO_MODEL_CATALOG}


class ModelStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else default_model_directory()
        self._verification_cache: dict[
            tuple[str, int, str], tuple[tuple[int, int, int, int, int], bool]
        ] = {}

    def path_for(self, model: CatalogModel) -> Path:
        return self.root / model.filename

    def is_installed(self, model: CatalogModel) -> bool:
        return self._verified_file(self.path_for(model), model.size_bytes, model.sha256)

    def bundle_dir_for(self, model: CatalogVideoModel) -> Path:
        return self.root / model.family

    def bundle_file_path(self, model: CatalogVideoModel, file: ModelFile) -> Path:
        return self.bundle_dir_for(model) / file.filename

    def is_bundle_file_installed(self, model: CatalogVideoModel, file: ModelFile) -> bool:
        return self._verified_file(self.bundle_file_path(model, file), file.size_bytes, file.sha256)

    def is_bundle_installed(self, model: CatalogVideoModel) -> bool:
        for file in model.files:
            if not self.is_bundle_file_installed(model, file):
                return False
        return True

    def _verified_file(self, path: Path, expected_size: int, expected_sha256: str) -> bool:
        """Verify installed model bytes, caching only while filesystem metadata is unchanged."""
        try:
            stat = path.stat()
            if not path.is_file() or stat.st_size != expected_size:
                return False
        except OSError:
            return False
        signature = (
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        )
        key = (os.fspath(path.absolute()), expected_size, expected_sha256.lower())
        cached = self._verification_cache.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        verified = file_matches_checksum(path, expected_size, expected_sha256)
        self._verification_cache[key] = (signature, verified)
        return verified


def file_sha256(path: str | Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_matches_checksum(path: str | Path, expected_size: int, expected_sha256: str) -> bool:
    candidate = Path(path)
    try:
        if not candidate.is_file() or candidate.stat().st_size != expected_size:
            return False
        return file_sha256(candidate) == expected_sha256.lower()
    except OSError:
        return False


class ModelDownloadError(RuntimeError):
    pass


class ModelDownloadCancelled(ModelDownloadError):
    pass


def _open_download(request: urllib.request.Request, timeout: float) -> BinaryIO:
    if urlparse(request.full_url).scheme != "https":
        raise ModelDownloadError("Model downloads require HTTPS.")
    # B310 is safe here because the parsed scheme is required to be HTTPS above.
    return urllib.request.urlopen(request, timeout=timeout)  # nosec B310


def download_model(
    model: CatalogModel,
    destination: str | Path,
    progress_callback=None,
    cancel_event: threading.Event | None = None,
    opener=None,
    chunk_size: int = 1024 * 1024,
    max_attempts: int = 4,
    retry_wait_seconds: float = 2.0,
) -> Path:
    """Download with pinned SHA-256 verification, resume, and retries.

    A transient network failure (read timeout, dropped connection) keeps the
    partial file and retries with an HTTP Range request, so a multi-gigabyte
    download survives an unreliable connection. Cancellation also keeps the
    partial, letting a later attempt resume. Only a verification failure
    removes the partial; the final file appears atomically via os.replace.
    """
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
    open_request = opener or _open_download

    def check_cancel() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise ModelDownloadCancelled("Model download cancelled.")

    attempts = 0
    last_error: Exception | None = None
    while attempts < max_attempts:
        attempts += 1
        check_cancel()

        try:
            offset = partial_path.stat().st_size if partial_path.is_file() else 0
        except OSError:
            offset = 0
        if offset > model.size_bytes:
            try:
                partial_path.unlink()
            except FileNotFoundError:
                pass
            offset = 0

        headers = {"User-Agent": f"LocalSR (+{PROJECT_URL})"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(model.download_url, headers=headers)

        try:
            with (
                open_request(request, timeout=15.0) as response,
                partial_path.open("ab" if offset else "wb") as output,
            ):
                status = getattr(response, "status", 206 if offset else 200)
                if offset and status != 206:
                    # The server ignored the Range request; start over.
                    output.seek(0)
                    output.truncate()
                    offset = 0
                downloaded = offset
                if progress_callback is not None:
                    progress_callback(downloaded, model.size_bytes)
                while True:
                    check_cancel()
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback is not None:
                        progress_callback(downloaded, model.size_bytes)
        except ModelDownloadCancelled:
            raise
        except (OSError, urllib.error.URLError) as error:
            last_error = error
            if attempts >= max_attempts:
                break
            waited = 0.0
            while waited < retry_wait_seconds * attempts:
                check_cancel()
                time.sleep(min(0.2, retry_wait_seconds))
                waited += 0.2
            continue

        if downloaded < model.size_bytes:
            # The connection ended early without an exception; resume.
            last_error = ModelDownloadError(
                f"Incomplete model download: received {downloaded:,} of {model.size_bytes:,} bytes."
            )
            if attempts >= max_attempts:
                break
            continue
        if downloaded > model.size_bytes:
            try:
                partial_path.unlink()
            except FileNotFoundError:
                pass
            raise ModelDownloadError(
                f"Download produced {downloaded:,} bytes but expected "
                f"{model.size_bytes:,}. The file was removed."
            )

        hasher = hashlib.sha256()
        with partial_path.open("rb") as completed:
            while True:
                check_cancel()
                block = completed.read(chunk_size)
                if not block:
                    break
                hasher.update(block)
        if hasher.hexdigest() != model.sha256:
            try:
                partial_path.unlink()
            except FileNotFoundError:
                pass
            raise ModelDownloadError(
                "Downloaded model failed SHA-256 verification. The untrusted file was removed."
            )

        os.replace(partial_path, destination)
        return destination

    message = (
        f"Could not download model: {last_error}" if last_error else "Could not download model."
    )
    if isinstance(last_error, ModelDownloadError):
        raise last_error
    raise ModelDownloadError(message) from last_error


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
        if store.is_bundle_file_installed(model, file):
            completed += file.size_bytes
            if progress_callback is not None:
                progress_callback(completed, total)
            continue

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
