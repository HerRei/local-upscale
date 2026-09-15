from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "packaging" / "ffmpeg" / "codec-policy.json"


def load_script(path: Path):
    module_name = "_codec_allowlist_test_" + path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


verifier = load_script(ROOT / "scripts" / "verify_codec_allowlist.py")

LGPL = "LGPL version 2.1 or later"
CLEAN_CONFIGURATION = (
    "--prefix=/opt/prefix --disable-everything --disable-autodetect --enable-shared "
    "--enable-libdav1d --enable-libsvtav1 --enable-libvpx --enable-libopus"
)


@pytest.fixture(scope="module")
def policy() -> dict:
    return verifier.load_policy(POLICY_PATH)


def clean_probe(policy: dict, **overrides) -> dict:
    allowed = sorted(verifier.allowed_codec_names(policy) - {"libvpx_vp9"})
    meta = {
        name: {"version": [1, 0, 0], "configuration": CLEAN_CONFIGURATION, "license": LGPL}
        for name in (
            "libavutil",
            "libavcodec",
            "libavformat",
            "libavdevice",
            "libavfilter",
            "libswscale",
            "libswresample",
        )
    }
    probe = {
        "executable": "/fake/python",
        "av_version": "18.1.0",
        "codecs_available": allowed,
        "library_versions": {name: [1, 0, 0] for name in meta},
        "library_meta": meta,
    }
    probe.update(overrides)
    return probe


def checks(failures) -> set[str]:
    return {failure.check for failure in failures}


def test_policy_expectations(policy):
    assert policy["expected_license"] == LGPL
    allowed = verifier.allowed_codec_names(policy)
    assert "libvpx-vp9" in allowed  # runtime spelling mapped from libvpx_vp9
    assert not any(name.startswith(("h264", "hevc", "aac", "libx26")) for name in allowed)


def test_clean_probe_passes(policy):
    failures, details = verifier.evaluate_probe(clean_probe(policy), policy)
    assert failures == []
    assert details["licenses"]["libavcodec"] == LGPL


@pytest.mark.parametrize(
    "codec",
    ["h264", "hevc", "libx264", "libx265", "aac", "aac_at", "h264_videotoolbox", "mpeg4", "vvc"],
)
def test_forbidden_codec_detected(policy, codec):
    failures = verifier.check_codecs(["vp9", codec], policy)
    forbidden = [failure for failure in failures if failure.check == "forbidden_codecs"]
    assert forbidden, codec
    assert any(item.startswith(codec + " ") for item in forbidden[0].items)


def test_unknown_codec_detected(policy):
    probe = clean_probe(policy)
    probe["codecs_available"] = probe["codecs_available"] + ["cinepak", "indeo3"]
    failures, _ = verifier.evaluate_probe(probe, policy)
    assert checks(failures) == {"unlisted_codecs"}
    unlisted = next(failure for failure in failures if failure.check == "unlisted_codecs")
    assert unlisted.items == ["cinepak", "indeo3"]


def test_forbidden_codec_is_also_unlisted(policy):
    failures = verifier.check_codecs(["libx264"], policy)
    assert checks(failures) == {"forbidden_codecs", "unlisted_codecs"}


def test_license_mismatch(policy):
    probe = clean_probe(policy)
    probe["library_meta"]["libavcodec"]["license"] = "GPL version 2 or later"
    probe["library_meta"]["libswscale"]["license"] = "LGPL version 3 or later"
    failures, _ = verifier.evaluate_probe(probe, policy)
    assert checks(failures) == {"license"}
    assert len(failures[0].items) == 2


def test_missing_library_meta_fails(policy):
    probe = clean_probe(policy)
    del probe["library_meta"]
    failures, _ = verifier.evaluate_probe(probe, policy)
    assert "library_meta" in checks(failures)


