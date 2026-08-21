"""macOS desktop integrations: notifications, Finder Quick Actions, CLI symlinks."""

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from .base import PlatformService


class MacOSPlatformService(PlatformService):
    def send_notification(self, title: str, message: str, sound: bool = True) -> bool:
        clean_title = (
            title.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")
        )
        clean_message = (
            message.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")
        )
        sound_clause = ' sound name "Glass"' if sound else ""
        script = f'display notification "{clean_message}" with title "{clean_title}"{sound_clause}'
        try:
            res = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return res.returncode == 0
        except Exception:
            return False

    def install_system_integrations(self, app_path: str | Path | None = None) -> bool:
        success = True
        try:
            self._install_cli_symlink(app_path)
        except Exception:
            success = False

        try:
            self._install_finder_quick_action(app_path)
        except Exception:
            success = False

        return success

    def uninstall_system_integrations(self) -> bool:
        removed = True
        try:
            workflow = Path.home() / "Library" / "Services" / "Upscale with LocalSR.workflow"
            if workflow.exists():
                if workflow.is_dir():
                    shutil.rmtree(workflow)
                else:
                    workflow.unlink()
        except Exception:
            removed = False

        for link_path in [
            Path.home() / ".local" / "bin" / "localsr",
            Path("/usr/local/bin/localsr"),
        ]:
            try:
                if link_path.is_symlink() or link_path.is_file():
                    link_path.unlink()
            except Exception:
                pass
        return removed

    def _resolve_binary_path(self, app_path: str | Path | None = None) -> Path | None:
        if app_path:
            p = Path(app_path)
            if p.is_dir() and (p / "Contents" / "MacOS" / "LocalSR").is_file():
                return p / "Contents" / "MacOS" / "LocalSR"
            if p.is_file():
                return p

        standard_app = Path("/Applications/LocalSR.app/Contents/MacOS/LocalSR")
        if standard_app.is_file():
            return standard_app

        venv_bin = Path(sys.executable).parent / "localsr"
        if venv_bin.is_file():
            return venv_bin

        which_bin = shutil.which("localsr")
        if which_bin:
            return Path(which_bin)

        return None

    def _install_cli_symlink(self, app_path: str | Path | None = None) -> None:
        target_bin = self._resolve_binary_path(app_path)
        if not target_bin or not target_bin.exists():
            return

        # 1. ~/.local/bin/localsr
        local_bin = Path.home() / ".local" / "bin"
        local_bin.mkdir(parents=True, exist_ok=True)
        symlink_path = local_bin / "localsr"
        try:
            if symlink_path.is_symlink() or symlink_path.is_file():
                symlink_path.unlink()
            symlink_path.symlink_to(target_bin)
        except OSError:
            pass

        # 2. /usr/local/bin/localsr (if writable or creatable)
        usr_local = Path("/usr/local/bin")
        if (
            not usr_local.exists()
            and Path("/usr/local").is_dir()
            and os.access("/usr/local", os.W_OK)
        ):
            try:
                usr_local.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        if usr_local.is_dir() and os.access(usr_local, os.W_OK):
            usr_symlink = usr_local / "localsr"
            try:
                if usr_symlink.is_symlink() or usr_symlink.is_file():
                    usr_symlink.unlink()
                usr_symlink.symlink_to(target_bin)
            except OSError:
                pass

    def _install_finder_quick_action(self, app_path: str | Path | None = None) -> None:
        services_dir = Path.home() / "Library" / "Services"
        services_dir.mkdir(parents=True, exist_ok=True)
        workflow_dir = services_dir / "Upscale with LocalSR.workflow"
        contents_dir = workflow_dir / "Contents"
        contents_dir.mkdir(parents=True, exist_ok=True)

        info_plist_path = contents_dir / "Info.plist"
        info_plist_data = {
            "NSServices": [
                {
                    "NSMenuItem": {"default": "Upscale with LocalSR"},
                    "NSMessage": "runWorkflowAsService",
                    "NSSendFileTypes": [
                        "public.image",
                        "public.movie",
                        "public.item",
                        "public.content",
                        "public.data",
                    ],
                }
            ]
        }
        with open(info_plist_path, "wb") as f:
            plistlib.dump(info_plist_data, f)

        # Build runner script inside the workflow
        wflow_path = contents_dir / "document.wflow"
        shell_script = self._generate_workflow_shell_script(app_path)
        wflow_content = self._generate_wflow_xml(shell_script)
        wflow_path.write_text(wflow_content, encoding="utf-8")

        # Flush macOS services menu cache
        try:
            subprocess.run(
                ["/System/Library/CoreServices/pbs", "-update"],
                capture_output=True,
                timeout=3,
                check=False,
            )
        except Exception:
            pass

    def _generate_workflow_shell_script(self, app_path: str | Path | None = None) -> str:
        target_app = "/Applications/LocalSR.app"
        if app_path:
            p = Path(app_path)
            if p.suffix == ".app" and p.is_dir():
                target_app = str(p)
            elif (
                len(p.parents) >= 2
                and p.parent.name == "MacOS"
                and p.parent.parent.name == "Contents"
                and p.parent.parent.parent.suffix == ".app"
            ):
                target_app = str(p.parent.parent.parent)

        return f"""
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"

# 1. Fetch available recipes from settings
PYTHON_BIN="$(command -v python3 || echo "/usr/bin/python3")"
RECIPES_JSON=$("$PYTHON_BIN" -c "
import json, os, sys
try:
    p = os.path.expanduser('~/Library/Application Support/LocalSR/settings.json')
    if os.path.exists(p):
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for r in data.get('custom_recipes', []):
            name = str(r.get('name', '')).strip()
            if name:
                print(name)
except Exception:
    pass
" 2>/dev/null)

RECIPE_NAMES=()
if [ -n "$RECIPES_JSON" ]; then
    while IFS= read -r line; do
        if [ -n "$line" ]; then
            RECIPE_NAMES+=("$line")
        fi
    done <<< "$RECIPES_JSON"
fi

# Build AppleScript choices list
APPLESCRIPT_ITEMS='{{"Active App Settings", "Quick Preset (Fast)", "Best Quality Preset"'
for r in "${{RECIPE_NAMES[@]}}"; do
    CLEAN_R=$(printf "%s" "$r" | sed 's/\\\\/\\\\\\\\/g; s/"/\\\\"/g')
    APPLESCRIPT_ITEMS="$APPLESCRIPT_ITEMS, \\"$CLEAN_R\\""
done
APPLESCRIPT_ITEMS="$APPLESCRIPT_ITEMS}}"

CHOICE=$(osascript -e "
set recipeList to $APPLESCRIPT_ITEMS
set chosen to choose from list recipeList with prompt \\"Choose recipe for LocalSR upscaling:\\" default items {{\\"Active App Settings\\"}} with title \\"LocalSR Quick Action\\"
if chosen is false then
    return \\"CANCEL\\"
else
    return item 1 of chosen
end if
" 2>/dev/null)

if [ "$CHOICE" = "CANCEL" ] || [ -z "$CHOICE" ]; then
    exit 0
fi

ARGS=()
if [ "$CHOICE" = "Quick Preset (Fast)" ]; then
    ARGS+=(--preset quick)
elif [ "$CHOICE" = "Best Quality Preset" ]; then
    ARGS+=(--preset best)
elif [ "$CHOICE" != "Active App Settings" ]; then
    ARGS+=(--recipe "$CHOICE")
fi
ARGS+=(--auto-start)
ARGS+=("$@")

# Launch LocalSR with queued files and auto-start
if [ -d "{target_app}" ]; then
    open -n -a "{target_app}" --args "${{ARGS[@]}}"
elif [ -d "/Applications/LocalSR.app" ]; then
    open -n -a "/Applications/LocalSR.app" --args "${{ARGS[@]}}"
elif command -v localsr >/dev/null 2>&1; then
    localsr "${{ARGS[@]}}" &
fi
"""

    def _generate_wflow_xml(self, shell_script: str) -> str:
        escaped_script = (
            shell_script.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>AMApplicationBuild</key>
	<string>523</string>
	<key>AMApplicationVersion</key>
	<string>2.10</string>
	<key>AMDocumentVersion</key>
	<string>2</string>
	<key>actions</key>
	<array>
		<dict>
			<key>action</key>
			<dict>
				<key>AMAccepts</key>
				<dict>
					<key>Container</key>
					<string>List</string>
					<key>Types</key>
					<array>
						<string>com.apple.cocoa.path</string>
					</array>
				</dict>
				<key>AMActionVersion</key>
				<string>2.0.3</string>
				<key>AMParameterProperties</key>
				<dict>
					<key>COMMAND_STRING</key>
					<dict/>
					<key>CheckedForUserDefaultShell</key>
					<dict/>
					<key>inputMethod</key>
					<dict/>
					<key>shell</key>
					<dict/>
					<key>source</key>
					<dict/>
				</dict>
				<key>AMProvides</key>
				<dict>
					<key>Container</key>
					<string>List</string>
					<key>Types</key>
					<array>
						<string>com.apple.cocoa.path</string>
					</array>
				</dict>
				<key>ActionBundlePath</key>
				<string>/System/Library/Automator/Run Shell Script.action</string>
				<key>ActionName</key>
				<string>Run Shell Script</string>
				<key>ActionParameters</key>
				<dict>
					<key>COMMAND_STRING</key>
					<string>{escaped_script}</string>
					<key>CheckedForUserDefaultShell</key>
					<true/>
					<key>inputMethod</key>
					<integer>1</integer>
					<key>shell</key>
					<string>/bin/bash</string>
					<key>source</key>
					<string></string>
				</dict>
				<key>BundleIdentifier</key>
				<string>com.apple.RunShellScript</string>
				<key>CFBundleVersion</key>
				<string>2.0.3</string>
				<key>CanShowSelectedItemsWhenRun</key>
				<false/>
				<key>CanShowWhenRun</key>
				<true/>
				<key>Category</key>
				<array>
					<string>AMCategoryUtilities</string>
				</array>
				<key>Class Name</key>
				<string>RunShellScriptAction</string>
				<key>InputUUID</key>
				<string>1A2B3C4D-5E6F-7A8B-9C0D-1E2F3A4B5C6D</string>
				<key>Keywords</key>
				<array>
					<string>Shell</string>
					<string>Script</string>
					<string>Command</string>
					<string>Run</string>
					<string>Unix</string>
				</array>
				<key>OutputUUID</key>
				<string>2B3C4D5E-6F7A-8B9C-0D1E-2F3A4B5C6D7E</string>
				<key>UUID</key>
				<string>3C4D5E6F-7A8B-9C0D-1E2F-3A4B5C6D7E8F</string>
				<key>UnlocalizedApplications</key>
				<array>
					<string>Automator</string>
				</array>
			</dict>
		</dict>
	</array>
	<key>connectors</key>
	<dict/>
	<key>workflowMetaData</key>
	<dict>
		<key>workflowTypeIdentifier</key>
		<string>com.apple.Automator.servicesMenu</string>
	</dict>
</dict>
</plist>
"""
