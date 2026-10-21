"""Dispatch codec-only helpers before importing the inference runtime."""

import sys


def main():
    if sys.argv[1:2] == ["--large-result-preview"]:
        from localsr.worker.result_preview import large_result_main

        large_result_main()
        return
    if sys.argv[1:2] == ["--edit-probe"]:
        from localsr.worker.edit_guard import probe

        raise SystemExit(probe(sys.argv[2:]))
    if sys.argv[1:2] == ["--edit-guard"]:
        from localsr.worker.edit_guard import main as edit_guard_main

        raise SystemExit(edit_guard_main(sys.argv[2:]))
    if sys.argv[1:2] == ["--video-playback-preview"]:
        from localsr.core.video_playback import main as playback_main

        raise SystemExit(playback_main(sys.argv[2:]))
    from localsr.worker.server import main as server_main

    server_main()
