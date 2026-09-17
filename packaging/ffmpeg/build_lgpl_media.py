#!/usr/bin/env python3
"""Build an LGPL-2.1-or-later FFmpeg and a PyAV wheel linked against it.

The component set is derived from ``codec-policy.json`` next to this script and the
pinned sources from ``sources.json``. The recipe is standard-library only; it drives
meson/cmake/make/ninja, a private build virtual environment, and the platform wheel
repair tool (``delocate-wheel`` on macOS, ``auditwheel`` on Linux, ``delvewheel`` on
Windows).

On Windows the codec libraries and FFmpeg are built with MSYS2's MinGW-w64 UCRT64
toolchain (the codec libraries statically, FFmpeg as DLLs), ``lib.exe`` from the MSVC
developer environment turns FFmpeg's export definitions into import libraries, and PyAV
is compiled with MSVC against them, the same arrangement PyAV's own Windows wheels use.

Outputs in ``--output-dir``:

* ``av-<version>-*.whl``: the repaired wheel,
* ``manifest.json``: platform, versions, hashes, configure line, runtime metadata,
* ``corresponding-source/``: exact source archives, configure line, patches, this
  script, the policy, and rebuild/relink instructions.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path, PureWindowsPath

HERE = Path(__file__).resolve().parent
POLICY_PATH = HERE / "codec-policy.json"
SOURCES_PATH = HERE / "sources.json"
MACOS_DEPLOYMENT_TARGET = "12.0"
PYAV_VERSION = "18.1.0"

# Pinned Python build requirements for the private build virtual environment.
# Cython 3.2.9 is the last release that predates PyAV 18.1.0 (PyAV requires >=3.1,<4).
BUILD_REQUIREMENTS = ["pip==26.2.1", "setuptools==80.9.0", "wheel==0.46.2", "cython==3.2.9"]
MACOS_REPAIR_REQUIREMENTS = ["delocate==0.13.0"]
LINUX_REPAIR_REQUIREMENTS = ["auditwheel==6.8.2", "patchelf==0.19.1.0"]
WINDOWS_REPAIR_REQUIREMENTS = ["delvewheel==1.13.1"]

# MSYS2 with make, perl (libvpx's configure), diffutils and the UCRT64 packages gcc,
# binutils, meson, ninja, cmake, pkgconf, nasm and zlib; GitHub's Windows runners ship it
# in C:\msys64.
MSYS2_ROOT = Path(os.environ.get("LOCALSR_MSYS2_ROOT", r"C:\msys64"))
# What FFmpeg's DLLs may import on Windows: its own DLLs, Windows system libraries and the
# Universal CRT, plus the zlib and winpthreads DLLs from MSYS2, which are copied next to
# FFmpeg (zlib and MIT licences).
WINDOWS_SYSTEM_DLL = re.compile(
    r"^(kernel32|user32|gdi32|advapi32|bcrypt|ole32|oleaut32|shell32|shlwapi|ws2_32|secur32"
    r"|psapi|ucrtbase|msvcrt|api-ms-win-[a-z0-9-]+)\.dll$",
    re.IGNORECASE,
)
WINDOWS_TOOLCHAIN_DLLS = ("zlib1.dll", "libwinpthread-1.dll")

# FFmpeg's buffer/abuffer sources and sinks are part of libavfilter's public API; they are
# always compiled and registered manually, so configure has no switch for them.
ALWAYS_BUILT_FILTERS = {"buffer", "buffersink", "abuffer", "abuffersink"}

# Explicit belt-and-braces disables for the autodetected system features the policy
# forbids (``--disable-autodetect`` already covers them). Never-autodetected external
# libraries (x264, x265, openh264, lame, opencore-amr, gnutls) are not named here: they
# stay off by default, config.h is checked for them below, and naming them would embed
# "libx264"/"libx265" in the configuration string that release verifiers scan for.
EXPLICIT_DISABLES = [
    "--disable-videotoolbox",
    "--disable-audiotoolbox",
    "--disable-securetransport",
    "--disable-iconv",
    "--disable-bzlib",
    "--disable-lzma",
]

# config.h symbols that must be 0 in the configured tree.
FORBIDDEN_CONFIG_SYMBOLS = [
    "CONFIG_GPL",
    "CONFIG_NONFREE",
    "CONFIG_VERSION3",
    "CONFIG_VIDEOTOOLBOX",
    "CONFIG_AUDIOTOOLBOX",
    "CONFIG_SECURETRANSPORT",
    "CONFIG_ICONV",
    "CONFIG_BZLIB",
    "CONFIG_LZMA",
    "CONFIG_LIBX264",
    "CONFIG_LIBX265",
    "CONFIG_LIBOPENH264",
    "CONFIG_LIBMP3LAME",
    "CONFIG_LIBOPENCORE_AMRNB",
    "CONFIG_LIBOPENCORE_AMRWB",
    "CONFIG_GNUTLS",
    "CONFIG_OPENSSL",
    "CONFIG_NETWORK",
    "CONFIG_MEDIAFOUNDATION",
]

COMPONENT_KINDS = {
    # policy key -> (configure option name, config.mak suffix)
    "decoders": ("decoder", "DECODER"),
    "encoders": ("encoder", "ENCODER"),
    "parsers": ("parser", "PARSER"),
    "bsfs": ("bsf", "BSF"),
    "demuxers": ("demuxer", "DEMUXER"),
    "muxers": ("muxer", "MUXER"),
    "filters": ("filter", "FILTER"),
    "protocols": ("protocol", "PROTOCOL"),
}


class BuildError(RuntimeError):
    """A build step failed; the message is shown to the user."""


def log(message: str) -> None:
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
    capture: bool = False,
) -> str:
    """Run a command, appending its output to ``log_path``; raise BuildError on failure."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as log_stream:
        log_stream.write(f"\n$ (cd {shlex.quote(str(cwd))} && {shlex.join(command)})\n")
        log_stream.flush()
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        log_stream.write(result.stdout)
    if result.returncode != 0:
        tail = "\n".join(line[:400] for line in result.stdout.splitlines()[-30:])
        raise BuildError(
            f"Command failed with exit code {result.returncode}: {shlex.join(command)}\n"
            f"(full log: {log_path})\n{tail}"
        )
    return result.stdout if capture else ""


