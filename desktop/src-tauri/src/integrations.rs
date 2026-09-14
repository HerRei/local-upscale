use std::{
    env, fs,
    path::{Path, PathBuf},
    process::Command,
};

use directories::BaseDirs;
use serde::Serialize;

use crate::error::{AppError, AppResult};

#[cfg(any(target_os = "linux", test))]
const DISPLAY_NAME: &str = "LocalSR Next Preview";
#[cfg(target_os = "linux")]
const LINUX_ICON_PNG: &[u8] = include_bytes!("../../../packaging/icons/LocalSR.png");

#[derive(Clone, Debug, Serialize)]
pub struct IntegrationStatus {
    pub platform: String,
    pub installed: bool,
    pub summary: String,
    pub command_name: String,
}

pub fn status() -> AppResult<IntegrationStatus> {
    let installed = platform_marker()?.exists();
    Ok(IntegrationStatus {
        platform: std::env::consts::OS.into(),
        installed,
        summary: if installed {
            platform_installed_summary()
        } else {
            "Optional file-manager actions are not installed.".into()
        },
        command_name: "localsr-next".into(),
    })
}

pub fn install() -> AppResult<IntegrationStatus> {
    let executable = integration_executable()?;
    install_platform(&executable)?;
    status()
}

pub fn uninstall() -> AppResult<IntegrationStatus> {
    uninstall_platform()?;
    status()
}

fn base_dirs() -> AppResult<BaseDirs> {
    BaseDirs::new().ok_or_else(|| AppError::Config("home directory unavailable".into()))
}

fn integration_executable() -> AppResult<PathBuf> {
    let current = fs::canonicalize(env::current_exe()?)?;
    #[cfg(target_os = "linux")]
    {
        Ok(resolve_linux_executable(
            current,
            env::var_os("APPIMAGE"),
            env::var_os("APPDIR"),
        ))
    }
    #[cfg(not(target_os = "linux"))]
    {
        Ok(current)
    }
}

#[cfg(any(target_os = "linux", test))]
fn resolve_linux_executable(
    current: PathBuf,
    appimage: Option<std::ffi::OsString>,
    appdir: Option<std::ffi::OsString>,
) -> PathBuf {
    let Some(appimage) = appimage.map(PathBuf::from) else {
        return current;
    };
    let Some(appdir) = appdir.map(PathBuf::from) else {
        return current;
    };
    let Ok(appimage) = fs::canonicalize(appimage) else {
        return current;
    };
    let Ok(appdir) = fs::canonicalize(appdir) else {
        return current;
    };
    if appimage.is_file() && current.starts_with(appdir) {
        appimage
    } else {
        current
    }
}

#[cfg(target_os = "macos")]
fn platform_marker() -> AppResult<PathBuf> {
    Ok(base_dirs()?
        .home_dir()
        .join("Library/Services/Enhance with LocalSR Next Preview.workflow"))
}

#[cfg(target_os = "windows")]
fn platform_marker() -> AppResult<PathBuf> {
    Ok(base_dirs()?
        .data_local_dir()
        .join("LocalSR/next/integrations/installed"))
}

#[cfg(target_os = "linux")]
fn platform_marker() -> AppResult<PathBuf> {
    Ok(base_dirs()?
        .data_local_dir()
        .join("applications/localsr-next.desktop"))
}

#[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
fn platform_marker() -> AppResult<PathBuf> {
    Ok(base_dirs()?
        .data_local_dir()
        .join("LocalSR/next/integrations"))
}

#[cfg(target_os = "macos")]
fn platform_installed_summary() -> String {
    "Finder Quick Action and the optional localsr-next command are installed for this account."
        .into()
}

#[cfg(target_os = "windows")]
fn platform_installed_summary() -> String {
    "Explorer Quick, Best, active-settings, and recipe-picker actions are installed for this account."
        .into()
}

#[cfg(target_os = "linux")]
fn platform_installed_summary() -> String {
    "Desktop, Dolphin, Nautilus, and the optional localsr-next command are installed for this account."
        .into()
}

#[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
fn platform_installed_summary() -> String {
    "System integrations are installed.".into()
}

