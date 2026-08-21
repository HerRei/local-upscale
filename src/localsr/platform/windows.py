"""Windows desktop integrations: WinRT toast notifications, Explorer context menus, CLI."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .base import PlatformService


class WindowsPlatformService(PlatformService):
    def send_notification(self, title: str, message: str, sound: bool = True) -> bool:
        clean_title = (
            title.replace("`", "``").replace('"', '`"').replace("\r", " ").replace("\n", " ")
        )
        clean_message = (
            message.replace("`", "``").replace('"', '`"').replace("\r", " ").replace("\n", " ")
        )
        sound_attr = "" if sound else ' audio="{silent: true}"'
        ps_script = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
            "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastGeneric);"
            f'$xml = [xml]"<toast{sound_attr}><visual><binding template=\\"ToastGeneric\\"><text>{clean_title}</text><text>{clean_message}</text></binding></visual></toast>";'
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml);"
            '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("LocalSR.Desktop.App").Show($toast);'
        )
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                timeout=5,
                check=False,
            )
            return res.returncode == 0
        except Exception:
            return False

    def install_system_integrations(self, app_path: str | Path | None = None) -> bool:
        success = True
        try:
            self._install_helper_scripts()
        except Exception:
            success = False

        try:
            self._install_registry_context_menus(app_path)
        except Exception:
            success = False

        return success

    def uninstall_system_integrations(self) -> bool:
        success = True
        try:
            self._uninstall_registry_context_menus()
        except Exception:
            success = False

        try:
            scripts_dir = self._get_scripts_dir()
            if scripts_dir.exists():
                shutil.rmtree(scripts_dir, ignore_errors=True)
        except Exception:
            success = False

        return success

    def _get_scripts_dir(self) -> Path:
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        scripts_dir = local_app_data / "LocalSR" / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        return scripts_dir

    def _install_helper_scripts(self) -> Path:
        scripts_dir = self._get_scripts_dir()
        picker_script = scripts_dir / "recipe_picker.ps1"
        picker_script.write_text(self.generate_recipe_picker_script(), encoding="utf-8")
        return picker_script

    def generate_recipe_picker_script(self) -> str:
        return r"""# LocalSR Recipe Picker Dialog for Windows Explorer
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

[System.Windows.Forms.Application]::EnableVisualStyles()

$settingsPath = Join-Path $env:LOCALAPPDATA "LocalSR\settings.json"
$recipes = @("Active App Settings", "Quick Preset (Fast)", "Best Quality Preset")