# ---------------------------------------------------------------------------
# Policy derivation
# ---------------------------------------------------------------------------


def load_policy(path: Path = POLICY_PATH) -> dict:
    return json.loads(path.read_text())


def load_sources(path: Path = SOURCES_PATH) -> dict[str, dict]:
    data = json.loads(path.read_text())
    return {entry["name"]: entry for entry in data["sources"]}


def derive_component_selection(
    policy: dict, available: dict[str, set[str]]
) -> tuple[dict[str, list[str]], list[str]]:
    """Map the policy to configure component names.

    ``available`` maps policy kinds to the names that ``configure --list-<kind>`` reports.
    Returns the names to enable per kind and human-readable deviation notes. Unknown names
    that are not a documented special case raise BuildError so the policy gets fixed.
    """
    selected: dict[str, list[str]] = {}
    notes: list[str] = []
    unknown: list[str] = []
    for kind in COMPONENT_KINDS:
        names = []
        for name in policy.get(kind, []):
            if name in available[kind]:
                names.append(name)
            elif kind == "filters" and name in ALWAYS_BUILT_FILTERS:
                notes.append(
                    f"filter '{name}' is always built into libavfilter (no configure switch); "
                    "not passed to --enable-filter"
                )
            else:
                unknown.append(f"{kind[:-1]} '{name}'")
        selected[kind] = names
    if unknown:
        raise BuildError(
            "codec-policy.json names components FFmpeg's configure does not know: "
            + ", ".join(unknown)
        )
    return selected, notes


def msys_path(path: str | os.PathLike) -> str:
    """``C:\\work\\prefix`` -> ``/c/work/prefix``, the form MSYS2 shell tools expect."""
    pure = PureWindowsPath(path)
    if not pure.drive:
        return pure.as_posix()
    return "/" + pure.drive.rstrip(":").lower() + pure.as_posix()[len(pure.drive) :]


def ffmpeg_configure_args(
    prefix: str | Path, selected: dict[str, list[str]], system: str, extra: list[str] | None = None
) -> list[str]:
    args = [
        f"--prefix={prefix}",
        "--disable-everything",
        "--disable-autodetect",
        "--disable-network",
        "--disable-programs",
        "--disable-doc",
        "--enable-shared",
        "--disable-static",
        "--enable-pic",
        "--enable-zlib",
        "--enable-libdav1d",
        "--enable-libsvtav1",
        "--enable-libvpx",
        "--enable-libopus",
        *EXPLICIT_DISABLES,
    ]
    for kind, (option, _suffix) in COMPONENT_KINDS.items():
        if selected.get(kind):
            args.append(f"--enable-{option}={','.join(selected[kind])}")
    if system == "Darwin":
        flag = f"-mmacosx-version-min={MACOS_DEPLOYMENT_TARGET}"
        # -dead_strip_dylibs drops CoreFoundation/CoreMedia/CoreVideo, which configure
        # links into libavutil unconditionally although no Apple media API is enabled.
        args += [f"--extra-cflags={flag}", f"--extra-ldflags={flag} -Wl,-dead_strip_dylibs"]
    if system == "Windows":
        # MinGW-w64 UCRT build: FFmpeg's own Win32 threads instead of winpthreads, the
        # static codec libraries resolved through pkg-config --static, and libgcc linked
        # in rather than shipped as a DLL.
        args += [
            "--target-os=mingw32",
            "--arch=x86_64",
            "--disable-pthreads",
            "--enable-w32threads",
            "--pkg-config-flags=--static",
            "--extra-ldflags=-static-libgcc",
        ]
    if extra:
        args += extra
    return args


def check_forbidden_flags(args: list[str], policy: dict) -> None:
    bad = [flag for flag in args if flag.split("=")[0] in policy["configure_forbidden_flags"]]
    if bad:
        raise BuildError(f"Forbidden configure flags derived: {bad}")


def parse_enabled_components(
    config_mak: str, available: dict[str, set[str]]
) -> dict[str, list[str]]:
    """Read ``ffbuild/config.mak`` and list the enabled components per policy kind.

    Only names that configure lists as components count: internal switches such as
    ``CONFIG_FRAME_THREAD_ENCODER`` share the naming scheme but are not encoders.
    """
    enabled: dict[str, list[str]] = {kind: [] for kind in COMPONENT_KINDS}
    for kind, (_option, suffix) in COMPONENT_KINDS.items():
        pattern = re.compile(rf"^CONFIG_([A-Z0-9_]+)_{suffix}=yes$", re.MULTILINE)
        names = {match.group(1).lower() for match in pattern.finditer(config_mak)}
        enabled[kind] = sorted(names & available[kind])
    return enabled


def configure_warnings(output: str) -> list[str]:
    """Return configure warnings that indicate a policy/derivation mismatch."""
    problems = []
    for line in output.splitlines():
        lowered = line.strip().lower()
        if "did not match anything" in lowered or lowered.startswith("warning: disabled "):
            problems.append(line.strip())
        elif lowered.startswith("unknown option") or "is not a valid option" in lowered:
            problems.append(line.strip())
    return problems


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def fetch_source(entry: dict, cache: Path) -> Path:
    """Return a verified archive path, downloading it into the cache if absent."""
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / entry["filename"]
    if target.exists():
        actual = sha256_file(target)
        if actual == entry["sha256"]:
            return target
        raise BuildError(
            f"{target} has SHA-256 {actual}, expected {entry['sha256']}. "
            "Remove the file to re-download it."
        )
    errors = []
    for url in [entry["url"], *entry.get("mirrors", [])]:
        partial = target.with_name(target.name + ".part")
        log(f"downloading {entry['name']} from {url}")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "LocalSR-build/1"})
            with urllib.request.urlopen(request, timeout=120) as response:
                with partial.open("wb") as out:
                    shutil.copyfileobj(response, out)
        except OSError as exc:
            errors.append(f"{url}: {exc}")
            partial.unlink(missing_ok=True)
            continue
        actual = sha256_file(partial)
        if actual != entry["sha256"]:
            errors.append(f"{url}: SHA-256 {actual} != {entry['sha256']}")
            partial.unlink(missing_ok=True)
            continue
        partial.rename(target)
        return target
    raise BuildError(f"Could not fetch a verified {entry['name']} archive: " + "; ".join(errors))