#[cfg(target_os = "macos")]
fn install_platform(executable: &Path) -> AppResult<()> {
    let home = base_dirs()?.home_dir().to_path_buf();
    let app = enclosing_app_bundle(executable)
        .ok_or_else(|| AppError::Config("install integrations from the packaged app".into()))?;
    if app.starts_with("/Volumes") {
        return Err(AppError::Config(
            "move LocalSR Next Preview to Applications before installing Finder integrations"
                .into(),
        ));
    }
    install_symlink(executable, &home.join(".local/bin/localsr-next"))?;

    let workflow = platform_marker()?;
    let contents = workflow.join("Contents");
    fs::create_dir_all(&contents)?;
    write_atomic(&contents.join("Info.plist"), macos_info_plist().as_bytes())?;
    write_atomic(
        &contents.join("document.wflow"),
        macos_workflow(&app).as_bytes(),
    )?;
    let _ = Command::new("/System/Library/CoreServices/pbs")
        .arg("-update")
        .status();
    Ok(())
}

#[cfg(target_os = "macos")]
fn uninstall_platform() -> AppResult<()> {
    remove_owned_symlink(&base_dirs()?.home_dir().join(".local/bin/localsr-next"))?;
    remove_exact_tree(&platform_marker()?)?;
    let _ = Command::new("/System/Library/CoreServices/pbs")
        .arg("-update")
        .status();
    Ok(())
}

#[cfg(target_os = "windows")]
fn install_platform(executable: &Path) -> AppResult<()> {
    let integration_root = base_dirs()?
        .data_local_dir()
        .join("LocalSR/next/integrations");
    fs::create_dir_all(&integration_root)?;
    let picker = integration_root.join("recipe_picker.ps1");
    write_atomic(&picker, windows_recipe_picker(executable).as_bytes())?;
    for entry in windows_registry_entries(executable, &picker) {
        registry_add(&entry.key, entry.name.as_deref(), &entry.value)?;
    }
    write_atomic(
        &integration_root.join("installed"),
        b"LocalSR Next Preview\n",
    )?;
    Ok(())
}

#[cfg(target_os = "windows")]
fn uninstall_platform() -> AppResult<()> {
    for key in windows_registry_roots() {
        let status = Command::new("reg.exe")
            .args(["delete", key, "/f"])
            .status()?;
        // reg.exe returns 1 when an optional key did not exist.
        if !status.success() && status.code() != Some(1) {
            return Err(AppError::Config(format!(
                "could not remove Explorer integration key {key}"
            )));
        }
    }
    remove_exact_tree(
        &base_dirs()?
            .data_local_dir()
            .join("LocalSR/next/integrations"),
    )
}

#[cfg(target_os = "linux")]
fn install_platform(executable: &Path) -> AppResult<()> {
    use std::os::unix::fs::PermissionsExt;

    let base = base_dirs()?;
    let home = base.home_dir();
    install_symlink(executable, &home.join(".local/bin/localsr-next"))?;

    let data = base.data_local_dir();
    let scripts = data.join("localsr-next/scripts");
    let picker = scripts.join("recipe_picker.sh");
    fs::create_dir_all(&scripts)?;
    write_atomic(&picker, linux_recipe_picker(executable).as_bytes())?;
    fs::set_permissions(&picker, fs::Permissions::from_mode(0o755))?;

    let application = data.join("applications/localsr-next.desktop");
    write_atomic(
        &data.join("icons/hicolor/1024x1024/apps/localsr-next.png"),
        LINUX_ICON_PNG,
    )?;
    write_atomic(&application, linux_desktop_entry(executable).as_bytes())?;
    for directory in [
        data.join("kservices5/ServiceMenus"),
        data.join("kio/servicemenus"),
    ] {
        write_atomic(
            &directory.join("localsr-next.desktop"),
            linux_kde_service_menu(executable, &picker).as_bytes(),
        )?;
    }
    let nautilus = data
        .join("nautilus/scripts")
        .join("Enhance with LocalSR Next Preview");
    write_atomic(&nautilus, linux_nautilus_script(&picker).as_bytes())?;
    fs::set_permissions(&nautilus, fs::Permissions::from_mode(0o755))?;

    let _ = Command::new("update-desktop-database")
        .arg(data.join("applications"))
        .status();
    Ok(())
}

