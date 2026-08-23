#!/usr/bin/env python3
"""Prepare a universal2 dependency environment for an Intel -> arm64 build.

PyInstaller can target arm64 while running on an Intel Mac only when Python and
every imported native extension are universal2.  This tool downloads the exact
x86_64 and arm64 wheel sets, merges paired native wheels with delocate-merge,
and writes a provenance manifest for the build metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from packaging.utils import canonicalize_name, parse_wheel_filename


@dataclass(frozen=True)
class WheelRecord:
    distribution: str
    version: str
    output: str
    sources: tuple[str, ...]
    source_sha256: tuple[str, ...]
    operation: str


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wheel_key(path: Path) -> tuple[str, str]:
    name, version, _build, _tags = parse_wheel_filename(path.name)
    return canonicalize_name(name), str(version)


def wheel_kind(path: Path) -> str:
    name = path.name.lower()
    if name.endswith("-none-any.whl"):
        return "pure"
    if "universal2" in name:
        return "universal2"
    if "arm64" in name:
        return "arm64"
    if "x86_64" in name:
        return "x86_64"
    return "other"


def is_dual_arch_wheel(path: Path) -> bool:
    name = path.name.lower()
    return "universal2" in name or ("x86_64" in name and "arm64" in name)


def index_wheels(directory: Path) -> dict[tuple[str, str], list[Path]]:
    result: dict[tuple[str, str], list[Path]] = {}
    for path in sorted(directory.glob("*.whl")):
        result.setdefault(wheel_key(path), []).append(path)
    return result


def select_one(paths: list[Path], allowed: set[str], key: tuple[str, str]) -> Path | None:
    matches = [path for path in paths if wheel_kind(path) in allowed]
    if len(matches) > 1:
        fingerprints = {(path.name, sha256(path)) for path in matches}
        if len(fingerprints) == 1:
            return matches[0]
        names = ", ".join(path.name for path in matches)
        raise RuntimeError(f"Ambiguous wheels for {key}: {names}")
    return matches[0] if matches else None


def copy_verified(source: Path, output_dir: Path) -> Path:
    destination = output_dir / source.name
    if destination.exists() and sha256(destination) != sha256(source):
        raise RuntimeError(f"Refusing to overwrite different wheel: {destination}")
    if not destination.exists():
        shutil.copy2(source, destination)
    return destination


def lipo_arches(path: Path, lipo: str = "lipo") -> set[str]:
    output = subprocess.check_output([lipo, "-archs", str(path)], text=True)
    return set(output.split())


def make_fat_binary(
    x86_source: Path,
    arm_source: Path,
    destination: Path,
    *,
    lipo: str = "lipo",
    codesign: str = "codesign",
) -> None:
    subprocess.run(
        [lipo, "-create", str(x86_source), str(arm_source), "-output", str(destination)],
        check=True,
    )
    subprocess.run([codesign, "--force", "--sign", "-", str(destination)], check=True)
    arches = lipo_arches(destination, lipo)
    if arches != {"x86_64", "arm64"}:
        raise RuntimeError(f"OpenMP merge produced {sorted(arches)} for {destination}")


def atomic_replace(source: Path, destination: Path) -> None:
    with tempfile.NamedTemporaryFile(
        dir=destination.parent,
        prefix=f".{destination.name}.ci-",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
    try:
        shutil.copyfile(source, temporary)
        os.chmod(temporary, destination.stat().st_mode)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def normalize_torch_openmp(
    site_packages: Path,
    *,
    lipo: str = "lipo",
    codesign: str = "codesign",
) -> dict[str, object]:
    """Pair Torch's differently named Intel/ARM OpenMP runtime slices.

    torch 2.2.2's Intel wheel calls its OpenMP runtime libiomp5.dylib, while
    its Apple Silicon wheel calls the same ABI-compatible LLVM runtime
    libomp.dylib. delocate cannot merge binaries with different basenames, so
    create fat aliases explicitly. Both aliases are retained because each
    architecture's Torch load commands refer to its original name.
    """

    site_packages = site_packages.resolve()
    torch_iomp = site_packages / "torch" / "lib" / "libiomp5.dylib"
    functorch_iomp = site_packages / "functorch" / ".dylibs" / "libiomp5.dylib"
    functorch_omp = site_packages / "functorch" / ".dylibs" / "libomp.dylib"
    required = (torch_iomp, functorch_iomp, functorch_omp)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("Missing expected Torch OpenMP libraries: " + ", ".join(missing))
    expected = {
        torch_iomp: {"x86_64"},
        functorch_iomp: {"x86_64"},
        functorch_omp: {"arm64"},
    }
    for path, arches in expected.items():
        actual = lipo_arches(path, lipo)
        if actual != arches:
            raise RuntimeError(f"Unexpected pre-normalization slices for {path}: {sorted(actual)}")

    source_hashes = {str(path.relative_to(site_packages)): sha256(path) for path in required}
    with tempfile.TemporaryDirectory(dir=site_packages, prefix=".localsr-openmp-") as directory:
        workspace = Path(directory)
        torch_fat = workspace / "torch-libiomp5.dylib"
        functorch_fat = workspace / "functorch-openmp.dylib"
        make_fat_binary(
            torch_iomp,
            functorch_omp,
            torch_fat,
            lipo=lipo,
            codesign=codesign,
        )
        make_fat_binary(
            functorch_iomp,
            functorch_omp,
            functorch_fat,
            lipo=lipo,
            codesign=codesign,
        )
        atomic_replace(torch_fat, torch_iomp)
        atomic_replace(functorch_fat, functorch_iomp)
        atomic_replace(functorch_fat, functorch_omp)

    outputs = []
    for path in required:
        arches = lipo_arches(path, lipo)
        if arches != {"x86_64", "arm64"}:
            raise RuntimeError(f"Post-normalization slice failure for {path}: {sorted(arches)}")
        outputs.append(
            {
                "path": str(path.relative_to(site_packages)),
                "architectures": sorted(arches),
                "sha256": sha256(path),
            }
        )
    return {
        "schema_version": 1,
        "operation": "pair-torch-openmp-aliases",
        "source_sha256": source_hashes,
        "outputs": outputs,
    }


def merge_wheel_sets(
    x86_dir: Path,
    arm_dir: Path,
    output_dir: Path,
    delocate_merge: str | None = None,
) -> list[WheelRecord]:
    output_dir.mkdir(parents=True, exist_ok=True)
    x86 = index_wheels(x86_dir)
    arm = index_wheels(arm_dir)
    keys = sorted(set(x86) | set(arm))
    records: list[WheelRecord] = []

    for key in keys:
        left = x86.get(key, [])
        right = arm.get(key, [])
        reusable = select_one(left + right, {"pure", "universal2"}, key)
        if reusable:
            destination = copy_verified(reusable, output_dir)
            records.append(
                WheelRecord(
                    distribution=key[0],
                    version=key[1],
                    output=destination.name,
                    sources=(reusable.name,),
                    source_sha256=(sha256(reusable),),
                    operation="copy-" + wheel_kind(reusable),
                )
            )
            continue

        x86_wheel = select_one(left, {"x86_64"}, key)
        arm_wheel = select_one(right, {"arm64"}, key)
        if not x86_wheel or not arm_wheel:
            available = ", ".join(path.name for path in left + right) or "none"
            raise RuntimeError(f"{key[0]}=={key[1]} lacks a paired x86_64/arm64 wheel: {available}")

        before = set(output_dir.glob("*.whl"))
        merge_executable = delocate_merge or str(Path(sys.executable).parent / "delocate-merge")
        subprocess.run(
            [merge_executable, str(arm_wheel), str(x86_wheel), "-w", str(output_dir)],
            check=True,
        )
        created = set(output_dir.glob("*.whl")) - before
        if len(created) != 1:
            names = ", ".join(path.name for path in sorted(created)) or "none"
            raise RuntimeError(f"delocate-merge produced {len(created)} wheels for {key}: {names}")
        destination = created.pop()
        if not is_dual_arch_wheel(destination):
            raise RuntimeError(f"Merged wheel lacks dual-architecture tags: {destination.name}")
        records.append(
            WheelRecord(
                distribution=key[0],
                version=key[1],
                output=destination.name,
                sources=(x86_wheel.name, arm_wheel.name),
                source_sha256=(sha256(x86_wheel), sha256(arm_wheel)),
                operation="delocate-merge",
            )
        )

    return records


def download(requirements: Path, destination: Path, platform_tag: str) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "--implementation=cp",
            "--python-version=3.11",
            f"--platform={platform_tag}",
            f"--dest={destination}",
            f"--requirement={requirements}",
        ],
        check=True,
    )


def command_prepare(args: argparse.Namespace) -> None:
    root = args.output.resolve()
    x86_dir = root / "downloads" / "x86_64"
    arm_dir = root / "downloads" / "arm64"
    merged_dir = root / "wheelhouse"
    download(args.requirements, x86_dir, "macosx_12_0_x86_64")
    download(args.requirements, arm_dir, "macosx_12_0_arm64")
    records = merge_wheel_sets(x86_dir, arm_dir, merged_dir, args.delocate_merge)
    manifest = {
        "schema_version": 1,
        "requirements": str(args.requirements.resolve()),
        "wheels": [asdict(record) for record in records],
    }
    (root / "wheel-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Prepared {len(records)} universal2/pure wheels in {merged_dir}")


def command_normalize_openmp(args: argparse.Namespace) -> None:
    report = normalize_torch_openmp(
        args.site_packages,
        lipo=args.lipo,
        codesign=args.codesign,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--requirements", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--delocate-merge")
    prepare.set_defaults(func=command_prepare)
    normalize = subparsers.add_parser("normalize-openmp")
    normalize.add_argument("--site-packages", type=Path, required=True)
    normalize.add_argument("--report", type=Path)
    normalize.add_argument("--lipo", default="lipo")
    normalize.add_argument("--codesign", default="codesign")
    normalize.set_defaults(func=command_normalize_openmp)
    return result


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