def extract_fresh(archive: Path, destination: Path) -> Path:
    """Extract an archive with a single top-level directory; return that directory."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        tar.extractall(destination, filter="data")
    children = [child for child in destination.iterdir() if not child.name.startswith(".")]
    if len(children) != 1 or not children[0].is_dir():
        raise BuildError(f"{archive.name} does not have a single top-level directory")
    return children[0]


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class Builder:
    def __init__(self, args: argparse.Namespace) -> None:
        self.system = platform.system()
        self.windows = self.system == "Windows"
        self.machine = platform.machine().lower()
        self.work = args.work_dir.resolve()
        self.output = args.output_dir.resolve()
        self.cache = args.source_cache.resolve()
        self.jobs = args.jobs
        self.prefix = self.work / "prefix"
        self.logs = self.work / "logs"
        self.stamps = self.work / "stamps"
        self.policy = load_policy()
        self.sources = load_sources()
        self.archives: dict[str, Path] = {}
        self.notes: list[str] = []
        self.ffmpeg_args: list[str] = []
        self.enabled_components: dict[str, list[str]] = {}
        self.available_components: dict[str, set[str]] = {}

    # -- helpers -----------------------------------------------------------

    def env(self) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "CPATH",
                "C_INCLUDE_PATH",
                "CPLUS_INCLUDE_PATH",
                "LIBRARY_PATH",
                "PKG_CONFIG_PATH",
                "PKG_CONFIG_LIBDIR",
                "CFLAGS",
                "CXXFLAGS",
                "LDFLAGS",
                "CMAKE_PREFIX_PATH",
                "DYLD_LIBRARY_PATH",
                "DYLD_FALLBACK_LIBRARY_PATH",
                "LD_LIBRARY_PATH",
                "PYTHONPATH",
                "VIRTUAL_ENV",
            }
        }
        pkgconfig = str(self.prefix / "lib" / "pkgconfig")
        # PKG_CONFIG_LIBDIR replaces the default search path so that Homebrew or distro
        # copies of dav1d/SVT-AV1/libvpx/opus (or x264/x265) can never be picked up.
        env["PKG_CONFIG_LIBDIR"] = pkgconfig
        env["PKG_CONFIG_PATH"] = pkgconfig
        env["SOURCE_DATE_EPOCH"] = env.get("SOURCE_DATE_EPOCH", "1767225600")
        if self.system == "Darwin":
            env["MACOSX_DEPLOYMENT_TARGET"] = MACOS_DEPLOYMENT_TARGET
        if self.system == "Linux":
            env["LD_LIBRARY_PATH"] = str(self.prefix / "lib")
        return env

    def path(self, path: Path) -> str:
        """A path argument for the native build tools (MSYS2 form on Windows)."""
        return msys_path(path) if self.windows else str(path)

    def sh(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        log_path: Path,
        capture: bool = False,
    ) -> str:
        """Run a native build tool; on Windows inside an MSYS2 UCRT64 login shell."""
        if not self.windows:
            return run(command, cwd=cwd, env=env, log_path=log_path, capture=capture)
        shell_env = dict(env)
        pkgconfig = msys_path(self.prefix / "lib" / "pkgconfig")
        shell_env.update(
            {
                "MSYSTEM": "UCRT64",
                "CHERE_INVOKING": "1",
                # A minimal PATH keeps MSVC's cl.exe and link.exe away from meson and cmake.
                "MSYS2_PATH_TYPE": "minimal",
                "CC": "gcc",
                "CXX": "g++",
                "PKG_CONFIG_LIBDIR": pkgconfig,
                "PKG_CONFIG_PATH": pkgconfig,
            }
        )
        script = f"cd {shlex.quote(msys_path(cwd))} && {shlex.join(command)}"
        bash = str(MSYS2_ROOT / "usr" / "bin" / "bash.exe")
        return run(
            [bash, "-lc", script], cwd=cwd, env=shell_env, log_path=log_path, capture=capture
        )

    def stamp_ok(self, step: str, signature: object) -> bool:
        stamp = self.stamps / f"{step}.json"
        return stamp.exists() and json.loads(stamp.read_text()) == signature

    def write_stamp(self, step: str, signature: object) -> None:
        self.stamps.mkdir(parents=True, exist_ok=True)
        (self.stamps / f"{step}.json").write_text(json.dumps(signature, indent=2))

    def tool(self, name: str) -> str:
        path = shutil.which(name)
        if path is None:
            raise BuildError(f"Required build tool '{name}' was not found on PATH")
        return path

    def tool_version(self, command: list[str]) -> str:
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except OSError:
            return "unavailable"
        text = (result.stdout or result.stderr).strip().splitlines()
        return text[0] if text else "unknown"

    # -- steps -------------------------------------------------------------

    def check_tools(self) -> None:
        if self.windows:
            if not (MSYS2_ROOT / "usr" / "bin" / "bash.exe").exists():
                raise BuildError(f"MSYS2 was not found in {MSYS2_ROOT} (set LOCALSR_MSYS2_ROOT)")
            self.tool("lib.exe")  # from the MSVC developer environment
            tools = "gcc make perl meson ninja cmake pkg-config nasm objdump"
            missing = self.sh(
                ["sh", "-c", f"for t in {tools}; do command -v $t >/dev/null || echo $t; done"],
                cwd=self.work,
                env=self.env(),
                log_path=self.logs / "tools.log",
                capture=True,
            ).split()
            if missing:
                raise BuildError(f"MSYS2 UCRT64 is missing build tools: {', '.join(missing)}")
            return
        required = ["meson", "ninja", "cmake", "make", "pkg-config", "cc"]
        if self.machine in {"x86_64", "amd64", "i686", "i386"}:
            required.append("nasm")
        if self.system == "Darwin":
            required += ["install_name_tool", "otool", "codesign"]
        else:
            required += ["readelf"]
        for name in required:
            self.tool(name)

    def fetch(self) -> None:
        for name, entry in self.sources.items():
            self.archives[name] = fetch_source(entry, self.cache)
            log(f"verified {entry['filename']} ({entry['sha256'][:12]}...)")

    def build_dav1d(self) -> None:
        signature = {
            "sha256": self.sources["dav1d"]["sha256"],
            "target": MACOS_DEPLOYMENT_TARGET,
            "system": self.system,
        }
        if self.stamp_ok("dav1d", signature):
            return
        log("building dav1d")
        env = self.env()
        src = extract_fresh(self.archives["dav1d"], self.work / "src" / "dav1d")
        build = src / "build"
        lp = self.logs / "dav1d.log"
        # On Windows the codec libraries are linked statically into FFmpeg's DLLs.
        library = "static" if self.windows else "shared"
        self.sh(
            [
                "meson",
                "setup",
                self.path(build),
                f"--prefix={self.path(self.prefix)}",
                "--libdir=lib",
                "--buildtype=release",
                f"-Ddefault_library={library}",
                "-Denable_tools=false",
                "-Denable_tests=false",
                "-Denable_examples=false",
                "-Denable_docs=false",
            ],
            cwd=src,
            env=env,
            log_path=lp,
        )
        self.sh(["ninja", "-C", self.path(build), f"-j{self.jobs}"], cwd=src, env=env, log_path=lp)
        self.sh(["ninja", "-C", self.path(build), "install"], cwd=src, env=env, log_path=lp)
        self.fix_install_names()
        self.write_stamp("dav1d", signature)

    def cmake_common(self) -> list[str]:
        args = [
            "-G",
            "Ninja",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_INSTALL_PREFIX={self.path(self.prefix)}",
            "-DCMAKE_INSTALL_LIBDIR=lib",
            f"-DBUILD_SHARED_LIBS={'OFF' if self.windows else 'ON'}",
            "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
        ]
        if self.system == "Darwin":
            args += [
                f"-DCMAKE_OSX_DEPLOYMENT_TARGET={MACOS_DEPLOYMENT_TARGET}",
                f"-DCMAKE_OSX_ARCHITECTURES={platform.machine()}",
                f"-DCMAKE_INSTALL_NAME_DIR={self.prefix / 'lib'}",
            ]
        return args

    def build_cmake_package(self, name: str, options: list[str]) -> None:
        signature = {
            "sha256": self.sources[name]["sha256"],
            "options": options,
            "common": self.cmake_common(),
        }
        if self.stamp_ok(name, signature):
            return
        log(f"building {name}")
        env = self.env()
        src = extract_fresh(self.archives[name], self.work / "src" / name)
        build = src / "_build"
        lp = self.logs / f"{name}.log"
        self.sh(
            [
                "cmake",
                "-S",
                self.path(src),
                "-B",
                self.path(build),
                *self.cmake_common(),
                *options,
            ],
            cwd=src,
            env=env,
            log_path=lp,
        )
        self.sh(
            ["cmake", "--build", self.path(build), "--parallel", str(self.jobs)],
            cwd=src,
            env=env,
            log_path=lp,
        )
        self.sh(["cmake", "--install", self.path(build)], cwd=src, env=env, log_path=lp)
        self.fix_install_names()
        self.write_stamp(name, signature)

    def build_svtav1(self) -> None:
        self.build_cmake_package(
            "svt-av1",
            [
                "-DBUILD_APPS=OFF",
                "-DBUILD_DEC=OFF",
                "-DBUILD_ENC=ON",
                "-DBUILD_TESTING=OFF",
                "-DREPRODUCIBLE_BUILDS=ON",
                # EXCLUDE_HASH adds -Wl,--build-id=none, which Apple's linker rejects.
                *(["-DEXCLUDE_HASH=ON"] if self.system == "Linux" else []),
            ],
        )

    def build_opus(self) -> None:
        self.build_cmake_package(
            "opus",
            [
                f"-DOPUS_BUILD_SHARED_LIBRARY={'OFF' if self.windows else 'ON'}",
                "-DOPUS_BUILD_TESTING=OFF",
                "-DOPUS_BUILD_PROGRAMS=OFF",
                "-DOPUS_INSTALL_PKG_CONFIG_MODULE=ON",
            ],
        )

    def build_libvpx(self) -> None:
        options = [
            f"--prefix={self.path(self.prefix)}",
            *(
                ["--target=x86_64-win64-gcc", "--as=nasm", "--disable-shared", "--enable-static"]
                if self.windows
                else ["--enable-shared", "--disable-static"]
            ),
            "--disable-examples",
            "--disable-tools",
            "--disable-docs",
            "--disable-unit-tests",
            "--enable-vp9-highbitdepth",
            "--enable-pic",
            "--disable-dependency-tracking",
        ]
        if self.system == "Darwin":
            flag = f"-mmacosx-version-min={MACOS_DEPLOYMENT_TARGET}"
            options += [f"--extra-cflags={flag}", f"--extra-cxxflags={flag}"]
        signature = {"sha256": self.sources["libvpx"]["sha256"], "options": options}
        if self.stamp_ok("libvpx", signature):
            return
        log("building libvpx")
        env = self.env()
        if self.system == "Darwin":
            env["LDFLAGS"] = f"-mmacosx-version-min={MACOS_DEPLOYMENT_TARGET}"
        src = extract_fresh(self.archives["libvpx"], self.work / "src" / "libvpx")
        build = src / "_build"
        build.mkdir()
        lp = self.logs / "libvpx.log"
        self.sh([self.path(src / "configure"), *options], cwd=build, env=env, log_path=lp)
        self.sh(["make", f"-j{self.jobs}"], cwd=build, env=env, log_path=lp)
        self.sh(["make", "install"], cwd=build, env=env, log_path=lp)
        self.fix_install_names()
        self.write_stamp("libvpx", signature)

    def fix_install_names(self) -> None:
        """macOS: give every dylib in the prefix an absolute install name.

        CMake defaults to ``@rpath/`` ids and libvpx uses a bare file name; delocate
        (and FFmpeg's link step) need ids that resolve without an rpath.
        """
        if self.system != "Darwin":
            return
        lib = self.prefix / "lib"
        env = self.env()
        lp = self.logs / "install-names.log"
        for dylib in sorted(lib.glob("*.dylib")):
            if dylib.is_symlink():
                continue
            ident = run(["otool", "-D", str(dylib)], cwd=lib, env=env, log_path=lp, capture=True)
            lines = [line.strip() for line in ident.splitlines()[1:] if line.strip()]
            if not lines:
                continue
            current = lines[0]
            wanted = str(lib / Path(current).name)
            if current != wanted and (lib / Path(current).name).exists():
                run(
                    ["install_name_tool", "-id", wanted, str(dylib)],
                    cwd=lib,
                    env=env,
                    log_path=lp,
                )
                run(
                    ["codesign", "--force", "--sign", "-", str(dylib)],
                    cwd=lib,
                    env=env,
                    log_path=lp,
                )

    def list_configure_components(self, src: Path, env: dict[str, str]) -> dict[str, set[str]]:
        available = {}
        for kind, (option, _suffix) in COMPONENT_KINDS.items():
            out = self.sh(
                [self.path(src / "configure"), f"--list-{option}s"],
                cwd=src,
                env=env,
                log_path=self.logs / "ffmpeg-list.log",
                capture=True,
            )
            available[kind] = set(out.split())
        return available

    def build_ffmpeg(self) -> None:
        env = self.env()
        src_root = self.work / "src" / "ffmpeg"
        # Always derive against a pristine tree so the component lists match the archive.
        src = extract_fresh(self.archives["ffmpeg"], src_root)
        available = self.list_configure_components(src, env)
        self.available_components = available
        selected, notes = derive_component_selection(self.policy, available)
        self.notes += notes
        args = ffmpeg_configure_args(self.path(self.prefix), selected, self.system)
        check_forbidden_flags(args, self.policy)
        self.ffmpeg_args = [self.path(src / "configure"), *args]
        build = self.work / "build-ffmpeg"
        signature = {
            "sha256": self.sources["ffmpeg"]["sha256"],
            "args": args,
            "deps": [self.sources[n]["sha256"] for n in ("dav1d", "svt-av1", "libvpx", "opus")],
        }
        config_mak = build / "ffbuild" / "config.mak"
        if self.stamp_ok("ffmpeg", signature) and config_mak.exists():
            self.inspect_ffmpeg_configuration(build, (build / "configure.out").read_text())
            return
        log("configuring FFmpeg")
        if build.exists():
            shutil.rmtree(build)
        build.mkdir(parents=True)
        lp = self.logs / "ffmpeg.log"
        try:
            output = self.sh(self.ffmpeg_args, cwd=build, env=env, log_path=lp, capture=True)
        except BuildError as exc:
            raise BuildError(f"{exc}\nSee {build / 'ffbuild' / 'config.log'}") from exc
        (build / "configure.out").write_text(output)
        self.inspect_ffmpeg_configuration(build, output)
        log("building FFmpeg")
        self.sh(["make", f"-j{self.jobs}"], cwd=build, env=env, log_path=lp)
        self.sh(["make", "install"], cwd=build, env=env, log_path=lp)
        self.fix_install_names()
        self.write_stamp("ffmpeg", signature)

    def inspect_ffmpeg_configuration(self, build: Path, output: str) -> None:
        problems = configure_warnings(output)
        if problems:
            raise BuildError(
                "FFmpeg configure reported component mismatches:\n  " + "\n  ".join(problems)
            )
        for line in output.splitlines():
            if (
                line.strip().startswith("WARNING:")
                and f"configure {line.strip()}" not in self.notes
            ):
                self.notes.append(f"configure {line.strip()}")
        license_match = re.search(r"^License:\s*(.+)$", output, re.MULTILINE)
        license_text = license_match.group(1).strip() if license_match else None
        if license_text != self.policy["expected_license"]:
            raise BuildError(
                f"FFmpeg configure license is {license_text!r}, "
                f"expected {self.policy['expected_license']!r}"
            )
        config_h = (build / "config.h").read_text()
        enabled_symbols = [
            symbol
            for symbol in FORBIDDEN_CONFIG_SYMBOLS
            if re.search(rf"^#define {symbol} 1$", config_h, re.MULTILINE)
        ]
        if enabled_symbols:
            raise BuildError(f"Forbidden features enabled in config.h: {enabled_symbols}")
        enabled = parse_enabled_components(
            (build / "ffbuild" / "config.mak").read_text(), self.available_components
        )
        self.enabled_components = enabled
        forbidden = [re.compile(p) for p in self.policy["forbidden_codec_patterns"]]
        bad = [
            name
            for kind in ("decoders", "encoders")
            for name in enabled[kind]
            if any(p.search(name) for p in forbidden)
        ]
        if bad:
            raise BuildError(f"Configure enabled forbidden codecs: {bad}")
        for kind in COMPONENT_KINDS:
            wanted = set(self.policy.get(kind, []))
            if kind == "filters":
                wanted -= ALWAYS_BUILT_FILTERS
            extras = sorted(set(enabled[kind]) - wanted)
            missing = sorted(wanted - set(enabled[kind]))
            if missing:
                raise BuildError(f"Policy {kind} not enabled after configure: {missing}")
            if extras:
                note = (
                    f"configure auto-selected {kind} outside the policy (dependency 'select' "
                    f"of an enabled component): {', '.join(extras)}"
                )
                if note not in self.notes:
                    self.notes.append(note)

    def check_prefix_linkage(self) -> None:
        """Fail if any installed library links outside the prefix and the OS."""
        env = self.env()
        lp = self.logs / "linkage.log"
        lib = self.prefix / "lib"
        problems = []
        if self.windows:
            problems = self.check_windows_linkage(env, lp)
        elif self.system == "Darwin":
            allowed = (str(lib) + "/", "/usr/lib/", "/System/Library/")
            for dylib in sorted(lib.glob("*.dylib")):
                if dylib.is_symlink():
                    continue
                out = run(["otool", "-L", str(dylib)], cwd=lib, env=env, log_path=lp, capture=True)
                for line in out.splitlines()[1:]:
                    dep = line.strip().split(" (")[0]
                    if dep and not dep.startswith(allowed):
                        problems.append(f"{dylib.name} -> {dep}")
        else:
            for so in sorted(lib.glob("*.so*")):
                if so.is_symlink():
                    continue
                out = run(["readelf", "-d", str(so)], cwd=lib, env=env, log_path=lp, capture=True)
                for needed in re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", out):
                    if (lib / needed).exists():
                        continue
                    if re.match(
                        r"^(libc|libm|libdl|libpthread|librt|libz|libgcc_s|libstdc\+\+|"
                        r"ld-linux[^.]*|libmvec)\.so",
                        needed,
                    ):
                        continue
                    problems.append(f"{so.name} -> {needed}")
        if problems:
            raise BuildError("Unexpected library linkage:\n  " + "\n  ".join(problems))

    def check_windows_linkage(self, env: dict[str, str], log_path: Path) -> list[str]:
        """Copy the MSYS2 runtime DLLs FFmpeg needs next to it; report any other import."""
        bin_dir = self.prefix / "bin"
        problems = []
        for _ in range(2):  # a copied toolchain DLL may itself import another one
            problems = []
            for dll in sorted(bin_dir.glob("*.dll")):
                out = self.sh(
                    ["objdump", "-p", self.path(dll)],
                    cwd=bin_dir,
                    env=env,
                    log_path=log_path,
                    capture=True,
                )
                for needed in re.findall(r"DLL Name: (\S+)", out):
                    if (bin_dir / needed).exists() or WINDOWS_SYSTEM_DLL.match(needed):
                        continue
                    toolchain = MSYS2_ROOT / "ucrt64" / "bin" / needed
                    if needed.lower() in WINDOWS_TOOLCHAIN_DLLS and toolchain.exists():
                        shutil.copy2(toolchain, bin_dir / needed)
                        note = f"copied the MSYS2 runtime library {needed} next to FFmpeg"
                        if note not in self.notes:
                            self.notes.append(note)
                        continue
                    problems.append(f"{dll.name} -> {needed}")
        return problems

    def make_import_libraries(self) -> None:
        """Windows: turn FFmpeg's export definitions into MSVC import libraries for PyAV."""
        if not self.windows:
            return
        lib = self.prefix / "lib"
        definitions = sorted(lib.glob("*-*.def"))
        if not definitions:
            raise BuildError(f"FFmpeg installed no .def export files in {lib}")
        for definition in definitions:
            name = re.sub(r"-\d+$", "", definition.stem)
            run(
                [
                    self.tool("lib.exe"),
                    "/nologo",
                    "/machine:x64",
                    f"/def:{definition}",
                    f"/out:{lib / (name + '.lib')}",
                ],
                cwd=lib,
                env=self.env(),
                log_path=self.logs / "import-libraries.log",
            )

    def uv_python_request(self) -> str:
        """An exact uv interpreter request for the host architecture.

        A bare ``3.11`` may resolve to an x86_64 interpreter under Rosetta on Apple
        Silicon, which silently produces an x86_64 wheel.
        """
        arch = {"arm64": "aarch64", "aarch64": "aarch64", "amd64": "x86_64"}.get(
            self.machine, self.machine
        )
        if self.system == "Darwin":
            return f"cpython-3.11-macos-{arch}-none"
        if self.windows:
            return f"cpython-3.11-windows-{arch}-none"
        return f"cpython-3.11-linux-{arch}-gnu"

    def create_venv(self, path: Path, requirements: list[str]) -> Path:
        python = path / ("Scripts/python.exe" if self.windows else "bin/python")
        if not python.exists():
            uv = shutil.which("uv")
            lp = self.logs / "venv.log"
            if uv:
                run(
                    [uv, "venv", "--python", self.uv_python_request(), "--seed", str(path)],
                    cwd=self.work,
                    env=self.env(),
                    log_path=lp,
                )
            else:
                run(
                    [sys.executable, "-m", "venv", str(path)],
                    cwd=self.work,
                    env=self.env(),
                    log_path=lp,
                )
        probe = run(
            [
                str(python),
                "-c",
                "import platform, sys; print(platform.machine(), *sys.version_info[:2])",
            ],
            cwd=self.work,
            env=self.env(),
            log_path=self.logs / "venv.log",
            capture=True,
        ).split()
        machine = {"aarch64": "arm64", "amd64": "x86_64"}.get(probe[0].lower(), probe[0].lower())
        host = {"aarch64": "arm64", "amd64": "x86_64"}.get(self.machine, self.machine)
        if machine != host or probe[1:] != ["3", "11"]:
            raise BuildError(
                f"{python} is {probe[0]} Python {'.'.join(probe[1:])}; "
                f"expected native {host} Python 3.11"
            )
        if requirements:
            run(
                [str(python), "-m", "pip", "install", "--disable-pip-version-check", *requirements],
                cwd=self.work,
                env=self.env(),
                log_path=self.logs / "venv.log",
            )
        return python

    def build_pyav(self) -> Path:
        python = self.create_venv(
            self.work / "buildenv", BUILD_REQUIREMENTS + self.repair_requirements()
        )
        env = self.env()
        env["PATH"] = str(python.parent) + os.pathsep + env["PATH"]
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        env["PIP_NO_CACHE_DIR"] = "1"
        lp = self.logs / "pyav.log"
        build_options = []
        if self.windows:
            # PyAV compiles with MSVC against the import libraries in <prefix>/lib.
            self.make_import_libraries()
            env["DISTUTILS_USE_SDK"] = "1"
            env["MSSdk"] = "1"
            build_options = [f"--config-settings=--build-option=--ffmpeg-dir={self.prefix}"]
        else:
            # pkg-config must resolve the private prefix and nothing else.
            run(
                ["pkg-config", "--cflags", "--libs", "libavcodec", "libavformat", "libavdevice"],
                cwd=self.work,
                env=env,
                log_path=lp,
            )
        src = extract_fresh(self.archives["pyav"], self.work / "src" / "pyav")
        raw = self.work / "wheel-raw"
        if raw.exists():
            shutil.rmtree(raw)
        raw.mkdir(parents=True)
        log("building PyAV wheel")
        run(
            [
                str(python),
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--no-binary",
                "av",
                "--wheel-dir",
                str(raw),
                "--verbose",
                *build_options,
                str(src),
            ],
            cwd=src,
            env=env,
            log_path=lp,
        )
        wheels = sorted(raw.glob(f"av-{PYAV_VERSION}-*.whl"))
        if len(wheels) != 1:
            raise BuildError(f"Expected one raw PyAV wheel in {raw}, found {wheels}")
        self.output.mkdir(parents=True, exist_ok=True)
        for old in self.output.glob("av-*.whl"):
            old.unlink()
        log("repairing wheel")
        if self.system == "Darwin":
            run(
                [
                    str(python.parent / "delocate-wheel"),
                    "--require-archs",
                    platform.machine(),
                    "--require-target-macos-version",
                    MACOS_DEPLOYMENT_TARGET,
                    "-w",
                    str(self.output),
                    "-v",
                    str(wheels[0]),
                ],
                cwd=self.work,
                env=env,
                log_path=lp,
            )
        elif self.windows:
            # Original DLL names keep FFmpeg replaceable, as the relinking notes describe.
            run(
                [
                    str(python),
                    "-m",
                    "delvewheel",
                    "repair",
                    "--add-path",
                    str(self.prefix / "bin"),
                    "--no-mangle-all",
                    "-w",
                    str(self.output),
                    str(wheels[0]),
                ],
                cwd=self.work,
                env=env,
                log_path=lp,
            )
        else:
            command = [str(python.parent / "auditwheel"), "repair", "-w", str(self.output)]
            if os.environ.get("AUDITWHEEL_PLAT"):
                command += ["--plat", os.environ["AUDITWHEEL_PLAT"]]
            run(command + [str(wheels[0])], cwd=self.work, env=env, log_path=lp)
        repaired = sorted(self.output.glob(f"av-{PYAV_VERSION}-*.whl"))
        if len(repaired) != 1:
            raise BuildError(f"Expected one repaired wheel in {self.output}, found {repaired}")
        return repaired[0]

    def repair_requirements(self) -> list[str]:
        if self.system == "Darwin":
            return MACOS_REPAIR_REQUIREMENTS
        return WINDOWS_REPAIR_REQUIREMENTS if self.windows else LINUX_REPAIR_REQUIREMENTS

    def probe_wheel(self, wheel: Path) -> dict:
        """Install the repaired wheel into a clean venv and read its runtime metadata."""
        probe_env_dir = self.work / "probeenv"
        if probe_env_dir.exists():
            shutil.rmtree(probe_env_dir)
        python = self.create_venv(probe_env_dir, [])
        env = self.env()
        env.pop("LD_LIBRARY_PATH", None)  # the repaired wheel must be self-contained
        lp = self.logs / "probe.log"
        run(
            [str(python), "-m", "pip", "install", "--no-deps", "--no-index", str(wheel)],
            cwd=self.work,
            env=env,
            log_path=lp,
        )
        script = (
            "import json, av, av._core as core\n"
            "print(json.dumps({'av_version': av.__version__,"
            " 'ffmpeg_version_info': core.ffmpeg_version_info,"
            " 'library_versions': {k: list(v) for k, v in av.library_versions.items()},"
            " 'library_meta': {k: {'version': list(m['version']),"
            " 'configuration': m['configuration'], 'license': m['license']}"
            " for k, m in core.library_meta.items()},"
            " 'codecs_available': sorted(av.codecs_available)}))\n"
        )
        out = run(
            [str(python), "-I", "-c", script], cwd=self.work, env=env, log_path=lp, capture=True
        )
        return json.loads(out.strip().splitlines()[-1])

    def check_runtime(self, runtime: dict) -> None:
        """Fail closed if the repaired wheel's runtime disagrees with the policy.

        This mirrors scripts/verify_codec_allowlist.py so the recipe stays self-contained
        when copied into the corresponding-source directory.
        """
        policy = self.policy
        runtime_names = policy.get("runtime_codec_names", {})
        allowed = set(policy["decoders"]) | set(policy["encoders"])
        allowed |= {runtime_names[name] for name in allowed if name in runtime_names}
        patterns = [re.compile(pattern) for pattern in policy["forbidden_codec_patterns"]]
        codecs = runtime["codecs_available"]
        problems = [f"forbidden codec {n}" for n in codecs if any(p.search(n) for p in patterns)]
        problems += [f"codec {n} not in policy" for n in codecs if n not in allowed]
        for name, meta in runtime["library_meta"].items():
            if meta["license"] != policy["expected_license"]:
                problems.append(f"{name} license {meta['license']!r}")
            flags = {token.split("=", 1)[0] for token in meta["configuration"].split()}
            problems += [
                f"{name} configured with {flag}"
                for flag in policy["configure_forbidden_flags"]
                if flag in flags
            ]
        if problems:
            raise BuildError("Built wheel violates codec-policy.json:\n  " + "\n  ".join(problems))

    def write_outputs(self, wheel: Path, runtime: dict) -> None:
        corresponding = self.output / "corresponding-source"
        if corresponding.exists():
            shutil.rmtree(corresponding)
        corresponding.mkdir(parents=True)
        for archive in self.archives.values():
            shutil.copy2(archive, corresponding / archive.name)
        configure_line = shlex.join(self.ffmpeg_args)
        (corresponding / "ffmpeg-configure.txt").write_text(configure_line + "\n")
        # No FFmpeg or PyAV patches are applied; keep the file so the set is explicit.
        (corresponding / "changes.diff").write_text("")
        shutil.copy2(Path(__file__).resolve(), corresponding / "build_lgpl_media.py")
        shutil.copy2(POLICY_PATH, corresponding / "codec-policy.json")
        shutil.copy2(SOURCES_PATH, corresponding / "sources.json")
        (corresponding / "README.md").write_text(self.readme(wheel, configure_line))

        licenses = {
            meta["license"] for meta in runtime["library_meta"].values() if meta.get("license")
        }
        manifest = {
            "schema_version": 1,
            "generated_utc": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            "platform": {
                "system": self.system,
                "machine": platform.machine(),
                "release": platform.release(),
                "mac_ver": platform.mac_ver()[0] or None,
                "libc": "-".join(platform.libc_ver()) if self.system == "Linux" else None,
                "macos_deployment_target": MACOS_DEPLOYMENT_TARGET
                if self.system == "Darwin"
                else None,
            },
            "license": self.policy["expected_license"],
            "ffmpeg_reported_licenses": sorted(licenses),
            "sources": [
                {
                    "name": entry["name"],
                    "version": entry["version"],
                    "url": entry["url"],
                    "filename": entry["filename"],
                    "sha256": sha256_file(self.archives[name]),
                    "license": entry["license"],
                }
                for name, entry in self.sources.items()
            ],
            "wheel": {"filename": wheel.name, "sha256": sha256_file(wheel)},
            "ffmpeg_configure_command": configure_line,
            "ffmpeg_configure_argv": self.ffmpeg_args,
            "enabled_components": self.enabled_components,
            "policy_deviations": self.notes,
            "patches": [],
            "python_build_requirements": BUILD_REQUIREMENTS + self.repair_requirements(),
            "build_tools": self.build_tool_versions(),
            "av_version": runtime["av_version"],
            "ffmpeg_version_info": runtime["ffmpeg_version_info"],
            "av_library_versions": runtime["library_versions"],
            "av_library_meta": runtime["library_meta"],
            "codecs_available": runtime["codecs_available"],
        }
        (self.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    def build_tool_versions(self) -> dict[str, str]:
        commands = {
            "cc": ["gcc" if self.windows else "cc", "--version"],
            "meson": ["meson", "--version"],
            "ninja": ["ninja", "--version"],
            "cmake": ["cmake", "--version"],
            "make": ["make", "--version"],
            "pkg-config": ["pkg-config", "--version"],
            "nasm": ["nasm", "-v"],
        }
        if not self.windows:
            return {name: self.tool_version(command) for name, command in commands.items()}
        bash = str(MSYS2_ROOT / "usr" / "bin" / "bash.exe")
        env = {**self.env(), "MSYSTEM": "UCRT64", "MSYS2_PATH_TYPE": "minimal"}
        versions = {}
        for name, command in commands.items():
            result = subprocess.run(
                [bash, "-lc", shlex.join(command)], capture_output=True, text=True, env=env
            )
            text = (result.stdout or result.stderr).strip().splitlines()
            versions[name] = text[0] if text else "unknown"
        versions["lib.exe"] = shutil.which("lib.exe") or "unavailable"
        return versions

    def readme(self, wheel: Path, configure_line: str) -> str:
        names = "\n".join(
            f"- `{self.archives[name].name}`: {entry['name']} {entry['version']} "
            f"({entry['license']}), sha256 `{entry['sha256']}`, from <{entry['url']}>"
            for name, entry in self.sources.items()
        )
        return f"""# Corresponding source for `{wheel.name}`

This directory is the complete corresponding source for the FFmpeg libraries
(LGPL-2.1-or-later) bundled in the PyAV wheel `{wheel.name}`, together with the
permissively licensed codec libraries and PyAV itself.

## Contents

{names}
- `ffmpeg-configure.txt`: the exact FFmpeg configure command line used.
- `changes.diff`: modifications to FFmpeg or PyAV sources (empty: none were made).
- `build_lgpl_media.py`, `codec-policy.json`, `sources.json`: the build recipe and its inputs.

## Rebuilding

Requirements: a C/C++ toolchain, meson, ninja, cmake, make, pkg-config, Python 3.11+ and
(optionally) uv; nasm on x86. On Windows: MSYS2 with the UCRT64 toolchain for the native
libraries and the MSVC build tools (`lib.exe`, `cl.exe`) for PyAV. Place the archives above in a directory and run:

```
python3 build_lgpl_media.py --source-cache <this directory> \\
    --work-dir /tmp/lgpl-media-work --output-dir /tmp/lgpl-media-dist
```

The script verifies every archive's SHA-256 before use (it downloads missing archives
from the recorded URLs), builds dav1d, SVT-AV1, libvpx and Opus (as shared libraries, or static ones linked into
FFmpeg on Windows),
configures FFmpeg with exactly the command in `ffmpeg-configure.txt` (paths differ by
work directory), builds PyAV {PYAV_VERSION} against it and repairs the wheel.

## Relinking with a modified FFmpeg

The FFmpeg libraries are shared libraries that PyAV loads dynamically. To use a modified
FFmpeg, rebuild it with the same configure options (or any compatible ones) and replace
the bundled `libav*`/`libsw*` libraries inside the installed wheel:

- macOS: the libraries live in `av/.dylibs/`; keep the file names, then re-sign
  ad hoc (`codesign --force --sign - <file>`).
- Linux: the libraries live in `av.libs/` with hashed names; replace them keeping the
  names, or rebuild PyAV against your FFmpeg with `pip wheel --no-binary av`.
- Windows: the DLLs (`avcodec-*.dll` and the others) live in `av.libs/` under their
  original names; build FFmpeg with MSYS2 UCRT64 as the script does and replace them.

Alternatively rebuild the whole stack with the script after editing the FFmpeg sources
and recording your patch in `changes.diff`.
"""

    def run_all(self) -> None:
        self.work.mkdir(parents=True, exist_ok=True)
        self.check_tools()
        self.fetch()
        self.build_dav1d()
        self.build_svtav1()
        self.build_libvpx()
        self.build_opus()
        self.build_ffmpeg()
        self.check_prefix_linkage()
        wheel = self.build_pyav()
        runtime = self.probe_wheel(wheel)
        self.check_runtime(runtime)
        self.write_outputs(wheel, runtime)
        log(f"wheel: {wheel}")
        log(f"manifest: {self.output / 'manifest.json'}")
        for note in self.notes:
            log(f"deviation: {note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work-dir", type=Path, required=True, help="Build tree and prefix")
    parser.add_argument("--output-dir", type=Path, required=True, help="Wheel and manifest")
    parser.add_argument(
        "--source-cache", type=Path, required=True, help="Directory of verified source archives"
    )
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 2, help="Parallel jobs")
    args = parser.parse_args(argv)
    system = platform.system()
    if system not in {"Darwin", "Linux", "Windows"}:
        print(f"Unsupported build platform: {system}", file=sys.stderr)
        return 2
    try:
        Builder(args).run_all()
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