@pytest.mark.parametrize("flag", ["--enable-gpl", "--enable-nonfree", "--enable-version3"])
def test_forbidden_configure_flags(policy, flag):
    probe = clean_probe(policy)
    probe["library_meta"]["libavformat"]["configuration"] = f"{CLEAN_CONFIGURATION} {flag}"
    failures, _ = verifier.evaluate_probe(probe, policy)
    assert checks(failures) == {"configure_flags"}
    assert failures[0].items == [f"libavformat: {flag}"]


def test_configure_flag_matching_is_token_based(policy):
    config = "--extra-cflags='-DNOT--enable-gpl' --enable-gpl=yes --enable-gplish"
    assert verifier.forbidden_configure_flags(config, policy) == ["--enable-gpl=yes"]
    assert verifier.forbidden_configure_flags(CLEAN_CONFIGURATION, policy) == []


def test_import_error_fails(policy):
    failures, _ = verifier.evaluate_probe({"error": "import av failed: boom"}, policy)
    assert checks(failures) == {"import"}


def make_tree(root: Path, files: dict[str, bytes]) -> Path:
    for relative, data in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


CLEAN_FILES = {
    "av/.dylibs/libavcodec.62.dylib": b"\0license LGPL version 2.1 or later\0--disable-everything",
    "av/.dylibs/libdav1d.7.dylib": b"\0",
    "cv2/cv2.abi3.so": b"\0",
    "lib/gstreamer-1.0/libgstvideoconvertscale.dylib": b"\0",
}


def test_clean_tree_passes(tmp_path, policy):
    root = make_tree(tmp_path, CLEAN_FILES)
    failures, details = verifier.verify_tree(root, policy)
    assert failures == []
    assert details["libavcodec"] == ["av/.dylibs/libavcodec.62.dylib"]


@pytest.mark.parametrize(
    "relative",
    [
        "worker/_internal/libx264-164.dll",
        "worker/x264.dll",
        "av.libs/libx265-2b3c.so.215",
        "lib/libopenh264.7.dylib",
        "cv2/.dylibs/libavcodec.61.dylib",
        "opencv_python_headless.libs/libavformat-12ab.so.61",
        "cv2/opencv_videoio_ffmpeg4100_64.dll",
        "Frameworks/gstreamer-1.0/libgstlibav.dylib",
        "Frameworks/gstreamer-1.0/libgstx264.dylib",
        "lib/libfdk-aac.so.2",
    ],
)
def test_forbidden_files_detected(tmp_path, policy, relative):
    root = make_tree(tmp_path, {**CLEAN_FILES, relative: b"\0"})
    failures, _ = verifier.verify_tree(root, policy)
    forbidden = [failure for failure in failures if failure.check == "forbidden_files"]
    assert forbidden and forbidden[0].items[0].startswith(relative + " ")


@pytest.mark.parametrize("marker", [b"--enable-gpl", b"--enable-nonfree", b"libx264", b"libx265"])
def test_libavcodec_marker_detected(tmp_path, policy, marker):
    root = make_tree(
        tmp_path,
        {"_internal/av.libs/libavcodec-1a2b3c.so.62.11.100": b"\x00config " + marker + b" \x00"},
    )
    failures, _ = verifier.verify_tree(root, policy)
    assert checks(failures) == {"libavcodec_markers"}
    assert marker.decode() in failures[0].items[0]


def test_libavcodec_disable_flags_are_not_markers(tmp_path, policy):
    config = b"\x00--disable-everything --disable-libx264 --disable-libx265\x00"
    root = make_tree(tmp_path, {"av/.dylibs/libavcodec.62.dylib": config})
    failures, _ = verifier.verify_tree(root, policy)
    assert failures == []
    dirty = make_tree(
        tmp_path / "dirty", {"av/.dylibs/libavcodec.62.dylib": config + b"--enable-libx264"}
    )
    failures, _ = verifier.verify_tree(dirty, policy)
    assert checks(failures) == {"libavcodec_markers"}
    assert len(failures[0].items) == 1


def test_windows_libavcodec_dll_is_scanned(tmp_path, policy):
    root = make_tree(tmp_path, {"av.libs/avcodec-62.dll": b"--enable-gpl --enable-libx264"})
    failures, details = verifier.verify_tree(root, policy)
    assert details["libavcodec"] == ["av.libs/avcodec-62.dll"]
    assert len(failures[0].items) == 2


