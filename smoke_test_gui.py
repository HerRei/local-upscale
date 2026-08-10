"""Small cross-platform Slint/worker startup and shutdown smoke test."""

import tempfile
import time
from pathlib import Path

from localsr.ui.slint_app import create_slint_application


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="localsr-smoke-") as temporary:
        root = Path(temporary)
        application = create_slint_application(
            start_worker=True,
            settings_path=root / "settings.json",
            model_root=root / "models",
        )
        deadline = time.monotonic() + 15
        ready = False
        while time.monotonic() < deadline:
            application.poll_events()
            if application.ui.status_title == "Ready":
                ready = True
                break
            time.sleep(0.025)
        application.shutdown()
        clean_exit = not application.worker.running
        if ready and clean_exit:
            print("SUCCESS: Slint bridge worker became ready and exited cleanly")
            return 0
        print("FAILED: Slint bridge did not complete its startup/shutdown cycle")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
