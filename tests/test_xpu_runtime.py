from __future__ import annotations

import json
import os
import shutil
import sys
from importlib.metadata import PathDistribution
from pathlib import Path
from types import SimpleNamespace

import pytest

from localsr.core.xpu_runtime import MANIFEST_NAME, verify_bundled_runtime

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from collect_xpu_runtime import collect_runtime

TORCH_VERSION = "2.13.0+xpu"
LIBRARIES = [
    "libsycl.so.9",
    "libur_loader.so.0",
    "libur_adapter_level_zero.so.0",
    "libur_adapter_level_zero_v2.so.0",
    "libur_adapter_opencl.so.0",
]


def elf(machine: int = 62) -> bytes:
    header = bytearray(20)
    header[:6] = b"\x7fELF\x02\x01"
    header[18:20] = machine.to_bytes(2, "little")
    return bytes(header)


def distribution(prefix: Path, name: str, files: dict[str, bytes]) -> PathDistribution:
    site = prefix / "lib/python3.11/site-packages"
    info = site / f"{name.replace('-', '_')}-2026.0.0.dist-info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 2026.0.0\n")
    records = []
    for name, content in files.items():
        path = prefix / "lib" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        records.append(f"{os.path.relpath(path, site).replace(os.sep, '/')},,{len(content)}\n")
    (info / "RECORD").write_text("".join(records))
    return PathDistribution(info)


@pytest.fixture
def intel_runtime(tmp_path: Path):
    prefix = tmp_path / "venv"
    distributions = [
        distribution(prefix, "intel-cmplr-lib-ur", {name: elf() for name in LIBRARIES[1:]}),
        distribution(
            prefix,
            "intel-sycl-rt",
            {
                LIBRARIES[0]: elf(),
                "libsycl-fallback.spv": b"device library",
                "sycl.hpp": b"header",
                "libsycl.so.9.0.0-gdb.py": b"debugger helper",
            },
        ),
        distribution(prefix, "unrelated-package", {"unrelated.so": elf()}),
    ]
    return prefix, distributions


def assemble(tmp_path: Path, intel_runtime) -> Path:
    prefix, distributions = intel_runtime
    files = collect_runtime(
        tmp_path / "staging",
        torch_version=TORCH_VERSION,
        prefix=prefix,
        distributions=distributions,
    )
    root = tmp_path / "engine/_internal"
    for source, directory in files.binaries + files.datas:
        destination = root / directory / Path(source).name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return root


def test_collects_dynamically_loaded_adapters_and_device_data_from_wheel_records(
    tmp_path: Path, intel_runtime
) -> None:
    prefix, distributions = intel_runtime
    files = collect_runtime(
        tmp_path / "staging",
        torch_version=TORCH_VERSION,
        prefix=prefix,
        distributions=distributions,
    )
    assert {Path(path).name for path, _ in files.binaries} == set(LIBRARIES)
    assert {Path(path).name for path, _ in files.datas} == {MANIFEST_NAME, "libsycl-fallback.spv"}
    assert files.metadata_names == ["intel-cmplr-lib-ur", "intel-sycl-rt"]
    assert all(directory == "." for _, directory in files.binaries)


def test_collection_rejects_an_installed_record_that_points_outside_the_environment(
    tmp_path: Path, intel_runtime
) -> None:
    prefix, distributions = intel_runtime
    info = prefix / "lib/python3.11/site-packages/intel_sycl_rt-2026.0.0.dist-info"
    outside = tmp_path / "outside.so"
    outside.write_bytes(elf())
    with (info / "RECORD").open("a") as stream:
        stream.write(f"{os.path.relpath(outside, info.parent).replace(os.sep, '/')},,20\n")
    with pytest.raises(RuntimeError, match="outside the build environment"):
        collect_runtime(
            tmp_path / "staging",
            torch_version=TORCH_VERSION,
            prefix=prefix,
            distributions=distributions,
        )


def test_collection_refuses_an_incomplete_adapter_inventory(tmp_path: Path, intel_runtime) -> None:
    prefix, distributions = intel_runtime
    info = prefix / "lib/python3.11/site-packages/intel_cmplr_lib_ur-2026.0.0.dist-info/RECORD"
    info.write_text(
        "".join(line for line in info.read_text().splitlines(True) if "level_zero_v2" not in line)
    )
    with pytest.raises(RuntimeError, match="missing native libraries.*level_zero_v2"):
        collect_runtime(
            tmp_path / "staging",
            torch_version=TORCH_VERSION,
            prefix=prefix,
            distributions=distributions,
        )


def test_validates_bundled_runtime_without_a_gpu(tmp_path: Path, intel_runtime) -> None:
    result = verify_bundled_runtime(assemble(tmp_path, intel_runtime), TORCH_VERSION)
    assert result["xpu_runtime_files_verified"] is True
    assert result["xpu_runtime_library_count"] == len(LIBRARIES)
    assert result["xpu_runtime_data_file_count"] == 1


@pytest.mark.parametrize(
    "failure", ["missing-adapter", "wrong-architecture", "unsafe-path", "wrong-version"]
)
def test_rejects_incomplete_or_mismatched_installed_runtime(
    tmp_path: Path, intel_runtime, failure: str
) -> None:
    root = assemble(tmp_path, intel_runtime)
    manifest_path = root / "localsr" / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    if failure == "missing-adapter":
        (root / LIBRARIES[3]).unlink()
    elif failure == "wrong-architecture":
        (root / LIBRARIES[3]).write_bytes(elf(machine=183))
    elif failure == "unsafe-path":
        manifest["data_files"].append("../outside.spv")
    else:
        manifest["torch_version"] = "2.14.0+xpu"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError):
        verify_bundled_runtime(root, TORCH_VERSION)


def test_frozen_xpu_probe_requires_the_real_bundled_adapters(
    tmp_path: Path, intel_runtime, monkeypatch
) -> None:
    import torch

    from localsr.core import backend_validation

    root = assemble(tmp_path, intel_runtime)
    monkeypatch.setattr(backend_validation, "sys", SimpleNamespace(frozen=True, _MEIPASS=str(root)))
    monkeypatch.setattr(torch, "__version__", TORCH_VERSION)
    result = backend_validation.probe("Intel-XPU")
    assert result["xpu_runtime_files_verified"] is True
    assert result["cpu_inference_verified"] is True
    assert result["hardware_tested"] is False
    (root / LIBRARIES[3]).unlink()
    with pytest.raises(RuntimeError, match="file is missing"):
        backend_validation.probe("Intel-XPU")
