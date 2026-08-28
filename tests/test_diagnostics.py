from unittest.mock import patch

from localsr.platform.diagnostics import build_diagnostic_summary, copy_to_clipboard


def test_diagnostics_omit_media_paths_and_include_support_facts():
    summary = build_diagnostic_summary(
        version="0.0.7-alpha",
        task="Upscale",
        model="SPAN Quick",
        device="CPU",
    )
    assert "LocalSR: 0.0.7-alpha" in summary
    assert "Task: Upscale" in summary
    assert "file paths" in summary
    assert "/Users/" not in summary


def test_clipboard_uses_available_platform_command():
    with (
        patch("localsr.platform.diagnostics.sys.platform", "darwin"),
        patch("localsr.platform.diagnostics.shutil.which", return_value="/usr/bin/pbcopy"),
        patch("localsr.platform.diagnostics.subprocess.run") as run,
    ):
        assert copy_to_clipboard("diagnostics") is True
    assert run.call_args.args[0] == ("pbcopy",)
    assert run.call_args.kwargs["input"] == "diagnostics"