#[cfg(target_os = "linux")]
fn uninstall_platform() -> AppResult<()> {
    let base = base_dirs()?;
    remove_owned_symlink(&base.home_dir().join(".local/bin/localsr-next"))?;
    let data = base.data_local_dir();
    for file in [
        data.join("applications/localsr-next.desktop"),
        data.join("kservices5/ServiceMenus/localsr-next.desktop"),
        data.join("kio/servicemenus/localsr-next.desktop"),
        data.join("nautilus/scripts/Enhance with LocalSR Next Preview"),
        data.join("icons/hicolor/1024x1024/apps/localsr-next.png"),
    ] {
        remove_exact_file(&file)?;
    }
    remove_exact_tree(&data.join("localsr-next"))
}

#[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
fn install_platform(_executable: &Path) -> AppResult<()> {
    Err(AppError::Config(
        "system integrations are not supported on this platform".into(),
    ))
}

#[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
fn uninstall_platform() -> AppResult<()> {
    Ok(())
}

fn write_atomic(path: &Path, contents: &[u8]) -> AppResult<()> {
    let parent = path
        .parent()
        .ok_or_else(|| AppError::Config("integration path has no parent".into()))?;
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(
        ".{}.{}.tmp",
        path.file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("localsr-next"),
        std::process::id()
    ));
    fs::write(&temporary, contents)?;
    if let Err(error) = fs::rename(&temporary, path) {
        #[cfg(target_os = "windows")]
        {
            // std::fs::rename cannot replace an existing file on Windows.
            // These paths are all exact, per-user files owned by this preview.
            if path.is_file() {
                fs::remove_file(path)?;
                fs::rename(&temporary, path)?;
            } else {
                let _ = fs::remove_file(&temporary);
                return Err(error.into());
            }
        }
        #[cfg(not(target_os = "windows"))]
        {
            let _ = fs::remove_file(&temporary);
            return Err(error.into());
        }
    }
    Ok(())
}

#[cfg(any(target_os = "macos", target_os = "linux"))]
fn install_symlink(target: &Path, link: &Path) -> AppResult<()> {
    use std::os::unix::fs::symlink;

    let parent = link
        .parent()
        .ok_or_else(|| AppError::Config("command path has no parent".into()))?;
    fs::create_dir_all(parent)?;
    if link.symlink_metadata().is_ok() {
        if link.is_symlink() {
            fs::remove_file(link)?;
        } else {
            return Err(AppError::Config(format!(
                "refusing to overwrite the existing file {}",
                link.display()
            )));
        }
    }
    symlink(target, link)?;
    Ok(())
}

#[cfg(any(target_os = "macos", target_os = "linux"))]
fn remove_owned_symlink(path: &Path) -> AppResult<()> {
    if path.symlink_metadata().is_ok() && path.is_symlink() {
        fs::remove_file(path)?;
    }
    Ok(())
}

fn remove_exact_file(path: &Path) -> AppResult<()> {
    if path.is_file() || path.is_symlink() {
        fs::remove_file(path)?;
    }
    Ok(())
}

