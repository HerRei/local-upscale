"""Compile the packaged Slint interface without opening a window."""

from pathlib import Path

import slint


def main() -> int:
    path = Path(__file__).with_name("slint") / "main.slint"
    slint.load_file(str(path), style="fluent-dark")
    print(f"Slint interface compiled successfully: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
