import hashlib
import os
import shutil
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

CATALOG_REVISION = "867e7c0ad519aba5d36b5bb4ef4a4a91781914c4"
CATALOG_BASE_URL = (
    f"https://huggingface.co/jaideepsingh/upscale_models/resolve/{CATALOG_REVISION}/HAT"
)
HAT_SOURCE_URL = "https://github.com/XPixelGroup/HAT"


@dataclass(frozen=True)
class CatalogModel:
    model_id: str
    name: str
    filename: str
    description: str
    size_bytes: int
    sha256: str
    download_url: str
    source_url: str = HAT_SOURCE_URL
    license_name: str = "Apache-2.0"
    memory_factor: float = 1.0
    time_factor: float = 1.0

    @property
    def size_megabytes(self) -> float:
        return self.size_bytes / 1_000_000


MODEL_CATALOG = (
    CatalogModel(
        model_id="hat_s_x4",
        name="HAT-S ×4 — Fast",
        filename="HAT-S_SRx4.pth",
        description="The lightest official HAT variant. Best default for laptops and smaller GPUs.",
        size_bytes=81_089_561,
        sha256="a92f81bd2c0c1aaa371a6e4d6cac69e749fde2e36196885ee47a4a3667542c9a",
        download_url=f"{CATALOG_BASE_URL}/HAT-S_SRx4.pth?download=true",
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
        memory_factor=1.8,
        time_factor=1.8,
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
        headers={"User-Agent": "LocalSR/0.2 (+https://github.com/XPixelGroup/HAT)"},
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