fn remove_exact_tree(path: &Path) -> AppResult<()> {
    if path.is_dir() {
        fs::remove_dir_all(path)?;
    } else {
        remove_exact_file(path)?;
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn enclosing_app_bundle(executable: &Path) -> Option<PathBuf> {
    executable
        .ancestors()
        .find(|path| path.extension().is_some_and(|extension| extension == "app"))
        .map(Path::to_path_buf)
}

#[cfg(any(target_os = "macos", test))]
fn xml_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

#[cfg(any(target_os = "macos", target_os = "linux", test))]
fn shell_quote(path: &Path) -> String {
    format!("'{}'", path.to_string_lossy().replace('\'', "'\\''"))
}

#[cfg(any(target_os = "linux", test))]
fn desktop_exec_quote(path: &Path) -> String {
    let escaped = path
        .to_string_lossy()
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('`', "\\`")
        .replace('$', "\\$");
    format!("\"{escaped}\"")
}

#[cfg(any(target_os = "macos", test))]
fn macos_info_plist() -> String {
    r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>NSServices</key><array><dict>
<key>NSMenuItem</key><dict><key>default</key><string>Enhance with LocalSR Next Preview</string></dict>
<key>NSMessage</key><string>runWorkflowAsService</string>
<key>NSSendFileTypes</key><array><string>public.image</string><string>public.movie</string><string>public.folder</string></array>
</dict></array></dict></plist>
"#
    .into()
}

#[cfg(any(target_os = "macos", test))]
fn macos_workflow(app: &Path) -> String {
    let script = format!(
        "choice=$(osascript -e 'choose from list {{\"Active App Settings\", \"Quick Preset (Fast)\", \"Best Quality Preset\"}} with title \"LocalSR Next Preview\" with prompt \"Choose settings for the selected media:\"' 2>/dev/null) || exit 0\ncase \"$choice\" in\n  \"Quick Preset (Fast)\") preset='--preset quick' ;;\n  \"Best Quality Preset\") preset='--preset best' ;;\n  *) preset='' ;;\nesac\nopen -n -a {} --args $preset --auto-start \"$@\"",
        shell_quote(app)
    );
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>AMApplicationBuild</key><string>512</string><key>AMApplicationVersion</key><string>2.10</string><key>AMDocumentVersion</key><string>2</string><key>actions</key><array><dict><key>action</key><dict><key>AMAccepts</key><dict><key>Container</key><string>List</string><key>Optional</key><true/><key>Types</key><array><string>com.apple.cocoa.path</string></array></dict><key>AMActionVersion</key><string>2.0.3</string><key>AMApplication</key><array><string>Automator</string></array><key>AMParameterProperties</key><dict><key>COMMAND_STRING</key><dict/></dict><key>AMProvides</key><dict><key>Container</key><string>List</string><key>Types</key><array><string>com.apple.cocoa.path</string></array></dict><key>ActionBundlePath</key><string>/System/Library/Automator/Run Shell Script.action</string><key>ActionName</key><string>Run Shell Script</string><key>ActionParameters</key><dict><key>COMMAND_STRING</key><string>{}</string><key>CheckedForUserDefaultShell</key><true/><key>inputMethod</key><integer>1</integer><key>shell</key><string>/bin/zsh</string></dict><key>BundleIdentifier</key><string>com.apple.RunShellScript</string><key>CFBundleVersion</key><string>2.0.3</string><key>CanShowSelectedItemsWhenRun</key><false/><key>CanShowWhenRun</key><true/><key>Category</key><array><string>AMCategoryUtilities</string></array><key>Class Name</key><string>RunShellScriptAction</string><key>InputUUID</key><string>localsr-next-input</string><key>OutputUUID</key><string>localsr-next-output</string><key>UUID</key><string>localsr-next-action</string></dict><key>isViewVisible</key><true/></dict></array><key>connectors</key><dict/><key>workflowMetaData</key><dict><key>serviceInputTypeIdentifier</key><string>com.apple.Automator.fileSystemObject</string><key>serviceOutputTypeIdentifier</key><string>com.apple.Automator.nothing</string><key>serviceProcessesInput</key><integer>0</integer><key>workflowTypeIdentifier</key><string>com.apple.Automator.servicesMenu</string></dict></dict></plist>
"#,
        xml_escape(&script)
    )
}

#[cfg(any(target_os = "linux", test))]
fn linux_desktop_entry(executable: &Path) -> String {
    let executable = desktop_exec_quote(executable);
    format!(
        "[Desktop Entry]\nVersion=1.0\nType=Application\nName={DISPLAY_NAME}\nGenericName=Local AI Image and Video Restoration\nComment=Enhance media locally with external AI models\nExec={executable} %F\nIcon=localsr-next\nTerminal=false\nCategories=Graphics;Photography;AudioVideo;Video;\nMimeType=image/png;image/jpeg;image/webp;image/tiff;image/x-adobe-dng;video/mp4;video/quicktime;video/x-matroska;video/webm;video/mpeg;video/mp2t;video/x-ms-wmv;video/x-ms-asf;video/x-flv;video/3gpp;video/3gpp2;video/ogg;video/x-msvideo;inode/directory;\nStartupNotify=true\nStartupWMClass=LocalSR Next Preview\nActions=QuickUpscale;BestQuality;\n\n[Desktop Action QuickUpscale]\nName=Quick Upscale\nExec={executable} --preset quick --auto-start %F\n\n[Desktop Action BestQuality]\nName=Best Quality Upscale\nExec={executable} --preset best --auto-start %F\n"
    )
}

#[cfg(any(target_os = "linux", test))]
fn linux_kde_service_menu(executable: &Path, picker: &Path) -> String {
    let executable = desktop_exec_quote(executable);
    let picker = desktop_exec_quote(picker);
    format!(
        "[Desktop Entry]\nType=Service\nServiceTypes=KonqPopupMenu/Plugin\nMimeType=image/png;image/jpeg;image/webp;image/tiff;image/x-adobe-dng;video/mp4;video/quicktime;video/x-matroska;video/webm;video/mpeg;video/mp2t;video/x-ms-wmv;video/x-ms-asf;video/x-flv;video/3gpp;video/3gpp2;video/ogg;inode/directory;\nActions=LocalSRNextActive;LocalSRNextQuick;LocalSRNextBest;LocalSRNextPicker;\nX-KDE-Submenu=Enhance with LocalSR Next Preview\nX-KDE-Icon=localsr-next\n\n[Desktop Action LocalSRNextActive]\nName=Active App Settings\nExec={executable} --auto-start %F\n\n[Desktop Action LocalSRNextQuick]\nName=Quick Preset (Fast)\nExec={executable} --preset quick --auto-start %F\n\n[Desktop Action LocalSRNextBest]\nName=Best Quality Preset\nExec={executable} --preset best --auto-start %F\n\n[Desktop Action LocalSRNextPicker]\nName=Choose Recipe…\nExec={picker} %F\n"
    )
}

#[cfg(any(target_os = "linux", test))]
fn linux_recipe_picker(executable: &Path) -> String {
    format!(
        r#"#!/bin/sh
choice=""
if command -v zenity >/dev/null 2>&1; then
  choice=$(printf '%s\n' 'Active App Settings' 'Quick Preset (Fast)' 'Best Quality Preset' | zenity --list --title='LocalSR Next Preview' --text='Choose settings:' --column='Available settings' --height=280 --width=380 2>/dev/null)
elif command -v kdialog >/dev/null 2>&1; then
  choice=$(kdialog --combobox 'Choose settings:' 'Active App Settings' 'Quick Preset (Fast)' 'Best Quality Preset' --title 'LocalSR Next Preview' 2>/dev/null)
fi
case "$choice" in
  'Quick Preset (Fast)') exec {} --preset quick --auto-start "$@" ;;
  'Best Quality Preset') exec {} --preset best --auto-start "$@" ;;
  'Active App Settings') exec {} --auto-start "$@" ;;
