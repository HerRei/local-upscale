"""Linux desktop integrations: FreeDesktop .desktop, KDE Dolphin, GNOME Nautilus, D-Bus notifications."""

import shutil
import subprocess
import sys
from pathlib import Path

from .base import PlatformService


class LinuxPlatformService(PlatformService):
    def send_notification(self, title: str, message: str, sound: bool = True) -> bool:
        # 1. Try D-Bus org.freedesktop.Notifications directly via python if available or notify-send
        notify_send = shutil.which("notify-send")
        if notify_send:
            try:
                cmd = [
                    notify_send,
                    "-a",
                    "LocalSR",
                    "-i",
                    "localsr",
                    title,
                    message,
                ]
                res = subprocess.run(cmd, capture_output=True, timeout=5, check=False)
                if res.returncode == 0:
                    return True
            except Exception:
                pass

        # 2. Try direct D-Bus call via gdbus or dbus-send
        gdbus = shutil.which("gdbus")
        if gdbus:
            try:
                clean_title = title.replace('"', '\\"')
                clean_message = message.replace('"', '\\"')
                cmd = [
                    gdbus,
                    "call",
                    "--session",
                    "--dest",
                    "org.freedesktop.Notifications",
                    "--object-path",
                    "/org/freedesktop/Notifications",
                    "--method",
                    "org.freedesktop.Notifications.Notify",
                    "LocalSR",
                    "0",
                    "localsr",
                    clean_title,
                    clean_message,
                    "[]",
                    "{}",
                    "5000",
                ]
                res = subprocess.run(cmd, capture_output=True, timeout=5, check=False)
                return res.returncode == 0
            except Exception:
                pass

        return False

    def install_system_integrations(self, app_path: str | Path | None = None) -> bool:
        success = True
        try:
            self._install_desktop_entry(app_path)
        except Exception:
            success = False

        try:
            self._install_helper_scripts(app_path)
        except Exception:
            success = False

        try:
            self._install_file_manager_actions()
        except Exception:
            success = False

        return success

    def uninstall_system_integrations(self) -> bool:
        success = True
        # 1. Remove .desktop files
        for desktop_file in [
            Path.home() / ".local" / "share" / "applications" / "localsr.desktop",
            Path.home() / ".local" / "share" / "kservices5" / "ServiceMenus" / "localsr.desktop",
            Path.home() / ".local" / "share" / "kio" / "servicemenus" / "localsr.desktop",
        ]:
            try:
                if desktop_file.exists():
                    desktop_file.unlink()
            except Exception:
                success = False

        # 2. Remove Nautilus scripts
        nautilus_script = (
            Path.home() / ".local" / "share" / "nautilus" / "scripts" / "Upscale with LocalSR"
        )
        try:
            if nautilus_script.exists():
                nautilus_script.unlink()
        except Exception:
            success = False

        # 3. Remove helper scripts
        scripts_dir = Path.home() / ".local" / "share" / "localsr"
        try:
            if scripts_dir.exists():
                shutil.rmtree(scripts_dir, ignore_errors=True)
        except Exception:
            success = False

        return success

    def _resolve_binary_path(self, app_path: str | Path | None = None) -> str:
        if app_path:
            p = Path(app_path)
            if p.is_file():
                return str(p.resolve())

        venv_bin = Path(sys.executable).parent / "localsr"
        if venv_bin.is_file():
            return str(venv_bin.resolve())

        which_bin = shutil.which("localsr")
        if which_bin:
            return str(Path(which_bin).resolve())

        return "localsr"

    def generate_desktop_entry(self, exec_path: str) -> str:
        return f"""[Desktop Entry]
Version=1.0
Type=Application
Name=LocalSR
GenericName=AI Image and Video Super-Resolution
Comment=High-performance local super-resolution using state-of-the-art neural architectures
Exec={exec_path} %U
Icon=localsr
Terminal=false
Categories=Graphics;Photography;AudioVideo;Video;
MimeType=image/png;image/jpeg;image/webp;image/tiff;image/x-adobe-dng;video/mp4;video/quicktime;video/x-matroska;video/webm;video/x-msvideo;
StartupNotify=true
StartupWMClass=LocalSR
Actions=QuickUpscale;BestQuality;

[Desktop Action QuickUpscale]
Name=⚡ Quick Upscale
Exec={exec_path} --preset quick --auto-start %U

[Desktop Action BestQuality]
Name=✨ Best Quality Upscale
Exec={exec_path} --preset best --auto-start %U
"""

    def generate_kde_servicemenu(self, exec_path: str, picker_path: str) -> str:
        return f"""[Desktop Entry]
Type=Service
ServiceTypes=KonqPopupMenu/Plugin
MimeType=image/png;image/jpeg;image/webp;image/tiff;image/x-adobe-dng;video/mp4;video/quicktime;video/x-matroska;video/webm;
Actions=LocalSRActive;LocalSRQuick;LocalSRBest;LocalSRPicker;
X-KDE-Submenu=Upscale with LocalSR
X-KDE-Icon=localsr

[Desktop Action LocalSRActive]
Name=Active App Settings
Icon=localsr
Exec={exec_path} --auto-start %U

[Desktop Action LocalSRQuick]
Name=⚡ Quick Preset (Fast)
Icon=localsr
Exec={exec_path} --preset quick --auto-start %U

[Desktop Action LocalSRBest]
Name=✨ Best Quality Preset
Icon=localsr
Exec={exec_path} --preset best --auto-start %U

[Desktop Action LocalSRPicker]
Name=Choose Recipe...
Icon=localsr
Exec={picker_path} %U
"""

    def generate_recipe_picker_script(self, exec_path: str) -> str:
        return f"""#!/bin/sh
# LocalSR Recipe Picker for Linux File Managers (Zenity / KDialog fallback)

SETTINGS_FILE="$HOME/.local/share/LocalSR/settings.json"
RECIPES="Active App Settings\\nQuick Preset (Fast)\\nBest Quality Preset"

if [ -f "$SETTINGS_FILE" ]; then
    CUSTOM_RECIPES=$(python3 -c "
import json
try:
    with open('$SETTINGS_FILE') as f:
        data = json.load(f)
    for r in data.get('custom_recipes', []):
        name = r.get('name', '').strip()
        if name:
            print(name)
except Exception:
    pass
" 2>/dev/null)
    if [ -n "$CUSTOM_RECIPES" ]; then
        RECIPES="$RECIPES\\n$CUSTOM_RECIPES"
    fi
fi

CHOICE=""
if command -v zenity >/dev/null 2>&1; then
    CHOICE=$(echo -e "$RECIPES" | zenity --list --title="LocalSR" --text="Select upscaling recipe for selected files:" --column="Available Recipes" --height=320 --width=380 2>/dev/null)
elif command -v kdialog >/dev/null 2>&1; then
    CHOICE=$(echo -e "$RECIPES" | kdialog --combobox "Select upscaling recipe for selected files:" "Active App Settings" "Quick Preset (Fast)" "Best Quality Preset" --title "LocalSR" 2>/dev/null)
fi

if [ -z "$CHOICE" ]; then
    exit 0
fi

RECIPE_ARG=""
if [ "$CHOICE" = "Quick Preset (Fast)" ]; then
    RECIPE_ARG="--preset quick"
elif [ "$CHOICE" = "Best Quality Preset" ]; then
    RECIPE_ARG="--preset best"
elif [ "$CHOICE" != "Active App Settings" ]; then
    RECIPE_ARG="--recipe \\"$CHOICE\\""
fi

exec {exec_path} $RECIPE_ARG --auto-start "$@"
"""

    def _install_desktop_entry(self, app_path: str | Path | None = None) -> None:
        exec_path = self._resolve_binary_path(app_path)
        apps_dir = Path.home() / ".local" / "share" / "applications"
        apps_dir.mkdir(parents=True, exist_ok=True)
        desktop_file = apps_dir / "localsr.desktop"
        desktop_file.write_text(self.generate_desktop_entry(exec_path), encoding="utf-8")

        if shutil.which("update-desktop-database"):
            subprocess.run(
                ["update-desktop-database", str(apps_dir)], capture_output=True, check=False
            )

    def _install_helper_scripts(self, app_path: str | Path | None = None) -> Path:
        exec_path = self._resolve_binary_path(app_path)
        scripts_dir = Path.home() / ".local" / "share" / "localsr" / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        picker_script = scripts_dir / "recipe_picker.sh"
        picker_script.write_text(self.generate_recipe_picker_script(exec_path), encoding="utf-8")
        try:
            picker_script.chmod(0o755)
        except OSError:
            pass
        return picker_script

    def _install_file_manager_actions(self, app_path: str | Path | None = None) -> None:
        exec_path = self._resolve_binary_path(app_path)
        scripts_dir = Path.home() / ".local" / "share" / "localsr" / "scripts"
        picker_path = str(scripts_dir / "recipe_picker.sh")

        # 1. KDE Dolphin ServiceMenu
        for kde_dir in [
            Path.home() / ".local" / "share" / "kservices5" / "ServiceMenus",
            Path.home() / ".local" / "share" / "kio" / "servicemenus",
        ]:
            try:
                kde_dir.mkdir(parents=True, exist_ok=True)
                menu_file = kde_dir / "localsr.desktop"
                menu_file.write_text(
                    self.generate_kde_servicemenu(exec_path, picker_path), encoding="utf-8"
                )
            except OSError:
                pass

        # 2. GNOME Nautilus Script
        nautilus_dir = Path.home() / ".local" / "share" / "nautilus" / "scripts"
        try:
            nautilus_dir.mkdir(parents=True, exist_ok=True)
            nautilus_script = nautilus_dir / "Upscale with LocalSR"
            nautilus_script.write_text(f'#!/bin/sh\nexec "{picker_path}" "$@"\n', encoding="utf-8")
            nautilus_script.chmod(0o755)
        except OSError:
            pass
