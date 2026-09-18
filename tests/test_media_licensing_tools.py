"""Packaging gates that keep patent-licensed codecs and non-redistributable libraries out."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_source_bundle  # noqa: E402
import build_tauri_preview  # noqa: E402
import fetch_extra_sources  # noqa: E402
import verify_nvidia_redistributables as nvidia  # noqa: E402


def _touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_appimage_prune_keeps_royalty_free_plugins_and_removes_codec_plugins(tmp_path: Path):
    appdir = tmp_path / "LocalSR.AppDir"
    plugins = appdir / "usr/lib/gstreamer-1.0"
    keep = [
        _touch(plugins / f"libgst{name}.so") for name in ("vpx", "matroska", "opus", "playback")
    ]
    drop = [
        _touch(plugins / f"libgst{name}.so")
        for name in ("libav", "x264", "openh264", "fdkaac", "videoparsersbad")
    ]
    host_keep = _touch(appdir / "usr/lib/libwebkit2gtk-4.1.so.0")
    host_drop = [
        _touch(appdir / "usr/lib/libavcodec.so.60"),
        _touch(appdir / "usr/lib/libx264.so.164"),
    ]
    engine_media = _touch(appdir / "usr/lib/LocalSR/engine/_internal/av.libs/libavcodec-1234.so.62")

    removed = build_tauri_preview.prune_patent_encumbered_media(appdir)

    assert sorted(removed) == sorted(drop + host_drop)
    assert all(path.exists() for path in keep + [host_keep, engine_media])
    assert not any(path.exists() for path in drop + host_drop)


def test_nvidia_policy_allows_attachment_a_and_rejects_unlisted_libraries():
    policy = json.loads((ROOT / "packaging/nvidia/redistributables.json").read_text())
    report = nvidia.classify(
        [
            "_internal/nvidia/cublas/lib/libcublas.so.12",
            "_internal/nvidia/cudnn/lib/libcudnn_cnn.so.9",
            "engine/_internal/torch/lib/cudnn64_9.dll",
            "engine/_internal/torch/lib/cudnn_engines_precompiled64_9.dll",
            "_internal/nvidia/nccl/lib/libnccl.so.2",
            "_internal/libcurl.so.4",
            "_internal/torch/cuda/nccl.py",
            "_internal/torch/_inductor/cutlass_mock_imports/cuda/cudart.py",
        ],
        policy,
    )
    assert report["ok"]
    assert set(report["allowed"]) == {"cuBLAS", "cuDNN", "NCCL"}

    blocked = nvidia.classify(
        [
            "_internal/nvidia/cufile/lib/libcufile.so.0",
            "_internal/nvidia/nvshmem/lib/libnvshmem_host.so.3",
            "_internal/libcuda.so.1",
            "_internal/nvidia/unknown/lib/libnvfuture.so.1",
        ],
        policy,
    )
    assert not blocked["ok"]
    assert {entry["component"] for entry in blocked["forbidden"]} == {
        "cuFile / GPUDirect Storage",
        "NVSHMEM",
        "NVIDIA driver libraries",
    }


def test_nvidia_verifier_cli_exit_status(tmp_path: Path, capsys):
    _touch(tmp_path / "engine/nvidia/cufft/lib/libcufft.so.11")
    assert nvidia.main([str(tmp_path)]) == 0
    _touch(tmp_path / "engine/nvidia/cufile/lib/libcufile.so.0")
    assert nvidia.main([str(tmp_path)]) == 1
    assert "redistribution check failed" in capsys.readouterr().err


def _bundle_arguments(inventory: Path, output: Path, **overrides) -> argparse.Namespace:
    values = dict(
        inventory=str(inventory),
        output=str(output),
        commit=None,
        media_source=None,
        opencv_source=None,
        apt_sources=False,
        allow_missing_host_sources=False,
        extra_source=None,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def test_source_bundle_requires_media_source_when_ffmpeg_is_shipped(tmp_path: Path):
    inventory = tmp_path / "engine"
    _touch(inventory / "_internal/av/.dylibs/libavcodec.62.dylib")
    with pytest.raises(SystemExit, match="--media-source"):
        build_source_bundle.build(_bundle_arguments(inventory, tmp_path / "out"))


def test_source_bundle_collects_sources_licenses_and_manifest(tmp_path: Path):
    inventory = tmp_path / "engine"
    _touch(inventory / "_internal/av/.dylibs/libavcodec.62.dylib")
    _touch(inventory / "_internal/cv2/cv2.abi3.so")
    _touch(inventory / "_internal/av-18.1.0.dist-info/licenses/LICENSE.txt", "BSD")
    media = tmp_path / "media-source"
    _touch(media / "ffmpeg-configure.txt", "--disable-everything")
    opencv = tmp_path / "opencv-source"
    _touch(opencv / "build_macos_face_runtime.py", "recipe")
    output = tmp_path / "out"

    report = build_source_bundle.build(
        _bundle_arguments(inventory, output, media_source=str(media), opencv_source=str(opencv))
    )

    assert (output / "media-runtime/ffmpeg-configure.txt").read_text() == "--disable-everything"
    assert (output / "opencv/build_macos_face_runtime.py").exists()
    assert (output / "licenses/av-18.1.0.dist-info/licenses/LICENSE.txt").read_text() == "BSD"
    assert (output / "THIRD_PARTY_NOTICES.md").exists()
    assert "FFmpeg project under the LGPLv2.1" in (output / "README.md").read_text()
    manifest = json.loads((output / "manifest.json").read_text())
    assert "codec-policy.json" in manifest["sha256"]
    assert report["components"]["python-distribution-licenses"] == 1


def test_source_bundle_requires_ubuntu_sources_for_every_host_library(tmp_path: Path):
    inventory = tmp_path / "LocalSR.AppDir"
    _touch(inventory / "usr/lib/libwebkit2gtk-4.1.so.0")
    with pytest.raises(SystemExit, match="--apt-sources"):
        build_source_bundle.build(_bundle_arguments(inventory, tmp_path / "out"))
    files = [
        "usr/lib/libcairo.so.2",
        "usr/lib/libgnutls.so.30.37.1",
        "usr/lib/gstreamer-1.0/libgstvpx.so",
        "usr/lib/LocalSR/engine/_internal/torch/lib/libtorch_cpu.so",
        "usr/lib/LocalSR/engine/_internal/numpy/core/_multiarray_umath.cpython-311-x86_64-linux-gnu.so",
        "usr/share/icons/hicolor/index.theme",
    ]
    assert build_source_bundle.host_libraries(files) == sorted(files[:4])


def test_bundled_libnuma_must_be_the_hosts_ubuntu_copy(tmp_path: Path, monkeypatch):
    host = tmp_path / "host/libnuma.so.1"
    _touch(host, "ubuntu build")
    monkeypatch.setattr(build_source_bundle, "host_library_path", lambda name: host)
    tree = tmp_path / "LocalSR.AppDir"
    name = "usr/lib/LocalSR/engine/_internal/torch/lib/libnuma.so.1"
    _touch(tree / name, "almalinux build")
    with pytest.raises(SystemExit, match="not the build host's Ubuntu copy"):
        build_source_bundle.verify_distribution_copies([name], tree)
    _touch(tree / name, "ubuntu build")
    assert build_source_bundle.verify_distribution_copies([name, "usr/lib/libz.so.1"], tree) == [
        name
    ]


def test_build_swaps_in_the_hosts_libnuma_and_libelf(tmp_path: Path, monkeypatch):
    engine = tmp_path / "engine"
    for name in ("libnuma.so.1", "libelf.so.1"):
        _touch(tmp_path / "host" / name, f"ubuntu {name}")
        _touch(engine / "_internal/torch/lib" / name, f"almalinux {name}")
    (engine / "_internal/libnuma.so.1").symlink_to("torch/lib/libnuma.so.1")
    _touch(engine / "_internal/torch/lib/libtorch_cpu.so", "unrelated")
    monkeypatch.setattr(build_tauri_preview, "ENGINE_DIR", engine)
    monkeypatch.setattr(build_source_bundle, "host_library_path", lambda name: tmp_path / "host" / name)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command[0])
        # Nothing in this engine links against the swapped libraries.
        return argparse.Namespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(build_tauri_preview.subprocess, "run", fake_run)
    replaced = build_tauri_preview.use_distribution_libraries()

    assert sorted(path.name for path in replaced) == ["libelf.so.1", "libnuma.so.1"]
    lib = engine / "_internal/torch/lib"
    assert (lib / "libnuma.so.1").read_text() == "ubuntu libnuma.so.1"
    assert (lib / "libelf.so.1").read_text() == "ubuntu libelf.so.1"
    assert (engine / "_internal/libnuma.so.1").is_symlink()
    assert "ldd" not in calls


def test_build_refuses_a_bundled_libnuma_the_host_lacks(tmp_path: Path, monkeypatch):
    engine = tmp_path / "engine"
    _touch(engine / "_internal/torch/lib/libnuma.so.1", "almalinux")
    monkeypatch.setattr(build_tauri_preview, "ENGINE_DIR", engine)
    monkeypatch.setattr(build_source_bundle, "host_library_path", lambda name: None)
    with pytest.raises(SystemExit, match="install the Ubuntu package"):
        build_tauri_preview.use_distribution_libraries()


def test_dpkg_search_output_skips_diversions_and_splits_owners():
    output = (
        "diversion by libreadline8t64 from: /lib/x86_64-linux-gnu/libhistory.so.8\n"
        "diversion by libreadline8t64 to: /lib/x86_64-linux-gnu/libhistory.so.8.2.usr-is-merged\n"
        "libreadline8t64:amd64: /usr/lib/x86_64-linux-gnu/libhistory.so.8\n"
        "libgtk-3-0t64:amd64, libgtk-3-common: /usr/lib/x86_64-linux-gnu/libgtk-3.so.0\n"
    )
    assert build_source_bundle.owning_packages(output) == {
        "libreadline8t64",
        "libgtk-3-0t64",
        "libgtk-3-common",
    }


def test_dsc_checksums_list_every_source_file():
    dsc = (
        "Format: 3.0 (quilt)\nSource: krb5\nVersion: 1.20.1-6ubuntu2.8\n"
        "Checksums-Sha1:\n 11 10 krb5_1.20.1.orig.tar.gz\n"
        "Checksums-Sha256:\n aa 8973449 krb5_1.20.1.orig.tar.gz\n"
        " bb 153328 krb5_1.20.1-6ubuntu2.8.debian.tar.xz\n"
        "Files:\n cc 8973449 krb5_1.20.1.orig.tar.gz\n"
    )
    assert build_source_bundle.dsc_files(dsc) == [
        ("aa", "krb5_1.20.1.orig.tar.gz"),
        ("bb", "krb5_1.20.1-6ubuntu2.8.debian.tar.xz"),
    ]


def test_source_package_spec_prefers_the_source_version():
    status = (
        "Package: libglib2.0-0t64\nSource: glib2.0 (2.80.0-6ubuntu3)\nVersion: 2.80.0-6ubuntu3.1\n"
    )
    assert build_source_bundle.source_package_spec("libglib2.0-0t64", status) == (
        "glib2.0=2.80.0-6ubuntu3"
    )
    plain = "Package: libsoup-3.0-0\nVersion: 3.4.4-5\n"
    assert (
        build_source_bundle.source_package_spec("libsoup-3.0-0", plain) == "libsoup-3.0-0=3.4.4-5"
    )


def test_source_bundle_requires_libraw_and_gcc_runtime_sources(tmp_path: Path):
    inventory = tmp_path / "engine"
    _touch(inventory / "_internal/libraw_r.25.dylib")
    _touch(inventory / "_internal/libquadmath.0.dylib")
    with pytest.raises(SystemExit, match="--extra-source libraw"):
        build_source_bundle.build(_bundle_arguments(inventory, tmp_path / "out"))
    rawpy = _touch(tmp_path / "rawpy-0.27.0.tar.gz", "rawpy source")
    gcc = tmp_path / "gcc"
    _touch(gcc / "README", "gcc source")
    output = tmp_path / "bundle"
    report = build_source_bundle.build(
        _bundle_arguments(
            inventory,
            output,
            extra_source=[f"libraw={rawpy}", f"gcc-runtime={gcc}"],
        )
    )
    assert (output / "extra-sources/libraw/rawpy-0.27.0.tar.gz").exists()
    assert (output / "extra-sources/gcc-runtime/README").exists()
    assert set(report["components"]) >= {"libraw", "gcc-runtime"}


def test_windows_rawpy_extension_needs_the_libraw_source(tmp_path: Path):
    inventory = tmp_path / "worker-dist"
    _touch(inventory / "engine/_internal/rawpy/_rawpy.cp311-win_amd64.pyd")
    with pytest.raises(SystemExit, match="--extra-source libraw"):
        build_source_bundle.build(_bundle_arguments(inventory, tmp_path / "out"))


def test_windows_openblas_dll_needs_the_gcc_runtime_source(tmp_path: Path):
    inventory = tmp_path / "worker-dist"
    _touch(
        inventory
        / "engine/_internal/numpy.libs/libopenblas64__v0.3.23-293-gc2f4bdbb-gcc_10_3_0-2bde.dll"
    )
    with pytest.raises(SystemExit, match="--extra-source gcc-runtime"):
        build_source_bundle.build(_bundle_arguments(inventory, tmp_path / "out"))


def test_every_platform_registers_its_extra_sources():
    registry = json.loads((ROOT / "packaging/extra-sources.json").read_text())
    for platform in ("macos-arm64", "linux-x86_64", "windows-x86_64"):
        names = [name for name, _ in fetch_extra_sources.entries_for(platform, registry)]
        assert sorted(names) == ["gcc-runtime", "libraw"], platform
        for _, entry in fetch_extra_sources.entries_for(platform, registry):
            assert entry["url"].startswith("https://")
            assert len(entry["sha256"]) == 64