esac
"#,
        shell_quote(executable),
        shell_quote(executable),
        shell_quote(executable)
    )
}

#[cfg(any(target_os = "linux", test))]
fn linux_nautilus_script(picker: &Path) -> String {
    format!(
        r#"#!/bin/sh
set -f
if [ -n "${{NAUTILUS_SCRIPT_SELECTED_FILE_PATHS:-}}" ]; then
  previous_ifs=$IFS
  IFS='
'
  set -- $NAUTILUS_SCRIPT_SELECTED_FILE_PATHS
  IFS=$previous_ifs
fi
exec {} "$@"
"#,
        shell_quote(picker)
    )
}

#[cfg(any(target_os = "windows", test))]
struct RegistryEntry {
    key: String,
    name: Option<String>,
    value: String,
}

#[cfg(any(target_os = "windows", test))]
fn windows_registry_entries(executable: &Path, picker: &Path) -> Vec<RegistryEntry> {
    let executable = executable.to_string_lossy();
    let picker = picker.to_string_lossy();
    let command_root =
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell";
    let file_menu_root = r"HKCU\Software\Classes\*\shell\LocalSRNext";
    let directory_menu_root = r"HKCU\Software\Classes\Directory\shell\LocalSRNext";
    let mut entries = Vec::new();
    for menu_root in [file_menu_root, directory_menu_root] {
        entries.extend([
            RegistryEntry {
                key: menu_root.into(),
                name: Some("MUIVerb".into()),
                value: "Enhance with LocalSR Next Preview".into(),
            },
            RegistryEntry {
                key: menu_root.into(),
                name: Some("Icon".into()),
                value: format!("\"{executable}\",0"),
            },
            RegistryEntry {
                key: menu_root.into(),
                name: Some("SubCommands".into()),
                value: "LocalSRNext.Active;LocalSRNext.Quick;LocalSRNext.Best;LocalSRNext.Picker"
                    .into(),
            },
        ]);
    }
    entries.extend([
        RegistryEntry {
            key: file_menu_root.into(),
            name: Some("MultiSelectModel".into()),
            value: "Player".into(),
        },
        RegistryEntry {
            key: file_menu_root.into(),
            name: Some("AppliesTo".into()),
            value: "System.FileExtension:=.png OR System.FileExtension:=.jpg OR System.FileExtension:=.jpeg OR System.FileExtension:=.webp OR System.FileExtension:=.bmp OR System.FileExtension:=.tif OR System.FileExtension:=.tiff OR System.FileExtension:=.dng OR System.FileExtension:=.mp4 OR System.FileExtension:=.mov OR System.FileExtension:=.m4v OR System.FileExtension:=.avi OR System.FileExtension:=.mkv OR System.FileExtension:=.webm OR System.FileExtension:=.mpg OR System.FileExtension:=.mpeg OR System.FileExtension:=.mpe OR System.FileExtension:=.vob OR System.FileExtension:=.ts OR System.FileExtension:=.mts OR System.FileExtension:=.m2ts OR System.FileExtension:=.wmv OR System.FileExtension:=.asf OR System.FileExtension:=.flv OR System.FileExtension:=.f4v OR System.FileExtension:=.3gp OR System.FileExtension:=.3g2 OR System.FileExtension:=.ogv OR System.FileExtension:=.divx".into(),
        },
    ]);
    for (id, title, arguments) in [
        (
            "Active",
            "Active App Settings",
            "--auto-start \"%1\"".to_owned(),
        ),
        (
            "Quick",
            "Quick Preset (Fast)",
            "--preset quick --auto-start \"%1\"".to_owned(),
        ),
        (
            "Best",
            "Best Quality Preset",
            "--preset best --auto-start \"%1\"".to_owned(),
        ),
        (
            "Picker",
            "Choose Recipe…",
            format!("-NoProfile -ExecutionPolicy Bypass -File \"{picker}\" \"%1\""),
        ),
    ] {
        let key = format!(r"{command_root}\LocalSRNext.{id}");
        entries.push(RegistryEntry {
            key: key.clone(),
            name: None,
            value: title.into(),
        });
        entries.push(RegistryEntry {
            key: key.clone(),
            name: Some("Icon".into()),
            value: format!("\"{executable}\",0"),
        });
        let command = if id == "Picker" {
            format!("powershell.exe {arguments}")
        } else {
            format!("\"{executable}\" {arguments}")
        };
        entries.push(RegistryEntry {
            key: format!(r"{key}\command"),
            name: None,
            value: command,
        });
    }
    entries
}

