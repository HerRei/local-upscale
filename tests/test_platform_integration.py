import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from localsr.__main__ import _parse_cli_args
from localsr.platform import (
    get_platform_service,
)
from localsr.platform.base import PlatformService
from localsr.platform.linux import LinuxPlatformService
from localsr.platform.macos import MacOSPlatformService
from localsr.platform.windows import WindowsPlatformService

ROOT = Path(__file__).resolve().parents[1]


def test_platform_service_dispatch():
    service = get_platform_service()
    assert isinstance(service, PlatformService)
    if sys.platform == "darwin":
        assert isinstance(service, MacOSPlatformService)
    elif sys.platform == "win32":
        assert isinstance(service, WindowsPlatformService)
    else:
        assert isinstance(service, LinuxPlatformService)


def test_macos_send_notification_escaping():
    service = MacOSPlatformService()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        res = service.send_notification('Test "Quotes" & More', "Line 1\nLine 2", sound=True)
        assert res is True
        assert mock_run.called
        args, kwargs = mock_run.call_args
        script = args[0][2]
        assert 'Test \\"Quotes\\" & More' in script
        assert 'sound name "Glass"' in script


def test_linux_notification_fallback():
    service = LinuxPlatformService()
    with (
        patch("shutil.which", return_value="/usr/bin/notify-send"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        res = service.send_notification("Title", "Message")
        assert res is True
        assert mock_run.called


def test_windows_notification_fallback():
    service = WindowsPlatformService()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        res = service.send_notification("Title", "Message")
        assert res is True
        assert mock_run.called


def test_cli_arg_parsing():
    parsed, unknown = _parse_cli_args(
        ["img1.png", "video.mp4", "--recipe", "My Recipe", "--auto-start"]
    )
    assert parsed.files == ["img1.png", "video.mp4"]
    assert parsed.recipe == "My Recipe"
    assert parsed.auto_start is True
    assert parsed.preset is None

    parsed_preset, _ = _parse_cli_args(["--preset", "quick", "--install-integrations"])
    assert parsed_preset.preset == "quick"
    assert parsed_preset.install_integrations is True


@pytest.mark.skipif(sys.platform == "win32", reason="macOS symlink semantics require POSIX")
def test_macos_integration_install_and_uninstall(tmp_path):
    service = MacOSPlatformService()
    with patch.object(Path, "home", return_value=tmp_path):
        app_mock_path = tmp_path / "Applications" / "LocalSR.app" / "Contents" / "MacOS" / "LocalSR"
        app_mock_path.parent.mkdir(parents=True, exist_ok=True)
        app_mock_path.write_text("#!/bin/sh\n")

        installed = service.install_system_integrations(app_mock_path)
        assert installed is True

        workflow = tmp_path / "Library" / "Services" / "Upscale with LocalSR.workflow"
        assert workflow.is_dir()
        assert (workflow / "Contents" / "Info.plist").is_file()
        assert (workflow / "Contents" / "document.wflow").is_file()

        wflow_text = (workflow / "Contents" / "document.wflow").read_text(encoding="utf-8")
        assert "Choose recipe for LocalSR upscaling:" in wflow_text
        assert "--auto-start" in wflow_text
        assert "ARGS+=" in wflow_text
        assert "open -n -a" in wflow_text

        symlink = tmp_path / ".local" / "bin" / "localsr"
        assert symlink.is_symlink()

        uninstalled = service.uninstall_system_integrations()
        assert uninstalled is True
        assert not workflow.exists()
        assert not symlink.exists()


def test_cli_intermixed_arguments():
    parsed, unknown = _parse_cli_args(
        ["file1.png", "--preset", "quick", "file2.png", "--auto-start", "file3.jpg"]
    )
    initial_files = list(parsed.files)
    for extra in unknown:
        if not extra.startswith("-"):
            initial_files.append(extra)

    assert initial_files == ["file1.png", "file2.png", "file3.jpg"]
    assert parsed.preset == "quick"
    assert parsed.auto_start is True


def test_quick_action_escaping_and_custom_app_path(tmp_path):
    service = MacOSPlatformService()
    custom_app = tmp_path / "CustomLocalSR.app"
    custom_app.mkdir()
    script = service._generate_workflow_shell_script(custom_app)
    assert f'open -n -a "{custom_app}"' in script
    assert "open -n -a" in script
    assert "sed 's/\\\\/\\\\\\\\/g; s/\"/\\\\\"/g'" in script


def test_spec_document_types_includes_video_extensions():
    spec_path = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
    spec_text = spec_path.read_text(encoding="utf-8")
    assert '"m4v"' in spec_text
    assert '"mp4"' in spec_text
    assert '"mov"' in spec_text
    assert '"dng"' in spec_text


def test_windows_notification_escaping():
    service = WindowsPlatformService()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        res = service.send_notification('Test `Backtick` and "Quotes"', "Line 1\r\nLine 2")
        assert res is True
        assert mock_run.called
        args, _ = mock_run.call_args
        ps_cmd = args[0][4]
        assert "``Backtick``" in ps_cmd
        assert '`"Quotes`"' in ps_cmd
        assert "\r" not in ps_cmd


def test_static_ast_no_top_level_foreign_imports():
    """Verify zero top-level foreign OS module imports across entire codebase (Requirement R3.1)."""
    import ast

    # Modules that must NEVER be imported anywhere outside src/localsr/platform/
    universal_foreign = {
        "winreg",
        "msvcrt",
        "osascript",
        "AppKit",
        "Foundation",
        "pydbus",
        "gi",
        "win32api",
        "win32con",
        "win32gui",
    }

    # Platform-specific isolation rules within platform modules
    platform_forbidden = {
        "macos.py": {"winreg", "msvcrt", "pydbus", "gi", "win32api", "win32con", "win32gui"},
        "windows.py": {"osascript", "AppKit", "Foundation", "pydbus", "gi"},
        "linux.py": {
            "winreg",
            "msvcrt",
            "osascript",
            "AppKit",
            "Foundation",
            "win32api",
            "win32con",
            "win32gui",
        },
        "base.py": universal_foreign,
        "__init__.py": universal_foreign,
    }

    for py_file in (ROOT / "src" / "localsr").rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))

        if "platform" in py_file.parts:
            forbidden = platform_forbidden.get(py_file.name, universal_foreign)
        else:
            forbidden = universal_foreign

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    assert root_mod not in forbidden, (
                        f"Forbidden foreign import '{alias.name}' in {py_file}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_mod = node.module.split(".")[0]
                    assert root_mod not in forbidden, (
                        f"Forbidden foreign from-import '{node.module}' in {py_file}"
                    )


def test_protocol_url_parsing_logic(tmp_path):
    """Verify URL protocol parsing correctly routes 'open' and 'reveal' actions for files and folders."""
    from urllib.parse import parse_qs, urlparse

    target_file = tmp_path / "result.png"
    target_file.write_text("data")
    target_dir = tmp_path / "batch_output"
    target_dir.mkdir()

    # Test open action with standard file
    url1 = f"localsr:action=open&path={str(target_file)}"
    parsed1 = urlparse(url1)
    params1 = parse_qs(parsed1.path if "=" in parsed1.path else parsed1.query)
    assert params1["action"][0] == "open"
    assert params1["path"][0] == str(target_file)

    # Test reveal action with standard file
    url2 = f"localsr:action=reveal&path={str(target_file)}"
    parsed2 = urlparse(url2)
    params2 = parse_qs(parsed2.path if "=" in parsed2.path else parsed2.query)
    assert params2["action"][0] == "reveal"
    assert params2["path"][0] == str(target_file)

    # Test reveal action with directory (folder batch upscale)
    url3 = f"localsr:action=reveal&path={str(target_dir)}"
    parsed3 = urlparse(url3)
    params3 = parse_qs(parsed3.path if "=" in parsed3.path else parsed3.query)
    assert params3["action"][0] == "reveal"
    assert params3["path"][0] == str(target_dir)

    # Test standard URL query format: localsr://?action=open&path=...
    url4 = f"localsr://?action=open&path={str(target_file)}"
    parsed4 = urlparse(url4)
    params4 = parse_qs(parsed4.query)
    assert params4["action"][0] == "open"
    assert params4["path"][0] == str(target_file)


def test_windows_registry_and_script_generation(tmp_path):
    service = WindowsPlatformService()
    with patch.dict(os.environ, {"LOCALAPPDATA": str(tmp_path)}):
        exe_dummy = tmp_path / "LocalSR.exe"
        exe_dummy.write_text("binary")

        commands = service.generate_registry_commands(str(exe_dummy))
        assert len(commands) >= 5
        assert any(c["key"] == r"Software\Classes\*\shell\LocalSR" for c in commands)

        script = service.generate_recipe_picker_script()
        assert "LocalSR" in script
        assert "recipeArg" in script

        installed = service.install_system_integrations(exe_dummy)
        assert installed is True
        scripts_dir = tmp_path / "LocalSR" / "scripts"
        assert (scripts_dir / "recipe_picker.ps1").is_file()

        try:
            if sys.platform == "win32":
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Classes\*\shell\LocalSR",
                ):
                    pass
                assert not (scripts_dir / "LocalSR_ContextMenu.reg").exists()
            else:
                assert (scripts_dir / "LocalSR_ContextMenu.reg").is_file()
        finally:
            assert service.uninstall_system_integrations() is True


