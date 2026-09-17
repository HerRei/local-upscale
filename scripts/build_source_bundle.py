#!/usr/bin/env python3
"""Assemble the corresponding-source and notice bundle published next to a binary.

The bundle is derived from the binary's actual file inventory (a frozen worker,
an unpacked app bundle or an AppImage AppDir), so every license obligation is
checked against what is really shipped:

* LocalSR's own source at the built commit (``git archive``),
* the LGPL media runtime's corresponding source produced by
  ``packaging/ffmpeg/build_lgpl_media.py``,
* the FFmpeg-free OpenCV source/recipe when ``cv2`` is present,
* optional Ubuntu source packages for host libraries in a Linux AppDir,
* license/notice texts from every bundled Python distribution,
* ``THIRD_PARTY_NOTICES.md``, the codec policy and a SHA-256 manifest.

The script fails closed when a component that needs corresponding source is
present in the inventory but its source was not supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LICENSE_NAMES = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|AUTHORS|COPYRIGHT)([.\-_].*)?$", re.I)
MEDIA_LIBRARY = re.compile(r"(^|/)(lib)?(avcodec|avformat|avutil|swscale|swresample)[^/]*$")
OPENCV_MARKER = re.compile(r"(^|/)cv2(/|$)")
# Bundled copyleft libraries that need their exact corresponding source. Pass each
# with --extra-source NAME=PATH (an archive or directory).
EXTRA_SOURCE_RULES = {
    "libraw": (
        re.compile(r"(^|/)libraw(_r)?[.\-][^/]*$"),
        "LibRaw (LGPL-2.1/CDDL-1.0) bundled by rawpy; use the matching rawpy sdist",
    ),
    "gcc-runtime": (
        # A separate library on macOS and Linux; linked into the OpenBLAS DLL on Windows.
        re.compile(r"(^|/)libquadmath[.\-][^/]*$|(^|/)libopenblas[^/]*gcc_[^/]*\.dll$"),
        "libquadmath (LGPL-2.1) bundled by NumPy's OpenBLAS; use the exact GCC source the "
        "NumPy wheel was built with",
    ),
}
# Shared libraries an AppDir carries from the build host. Tauri's AppImage bundles the
# WebKitGTK/GTK stack with dozens of LGPL dependencies (cairo, pango, gnutls, libsoup,
# GStreamer, ...), so rather than naming a subset, every library Debian packages provided
# gets its source package; libraries no package owns (those inside wheels) are skipped.
HOST_SHARED_LIBRARY = re.compile(r"(^|/)lib[^/]*\.so(\.[0-9]+)*$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory(tree: Path) -> list[str]:
    return sorted(
        path.relative_to(tree).as_posix()
        for path in tree.rglob("*")
        if path.is_file() or path.is_symlink()
    )


def needs_media_source(files: list[str]) -> bool:
    return any(MEDIA_LIBRARY.search(name) for name in files)


def needs_opencv_source(files: list[str]) -> bool:
    return any(OPENCV_MARKER.search(name) for name in files)


def host_libraries(files: list[str]) -> list[str]:
    return sorted(
        name for name in files if "/usr/lib/" in f"/{name}" and HOST_SHARED_LIBRARY.search(name)
    )


def collect_licenses(tree: Path, destination: Path) -> list[str]:
    """Copy license texts from ``*.dist-info`` directories and top-level notices."""
    copied: list[str] = []
    for dist_info in sorted(tree.rglob("*.dist-info")):
        if not dist_info.is_dir():
            continue
        for candidate in sorted(dist_info.rglob("*")):
            if candidate.is_file() and LICENSE_NAMES.match(candidate.name):
                target = destination / dist_info.name / candidate.relative_to(dist_info)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, target)
                copied.append(target.relative_to(destination).as_posix())
    return copied


def git_archive(commit: str, destination: Path) -> None:
    with destination.open("wb") as handle:
        subprocess.run(
            ["git", "-C", str(ROOT), "archive", "--format=tar.gz", "--prefix=localsr/", commit],
            check=True,
            stdout=handle,
        )


def apt_sources(libraries: list[str], tree: Path, destination: Path) -> list[str]:
    """Download Ubuntu source packages for bundled host libraries (Linux only).

    Returns the ``source=version`` specs fetched; files no Debian package owns are ignored.
    """
    packages: set[str] = set()
    for name in libraries:
        query = subprocess.run(
            ["dpkg", "-S", Path(name).name], capture_output=True, text=True, check=False
        )
        for line in query.stdout.splitlines():
            package = line.split(":", 1)[0].strip()
            if package:
                packages.add(package)
    fetched: list[str] = []
    destination.mkdir(parents=True, exist_ok=True)
    for package in sorted(packages):
        status = subprocess.run(
            ["dpkg", "-s", package], capture_output=True, text=True, check=False
        ).stdout
        spec = source_package_spec(package, status)
        if spec in fetched:
            continue
        subprocess.run(["apt-get", "source", "--download-only", spec], cwd=destination, check=True)
        fetched.append(spec)
    return fetched


def source_package_spec(package: str, status: str) -> str:
    """Map ``dpkg -s`` output to ``source=version`` for ``apt-get source``."""
    fields = dict(
        line.split(":", 1) for line in status.splitlines() if ":" in line and not line[0].isspace()
    )
    version = fields.get("Version", "").strip()
    source = fields.get("Source", "").strip()
    if source:
        name, _, rest = source.partition(" ")
        if rest.startswith("(") and rest.endswith(")"):
            version = rest[1:-1]
        return f"{name}={version}" if version else name
    return f"{package}={version}" if version else package


def copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise SystemExit(f"required corresponding-source directory is missing: {source}")
    shutil.copytree(source, destination, dirs_exist_ok=True)


def build(arguments: argparse.Namespace) -> dict:
    tree = Path(arguments.inventory).resolve()
    output = Path(arguments.output).resolve()
    if not tree.is_dir():
        raise SystemExit(f"inventory directory does not exist: {tree}")
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    files = inventory(tree)
    report: dict = {"inventory": str(tree), "files": len(files), "components": {}}

    if arguments.commit:
        commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--verify", f"{arguments.commit}^{{commit}}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        git_archive(commit, output / "localsr-source.tar.gz")
        report["components"]["localsr"] = {"commit": commit}

    if needs_media_source(files):
        if not arguments.media_source:
            raise SystemExit(
                "the inventory contains FFmpeg libraries; pass --media-source with the "
                "corresponding-source directory from build_lgpl_media.py"
            )
        copy_tree(Path(arguments.media_source), output / "media-runtime")
        report["components"]["media-runtime"] = {"source": arguments.media_source}

    if needs_opencv_source(files):
        if not arguments.opencv_source:
            raise SystemExit(
                "the inventory contains OpenCV; pass --opencv-source with its pinned source, "
                "patch and build recipe"
            )
        copy_tree(Path(arguments.opencv_source), output / "opencv")
        report["components"]["opencv"] = {"source": arguments.opencv_source}

    extras = dict(item.split("=", 1) for item in (arguments.extra_source or []))
    for name, (pattern, description) in EXTRA_SOURCE_RULES.items():
        if not any(pattern.search(file) for file in files):
            continue
        if name not in extras:
            raise SystemExit(
                f"the inventory contains {description}; pass --extra-source {name}=PATH"
            )
        supplied = Path(extras[name])
        target = output / "extra-sources" / name
        if supplied.is_dir():
            copy_tree(supplied, target)
        elif supplied.is_file():
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(supplied, target / supplied.name)
        else:
            raise SystemExit(f"--extra-source {name} does not exist: {supplied}")
        report["components"][name] = {"source": str(supplied)}

    host = host_libraries(files)
    if host:
        report["components"]["host-libraries"] = host
        if arguments.apt_sources:
            report["components"]["ubuntu-source-packages"] = apt_sources(
                host, tree, output / "ubuntu-sources"
            )
        elif not arguments.allow_missing_host_sources:
            raise SystemExit(
                "the inventory bundles host libraries; run on the build host with "
                "--apt-sources to include their Ubuntu source packages"
            )

    licenses = collect_licenses(tree, output / "licenses")
    report["components"]["python-distribution-licenses"] = len(licenses)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, output / name)
    shutil.copy2(ROOT / "packaging" / "ffmpeg" / "codec-policy.json", output / "codec-policy.json")
    (output / "README.md").write_text(
        "# LocalSR corresponding source and notices\n\n"
        "This archive accompanies one LocalSR binary download. It contains LocalSR's source, the\n"
        "exact sources and build instructions of its LGPL media runtime and other components that\n"
        "require them, and the license texts of bundled components. This software uses libraries\n"
        "from the FFmpeg project under the LGPLv2.1; LocalSR does not own FFmpeg. To relink the\n"
        "media runtime, rebuild it with `media-runtime/README.md` and replace the bundled\n"
        "`libav*`/`libsw*` shared libraries.\n",
        encoding="utf-8",
    )
    manifest = {
        path.relative_to(output).as_posix(): sha256(path)
        for path in sorted(output.rglob("*"))
        if path.is_file()
    }
    report["sha256"] = manifest
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, help="frozen worker, app bundle or AppDir")
    parser.add_argument("--output", required=True, help="empty output directory")
    parser.add_argument("--commit", help="LocalSR commit to archive (for example HEAD)")
    parser.add_argument("--media-source", help="corresponding-source dir from build_lgpl_media")
    parser.add_argument("--opencv-source", help="OpenCV source, patch and recipe directory")
    parser.add_argument(
        "--extra-source",
        action="append",
        metavar="NAME=PATH",
        help=f"corresponding source for: {', '.join(EXTRA_SOURCE_RULES)}",
    )
    parser.add_argument(
        "--apt-sources", action="store_true", help="fetch Ubuntu sources for host libraries"
    )
    parser.add_argument(
        "--allow-missing-host-sources",
        action="store_true",
        help="for local inspection only; never for a published download",
    )
    arguments = parser.parse_args(argv)
    report = build(arguments)
    print(json.dumps({key: value for key, value in report.items() if key != "sha256"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