#[cfg(any(target_os = "windows", test))]
fn windows_registry_roots() -> [&'static str; 6] {
    [
        r"HKCU\Software\Classes\*\shell\LocalSRNext",
        r"HKCU\Software\Classes\Directory\shell\LocalSRNext",
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSRNext.Active",
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSRNext.Quick",
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSRNext.Best",
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSRNext.Picker",
    ]
}

#[cfg(target_os = "windows")]
fn registry_add(key: &str, name: Option<&str>, value: &str) -> AppResult<()> {
    let mut command = Command::new("reg.exe");
    command.args(["add", key]);
    if let Some(name) = name {
        command.args(["/v", name]);
    } else {
        command.arg("/ve");
    }
    let status = command.args(["/d", value, "/f"]).status()?;
    if !status.success() {
        return Err(AppError::Config(format!(
            "could not register Explorer integration key {key}"
        )));
    }
    Ok(())
}

#[cfg(any(target_os = "windows", test))]
fn windows_recipe_picker(executable: &Path) -> String {
    let executable = executable.to_string_lossy().replace('\'', "''");
    format!(
        r#"Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$choices = @('Active App Settings', 'Quick Preset (Fast)', 'Best Quality Preset')
$form = New-Object System.Windows.Forms.Form
$form.Text = 'LocalSR Next Preview'
$form.Width = 390; $form.Height = 190; $form.StartPosition = 'CenterScreen'
$combo = New-Object System.Windows.Forms.ComboBox
$combo.Left = 20; $combo.Top = 25; $combo.Width = 335; $combo.DropDownStyle = 'DropDownList'
[void]$combo.Items.AddRange($choices); $combo.SelectedIndex = 0; $form.Controls.Add($combo)
$button = New-Object System.Windows.Forms.Button
$button.Text = 'Enhance'; $button.Left = 270; $button.Top = 75; $button.DialogResult = 'OK'; $form.Controls.Add($button); $form.AcceptButton = $button
if ($form.ShowDialog() -ne 'OK') {{ exit 0 }}
$preset = @()
if ($combo.SelectedItem -eq 'Quick Preset (Fast)') {{ $preset = @('--preset', 'quick') }}
if ($combo.SelectedItem -eq 'Best Quality Preset') {{ $preset = @('--preset', 'best') }}
$quotedFiles = $args | ForEach-Object {{ '"' + $_ + '"' }}
$nativeArguments = ($preset + @('--auto-start') + $quotedFiles) -join ' '
Start-Process -FilePath '{}' -ArgumentList $nativeArguments
"#,
        executable
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generated_integrations_use_the_additive_binary_and_identity() {
        let executable =
            Path::new("/Applications/LocalSR Next Preview.app/Contents/MacOS/localsr-next");
        let desktop = linux_desktop_entry(executable);
        assert!(desktop.contains("Name=LocalSR Next Preview"));
        assert!(desktop.contains("--preset quick --auto-start"));
        assert!(desktop.contains(
            "Exec=\"/Applications/LocalSR Next Preview.app/Contents/MacOS/localsr-next\" %F"
        ));
        assert!(desktop.contains("inode/directory"));
        assert!(desktop.contains("Icon=localsr-next"));
        assert!(!desktop.contains("%U"));
        assert!(!desktop.contains("Exec=localsr "));

        let workflow = macos_workflow(Path::new("/Applications/LocalSR Next Preview.app"));
        assert!(workflow.contains("--preset best"));
        assert!(workflow.contains("LocalSR Next Preview.app"));
        assert!(workflow.contains("open -n -a"));
        assert!(macos_info_plist().contains("public.folder"));

        let picker = linux_recipe_picker(executable);
        let service = linux_kde_service_menu(executable, Path::new("/tmp/picker.sh"));
        let nautilus = linux_nautilus_script(Path::new("/tmp/picker.sh"));
        assert!(picker.contains("--auto-start"));
        assert!(service.contains("LocalSRNextPicker"));
        assert!(service.contains("%F"));
        assert!(nautilus.contains("NAUTILUS_SCRIPT_SELECTED_FILE_PATHS"));

        let windows_executable = Path::new(r"C:\Program Files\LocalSR Next\localsr-next.exe");
        let windows_picker = Path::new(r"C:\LocalSR Next\recipe_picker.ps1");
        let registry = windows_registry_entries(windows_executable, windows_picker);
        assert!(registry.iter().any(|entry| {
            entry.key.contains(r"Classes\Directory\shell\LocalSRNext")
                && entry.name.as_deref() == Some("MUIVerb")
        }));
        assert!(registry.iter().any(|entry| {
            entry.name.as_deref() == Some("MultiSelectModel") && entry.value == "Player"
        }));
        assert!(windows_registry_roots()
            .iter()
            .any(|key| key.contains(r"Classes\Directory\shell\LocalSRNext")));
        assert!(windows_recipe_picker(windows_executable).contains("$quotedFiles"));
    }

    #[test]
    fn shell_quoting_does_not_split_paths() {
        assert_eq!(shell_quote(Path::new("/tmp/Local SR")), "'/tmp/Local SR'");
        assert_eq!(
            shell_quote(Path::new("/tmp/user's app")),
            "'/tmp/user'\\''s app'"
        );
        assert_eq!(
            desktop_exec_quote(Path::new("/tmp/Local $SR\\preview")),
            "\"/tmp/Local \\$SR\\\\preview\""
        );
    }

    #[test]
    fn appimage_integrations_target_the_persistent_container_only_from_its_mount() {
        let temporary = tempfile::tempdir().unwrap();
        let mount = temporary.path().join("mount/usr/bin");
        fs::create_dir_all(&mount).unwrap();
        let current = mount.join("localsr-next");
        let appimage = temporary.path().join("LocalSR Next Preview.AppImage");
        let other_appimage = temporary.path().join("other.AppImage");
        let unrelated_mount = temporary.path().join("elsewhere");
        fs::write(&current, b"host").unwrap();
        fs::write(&appimage, b"appimage").unwrap();
        fs::write(&other_appimage, b"other").unwrap();
        fs::create_dir_all(&unrelated_mount).unwrap();
        let current = fs::canonicalize(current).unwrap();

        assert_eq!(
            resolve_linux_executable(
                current.clone(),
                Some(appimage.clone().into_os_string()),
                Some(temporary.path().join("mount").into_os_string()),
            ),
            fs::canonicalize(appimage).unwrap()
        );
        assert_eq!(
            resolve_linux_executable(
                current.clone(),
                Some(other_appimage.into_os_string()),
                Some(unrelated_mount.into_os_string()),
            ),
            current
        );
    }
}
