#!/usr/bin/env python3
"""Build LocalSR Next Preview without changing the existing release artifacts."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
BUILD_ROOT = ROOT / "build" / "tauri-preview"
WORKER_DIST = BUILD_ROOT / "worker-dist"
WORKER_WORK = BUILD_ROOT / "worker-build"
ENGINE_DIR = WORKER_DIST / "engine"
CONFIG_PATH = BUILD_ROOT / "tauri-worker.conf.json"
LINUXDEPLOY_SYSTEM_LIB = Path("/usr/local/lib")
LINUXDEPLOY_DRIVER_LIBRARIES = (
    "libcuda.so.1",
    "libnvidia-ml.so.1",
    "librdmacm.so.1",
    "libibverbs.so.1",
)
LINUX_APPIMAGE_PAYLOAD_COMPRESSOR = "gzip"
# WebKitGTK plays media through GStreamer. Only plugins for royalty-free or
# patent-expired formats and generic plumbing are distributed; libav, x264,
# openh264, AAC and similar plugins pulled in from the build host are removed.
# See packaging/ffmpeg/codec-policy.json.
LINUX_GSTREAMER_PLUGIN_ALLOWLIST = frozenset(
    {
        "alsa",
        "app",
        "audioconvert",
        "audiomixer",
        "audioparsers",
        "audiorate",
        "audioresample",
        "autodetect",
        "coreelements",
        "coretracers",
        "dav1d",
        "deinterlace",
        "flac",
        "gio",
        "imagefreeze",
        "interleave",
        "isomp4",
        "jpeg",
        "matroska",
        "mpg123",
        "ogg",
        "opengl",
        "opus",
        "pbtypes",
        "playback",
        "png",
        "pulseaudio",
        "rawparse",
        "subparse",
        "theora",
        "typefindfunctions",
        "videoconvertscale",
        "videoconvert",
        "videofilter",
        "videorate",
        "videoscale",
        "volume",
        "vorbis",
        "vpx",
        "wavparse",
    }
)
# Host libraries that only patent-encumbered GStreamer plugins pull into usr/lib.
LINUX_FORBIDDEN_HOST_MEDIA_LIBRARIES = (
    "libavcodec",
    "libavformat",
    "libavfilter",
    "libavutil",
    "libavdevice",
    "libswscale",
    "libswresample",
    "libpostproc",
    "libx264",
    "libx265",
    "libopenh264",
    "libfdk-aac",
    "libfaad",
    "liba52",
    "libmpeg2",
    "libdvdread",
    "libxvidcore",
    "libopencore-amr",
    "libvo-amrwbenc",
    "libtwolame",
    "libdca",
    "libde265",
)
# Mesa is supplied by the host. Bundling older Wayland libraries alongside it
# makes WebKit abort on newer desktops with EGL_BAD_PARAMETER. These must come
# from the same host graphics stack (reproduced on Fedora / RX 9060 XT).
LINUXDEPLOY_HOST_GRAPHICS_LIBRARIES = (
    "libwayland-client.so.0",
    "libwayland-cursor.so.0",
    "libwayland-egl.so.1",
    "libwayland-server.so.0",
)
LINUXDEPLOY_PRIVATE_LIBRARY_ALIASES = {
    "libMIOpen.so.1": "libMIOpen.so",
    "libamd_comgr.so.3": "libamd_comgr.so",
    "libamdhip64.so.7": "libamdhip64.so",
    "libaotriton_v2.so.0.12.0": "libaotriton_v2.so",
    "libhipblas.so.3": "libhipblas.so",
    "libhipblaslt.so.1": "libhipblaslt.so",
    "libhipfft.so.0": "libhipfft.so",
    "libhiprand.so.1": "libhiprand.so",
    "libhiprtc.so.7": "libhiprtc.so",
    "libhipsolver.so.1": "libhipsolver.so",
    "libhipsparse.so.4": "libhipsparse.so",
    "libhipsparselt.so.0": "libhipsparselt.so",
    "libhsa-amd-aqlprofile64.so.1": "libhsa-amd-aqlprofile64.so",
    "libhsa-runtime64.so.1": "libhsa-runtime64.so",
    "librccl.so.1": "librccl.so",
    "librocblas.so.5": "librocblas.so",
    "librocfft.so.0": "librocfft.so",
    "librocm-core.so.1": "librocm-core.so",
    "librocm_smi64.so.1": "librocm_smi64.so",
    "librocprofiler-register.so.0": "librocprofiler-register.so",
    "librocprofiler-sdk.so.1": "librocprofiler-sdk.so",
    "librocrand.so.1": "librocrand.so",
    "librocroller.so.1": "librocroller.so",
    "librocsolver.so.0": "librocsolver.so",
    "librocsparse.so.1": "librocsparse.so",
    "libroctracer64.so.4": "libroctracer64.so",
    "libroctx64.so.4": "libroctx64.so",
}


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def rust_build_environment() -> dict[str, str]:
    """Return an environment where Cargo is visible to the Tauri CLI.

    GUI terminals and automation shells do not always source rustup's shell
    setup. Resolve the selected toolchain explicitly without changing the
    user's login-shell configuration.
    """
    env = os.environ.copy()
    if shutil.which("cargo", path=env.get("PATH")):
        return env
    rustup = shutil.which("rustup", path=env.get("PATH"))
    if not rustup:
        raise SystemExit("Cargo is missing. Install the stable Rust toolchain with rustup first.")
    result = subprocess.run(
        [rustup, "which", "cargo"],
        check=True,
        capture_output=True,
        text=True,
    )
    cargo = Path(result.stdout.strip())
    if not cargo.is_file():
        raise SystemExit(f"rustup returned an unavailable Cargo executable: {cargo}")
    env["PATH"] = os.pathsep.join([str(cargo.parent), env.get("PATH", "")])
    return env


def tauri_build_environment() -> dict[str, str]:
    """Return the environment used by the native Tauri bundler.

    linuxdeploy inspects every ELF file in an AppDir, including the already
    self-contained PyInstaller worker.  NumPy and other wheels deliberately
    give their private libraries unique names and resolve them from adjacent
    ``*.libs`` directories.  Expose those directories while *building* the
    AppImage so linuxdeploy can resolve the same private dependencies instead
    of failing after the Rust application has compiled.

    This path is only used by the packager.  It is not written into the app or
    inherited by the installed worker at runtime.
    """

    env = rust_build_environment()
    if not sys.platform.startswith("linux") or not ENGINE_DIR.is_dir():
        return env

    env["NO_STRIP"] = "true"
    library_dirs = sorted(
        {str(path.parent) for path in ENGINE_DIR.rglob("*.so*") if path.is_file()}
    )
    existing = env.get("LD_LIBRARY_PATH")
    if existing:
        library_dirs.append(existing)
    if library_dirs:
        env["LD_LIBRARY_PATH"] = os.pathsep.join(library_dirs)
    return env


def _symlink_engine_libs_for_linuxdeploy() -> list[Path]:
    """Symlink hash-named PyInstaller .so files into /usr/local/lib.

    linuxdeploy runs as an AppImage whose bundled dynamic linker ignores the
    caller's LD_LIBRARY_PATH.  When it encounters a PyInstaller-packaged
    binary that lists another hash-named PyInstaller lib (e.g.
    libavcodec-9aae324f.so.59.37.100) as a NEEDED entry, it fails with
    "Could not find dependency" because that hashed name does not exist at any
    standard system path.

    Creating temporary symlinks in /usr/local/lib lets linuxdeploy's bundled
    ldd/patchelf resolve these cross-references.  Temporary stub libraries also
    let ldd resolve host-provided NVIDIA driver sonames that must remain
    external to the AppImage.  Some wheel-packaged private libraries advertise
    versioned SONAME dependencies even though the wheel ships an unversioned
    file, so this also creates known private aliases back to the bundled engine
    libraries.  The caller is responsible for removing the returned paths after
    the build completes.
    """
    if not sys.platform.startswith("linux") or not ENGINE_DIR.is_dir():
        return []

    system_lib = LINUXDEPLOY_SYSTEM_LIB
    created: list[Path] = []

    driver_stub_dir = BUILD_ROOT / "linuxdeploy-driver-stubs"
    driver_stub_dir.mkdir(parents=True, exist_ok=True)
    driver_stub_source = driver_stub_dir / "stub.c"
    driver_stub_source.write_text("void __localsr_linuxdeploy_stub(void) {}\n", encoding="utf-8")
    for driver_library in LINUXDEPLOY_DRIVER_LIBRARIES:
        stub = driver_stub_dir / driver_library
        if not stub.exists():
            compiler = shutil.which("cc") or shutil.which("gcc")
            if compiler is None:
                raise SystemExit(
                    "cannot create linuxdeploy driver stubs because no C compiler is available"
                )
            subprocess.run(
                [
                    compiler,
                    "-shared",
                    f"-Wl,-soname,{driver_library}",
                    "-o",
                    str(stub),
                    str(driver_stub_source),
                ],
                check=True,
            )
        dest = system_lib / driver_library
        if not dest.exists() and not dest.is_symlink():
            result = subprocess.run(
                ["sudo", "ln", "-sf", str(stub.resolve()), str(dest)],
                capture_output=True,
            )
            if result.returncode == 0:
                created.append(dest)

    for so_file in sorted(ENGINE_DIR.rglob("*.so*")):
        if not so_file.is_file():
            continue
        dest = system_lib / so_file.name
        if dest.exists() or dest.is_symlink():
            continue
        try:
            dest.symlink_to(so_file.resolve())
            created.append(dest)
        except OSError:
            # We lack direct write access; try with sudo (self-hosted CI
            # runners running as a non-root user with NOPASSWD sudo).
            result = subprocess.run(
                ["sudo", "ln", "-sf", str(so_file.resolve()), str(dest)],
                capture_output=True,
            )
            if result.returncode == 0:
                created.append(dest)
    available_engine_libs = {
        so_file.name: so_file.resolve()
        for so_file in sorted(ENGINE_DIR.rglob("*.so*"))
        if so_file.is_file()
    }
    for alias, target_name in sorted(LINUXDEPLOY_PRIVATE_LIBRARY_ALIASES.items()):
        target = available_engine_libs.get(target_name)
        if target is None:
            continue
        dest = system_lib / alias
        if dest.exists() or dest.is_symlink():
            continue
        try:
            dest.symlink_to(target)
            created.append(dest)
        except OSError:
            result = subprocess.run(
                ["sudo", "ln", "-sf", str(target), str(dest)],
                capture_output=True,
            )
            if result.returncode == 0:
                created.append(dest)
    if created:
        # linuxdeploy resolves dependencies through ldd.  On Ubuntu runners, new
        # /usr/local/lib entries are not visible to ldd until ldconfig refreshes
        # the dynamic linker cache.
        ldconfig = subprocess.run(["sudo", "ldconfig"], capture_output=True, text=True)
        if ldconfig.returncode != 0:
            raise SystemExit(
                "created linuxdeploy library symlinks but sudo ldconfig failed: "
                + ldconfig.stderr.strip()
            )
        print(
            f"Created {len(created)} /usr/local/lib symlink(s) and refreshed"
            " ldconfig to expose PyInstaller engine libs to linuxdeploy.",
            flush=True,
        )
    return created


def _wrap_linuxdeploy_for_appimage() -> Path | None:
    """Temporarily add host graphics library excludes to linuxdeploy.

    Tauri invokes the cached linuxdeploy AppImage directly and does not expose
    linuxdeploy's ``--exclude-library`` arguments through tauri.conf.json.
    CUDA workers legitimately retain a runtime dependency on the host NVIDIA
    driver library, which is absent from the packaging VM and must not be
    bundled into the AppImage.  Replace the cached tool with a tiny compiled
    launcher only for the duration of this build, then restore the original
    binary.  Using an ELF launcher avoids Tauri/AppImage launch failures that
    occur when the cached AppImage path is replaced with a shell script.
    """
    if not sys.platform.startswith("linux"):
        return None

    linuxdeploy = Path.home() / ".cache" / "tauri" / "linuxdeploy-x86_64.AppImage"
    if not linuxdeploy.is_file():
        return None
    backup = linuxdeploy.with_name(f"{linuxdeploy.name}.localsr-original")
    if backup.exists():
        raise SystemExit(f"stale linuxdeploy backup requires manual cleanup: {backup}")

    compiler = shutil.which("cc") or shutil.which("gcc")
    if compiler is None:
        raise SystemExit("cannot wrap linuxdeploy because no C compiler is available")

    excludes = [*LINUXDEPLOY_DRIVER_LIBRARIES, *LINUXDEPLOY_HOST_GRAPHICS_LIBRARIES]
    exclude_arguments = "".join(
        f"  next[out++] = {json.dumps('--exclude-library=' + name)};\n" for name in excludes
    )
    wrapper_dir = BUILD_ROOT / "linuxdeploy-wrapper"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    source = wrapper_dir / "linuxdeploy-wrapper.c"
    binary = wrapper_dir / "linuxdeploy-wrapper"
    remove_libraries = "".join(
        f"  if (remove_library(appdir, {json.dumps(name)})) return 1;\n" for name in excludes
    )
    source.write_text(
        "#include <errno.h>\n"
        "#include <stdio.h>\n"
        "#include <stdlib.h>\n"
        "#include <string.h>\n"
        "#include <unistd.h>\n"
        "#include <sys/wait.h>\n"
        f"static const char *backup_path = {json.dumps(str(backup))};\n"
        "static int remove_library(const char *appdir, const char *name) {\n"
        "  size_t size = strlen(appdir) + strlen(name) + 16;\n"
        "  char *path = malloc(size); if (!path) return 1;\n"
        '  snprintf(path, size, "%s/usr/lib/%s", appdir, name);\n'
        "  int result = unlink(path); int error = errno; free(path);\n"
        "  if (result && error != ENOENT) { errno = error; perror(name); return 1; }\n"
        "  return 0;\n"
        "}\n"
        "int main(int argc, char **argv) {\n"
        '  FILE *log = fopen(getenv("LOCALSR_LINUXDEPLOY_WRAPPER_LOG") ? getenv("LOCALSR_LINUXDEPLOY_WRAPPER_LOG") : "/tmp/localsr-linuxdeploy-wrapper.log", "a");\n'
        "  if (log) {\n"
        '    fputs("wrapper argv:", log);\n'
        '    for (int i = 1; i < argc; ++i) fprintf(log, " <%s>", argv[i] ? argv[i] : "");\n'
        "    fputc('\\n', log);\n"
        "    fclose(log);\n"
        "  }\n"
        "  int first = 1;\n"
        "  while (first < argc && argv[first] && argv[first][0] == '\\0') first++;\n"
        f"  char **next = calloc((size_t)argc + {len(excludes) + 3}, sizeof(char *));\n"
        "  if (!next) return 127;\n"
        "  int out = 0;\n"
        "  next[out++] = (char *)backup_path;\n"
        '  if (first < argc && strcmp(argv[first], "--appimage-extract-and-run") == 0) next[out++] = argv[first++];\n'
        + exclude_arguments
        + "  const char *appdir = NULL; int appimage = 0;\n"
        "  for (int i = first; i < argc; ++i) {\n"
        '    if (!strcmp(argv[i], "--appdir") && i + 1 < argc) appdir = argv[i + 1];\n'
        '    if (!strncmp(argv[i], "--appdir=", 9)) appdir = argv[i] + 9;\n'
        '    if (!strcmp(argv[i], "--output=appimage") || (!strcmp(argv[i], "--output") && i + 1 < argc && !strcmp(argv[i + 1], "appimage"))) appimage = 1;\n'
        "  }\n"
        "  int prefix = out;\n"
        "  for (int i = first; i < argc; ++i) {\n"
        '    if (appimage && appdir && !strcmp(argv[i], "--output=appimage")) continue;\n'
        '    if (appimage && appdir && !strcmp(argv[i], "--output") && i + 1 < argc && !strcmp(argv[i + 1], "appimage")) { i++; continue; }\n'
        "    next[out++] = argv[i];\n"
        "  }\n"
        "  next[out] = NULL;\n"
        # Media plugins can copy excluded libraries back into the AppDir.
        # Deploy plugins first, remove only host libraries, then generate the
        # AppImage in a second pass that has no deployment plugins.
        "  if (appimage && appdir) {\n"
        "    pid_t child = fork(); if (child < 0) return 127;\n"
        "    if (!child) { execv(backup_path, next); _exit(127); }\n"
        "    int status; while (waitpid(child, &status, 0) < 0) { if (errno != EINTR) return 127; }\n"
        "    if (!WIFEXITED(status) || WEXITSTATUS(status)) return WIFEXITED(status) ? WEXITSTATUS(status) : 1;\n"
        + remove_libraries
        + "    out = prefix;\n"
        '    next[out++] = "--appdir"; next[out++] = (char *)appdir;\n'
        '    next[out++] = "--output"; next[out++] = "appimage"; next[out] = NULL;\n'
        "  }\n"
        "  execv(backup_path, next);\n"
        '  log = fopen(getenv("LOCALSR_LINUXDEPLOY_WRAPPER_LOG") ? getenv("LOCALSR_LINUXDEPLOY_WRAPPER_LOG") : "/tmp/localsr-linuxdeploy-wrapper.log", "a");\n'
        '  if (log) { fprintf(log, "execv failed: %s\\n", strerror(errno)); fclose(log); }\n'
        '  fprintf(stderr, "localsr linuxdeploy wrapper execv failed: %s\\n", strerror(errno));\n'
        "  return 127;\n"
        "}\n",
        encoding="utf-8",
    )
    subprocess.run([compiler, "-O2", "-o", str(binary), str(source)], check=True)

    linuxdeploy.rename(backup)
    shutil.copy2(binary, linuxdeploy)
    linuxdeploy.chmod(0o755)
    print(
        "Wrapped linuxdeploy with an ELF launcher to exclude host graphics libraries: "
        + ", ".join(excludes),
        flush=True,
    )
    return backup


def _restore_linuxdeploy_wrapper(backup: Path | None) -> None:
    if backup is None:
        return
    linuxdeploy = backup.with_name(backup.name.removesuffix(".localsr-original"))
    try:
        linuxdeploy.unlink(missing_ok=True)
        backup.rename(linuxdeploy)
    except OSError as error:
        raise RuntimeError(f"could not restore linuxdeploy wrapper: {error}") from error


def _find_appimage_squashfs_offset(artifact: Path) -> int:
    result = subprocess.run(
        [str(artifact), "--appimage-offset"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode == 0 and result.stdout.strip().isdigit():
        return int(result.stdout.strip())

    with artifact.open("rb") as stream:
        header = stream.read(64 * 1024 * 1024)
    offset = header.find(b"hsqs")
    if offset != -1:
        return offset
    raise RuntimeError(f"could not locate SquashFS payload offset in {artifact}")


def _copy_bytes(source: Path, destination: Path, *, limit: int | None = None) -> None:
    remaining = limit
    with source.open("rb") as src, destination.open("ab") as dst:
        while remaining is None or remaining > 0:
            chunk_size = 1024 * 1024 if remaining is None else min(1024 * 1024, remaining)
            chunk = src.read(chunk_size)
            if not chunk:
                break
            dst.write(chunk)
            if remaining is not None:
                remaining -= len(chunk)


def prune_patent_encumbered_media(appdir: Path) -> list[Path]:
    """Remove GStreamer plugins and host codec libraries outside the codec policy.

    Only the AppDir's host library directory and GStreamer plugin directories
    are pruned. The frozen worker's own LGPL media runtime lives elsewhere and
    is validated separately by ``verify_codec_allowlist.py``.
    """
    removed: list[Path] = []
    for plugin_dir in sorted(appdir.rglob("gstreamer-1.0")):
        if not plugin_dir.is_dir():
            continue
        for plugin in sorted(plugin_dir.glob("libgst*.so*")):
            name = plugin.name.removeprefix("libgst").split(".so", 1)[0]
            if name not in LINUX_GSTREAMER_PLUGIN_ALLOWLIST:
                plugin.unlink()
                removed.append(plugin)
    for library_dir in (appdir / "usr" / "lib", appdir / "usr" / "lib" / "x86_64-linux-gnu"):
        if not library_dir.is_dir():
            continue
        for library in sorted(library_dir.iterdir()):
            if (library.is_file() or library.is_symlink()) and library.name.startswith(
                LINUX_FORBIDDEN_HOST_MEDIA_LIBRARIES
            ):
                library.unlink()
                removed.append(library)
    return removed


def _repack_linux_appimages_with_system_mksquashfs(bundle_root: Path) -> list[Path]:
    """Rebuild Linux AppImage payloads with system gzip-capable mksquashfs.

    Current appimagetool builds bundle a zstd-only mksquashfs.  Large ROCm
    payloads have hit zstd squashfs corruption during extraction, so let Tauri
    create the AppDir/runtime and replace only the SquashFS image with the
    distro-provided mksquashfs from the release runner.
    """
    if not sys.platform.startswith("linux"):
        return []

    appimage_dir = bundle_root / "appimage"
    if not appimage_dir.is_dir():
        return []

    mksquashfs = shutil.which("mksquashfs")
    if mksquashfs is None:
        raise SystemExit("mksquashfs is required to repack Linux AppImage payloads")

    appdirs = sorted(path for path in appimage_dir.glob("*.AppDir") if path.is_dir())
    images = sorted(path for path in appimage_dir.glob("*.AppImage") if path.is_file())
    if images and len(appdirs) != 1:
        raise RuntimeError(
            f"expected one AppDir next to AppImage output, found {len(appdirs)} in {appimage_dir}"
        )
    if not images:
        return []

    appdir = appdirs[0]
    removed = prune_patent_encumbered_media(appdir)
    if removed:
        print(
            f"Removed {len(removed)} patent-encumbered media plugin/library files from the AppDir.",
            flush=True,
        )
    verify = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "verify_codec_allowlist.py"),
            "--tree",
            str(appdir),
        ],
        capture_output=True,
        text=True,
    )
    if verify.returncode != 0:
        raise SystemExit(
            "AppImage contents violate packaging/ffmpeg/codec-policy.json:\n"
            + verify.stdout
            + verify.stderr
        )
    repacked: list[Path] = []
    for image in images:
        offset = _find_appimage_squashfs_offset(image)
        squashfs = image.with_name(f"{image.name}.localsr-repacked.squashfs")
        replacement = image.with_name(f"{image.name}.localsr-repacked")
        squashfs.unlink(missing_ok=True)
        replacement.unlink(missing_ok=True)
        run(
            [
                mksquashfs,
                str(appdir),
                str(squashfs),
                "-noappend",
                "-comp",
                LINUX_APPIMAGE_PAYLOAD_COMPRESSOR,
                "-no-duplicates",
            ]
        )
        _copy_bytes(image, replacement, limit=offset)
        _copy_bytes(squashfs, replacement)
        replacement.chmod(image.stat().st_mode | 0o111)
        replacement.replace(image)
        squashfs.unlink(missing_ok=True)
        repacked.append(image)
        print(
            f"Repacked {image.name} AppImage payload with "
            f"{LINUX_APPIMAGE_PAYLOAD_COMPRESSOR} via system mksquashfs.",
            flush=True,
        )
    return repacked


def npm_executable() -> str:
    """Resolve npm to a directly executable path on every supported host."""

    executable = shutil.which("npm")
    if executable:
        return executable
    raise SystemExit("npm is missing. Install the locked frontend toolchain first.")


def worker_executable() -> Path:
    name = "localsr-worker.exe" if os.name == "nt" else "localsr-worker"
    return ENGINE_DIR / name


def worker_target_arch(target: str | None) -> str | None:
    if not target:
        return None
    if target.startswith("aarch64-"):
        return "arm64"
    if target.startswith("x86_64-"):
        return "x86_64"
    return None


def build_worker(target: str | None = None, private_preview_media: bool = False) -> None:
    if shutil.which("pyinstaller") is None:
        try:
            import PyInstaller  # noqa: F401
        except ImportError as error:
            raise SystemExit(
                "PyInstaller is missing. Install the package build dependencies first: "
                "python -m pip install -e '.[package,video,face]'"
            ) from error
    # Refuse to freeze a media runtime with GPL or patent-licensed codecs (for
    # example PyPI's PyAV wheels, which include x264/x265). Build the LGPL runtime
    # with packaging/ffmpeg/build_lgpl_media.py and install it first.
    if private_preview_media:
        print(
            "WARNING: private preview build without the licensing-clean media runtime. "
            "Never distribute this package.",
            flush=True,
        )
    else:
        run(
            [
                sys.executable,
                str(ROOT / "scripts" / "verify_codec_allowlist.py"),
                "--python-env",
                sys.executable,
            ]
        )
    WORKER_DIST.mkdir(parents=True, exist_ok=True)
    WORKER_WORK.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    if private_preview_media:
        environment["LOCALSR_PRIVATE_PREVIEW_MEDIA"] = "1"
    if target_arch := worker_target_arch(target):
        environment["LOCALSR_TARGET_ARCH"] = target_arch
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(WORKER_DIST),
            "--workpath",
            str(WORKER_WORK),
            str(ROOT / "packaging" / "tauri_worker.spec"),
        ],
        cwd=ROOT,
        env=environment,
    )
    if target_arch and sys.platform == "darwin":
        run(
            [
                sys.executable,
                str(ROOT / "scripts" / "thin_macho_binaries.py"),
                str(WORKER_DIST),
                target_arch,
            ],
            cwd=ROOT,
            env=environment,
        )
    executable = worker_executable()
    if not executable.is_file():
        raise SystemExit(f"worker build did not create {executable}")


def write_bundle_overlay(*, require_signing: bool = False, payload: Path | None = None) -> None:
    if not ENGINE_DIR.is_dir():
        raise SystemExit(f"missing worker engine directory: {ENGINE_DIR}")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    bundle: dict[str, object] = {
        "resources": {
            f"{ENGINE_DIR.as_posix()}/": "engine/",
            (ROOT / "LICENSE").as_posix(): "LICENSE",
            (ROOT / "THIRD_PARTY_NOTICES.md").as_posix(): "THIRD_PARTY_NOTICES.md",
        },
    }
    if payload is not None:
        resources = bundle["resources"]
        del resources[f"{ENGINE_DIR.as_posix()}/"]
        resources[payload.as_posix()] = "engine-payload.json"
        bundle["windows"] = {
            "nsis": {"installerHooks": str(ROOT / "packaging/windows/engine-payload.nsh")}
        }
    if sys.platform == "darwin":
        from importlib.metadata import version

        # The maintained Torch 2.13 ARM wheels have a macOS 14 deployment floor.
        if version("torch").split("+")[0].startswith("2.13."):
            bundle["macOS"] = {"minimumSystemVersion": "14.0"}
        apple_signing = os.environ.get("APPLE_SIGNING_IDENTITY") or os.environ.get(
            "APPLE_CERTIFICATE"
        )
        apple_id_notary = all(
            os.environ.get(name) for name in ("APPLE_ID", "APPLE_PASSWORD", "APPLE_TEAM_ID")
        )
        api_notary = all(
            os.environ.get(name)
            for name in ("APPLE_API_KEY", "APPLE_API_ISSUER", "APPLE_API_KEY_PATH")
        )
        if require_signing and not (apple_signing and (apple_id_notary or api_notary)):
            raise SystemExit("production macOS signing and notarization credentials are required")
        if not apple_signing:
            # Tauri recommends a final ad-hoc seal for credential-free Apple
            # Silicon previews. A real identity supplied by CI overrides this.
            bundle.setdefault("macOS", {}).update({"signingIdentity": "-"})
    if os.name == "nt":
        thumbprint = os.environ.get("WINDOWS_CERTIFICATE_THUMBPRINT", "").strip()
        timestamp_url = os.environ.get("WINDOWS_TIMESTAMP_URL", "").strip()
        if require_signing and (not thumbprint or not timestamp_url):
            raise SystemExit(
                "WINDOWS_CERTIFICATE_THUMBPRINT and WINDOWS_TIMESTAMP_URL are required"
            )
        if thumbprint and timestamp_url:
            bundle.setdefault("windows", {}).update(
                {
                    "certificateThumbprint": thumbprint,
                    "digestAlgorithm": "sha256",
                    "timestampUrl": timestamp_url,
                }
            )
    CONFIG_PATH.write_text(
        json.dumps(
            {
                "bundle": bundle,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Build an unoptimized desktop binary")
    parser.add_argument("--skip-worker", action="store_true", help="Reuse an existing engine build")
    parser.add_argument("--skip-checks", action="store_true")
    parser.add_argument(
        "--require-signing",
        action="store_true",
        help="Refuse release bundles unless platform production signing is configured",
    )
    parser.add_argument(
        "--private-preview-media",
        action="store_true",
        help="Allow a media runtime outside the codec policy for a never-distributed preview",
    )
    parser.add_argument("--target", help="Native Rust target triple")
    parser.add_argument(
        "--external-engine-prefix", help="Create adjacent CUDA engine payloads for NSIS"
    )
    bundle_mode = parser.add_mutually_exclusive_group()
    bundle_mode.add_argument(
        "--bundles",
        help="Comma-separated Tauri bundle formats (for example dmg, nsis, or appimage)",
    )
    bundle_mode.add_argument(
        "--no-bundle", action="store_true", help="Compile without an installer"
    )
    args = parser.parse_args()
    if args.private_preview_media and args.require_signing:
        parser.error("--private-preview-media can never be combined with a signed release build")

    run([sys.executable, str(ROOT / "scripts" / "export_desktop_catalog.py")])
    if not args.skip_worker:
        build_worker(args.target, args.private_preview_media)
    payload = None
    if args.external_engine_prefix:
        if os.name != "nt" or args.bundles != "nsis":
            parser.error("external engine payloads require a Windows NSIS build")
        from prepare_engine_payload import prepare

        output = BUILD_ROOT / "engine-payload"
        if output.exists():
            shutil.rmtree(output)
        payload = prepare(ENGINE_DIR, output, args.external_engine_prefix)
    write_bundle_overlay(require_signing=args.require_signing, payload=payload)

    if not (DESKTOP / "node_modules").is_dir():
        run([npm_executable(), "ci"], cwd=DESKTOP)
    if not args.skip_checks:
        run([npm_executable(), "run", "check"], cwd=DESKTOP)
        run([npm_executable(), "test"], cwd=DESKTOP)

    command = [
        npm_executable(),
        "run",
        "tauri",
        "--",
        "build",
        "--verbose",
        "--ci",
        "--config",
        str(CONFIG_PATH),
    ]
    if args.debug:
        command.append("--debug")
    if args.target:
        command.extend(["--target", args.target])
    if args.bundles:
        command.extend(["--bundles", args.bundles])
    if args.no_bundle:
        command.append("--no-bundle")
    environment = tauri_build_environment()
    cargo_target = Path(environment.get("CARGO_TARGET_DIR", DESKTOP / "src-tauri/target"))
    if args.target:
        cargo_target = cargo_target / args.target
    # Keep compiled dependencies, but never select installers left by a previous
    # backend or release from the shared Cargo output directory.
    bundles = cargo_target / ("debug" if args.debug else "release") / "bundle"
    if bundles.exists():
        shutil.rmtree(bundles)
    linuxdeploy_symlinks = _symlink_engine_libs_for_linuxdeploy()
    linuxdeploy_backup = _wrap_linuxdeploy_for_appimage() if args.bundles == "appimage" else None
    try:
        run(command, cwd=DESKTOP, env=environment)
        if args.bundles == "appimage":
            _repack_linux_appimages_with_system_mksquashfs(bundles)
    finally:
        _restore_linuxdeploy_wrapper(linuxdeploy_backup)
        removed_linuxdeploy_symlinks = False
        for link in linuxdeploy_symlinks:
            try:
                link.unlink()
                removed_linuxdeploy_symlinks = True
            except OSError:
                result = subprocess.run(["sudo", "rm", "-f", str(link)], capture_output=True)
                removed_linuxdeploy_symlinks = (
                    removed_linuxdeploy_symlinks or result.returncode == 0
                )
        if removed_linuxdeploy_symlinks:
            subprocess.run(["sudo", "ldconfig"], capture_output=True)
    print(
        f"LocalSR Next Preview built for {platform.system()} {platform.machine()}. "
        "This build uses the Tauri desktop and the separate Python inference worker.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