if (Test-Path $settingsPath) {
    try {
        $json = Get-Content $settingsPath -Raw | ConvertFrom-Json
        if ($json.custom_recipes) {
            foreach ($r in $json.custom_recipes) {
                if ($r.name) {
                    $recipes += $r.name
                }
            }
        }
    } catch {}
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "LocalSR — Select Recipe"
$form.Size = New-Object System.Drawing.Size(360, 240)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $false

$label = New-Object System.Windows.Forms.Label
$label.Location = New-Object System.Drawing.Point(20, 20)
$label.Size = New-Object System.Drawing.Size(300, 20)
$label.Text = "Select upscaling recipe for selected files:"
$form.Controls.Add($label)

$comboBox = New-Object System.Windows.Forms.ComboBox
$comboBox.Location = New-Object System.Drawing.Point(20, 50)
$comboBox.Size = New-Object System.Drawing.Size(300, 24)
$comboBox.DropDownStyle = "DropDownList"
foreach ($rec in $recipes) {
    [void]$comboBox.Items.Add($rec)
}
$comboBox.SelectedIndex = 0
$form.Controls.Add($comboBox)

$okBtn = New-Object System.Windows.Forms.Button
$okBtn.Location = New-Object System.Drawing.Point(140, 140)
$okBtn.Size = New-Object System.Drawing.Size(85, 30)
$okBtn.Text = "Upscale"
$okBtn.DialogResult = [System.Windows.Forms.DialogResult]::OK
$form.AcceptButton = $okBtn
$form.Controls.Add($okBtn)

$cancelBtn = New-Object System.Windows.Forms.Button
$cancelBtn.Location = New-Object System.Drawing.Point(235, 140)
$cancelBtn.Size = New-Object System.Drawing.Size(85, 30)
$cancelBtn.Text = "Cancel"
$cancelBtn.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.CancelButton = $cancelBtn
$form.Controls.Add($cancelBtn)

$result = $form.ShowDialog()

if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    $chosen = $comboBox.SelectedItem.ToString()
    $recipeArg = ""
    if ($chosen -eq "Quick Preset (Fast)") {
        $recipeArg = "--preset quick"
    } elseif ($chosen -eq "Best Quality Preset") {
        $recipeArg = "--preset best"
    } elseif ($chosen -ne "Active App Settings") {
        $recipeArg = "--recipe `"$chosen`""
    }
    
    $exePath = "$env:LOCALAPPDATA\Programs\LocalSR\LocalSR.exe"
    if (-not (Test-Path $exePath)) {
        $exePath = "localsr"
    }
    
    $files = $args | ForEach-Object { "`"$_`"" }
    $execArgs = "$recipeArg --auto-start $($files -join ' ')"
    Start-Process $exePath -ArgumentList $execArgs
}
"""

    def generate_registry_commands(self, exe_path: str) -> list[dict]:
        clean_exe = exe_path.replace('"', "")
        scripts_dir = self._get_scripts_dir()
        picker_ps1 = str(scripts_dir / "recipe_picker.ps1").replace('"', "")

        return [
            {
                "key": r"Software\Classes\*\shell\LocalSR",
                "values": {
                    "MUIVerb": "Upscale with LocalSR",
                    "Icon": f'"{clean_exe}",0',
                    "SubCommands": "LocalSR.Active;LocalSR.Quick;LocalSR.Best;LocalSR.Picker",
                    "AppliesTo": "System.ItemType:=.png OR System.ItemType:=.jpg OR System.ItemType:=.jpeg OR System.ItemType:=.webp OR System.ItemType:=.tiff OR System.ItemType:=.dng OR System.ItemType:=.mp4 OR System.ItemType:=.mov OR System.ItemType:=.mkv OR System.ItemType:=.webm",
                },
            },
            {
                "key": r"Software\Classes\Directory\shell\LocalSR",
                "values": {
                    "MUIVerb": "Upscale Folder with LocalSR",
                    "Icon": f'"{clean_exe}",0',
                    "SubCommands": "LocalSR.Active;LocalSR.Quick;LocalSR.Best;LocalSR.Picker",
                },
            },
            {
                "key": r"Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSR.Active",
                "values": {"": "Active App Settings", "Icon": f'"{clean_exe}",0'},
                "command": f'"{clean_exe}" --auto-start "%1"',
            },
            {
                "key": r"Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSR.Quick",
                "values": {"": "Quick Preset (Fast)", "Icon": f'"{clean_exe}",0'},
                "command": f'"{clean_exe}" --preset quick --auto-start "%1"',
            },
            {
                "key": r"Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSR.Best",
                "values": {"": "Best Quality Preset", "Icon": f'"{clean_exe}",0'},
                "command": f'"{clean_exe}" --preset best --auto-start "%1"',
            },
            {
                "key": r"Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSR.Picker",
                "values": {"": "Choose Recipe...", "Icon": f'"{clean_exe}",0'},
                "command": f'powershell.exe -ExecutionPolicy Bypass -NoProfile -File "{picker_ps1}" "%1"',
            },
        ]

    def _resolve_exe_path(self, app_path: str | Path | None = None) -> str:
        if app_path:
            p = Path(app_path)
            if p.is_file():
                return str(p.resolve())
            if p.is_dir() and (p / "LocalSR.exe").is_file():
                return str((p / "LocalSR.exe").resolve())

        default_exe = (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            / "Programs"
            / "LocalSR"
            / "LocalSR.exe"
        )
        if default_exe.is_file():
            return str(default_exe.resolve())

        which_exe = shutil.which("LocalSR.exe") or shutil.which("localsr")
        if which_exe:
            return str(Path(which_exe).resolve())

        return str(Path(sys.executable).resolve())

    def _install_registry_context_menus(self, app_path: str | Path | None = None) -> None:
        exe_path = self._resolve_exe_path(app_path)
        commands = self.generate_registry_commands(exe_path)

        if sys.platform == "win32":
            import winreg

            for item in commands:
                key_path = item["key"]
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                    for name, val in item.get("values", {}).items():
                        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(val))
                if "command" in item:
                    cmd_path = f"{key_path}\\command"
                    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, cmd_path) as cmd_key:
                        winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, str(item["command"]))
        else:
            # For non-Windows or test environments, write .reg export
            scripts_dir = self._get_scripts_dir()
            reg_file = scripts_dir / "LocalSR_ContextMenu.reg"
            lines = ["Windows Registry Editor Version 5.00", ""]
            for item in commands:
                lines.append(f"[HKEY_CURRENT_USER\\{item['key']}]")
                for name, val in item.get("values", {}).items():
                    escaped_val = str(val).replace("\\", "\\\\").replace('"', '\\"')
                    escaped_name = f'"{name}"' if name else "@"
                    lines.append(f'{escaped_name}="{escaped_val}"')
                lines.append("")
                if "command" in item:
                    lines.append(f"[HKEY_CURRENT_USER\\{item['key']}\\command]")
                    escaped_cmd = str(item["command"]).replace("\\", "\\\\").replace('"', '\\"')
                    lines.append(f'@="{escaped_cmd}"')
                    lines.append("")
            reg_file.write_text("\r\n".join(lines), encoding="utf-8")

    def _uninstall_registry_context_menus(self) -> None:
        if sys.platform == "win32":
            import winreg

            keys_to_delete = [
                r"Software\Classes\*\shell\LocalSR",
                r"Software\Classes\Directory\shell\LocalSR",
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSR.Active",
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSR.Quick",
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSR.Best",
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSR.Picker",
            ]
            for k in keys_to_delete:
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{k}\\command")
                except OSError:
                    pass
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, k)
                except OSError:
                    pass
