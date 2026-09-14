"""Run the installed Tauri app's bundled-worker startup smoke check."""

import sys

from localsr.desktop_launcher import launch_desktop


def main() -> int:
    try:
        return launch_desktop(["--smoke-test", *sys.argv[1:]])
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
