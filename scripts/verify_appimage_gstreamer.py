"""Play a VP9/WebM file through an AppDir's own GStreamer, as WebKitGTK does (issue #185).

0.6.0's AppImage bundled GStreamer's core without any plugin. On systems whose plugins
live elsewhere (Arch, Fedora) WebKitGTK found no playbin and video previews froze the
app; on the Ubuntu build host the host's plugins hid this. The check runs with the
environment the AppImage's GStreamer hook sets, loads the AppDir's libgstreamer, plays
packaging/linux/gstreamer-check.webm to its end with playbin and fake sinks, and fails
when the file does not play or any loaded plugin comes from outside the AppDir.

usage: verify_appimage_gstreamer.py --appdir PATH [--media FILE]
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "packaging" / "linux" / "gstreamer-check.webm"
TIMEOUT_SECONDS = 60
GST_SECOND = 1_000_000_000
GST_MESSAGE_EOS = 1 << 0
GST_MESSAGE_ERROR = 1 << 1
GST_STATE_NULL = 1
GST_STATE_PLAYING = 4


def hook_environment(appdir: Path, base: dict[str, str]) -> dict[str, str]:
    """The environment after AppRun sources the AppDir's GStreamer hooks."""
    hooks = sorted((appdir / "apprun-hooks").glob("*gstreamer*"))
    if not hooks:
        raise SystemExit(f"{appdir} has no GStreamer AppRun hook")
    script = (
        "".join(f'. "{hook}"\n' for hook in hooks)
        + 'python3 -c "import json,os;print(json.dumps(dict(os.environ)))"'
    )
    completed = subprocess.run(
        ["bash", "-c", script],
        env={**base, "APPDIR": str(appdir)},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout.splitlines()[-1])


def play(appdir: Path, media: Path) -> dict[str, object]:
    """Child side: runs with the hook environment and the AppDir's libraries first."""
    gst = ctypes.CDLL(str(appdir / "usr" / "lib" / "libgstreamer-1.0.so.0"))
    gobject = ctypes.CDLL("libgobject-2.0.so.0")
    gst.gst_init(None, None)
    gst.gst_version_string.restype = ctypes.c_char_p
    gst.gst_element_factory_make.restype = ctypes.c_void_p
    gst.gst_element_factory_make.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    gst.gst_element_set_state.argtypes = [ctypes.c_void_p, ctypes.c_int]
    gst.gst_element_get_bus.restype = ctypes.c_void_p
    gst.gst_element_get_bus.argtypes = [ctypes.c_void_p]
    gst.gst_bus_timed_pop_filtered.restype = ctypes.c_void_p
    gst.gst_bus_timed_pop_filtered.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_int]
    gst.gst_message_get_structure.restype = ctypes.c_void_p
    gst.gst_message_get_structure.argtypes = [ctypes.c_void_p]
    gst.gst_structure_to_string.restype = ctypes.c_char_p
    gst.gst_structure_to_string.argtypes = [ctypes.c_void_p]
    gst.gst_registry_get.restype = ctypes.c_void_p
    gst.gst_registry_get_plugin_list.restype = ctypes.c_void_p
    gst.gst_registry_get_plugin_list.argtypes = [ctypes.c_void_p]
    gst.gst_plugin_get_filename.restype = ctypes.c_char_p
    gst.gst_plugin_get_filename.argtypes = [ctypes.c_void_p]

    class GList(ctypes.Structure):
        pass

    GList._fields_ = [
        ("data", ctypes.c_void_p),
        ("next", ctypes.POINTER(GList)),
        ("prev", ctypes.POINTER(GList)),
    ]
    plugins = []
    node = ctypes.cast(
        gst.gst_registry_get_plugin_list(gst.gst_registry_get()), ctypes.POINTER(GList)
    )
    while node:
        filename = gst.gst_plugin_get_filename(node.contents.data)
        if filename:
            plugins.append(filename.decode())
        node = node.contents.next

    report: dict[str, object] = {
        "core": gst.gst_version_string().decode(),
        "plugins": len(plugins),
        "outside_appdir": sorted(
            path for path in plugins if not Path(path).resolve().is_relative_to(appdir.resolve())
        ),
    }
    playbin = gst.gst_element_factory_make(b"playbin", None)
    if not playbin:
        report["result"] = "no playbin"
        return report
    gobject.g_object_set.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_void_p,
    ]
    gobject.g_object_set(playbin, b"uri", media.resolve().as_uri().encode(), None)
    for prop in (b"video-sink", b"audio-sink"):
        sink = gst.gst_element_factory_make(b"fakesink", None)
        gobject.g_object_set.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        gobject.g_object_set(playbin, prop, sink, None)
    gst.gst_element_set_state(playbin, GST_STATE_PLAYING)
    message = gst.gst_bus_timed_pop_filtered(
        gst.gst_element_get_bus(playbin),
        TIMEOUT_SECONDS * GST_SECOND,
        GST_MESSAGE_EOS | GST_MESSAGE_ERROR,
    )
    if not message:
        report["result"] = f"no end of stream after {TIMEOUT_SECONDS} s"
    else:
        structure = gst.gst_message_get_structure(message)
        # The end-of-stream message carries no structure; an error message does.
        report["result"] = (
            "played" if not structure else gst.gst_structure_to_string(structure).decode()
        )
    gst.gst_element_set_state(playbin, GST_STATE_NULL)
    return report


def check(appdir: Path, media: Path = MEDIA) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="localsr-gst-check-") as scratch:
        base = {key: value for key, value in os.environ.items() if not key.startswith("GST_")}
        env = hook_environment(appdir, base)
        env["GST_REGISTRY_1_0"] = str(Path(scratch) / "registry.bin")
        env["LD_LIBRARY_PATH"] = str(appdir / "usr" / "lib")
        completed = subprocess.run(
            [sys.executable, __file__, "--child", "--appdir", str(appdir), "--media", str(media)],
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS + 60,
            check=False,
        )
    if completed.returncode != 0:
        raise SystemExit(
            f"GStreamer check crashed (exit {completed.returncode}):\n"
            + completed.stdout
            + completed.stderr
        )
    report = json.loads(completed.stdout.splitlines()[-1])
    problems = []
    if report["result"] != "played":
        problems.append(f"{media.name} did not play: {report['result']}")
    if report["outside_appdir"]:
        problems.append("plugins from outside the AppDir: " + ", ".join(report["outside_appdir"]))
    if problems:
        raise SystemExit("The AppImage's GStreamer cannot play video:\n- " + "\n- ".join(problems))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--appdir", type=Path, required=True)
    parser.add_argument("--media", type=Path, default=MEDIA)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        print(json.dumps(play(args.appdir, args.media)))
        return 0
    report = check(args.appdir, args.media)
    print(
        f"{report['core']}: {args.media.name} played with {report['plugins']} plugins, "
        "all from the AppDir."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
