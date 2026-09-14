"""Dispatch codec-only helpers before importing the inference runtime."""

import sys


def main():
    if sys.argv[1:2] == ["--video-playback-preview"]:
        from localsr.core.video_playback import main as playback_main

        raise SystemExit(playback_main(sys.argv[2:]))
    from localsr.worker.server import main as server_main

    server_main()
