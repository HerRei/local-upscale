"""Collect Intel runtime wheels that install native files outside torch/lib.

The UR adapters and some oneMKL kernels are loaded dynamically. ELF dependency
scanning cannot discover them, so use the installed wheel records instead.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import Distribution
from pathlib import Path

from localsr.core.xpu_runtime import (
    MANIFEST_NAME,
    REQUIRED_DISTRIBUTIONS,
    require_runtime_libraries,
)

RUNTIME_NAMES = frozenset({"dpcpp-cpp-rt", "impi-rt", "mkl", "tbb", "tcmlib", "umf"})
RUNTIME_PREFIXES = ("intel-", "onemkl-", "oneccl")
DEVICE_DATA_SUFFIXES = frozenset({".bc", ".spv", ".o"})


@dataclass
class RuntimeFiles:
    binaries: list[tuple[str, str]]
    datas: list[tuple[str, str]]
    metadata_names: list[str]


def collect_runtime(
    output: Path,
    *,
    torch_version: str,
    prefix: Path,
    distributions: Iterable[Distribution],
) -> RuntimeFiles:
    prefix = prefix.resolve()
    libraries: dict[str, Path] = {}
    device_data: dict[str, Path] = {}
    notices: list[tuple[str, str]] = []
    versions: dict[str, str] = {}
    for distribution in distributions:
        name = re.sub(r"[-_.]+", "-", distribution.metadata.get("Name", "")).lower()
        if name not in RUNTIME_NAMES and not name.startswith(RUNTIME_PREFIXES):
            continue
        records = distribution.files
        if records is None:
            raise RuntimeError(f"Intel runtime distribution has no installed file records: {name}")
        versions[name] = distribution.version
        for record in records:
            filename = Path(record).name
            is_library = re.fullmatch(r".+\.so(?:\.[0-9]+)*", filename) is not None
            parts = [part.lower() for part in Path(record).parts]
            # copy_metadata retains dist-info licenses; Intel also installs
            # compiler and oneMKL third-party notices outside site-packages.
            is_notice = not any(part.endswith(".dist-info") for part in parts) and (
                any(part in {"licensing", "licenses", "license"} for part in parts)
                or filename.lower().startswith(
                    ("license", "copying", "notice", "third-party", "third_party")
                )
            )
            if (
                not is_library
                and not is_notice
                and Path(filename).suffix not in DEVICE_DATA_SUFFIXES
            ):
                continue
            source = Path(distribution.locate_file(record)).resolve()
            if not source.is_relative_to(prefix) or not source.is_file():
                raise RuntimeError(
                    f"Intel runtime record is missing or outside the build environment: {record}"
                )
            if is_notice:
                notice_directory = Path("licenses/intel") / name / source.relative_to(prefix).parent
                notices.append((str(source), notice_directory.as_posix()))
                continue
            destination = libraries if is_library else device_data
            previous = libraries.get(filename) or device_data.get(filename)
            if previous is not None and previous != source:
                with previous.open("rb") as left, source.open("rb") as right:
                    if (
                        hashlib.file_digest(left, "sha256").digest()
                        != hashlib.file_digest(right, "sha256").digest()
                    ):
                        raise RuntimeError(
                            f"Intel runtime files collide after relocation: {filename}"
                        )
            destination[filename] = source
    if not REQUIRED_DISTRIBUTIONS.issubset(versions):
        raise RuntimeError(
            "XPU packaging requires the installed Intel SYCL and UR runtime distributions"
        )
    require_runtime_libraries(list(libraries))
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / MANIFEST_NAME
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "torch_version": torch_version,
                "distributions": versions,
                "libraries": sorted(libraries),
                "data_files": sorted(device_data),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return RuntimeFiles(
        binaries=[(str(source), ".") for _, source in sorted(libraries.items())],
        datas=[(str(source), ".") for _, source in sorted(device_data.items())]
        + [(str(manifest), "localsr")]
        + sorted(notices),
        metadata_names=sorted(versions),
    )