def test_linux_desktop_entry_and_script_generation(tmp_path):
    service = LinuxPlatformService()
    with patch.object(Path, "home", return_value=tmp_path):
        exec_dummy = tmp_path / "bin" / "localsr"
        exec_dummy.parent.mkdir(parents=True, exist_ok=True)
        exec_dummy.write_text("#!/bin/sh\n")

        desktop_entry = service.generate_desktop_entry(str(exec_dummy))
        assert "[Desktop Entry]" in desktop_entry
        assert "Name=LocalSR" in desktop_entry
        assert "QuickUpscale" in desktop_entry

        kde_menu = service.generate_kde_servicemenu(str(exec_dummy), "/dummy/picker.sh")
        assert "ServiceTypes=KonqPopupMenu/Plugin" in kde_menu
        assert "Upscale with LocalSR" in kde_menu

        installed = service.install_system_integrations(exec_dummy)
        assert installed is True

        app_desktop = tmp_path / ".local" / "share" / "applications" / "localsr.desktop"
        assert app_desktop.is_file()

        kde_menu_file = (
            tmp_path / ".local" / "share" / "kservices5" / "ServiceMenus" / "localsr.desktop"
        )
        assert kde_menu_file.is_file()

        nautilus_file = (
            tmp_path / ".local" / "share" / "nautilus" / "scripts" / "Upscale with LocalSR"
        )
        assert nautilus_file.is_file()

        uninstalled = service.uninstall_system_integrations()
        assert uninstalled is True
        assert not app_desktop.exists()