def test_wheel_file_is_extracted_and_scanned(tmp_path, policy):
    wheel = tmp_path / "av-18.1.0-cp311-abi3-macosx_12_0_arm64.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("av/.dylibs/libavcodec.62.dylib", b"LGPL")
        archive.writestr("av/.dylibs/libx264.164.dylib", b"\0")
    failures, details = verifier.verify_tree(wheel, policy)
    assert checks(failures) == {"forbidden_files"}
    assert details["root"] == str(wheel)


def test_main_reports_json_and_exit_codes(tmp_path, policy, capsys):
    clean = make_tree(tmp_path / "clean", CLEAN_FILES)
    assert verifier.main(["--tree", str(clean), "--policy", str(POLICY_PATH)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["passed"] is True

    dirty = make_tree(tmp_path / "dirty", {"cv2/.dylibs/libavutil.59.dylib": b"\0"})
    assert verifier.main(["--tree", str(dirty)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["passed"] is False
    assert report["failures"][0]["check"] == "forbidden_files"

    assert verifier.main(["--tree", str(tmp_path / "missing")]) == 2


def test_main_python_env_uses_probe(monkeypatch, policy, capsys):
    monkeypatch.setattr(
        verifier, "run_probe", lambda python: clean_probe(policy, codecs_available=["h264"])
    )
    assert verifier.main(["--python-env", "/fake/python"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert {failure["check"] for failure in report["failures"]} == {
        "forbidden_codecs",
        "unlisted_codecs",
    }


builder = load_script(ROOT / "packaging" / "ffmpeg" / "build_lgpl_media.py")


def configure_lists(policy: dict) -> dict[str, set[str]]:
    lists = {kind: set(policy.get(kind, [])) for kind in builder.COMPONENT_KINDS}
    lists["filters"] -= builder.ALWAYS_BUILT_FILTERS
    return lists


def test_build_derivation_matches_policy(policy):
    selected, notes = builder.derive_component_selection(policy, configure_lists(policy))
    assert selected["encoders"] == policy["encoders"]
    assert "buffer" not in selected["filters"]
    assert len(notes) == len(builder.ALWAYS_BUILT_FILTERS)
    args = builder.ffmpeg_configure_args(Path("/opt/prefix"), selected, "Linux")
    builder.check_forbidden_flags(args, policy)
    for required in ("--disable-everything", "--disable-autodetect", "--disable-network"):
        assert required in args
    assert not any("x264" in arg or "x265" in arg for arg in args)
    assert f"--enable-encoder={','.join(policy['encoders'])}" in args


def test_build_derivation_rejects_unknown_component(policy):
    lists = configure_lists(policy)
    lists["decoders"].discard("theora")
    with pytest.raises(builder.BuildError, match="decoder 'theora'"):
        builder.derive_component_selection(policy, lists)


def test_build_rejects_forbidden_flags(policy):
    with pytest.raises(builder.BuildError):
        builder.check_forbidden_flags(["--enable-shared", "--enable-gpl"], policy)


def test_build_parses_config_mak_components():
    config_mak = (
        "CONFIG_FRAME_THREAD_ENCODER=yes\nCONFIG_FFV1_ENCODER=yes\nCONFIG_VP3_DECODER=yes\n"
    )
    available = {kind: set() for kind in builder.COMPONENT_KINDS}
    available["encoders"] = {"ffv1"}
    available["decoders"] = {"vp3"}
    enabled = builder.parse_enabled_components(config_mak, available)
    assert enabled["encoders"] == ["ffv1"]
    assert enabled["decoders"] == ["vp3"]


def test_build_flags_configure_mismatch_warnings():
    output = (
        "WARNING: Option --enable-decoder=foo did not match anything\n"
        "WARNING: Disabled libsvtav1_encoder because not all dependencies are satisfied: x\n"
        "License: LGPL version 2.1 or later\n"
    )
    assert len(builder.configure_warnings(output)) == 2
