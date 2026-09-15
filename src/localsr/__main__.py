import argparse
import sys


def _parse_cli_args(args: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        prog="localsr",
        description=(
            "LocalSR: local image and video restoration. Launches the separately installed "
            "Tauri desktop by default. Use process, watch or benchmark for the Python CLI."
        ),
        add_help=False,
    )
    parser.add_argument("--worker", action="store_true", help="Launch inference worker process.")
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
    parsed, _unknown = _parse_cli_args(filtered_args)

    if parsed.test_mps:
        import torch

        print("torch version:", torch.__version__)
        print("MPS is_built:", torch.backends.mps.is_built())
        print("MPS is_available:", torch.backends.mps.is_available())
        x = torch.randn(1024, 1024, device="mps")
        y = x @ x
        print("Tensor device:", y.device)
        return

    from localsr.desktop_launcher import launch_desktop

    try:
        result = launch_desktop(filtered_args)
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
    raise SystemExit(result)


if __name__ == "__main__":
    main()
