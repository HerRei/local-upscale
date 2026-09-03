import argparse
import importlib
import os
import sys
import threading
from pathlib import Path


def _record_smoke_stage(stage: str) -> None:
    report_path = os.environ.get("LOCALSR_SMOKE_REPORT")
    if report_path:
        Path(report_path).write_text(stage, encoding="utf-8")


def _parse_cli_args(args: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        prog="localsr",
        description="LocalSR: Cross-platform local image and video super-resolution.",
        add_help=False,
    )
    parser.add_argument("--worker", action="store_true", help="Launch inference worker process.")
    parser.add_argument("--legacy", action="store_true", help="Use legacy QWidget interface.")
    parser.add_argument(
        "--smoke-test", action="store_true", help="Run automated packaging smoke test."
    )
    parser.add_argument(
        "--recipe", type=str, default=None, help="Apply a saved recipe by name on launch."
    )
    parser.add_argument(
        "--preset",
        type=str,
        choices=["quick", "best"],
        default=None,
        help="Apply quick or best preset.",
    )
    parser.add_argument(
        "--auto-start", action="store_true", help="Automatically start upscaling queued files."
    )
    parser.add_argument(
        "--install-integrations",
        action="store_true",
        help="Install OS integrations (Quick Actions, CLI symlinks).",
    )
    parser.add_argument(
        "--uninstall-integrations", action="store_true", help="Remove OS integrations."
    )
    parser.add_argument("--test-mps", action="store_true", help="Run MPS validation.")
    parser.add_argument("-h", "--help", action="help", help="Show this help message and exit.")
    parser.add_argument("files", nargs="*", help="Image or video files to queue on launch.")
    return parser.parse_known_args(args)


def main():
    if "--worker" in sys.argv:
        from localsr.worker.server import main as worker_main

        worker_main()
        return

    filtered_args = [arg for arg in sys.argv[1:] if not arg.startswith("-psn_")]
    if filtered_args and filtered_args[0] in {"process", "benchmark", "watch"}:
        from localsr.automation import run_automation_cli

        raise SystemExit(run_automation_cli(filtered_args))
    parsed, unknown = _parse_cli_args(filtered_args)

    if parsed.install_integrations:
        from localsr.platform import install_system_integrations

        success = install_system_integrations()
        print(
            "System integrations installed successfully."
            if success
            else "Failed to install system integrations."
        )
        return

    if parsed.uninstall_integrations:
        from localsr.platform import uninstall_system_integrations

        success = uninstall_system_integrations()
        print(
            "System integrations uninstalled successfully."
            if success
            else "Failed to uninstall system integrations."
        )
        return

    if parsed.test_mps:
        import torch

        print("torch version:", torch.__version__)
        print("MPS is_built:", torch.backends.mps.is_built())
        print("MPS is_available:", torch.backends.mps.is_available())
        x = torch.randn(1024, 1024, device="mps")
        y = x @ x
        print("Tensor device:", y.device)
        return

    smoke_test = parsed.smoke_test
    if parsed.legacy:
        try:
            QApplication = importlib.import_module("PySide6.QtWidgets").QApplication
            MainWindow = importlib.import_module("localsr.ui.main_window").MainWindow
        except ImportError as error:
            raise SystemExit(
                'The legacy UI is optional. Install it with: pip install -e ".[legacy]"'
            ) from error

        app = QApplication(sys.argv)
        app.setApplicationName("LocalSR")
        app.setOrganizationName("LocalSR")
        app.setApplicationDisplayName("LocalSR")
        window = MainWindow()
        window.show()
        if smoke_test:
            window.close()
            return
        raise SystemExit(app.exec())

    # Auto-register integrations in the background on normal launch
    if not smoke_test:

        def _bg_install():
            try:
                from localsr.platform import install_system_integrations

                install_system_integrations()
            except Exception:
                pass

        threading.Thread(target=_bg_install, daemon=True).start()

    from localsr.ui.slint_app import create_slint_application

    initial_files = list(parsed.files)
    for extra in unknown:
        if not extra.startswith("-"):
            initial_files.append(extra)

    application = create_slint_application(
        start_worker=not smoke_test,
        initial_files=initial_files if initial_files else None,
        initial_recipe=parsed.recipe,
        initial_preset=parsed.preset,
        auto_start=parsed.auto_start,
    )
    if smoke_test:
        _record_smoke_stage("package-ready")
        application.shutdown()
        return
    application.run()


if __name__ == "__main__":
    main()
