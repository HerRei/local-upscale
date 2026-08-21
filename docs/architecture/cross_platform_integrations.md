# LocalSR Cross-Platform Desktop Integrations: Windows & Linux Architecture Specification

**Status:** Approved Architectural Specification  
**Authors:** LocalSR Core Architecture Team  
**Target Systems:** Windows 10/11 (x86-64 / ARM64), Linux (X11 / Wayland / Flatpak / AppImage), macOS 12+ (Apple Silicon / Intel)  
**Primary Seams:** `src/localsr/platform/`, `src/localsr/ui/native_dialog.py`, `packaging/`

---

## Executive Summary & Architectural Vision

LocalSR provides private, local, high-fidelity image and video super-resolution. While macOS enjoys deep system integration (Finder Quick Actions, `osascript` notifications, unified Apple Silicon memory management), Windows and Linux users require an equally first-class, native desktop experience. 

This specification establishes the architectural foundation and concrete implementation schemas for **Windows** and **Linux** desktop integrations. It guarantees:
1. **First-Class OS Experience:** Native context menus with interactive recipe selection, actionable toast notifications, taskbar/dock integration, and complete file associations.
2. **Zero Cross-Platform Interference:** Strict encapsulation inside `src/localsr/platform/`, lazy runtime dispatch, zero foreign platform module imports, and 100% crash-free headless/CI operation.
3. **Unified Protocol & CLI Parity:** Seamless alignment between CLI parameters (`--preset`, `--recipe`, `--auto-start`), JSON-lines IPC worker communication, and persistent settings.

---

## 1. Windows Desktop Integration Architecture (R1)

### 1.1 Windows Explorer Context Menu Integration

Windows desktop users interact primarily through File Explorer. Integration requires supporting both the classic Windows 10 cascading registry verbs and the modern Windows 11 sparse MSIX / `IExplorerCommand` context menu.

```
                   [Right Click on Media File(s) or Folder]
                                     │
             ┌───────────────────────┴───────────────────────┐
             ▼                                               ▼
   [Windows 10 / Classic Shell]                 [Windows 11 Modern Shell]
    HKCU\Software\Classes\*\shell\LocalSR       MSIX Sparse Package Identity
    SystemFileAssociations (image/video)        `IExplorerCommand` COM Server
             │                                               │
             └───────────────────────┬───────────────────────┘
                                     │
                   ┌─────────────────┴─────────────────┐
                   ▼                                   ▼
          [Direct Presets]                    [Interactive Picker]
      "Quick Preset (Fast)"                  "Choose Recipe..."
      "Best Quality Preset"                            │
                   │                                   ▼
                   │                           PowerShell/Slint Dialog
                   │                           Reads settings.json recipes
                   └─────────────────┬─────────────────┘
                                     │
                                     ▼
                         Executes LocalSR.exe with:
                         --preset / --recipe / --auto-start
```

#### 1.1.1 Windows 10 Classic Context Menu (Registry Verbs)
For Windows 10 and classic Explorer contexts, LocalSR registers cascading verb hierarchies under `HKCU\Software\Classes\*\shell\LocalSR` (for universal file handling), `HKCU\Software\Classes\SystemFileAssociations` (for targeted `image` and `video` formats), as well as `Directory` and `Directory\Background` (to upscale folders of media).

##### Exact Registry Key Schema
```ini
; ==============================================================================
; Universal File Right-Click Cascading Menu (Requirement R1.1)
; ==============================================================================
[HKEY_CURRENT_USER\Software\Classes\*\shell\LocalSR]
"MUIVerb"="Upscale with LocalSR"
"Icon"="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\",0"
"SubCommands"="LocalSR.ActiveSettings;LocalSR.QuickPreset;LocalSR.BestPreset;LocalSR.ChooseRecipe"
"MultiSelectModel"="Player"
"AppliesTo"="System.ItemType:=.png OR System.ItemType:=.jpg OR System.ItemType:=.jpeg OR System.ItemType:=.webp OR System.ItemType:=.tiff OR System.ItemType:=.dng OR System.ItemType:=.mp4 OR System.ItemType:=.mov OR System.ItemType:=.m4v OR System.ItemType:=.mkv OR System.ItemType:=.webm OR System.ItemType:=.avi"

; ==============================================================================
; Image File Type Cascading Context Menu
; ==============================================================================
[HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\image\shell\LocalSR]
"MUIVerb"="Upscale with LocalSR"
"Icon"="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\",0"
"SubCommands"="LocalSR.ActiveSettings;LocalSR.QuickPreset;LocalSR.BestPreset;LocalSR.ChooseRecipe"
"MultiSelectModel"="Player"

; ==============================================================================
; Video File Type Cascading Context Menu
; ==============================================================================
[HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\video\shell\LocalSR]
"MUIVerb"="Upscale with LocalSR"
"Icon"="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\",0"
"SubCommands"="LocalSR.ActiveSettings;LocalSR.QuickPreset;LocalSR.BestPreset;LocalSR.ChooseRecipe"
"MultiSelectModel"="Player"

; ==============================================================================
; Directory Context Menu (Batch Upscale Entire Folder)
; ==============================================================================
[HKEY_CURRENT_USER\Software\Classes\Directory\shell\LocalSR]
"MUIVerb"="Upscale Folder with LocalSR"
"Icon"="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\",0"
"SubCommands"="LocalSR.ActiveSettings;LocalSR.QuickPreset;LocalSR.BestPreset;LocalSR.ChooseRecipe"

[HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\LocalSR]
"MUIVerb"="Upscale Current Folder with LocalSR"
"Icon"="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\",0"
"SubCommands"="LocalSR.ActiveSettings;LocalSR.QuickPreset;LocalSR.BestPreset;LocalSR.ChooseRecipe"

; ==============================================================================
; Verb Command Definitions under CommandStore
; ==============================================================================
[HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSR.ActiveSettings]
@="Upscale with Active Settings"
"Icon"="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\",0"

[HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSR.ActiveSettings\command]
@="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\" --auto-start \"%1\""

[HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSR.QuickPreset]
@="Quick Preset (Fast)"
"Icon"="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\",0"

[HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSR.QuickPreset\command]
@="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\" --preset quick --auto-start \"%1\""

[HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSR.BestPreset]
@="Best Quality Preset"
"Icon"="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\",0"

[HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSR.BestPreset\command]
@="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\" --preset best --auto-start \"%1\""

[HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSR.ChooseRecipe]
@="Choose Recipe..."
"Icon"="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\",0"

[HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSR.ChooseRecipe\command]
@="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\" --recipe-dialog \"%1\""
```

##### Enterprise / LTSC Nested Key Fallback
On legacy Windows 10 Enterprise or LTSC environments where `CommandStore` registry queries under `HKCU` are restricted by group policy, LocalSR falls back to nested `shell` verb hierarchies directly within the key path:
```ini
[HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\image\shell\LocalSR]
"MUIVerb"="Upscale with LocalSR"
"SubCommands"=""

[HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\image\shell\LocalSR\shell\1_Active]
@="Upscale with Active Settings"
[HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\image\shell\LocalSR\shell\1_Active\command]
@="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\" --auto-start \"%1\""

[HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\image\shell\LocalSR\shell\2_Quick]
@="Quick Preset (Fast)"
[HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\image\shell\LocalSR\shell\2_Quick\command]
@="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\" --preset quick --auto-start \"%1\""

[HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\image\shell\LocalSR\shell\3_Best]
@="Best Quality Preset"
[HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\image\shell\LocalSR\shell\3_Best\command]
@="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\" --preset best --auto-start \"%1\""

[HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\image\shell\LocalSR\shell\4_Choose]
@="Choose Recipe..."
[HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\image\shell\LocalSR\shell\4_Choose\command]
@="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\" --recipe-dialog \"%1\""
```

*Key Technical Details:*
- **`AppliesTo` AQS Filter**: Filters the universal `*\shell\LocalSR` menu item so it only appears for supported image and video file extensions, avoiding shell clutter on unsupported binaries or scripts.
- **`MultiSelectModel="Player"`**: Crucial setting. Without this, selecting 10 images in Explorer and clicking the context menu spawns 10 independent `LocalSR.exe` processes. With `"Player"`, Explorer passes the multi-selection into the application command invocation or drops them into the running single-instance window.
- **Per-User Scope (`HKCU`)**: Avoids mandatory UAC administrator elevation during registration and supports portable/user installs.
- **Native Binary Entry (`--recipe-dialog`)**: Invoking `LocalSR.exe --recipe-dialog "%1"` directly eliminates dependence on external PowerShell execution policies and ensures instant display.

---

#### 1.1.2 Windows 11 Modern Context Menu (Sparse MSIX & `IExplorerCommand`)

Windows 11 collapses classic registry verbs into the secondary "Show more options" menu unless an application implements the `IExplorerCommand` COM interface and registers package identity via a **Sparse MSIX Package**.

##### Sparse Package Manifest Structure (`AppxManifest.xml`)
A sparse package grants package identity to an uncontained desktop Win32 application without sandboxing file system access:

```xml
<?xml version="1.0" encoding="utf-8"?>
<Package
  xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:uap10="http://schemas.microsoft.com/appx/manifest/uap/windows10/10"
  xmlns:desktop4="http://schemas.microsoft.com/appx/manifest/desktop/windows10/4"
  xmlns:desktop5="http://schemas.microsoft.com/appx/manifest/desktop/windows10/5"
  xmlns:com="http://schemas.microsoft.com/appx/manifest/com/windows10"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
  IgnorableNamespaces="uap uap10 desktop4 desktop5 com rescap">

  <Identity
    Name="LocalSR.Desktop"
    Publisher="CN=LocalSR Contributors"
    Version="0.1.0.0"
    ProcessorArchitecture="x64" />

  <Properties>
    <DisplayName>LocalSR</DisplayName>
    <PublisherDisplayName>LocalSR</PublisherDisplayName>
    <Logo>Assets\StoreLogo.png</Logo>
    <uap10:AllowExternalContent>true</uap10:AllowExternalContent>
  </Properties>

  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.22621.0" />
  </Dependencies>

  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>

  <Applications>
    <Application Id="LocalSRApp" Executable="LocalSR.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements
        DisplayName="LocalSR"
        Description="Private local image and video super-resolution"
        Square150x150Logo="Assets\Square150x150Logo.png"
        Square44x44Logo="Assets\Square44x44Logo.png"
        BackgroundColor="transparent" />

      <Extensions>
        <!-- Windows 11 Modern Context Menu Handler Extension -->
        <desktop4:FileExplorerContextMenus>
          <desktop5:ItemType Type="*">
            <desktop5:Verb Id="LocalSRCommand" Clsid="AD28BB74-EBDC-4B94-86EF-C6390D878D92" />
          </desktop5:ItemType>
          <desktop5:ItemType Type="Directory">
            <desktop5:Verb Id="LocalSRDirCommand" Clsid="AD28BB74-EBDC-4B94-86EF-C6390D878D92" />
          </desktop5:ItemType>
        </desktop4:FileExplorerContextMenus>

        <!-- COM In-Process Server Registration for IExplorerCommand -->
        <com:ComServer>
          <com:InProcessServer Path="LocalSRContextMenu.dll">
            <com:Class Id="AD28BB74-EBDC-4B94-86EF-C6390D878D92" ThreadingModel="Apartment" DisplayName="LocalSR Explorer Command" />
          </com:InProcessServer>
        </com:ComServer>
      </Extensions>
    </Application>
  </Applications>
</Package>
```

##### Modern `IExplorerCommand` COM Server Implementation Architecture
`LocalSRContextMenu.dll` is a lightweight native COM DLL (built via C++ / Windows SDK or Rust `windows` crate) loaded by File Explorer.

```cpp
// LocalSRContextMenu.cpp - Windows 11 IExplorerCommand & IEnumExplorerCommand Implementation
#include <windows.h>
#include <shobjidl_core.h>
#include <shlwapi.h>
#include <wrl/module.h>
#include <wrl/implements.h>
#include <string>
#include <vector>
#include <memory>

#pragma comment(lib, "shlwapi.lib")

using namespace Microsoft::WRL;

// Global module handle for icon extraction and path resolution
HINSTANCE g_hModule = NULL;

BOOL WINAPI DllMain(HINSTANCE hinstDLL, DWORD fdwReason, LPVOID lpvReserved) {
    if (fdwReason == DLL_PROCESS_ATTACH) {
        g_hModule = hinstDLL;
        DisableThreadLibraryCalls(hinstDLL);
    }
    return TRUE;
}

// Subcommand definition descriptor
struct SubCommandDesc {
    const wchar_t* title;
    const wchar_t* cliArguments;
};

static const SubCommandDesc g_subCommands[] = {
    { L"Upscale with Active Settings", L"--auto-start" },
    { L"Quick Preset (Fast)",          L"--preset quick --auto-start" },
    { L"Best Quality Preset",         L"--preset best --auto-start" },
    { L"Choose Recipe...",             L"--recipe-dialog" }
};

// Helper: Resolve executable path from DLL directory or LocalAppData fallback
static std::wstring GetLocalSRExecutablePath() {
    WCHAR dllPath[MAX_PATH];
    if (GetModuleFileNameW(g_hModule, dllPath, MAX_PATH) > 0) {
        PathRemoveFileSpecW(dllPath);
        PathAppendW(dllPath, L"LocalSR.exe");
        if (PathFileExistsW(dllPath)) {
            return std::wstring(dllPath);
        }
    }
    WCHAR expandedPath[MAX_PATH];
    ExpandEnvironmentStringsW(L"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe", expandedPath, MAX_PATH);
    return std::wstring(expandedPath);
}

// Child Subcommand Implementation
class LocalSRSubCommand : public RuntimeClass<RuntimeClassFlags<ClassicCom>, IExplorerCommand> {
public:
    LocalSRSubCommand(const wchar_t* title, const wchar_t* flags) 
        : m_title(title), m_flags(flags) {}

    IFACEMETHODIMP GetTitle(IShellItemArray* psiItemArray, LPWSTR* ppszName) override {
        if (!ppszName) return E_POINTER;
        return SHStrDupW(m_title.c_str(), ppszName);
    }

    IFACEMETHODIMP GetIcon(IShellItemArray* psiItemArray, LPWSTR* ppszIcon) override {
        if (!ppszIcon) return E_POINTER;
        std::wstring exePath = GetLocalSRExecutablePath();
        return SHStrDupW((exePath + L",0").c_str(), ppszIcon);
    }

    IFACEMETHODIMP GetToolTip(IShellItemArray* psiItemArray, LPWSTR* ppszInfo) override {
        if (!ppszInfo) return E_POINTER;
        return SHStrDupW(m_title.c_str(), ppszInfo);
    }

    IFACEMETHODIMP GetState(IShellItemArray* psiItemArray, BOOL fOkToBeSlow, EXPCMDSTATE* pCmdState) override {
        if (!pCmdState) return E_POINTER;
        *pCmdState = ECS_ENABLED;
        return S_OK;
    }

    IFACEMETHODIMP GetFlags(EXPCMDFLAGS* pFlags) override {
        if (!pFlags) return E_POINTER;
        *pFlags = ECF_DEFAULT;
        return S_OK;
    }

    IFACEMETHODIMP EnumSubCommands(IEnumExplorerCommand** ppEnum) override {
        if (!ppEnum) return E_POINTER;
        *ppEnum = nullptr;
        return E_NOTIMPL;
    }

    IFACEMETHODIMP Invoke(IShellItemArray* psiItemArray, IBindCtx* pbc) override {
        if (!psiItemArray) return S_OK;

        std::wstring exePath = GetLocalSRExecutablePath();
        std::wstring cmdLine = L"\"" + exePath + L"\" " + m_flags;

        DWORD count = 0;
        psiItemArray->GetCount(&count);
        for (DWORD i = 0; i < count; ++i) {
            ComPtr<IShellItem> item;
            if (SUCCEEDED(psiItemArray->GetItemAt(i, &item))) {
                PWSTR path = nullptr;
                if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path))) {
                    std::wstring itemPath(path);
                    // If path ends with backslash, double it so closing quote is not escaped in CommandLineToArgvW
                    if (!itemPath.empty() && itemPath.back() == L'\\') {
                        itemPath += L'\\';
                    }
                    cmdLine += L" \"" + itemPath + L"\"";
                    CoTaskMemFree(path);
                }
            }
        }

        STARTUPINFOW si = { sizeof(si) };
        PROCESS_INFORMATION pi = {};
        std::vector<wchar_t> cmdBuf(cmdLine.begin(), cmdLine.end());
        cmdBuf.push_back(L'\0');

        if (CreateProcessW(NULL, cmdBuf.data(), NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) {
            CloseHandle(pi.hProcess);
            CloseHandle(pi.hThread);
        }
        return S_OK;
    }

    IFACEMETHODIMP GetCanonicalName(GUID* pguidCommandName) override {
        if (!pguidCommandName) return E_POINTER;
        *pguidCommandName = GUID_NULL;
        return S_OK;
    }

private:
    std::wstring m_title;
    std::wstring m_flags;
};

// Subcommand Enumerator Implementation
class LocalSRCommandEnumerator : public RuntimeClass<RuntimeClassFlags<ClassicCom>, IEnumExplorerCommand> {
public:
    LocalSRCommandEnumerator(ULONG index = 0) : m_index(index) {}

    IFACEMETHODIMP Next(ULONG celt, IExplorerCommand** pUICommand, ULONG* pceltFetched) override {
        if (!pUICommand) return E_POINTER;
        ULONG fetched = 0;
        const ULONG total = static_cast<ULONG>(sizeof(g_subCommands) / sizeof(g_subCommands[0]));

        while (m_index < total && fetched < celt) {
            auto sub = Make<LocalSRSubCommand>(g_subCommands[m_index].title, g_subCommands[m_index].cliArguments);
            sub.CopyTo(&pUICommand[fetched]);
            m_index++;
            fetched++;
        }
        if (pceltFetched) *pceltFetched = fetched;
        return (fetched == celt) ? S_OK : S_FALSE;
    }

    IFACEMETHODIMP Skip(ULONG celt) override {
        m_index += celt;
        return S_OK;
    }

    IFACEMETHODIMP Reset() override {
        m_index = 0;
        return S_OK;
    }

    IFACEMETHODIMP Clone(IEnumExplorerCommand** ppenum) override {
        if (!ppenum) return E_POINTER;
        return Make<LocalSRCommandEnumerator>(m_index).CopyTo(ppenum);
    }

private:
    ULONG m_index;
};

// Root Context Menu Command Handler
class DECLSPEC_UUID("AD28BB74-EBDC-4B94-86EF-C6390D878D92") LocalSRCommand 
    : public RuntimeClass<RuntimeClassFlags<ClassicCom>, IExplorerCommand> {
public:
    IFACEMETHODIMP GetTitle(IShellItemArray* psiItemArray, LPWSTR* ppszName) override {
        if (!ppszName) return E_POINTER;
        return SHStrDupW(L"Upscale with LocalSR", ppszName);
    }

    IFACEMETHODIMP GetIcon(IShellItemArray* psiItemArray, LPWSTR* ppszIcon) override {
        if (!ppszIcon) return E_POINTER;
        std::wstring exePath = GetLocalSRExecutablePath();
        return SHStrDupW((exePath + L",0").c_str(), ppszIcon);
    }

    IFACEMETHODIMP GetToolTip(IShellItemArray* psiItemArray, LPWSTR* ppszInfo) override {
        if (!ppszInfo) return E_POINTER;
        return SHStrDupW(L"Enhance image or video resolution with LocalSR neural networks", ppszInfo);
    }

    IFACEMETHODIMP GetState(IShellItemArray* psiItemArray, BOOL fOkToBeSlow, EXPCMDSTATE* pCmdState) override {
        if (!pCmdState) return E_POINTER;
        *pCmdState = ECS_ENABLED;
        return S_OK;
    }

    IFACEMETHODIMP GetFlags(EXPCMDFLAGS* pFlags) override {
        if (!pFlags) return E_POINTER;
        *pFlags = ECF_HASSUBCOMMANDS; // Enables cascading submenu in modern context menu
        return S_OK;
    }

    IFACEMETHODIMP EnumSubCommands(IEnumExplorerCommand** ppEnum) override {
        if (!ppEnum) return E_POINTER;
        return Make<LocalSRCommandEnumerator>().CopyTo(ppEnum);
    }

    IFACEMETHODIMP Invoke(IShellItemArray* psiItemArray, IBindCtx* pbc) override {
        // Default root action launches active settings
        auto defaultAction = Make<LocalSRSubCommand>(g_subCommands[0].title, g_subCommands[0].cliArguments);
        return defaultAction->Invoke(psiItemArray, pbc);
    }

    IFACEMETHODIMP GetCanonicalName(GUID* pguidCommandName) override {
        if (!pguidCommandName) return E_POINTER;
        *pguidCommandName = GUID_NULL;
        return S_OK;
    }
};

CoCreatableClass(LocalSRCommand);

// Standard COM In-Process Server Entry Points
STDAPI DllGetClassObject(REFCLSID rclsid, REFIID riid, LPVOID* ppv) {
    if (!ppv) return E_POINTER;
    return Module<InProc>::GetModule().GetClassObject(rclsid, riid, ppv);
}

STDAPI DllCanUnloadNow() {
    return Module<InProc>::GetModule().Terminate() ? S_OK : S_FALSE;
}
```

##### Sparse Package Registration Lifecycle
```powershell
# Registration during Inno Setup / CLI install (PowerShell 5.1+ / 7+):
Add-AppxPackage -Register "C:\Program Files\LocalSR\AppxManifest.xml" -ExternalLocation "C:\Program Files\LocalSR"

# Deregistration during uninstall:
Get-AppxPackage -Name "LocalSR.Desktop" | Remove-AppxPackage
```

---

#### 1.1.3 Windows Recipe Selection Dialog Workflow

When the user clicks "Choose Recipe...", the dialog reads user-configured custom recipes from `%LOCALAPPDATA%\LocalSR\settings.json`.

```
                  [User selects "Choose Recipe..."]
                                 │
                 ┌───────────────┴───────────────┐
                 ▼                               ▼
       [Primary Native UI]            [PowerShell Fallback Runner]
      LocalSR.exe --recipe-dialog     recipe_picker.ps1 (-STA -Bypass)
                 │                               │
                 └───────────────┬───────────────┘
                                 │
                                 ▼
                    [Interactive Recipe Picker]
                Reads %LOCALAPPDATA%\LocalSR\settings.json
                     Dropdown of Presets & Recipes
                                 │
                                 ▼
                     Executes LocalSR.exe with:
                     --recipe "<Choice>" --auto-start [FILES...]
```

##### Robust Fallback Script (`packaging/windows/recipe_picker.ps1`)
```powershell
# recipe_picker.ps1 - Native Windows Recipe Selection Helper with Per-Monitor High-DPI
param (
    [Parameter(Mandatory=$false, ValueFromRemainingArguments=$true)]
    [string[]]$Files = @()
)

# Enforce UTF-8 console and process execution
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

# Per-Monitor V2 High-DPI awareness initialization (Windows 10 1703+ with legacy fallback)
try {
    if (-not ([System.Management.Automation.PSTypeName]'LocalSR.User32Dpi').Type) {
        Add-Type -MemberDefinition '[DllImport("user32.dll", SetLastError=true)] public static extern bool SetProcessDpiAwarenessContext(int dpiContext);' -Name User32Dpi -Namespace LocalSR -PassThru > $null
    }
    [LocalSR.User32Dpi]::SetProcessDpiAwarenessContext(-4) > $null # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
} catch {
    try {
        if (-not ([System.Management.Automation.PSTypeName]'LocalSR.ShcoreDpi').Type) {
            Add-Type -MemberDefinition '[DllImport("shcore.dll", SetLastError=true)] public static extern int SetProcessDpiAwareness(int awareness);' -Name ShcoreDpi -Namespace LocalSR -PassThru > $null
        }
        [LocalSR.ShcoreDpi]::SetProcessDpiAwareness(2) > $null # PROCESS_PER_MONITOR_DPI_AWARE
    } catch {}
}

$settingsPath = "$env:LOCALAPPDATA\LocalSR\settings.json"
$recipes = @("Active App Settings", "Quick Preset (Fast)", "Best Quality Preset")

if (Test-Path -Path $settingsPath -PathType Leaf) {
    try {
        $jsonContent = [System.IO.File]::ReadAllText($settingsPath, [System.Text.Encoding]::UTF8)
        $json = $jsonContent | ConvertFrom-Json
        if ($json.custom_recipes) {
            foreach ($r in $json.custom_recipes) {
                if ($r.name) { $recipes += [string]$r.name }
            }
        }
    } catch {
        # Fallback to defaults on corrupt settings
    }
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "LocalSR - Recipe Selection"
$form.Size = New-Object System.Drawing.Size(420, 260)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.TopMost = $true
$form.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::Dpi
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9.5)

$label = New-Object System.Windows.Forms.Label
$label.Location = New-Object System.Drawing.Point(20, 20)
$label.Size = New-Object System.Drawing.Size(360, 26)
$label.Text = "Select recipe or preset for selected file(s):"
$form.Controls.Add($label)

$combo = New-Object System.Windows.Forms.ComboBox
$combo.Location = New-Object System.Drawing.Point(20, 52)
$combo.Size = New-Object System.Drawing.Size(360, 28)
$combo.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
foreach ($item in $recipes) { [void]$combo.Items.Add($item) }
$combo.SelectedIndex = 0
$form.Controls.Add($combo)

$btnOk = New-Object System.Windows.Forms.Button
$btnOk.Location = New-Object System.Drawing.Point(190, 165)
$btnOk.Size = New-Object System.Drawing.Size(90, 32)
$btnOk.Text = "Upscale"
$btnOk.DialogResult = [System.Windows.Forms.DialogResult]::OK
$form.Controls.Add($btnOk)

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Location = New-Object System.Drawing.Point(290, 165)
$btnCancel.Size = New-Object System.Drawing.Size(90, 32)
$btnCancel.Text = "Cancel"
$btnCancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.Controls.Add($btnCancel)
$form.AcceptButton = $btnOk
$form.CancelButton = $btnCancel

if ($form.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    $choice = $combo.SelectedItem.ToString()
    $argsList = @()
    if ($choice -eq "Quick Preset (Fast)") {
        $argsList += "--preset"
        $argsList += "quick"
    } elseif ($choice -eq "Best Quality Preset") {
        $argsList += "--preset"
        $argsList += "best"
    } elseif ($choice -ne "Active App Settings") {
        $argsList += "--recipe"
        $argsList += $choice
    }
    $argsList += "--auto-start"
    foreach ($f in $Files) {
        $argsList += $f
    }
    
    $exePath = "$env:LOCALAPPDATA\Programs\LocalSR\LocalSR.exe"
    if (-not (Test-Path $exePath)) {
        $exePath = "LocalSR.exe"
    }
    # Pass arguments safely preserving spaces and special characters
    Start-Process -FilePath $exePath -ArgumentList ($argsList | ForEach-Object {
        if ($_ -match '[\s"]') { '"{0}"' -f ($_ -replace '"', '\"') } else { $_ }
    })
}
```

---

### 1.2 Native WinRT Toast Notifications

Notifications inform users upon batch completion, report processing errors, and provide interactive action buttons ("Open Result", "Reveal in Explorer") using Windows Notification API (`Windows.UI.Notifications`).

```
  LocalSR Worker Completed
            │
            ▼
┌───────────────────────────────────────────────┐
│ WindowsPlatformService.send_notification()    │
└───────────────────────┬───────────────────────┘
                        │
       ┌────────────────┴────────────────┐
       ▼                                 ▼
[Native WinRT C++/Python]       [PowerShell Fallback Runner]
ToastNotificationManager        Non-Interactive Subprocess
Interactive Action Callbacks    ToastGeneric XML Template
Protocol URL Activation         Base64 Escaped Pipeline
```

#### 1.2.1 Toast XML Payload Structure
```xml
<toast scenario="reminder" activationType="protocol" launch="localsr:action=open&amp;path=C:\Outputs\upscaled.png">
  <visual>
    <binding template="ToastGeneric">
      <text hint-maxLines="1">LocalSR - Upscaling Complete</text>
      <text>upscaled_photo.png finished in 4.2s (4x HAT)</text>
      <image placement="appLogoOverride" hint-crop="circle" src="file:///C:/Program%20Files/LocalSR/resources/icon.png" />
      <image placement="hero" src="file:///C:/Outputs/upscaled_preview.jpg" />
    </binding>
  </visual>
  <actions>
    <action
      content="Open Result"
      arguments="localsr:action=open&amp;path=C:\Outputs\upscaled.png"
      activationType="protocol" />
    <action
      content="Reveal in Explorer"
      arguments="localsr:action=reveal&amp;path=C:\Outputs\upscaled.png"
      activationType="protocol" />
    <action
      content="Dismiss"
      arguments="dismiss"
      activationType="system" />
  </actions>
  <audio src="ms-winsoundevent:Notification.Default" />
</toast>
```

#### 1.2.2 Hardened Base64 PowerShell/WinRT Script Generator
To prevent quoting corruption, metacharacter interpretation, backtick hazards, and PowerShell `$variable` string interpolation, the XML payload is encoded in Base64 before dispatch:

```python
import base64
import html
import subprocess
from pathlib import Path


def generate_windows_toast_command(
    title: str,
    message: str,
    output_path: str | None = None,
    preview_image: str | None = None,
    sound: bool = True,
    aumid: str = "LocalSR.Desktop.App",
) -> list[str]:
    """Generate injection-proof PowerShell command to emit native WinRT toast."""
    clean_title = html.escape(title)
    clean_message = html.escape(message)

    actions_xml = ""
    if output_path:
        clean_path = html.escape(output_path).replace('"', "&quot;")
        actions_xml = f"""
        <actions>
            <action content="Open Result" arguments="localsr:action=open&amp;path={clean_path}" activationType="protocol"/>
            <action content="Reveal in Explorer" arguments="localsr:action=reveal&amp;path={clean_path}" activationType="protocol"/>
            <action content="Dismiss" arguments="dismiss" activationType="system"/>
        </actions>
        """

    hero_xml = ""
    if preview_image and Path(preview_image).is_file():
        clean_preview = preview_image.replace("\\", "/").replace('"', "&quot;")
        hero_xml = f'<image placement="hero" src="file:///{clean_preview}"/>'

    audio_xml = (
        '<audio src="ms-winsoundevent:Notification.Default"/>'
        if sound
        else '<audio silent="true"/>'
    )

    xml_payload = f"""<toast scenario="reminder">
        <visual>
            <binding template="ToastGeneric">
                <text hint-maxLines="1">{clean_title}</text>
                <text>{clean_message}</text>
                {hero_xml}
            </binding>
        </visual>
        {actions_xml}
        {audio_xml}
    </toast>"""

    encoded_xml = base64.b64encode(xml_payload.encode("utf-8")).decode("ascii")

    ps_script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
        f"$bytes = [System.Convert]::FromBase64String('{encoded_xml}');"
        "$xmlString = [System.Text.Encoding]::UTF8.GetString($bytes);"
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        "$xml.LoadXml($xmlString);"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml);"
        f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{aumid}").Show($toast);'
    )

    return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_script]
```

#### 1.2.3 Custom URL Protocol Dispatcher Architecture (`localsr:`)
When notification action buttons are clicked, Windows executes `LocalSR.exe --protocol "<URI>"`. The protocol dispatcher routes actions cleanly without spawning duplicate GUI processes:

```python
"""Custom URL Protocol Router for Toast Action Buttons."""
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def handle_protocol_url(url_string: str) -> None:
    """Parse and dispatch incoming localsr: protocol activation commands."""
    parsed = urlparse(url_string)
    # Parse query parameters from "localsr:action=open&path=..." or "localsr://?action=open&..."
    query = parsed.query or (parsed.path if "=" in parsed.path else "")
    params = parse_qs(query)

    action = params.get("action", [None])[0]
    target_path = params.get("path", [None])[0]

    if not target_path or not Path(target_path).exists():
        return

    if action == "open":
        # Launch system default viewer for upscaled media
        if sys.platform == "win32":
            os.startfile(target_path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target_path])
        else:
            subprocess.Popen(["xdg-open", target_path])

    elif action == "reveal":
        # Highlight and reveal item in file manager
        if sys.platform == "win32":
            subprocess.Popen(["explorer.exe", f"/select,{target_path}"])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", target_path])
        else:
            target_dir = str(Path(target_path).parent if Path(target_path).is_file() else target_path)
            subprocess.Popen(["xdg-open", target_dir])
```

---

### 1.3 Taskbar Drag-and-Drop & File Associations

#### 1.3.1 Explicit AppUserModelID (AUMID) & UIPI Message Filtering
Windows taskbar icon grouping, pinning, and toast notification attribution require setting an explicit AppUserModelID before initializing any GUI windows. Additionally, to support drag-and-drop across integrity levels (e.g. dragging from standard Explorer into an elevated LocalSR window), UIPI message filters are configured:

```python
import ctypes
import sys


def setup_windows_process_identity() -> None:
    """Set explicit AppUserModelID for Windows taskbar and toast integration."""
    if sys.platform != "win32":
        return
    try:
        aumid = "LocalSR.Desktop.App"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(aumid))
    except Exception:
        pass


def enable_windows_drag_and_drop(hwnd: int) -> None:
    """Allow drag-and-drop across User Interface Privilege Isolation (UIPI) boundaries."""
    if sys.platform != "win32" or not hwnd:
        return
    try:
        user32 = ctypes.windll.user32
        MSGFLT_ALLOW = 1
        WM_DROPFILES = 0x0233
        WM_COPYDATA = 0x004A
        WM_COPYGLOBALDATA = 0x0049

        for msg in (WM_DROPFILES, WM_COPYDATA, WM_COPYGLOBALDATA):
            user32.ChangeWindowMessageFilterEx(ctypes.c_void_p(hwnd), msg, MSGFLT_ALLOW, None)
    except Exception:
        pass
```

#### 1.3.2 File Associations, ProgID & RegisteredApplications Registry Schema
Associating `.png`, `.jpg`, `.jpeg`, `.webp`, `.tiff`, `.dng`, `.mp4`, `.mov`, `.m4v`, `.mkv`, `.webm`, `.avi` and the `localsr:` custom URL Protocol with LocalSR and integrating with Windows Default Apps:

```ini
; ==============================================================================
; LocalSR ProgID Definitions
; ==============================================================================
[HKEY_CURRENT_USER\Software\Classes\LocalSR.Image.1]
@="LocalSR Image File"
"AppUserModelID"="LocalSR.Desktop.App"

[HKEY_CURRENT_USER\Software\Classes\LocalSR.Image.1\DefaultIcon]
@="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\",0"

[HKEY_CURRENT_USER\Software\Classes\LocalSR.Image.1\shell\open\command]
@="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\" \"%1\""

[HKEY_CURRENT_USER\Software\Classes\LocalSR.Video.1]
@="LocalSR Video File"
"AppUserModelID"="LocalSR.Desktop.App"

[HKEY_CURRENT_USER\Software\Classes\LocalSR.Video.1\DefaultIcon]
@="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\",0"

[HKEY_CURRENT_USER\Software\Classes\LocalSR.Video.1\shell\open\command]
@="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\" \"%1\""

; ==============================================================================
; URL Protocol Handler Registration (for Toast Notification Actions)
; ==============================================================================
[HKEY_CURRENT_USER\Software\Classes\localsr]
@="URL:LocalSR Protocol"
"URL Protocol"=""
"AppUserModelID"="LocalSR.Desktop.App"

[HKEY_CURRENT_USER\Software\Classes\localsr\DefaultIcon]
@="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\",0"

[HKEY_CURRENT_USER\Software\Classes\localsr\shell\open\command]
@="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\" --protocol \"%1\""

; ==============================================================================
; Extension OpenWith Registration
; ==============================================================================
[HKEY_CURRENT_USER\Software\Classes\.png\OpenWithProgids]
"LocalSR.Image.1"=""

[HKEY_CURRENT_USER\Software\Classes\.jpg\OpenWithProgids]
"LocalSR.Image.1"=""

[HKEY_CURRENT_USER\Software\Classes\.jpeg\OpenWithProgids]
"LocalSR.Image.1"=""

[HKEY_CURRENT_USER\Software\Classes\.webp\OpenWithProgids]
"LocalSR.Image.1"=""

[HKEY_CURRENT_USER\Software\Classes\.tiff\OpenWithProgids]
"LocalSR.Image.1"=""

[HKEY_CURRENT_USER\Software\Classes\.dng\OpenWithProgids]
"LocalSR.Image.1"=""

[HKEY_CURRENT_USER\Software\Classes\.mp4\OpenWithProgids]
"LocalSR.Video.1"=""

[HKEY_CURRENT_USER\Software\Classes\.mov\OpenWithProgids]
"LocalSR.Video.1"=""

[HKEY_CURRENT_USER\Software\Classes\.m4v\OpenWithProgids]
"LocalSR.Video.1"=""

[HKEY_CURRENT_USER\Software\Classes\.mkv\OpenWithProgids]
"LocalSR.Video.1"=""

[HKEY_CURRENT_USER\Software\Classes\.webm\OpenWithProgids]
"LocalSR.Video.1"=""

[HKEY_CURRENT_USER\Software\Classes\.avi\OpenWithProgids]
"LocalSR.Video.1"=""

; ==============================================================================
; Windows Registered Applications & Capabilities (for Windows Default Apps Settings)
; ==============================================================================
[HKEY_CURRENT_USER\Software\RegisteredApplications]
"LocalSR"="Software\\LocalSR\\Capabilities"

[HKEY_CURRENT_USER\Software\LocalSR\Capabilities]
"ApplicationName"="LocalSR"
"ApplicationDescription"="Private local image and video super-resolution"
"ApplicationIcon"="\"%LOCALAPPDATA%\\Programs\\LocalSR\\LocalSR.exe\",0"

[HKEY_CURRENT_USER\Software\LocalSR\Capabilities\FileAssociations]
".png"="LocalSR.Image.1"
".jpg"="LocalSR.Image.1"
".jpeg"="LocalSR.Image.1"
".webp"="LocalSR.Image.1"
".tiff"="LocalSR.Image.1"
".dng"="LocalSR.Image.1"
".mp4"="LocalSR.Video.1"
".mov"="LocalSR.Video.1"
".m4v"="LocalSR.Video.1"
".mkv"="LocalSR.Video.1"
".webm"="LocalSR.Video.1"
".avi"="LocalSR.Video.1"

[HKEY_CURRENT_USER\Software\LocalSR\Capabilities\URLAssociations]
"localsr"="localsr"
```

---

### 1.4 Packaging & Deployment Strategy

#### 1.4.1 Inno Setup Installer (`packaging/windows/LocalSR.iss`)
```pascal
#define AppName "LocalSR"
#define AppPublisher "LocalSR Contributors"
#define AppURL "https://github.com/HerRei/local-upscale"
#define AppExeName "LocalSR.exe"
#define AppVersion GetEnv("LOCALSR_VERSION")

[Setup]
AppId={{AD28BB74-EBDC-4B94-86EF-C6390D878D92}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\release
OutputBaseFilename=LocalSR-Windows-x86_64-Setup
SetupIconFile=..\icons\LocalSR.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#AppExeName}

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "contextmenu"; Description: "Add Explorer right-click context menu"; GroupDescription: "System Integrations:"
Name: "fileassoc"; Description: "Register file associations (Open With)"; GroupDescription: "System Integrations:"

[Files]
Source: "..\..\dist\LocalSR\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; AppUserModelID: "LocalSR.Desktop.App"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon; AppUserModelID: "LocalSR.Desktop.App"

[Run]
; Auto-register integrations on install
Filename: "{app}\{#AppExeName}"; Parameters: "--install-integrations"; Flags: runhidden
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Cleanly remove integrations before binary deletion
Filename: "{app}\{#AppExeName}"; Parameters: "--uninstall-integrations"; Flags: runhidden
```

#### 1.4.2 WiX Toolset MSI Installer Specification (`packaging/windows/LocalSR.wxs`)
For enterprise deployments requiring Group Policy / SCCM / Intune MSI distribution:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">
  <Package Name="LocalSR"
           Manufacturer="LocalSR Contributors"
           Version="!(bind.FileVersion.LocalSRBinary)"
           UpgradeCode="AD28BB74-EBDC-4B94-86EF-C6390D878D92"
           Scope="perUserOrMachine">
    
    <MajorUpgrade DowngradeErrorMessage="A newer version of LocalSR is already installed." />
    <MediaTemplate EmbedCab="yes" />

    <StandardDirectory Id="ProgramFiles64Folder">
      <Directory Id="INSTALLFOLDER" Name="LocalSR">
        <Component Id="MainExecutable" Guid="5F87B442-9988-4F43-A5B2-10816BC5F932">
          <File Id="LocalSRBinary" Source="..\..\dist\LocalSR\LocalSR.exe" KeyPath="yes">
            <Shortcut Id="ApplicationStartMenuShortcut"
                      Directory="ProgramMenuFolder"
                      Name="LocalSR"
                      Description="Private local image and video super-resolution"
                      WorkingDirectory="INSTALLFOLDER"
                      Icon="LocalSRIcon.exe"
                      IconIndex="0"
                      Advertise="no">
              <!-- Explicit AUMID for Start Menu Shortcut to bind Windows Toast Notifications -->
              <ShortcutProperty Key="System.AppUserModel.ID" Value="LocalSR.Desktop.App" />
            </Shortcut>
          </File>
          <!-- URL Protocol Registration for WinRT Toasts -->
          <RegistryValue Root="HKMU" Key="Software\Classes\localsr" Value="URL:LocalSR Protocol" Type="string" />
          <RegistryValue Root="HKMU" Key="Software\Classes\localsr" Name="URL Protocol" Value="" Type="string" />
          <RegistryValue Root="HKMU" Key="Software\Classes\localsr\shell\open\command" Value="&quot;[#LocalSRBinary]&quot; --protocol &quot;%1&quot;" Type="string" />
        </Component>
      </Directory>
    </StandardDirectory>

    <Icon Id="LocalSRIcon.exe" SourceFile="..\icons\LocalSR.ico" />

    <!-- Custom Actions to Auto-Register and Unregister Desktop Integrations -->
    <CustomAction Id="RegisterIntegrations"
                  FileRef="LocalSRBinary"
                  ExeCommand="--install-integrations"
                  Execute="deferred"
                  Return="ignore" />

    <CustomAction Id="UnregisterIntegrations"
                  FileRef="LocalSRBinary"
                  ExeCommand="--uninstall-integrations"
                  Execute="deferred"
                  Return="ignore" />

    <InstallExecuteSequence>
      <Custom Action="RegisterIntegrations" After="InstallFiles" Condition="NOT Installed" />
      <Custom Action="UnregisterIntegrations" Before="RemoveFiles" Condition="Installed" />
    </InstallExecuteSequence>

    <Feature Id="MainFeature" Title="LocalSR" Level="1">
      <ComponentRef Id="MainExecutable" />
    </Feature>
  </Package>
</Wix>
```

---

## 2. Linux Desktop Integration Architecture (R2)

### 2.1 FreeDesktop Standards Compliance

Linux desktop integration strictly complies with the FreeDesktop.org (XDG) specifications: Desktop Entry Specification (v1.5), Icon Theme Specification, Shared MIME-info Database, and Desktop Notifications Specification.

```
       FreeDesktop Integration Hierarchy
                       │
   ┌───────────────────┼───────────────────┐
   ▼                   ▼                   ▼
XDG Desktop Entry   XDG Icon Theme     MIME Database
localsr.desktop     hicolor/{size}/    localsr.xml
   │                   │                   │
   ▼                   ▼                   ▼
App Launcher        Desktop/Taskbar    File Manager
& Jump Actions      Rendered Icons     Associations
```

#### 2.1.1 FreeDesktop `.desktop` Entry (`packaging/linux/localsr.desktop`)
```ini
[Desktop Entry]
Version=1.5
Type=Application
Name=LocalSR
GenericName=Image and Video Super-Resolution
Comment=Private local image and video super-resolution using state-of-the-art neural networks
Exec=localsr %U
Icon=localsr
Terminal=false
Categories=Graphics;Photography;2DGraphics;RasterGraphics;AudioVideo;Video;
MimeType=image/png;image/jpeg;image/webp;image/tiff;image/x-adobe-dng;video/mp4;video/quicktime;video/x-matroska;video/webm;video/x-msvideo;x-scheme-handler/localsr;
StartupNotify=true
StartupWMClass=localsr
SingleMainWindow=true
Keywords=upscale;super-resolution;ai;enhance;hat;face-restoration;video;
Actions=QuickUpscale;BestUpscale;ChooseRecipe;

[Desktop Action QuickUpscale]
Name=Quick Upscale (Fast Preset)
Exec=localsr --preset quick --auto-start %U

[Desktop Action BestUpscale]
Name=Best Quality Upscale (Best Preset)
Exec=localsr --preset best --auto-start %U

[Desktop Action ChooseRecipe]
Name=Choose Recipe...
Exec=localsr --recipe-dialog %U
```

#### 2.1.2 Shared MIME-Info Specification (`packaging/linux/localsr.xml`)
```xml
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-localsr-project">
    <comment>LocalSR Batch Project</comment>
    <glob pattern="*.lsrproj"/>
    <icon name="localsr"/>
  </mime-type>
</mime-info>
```

#### 2.1.3 Icon Theme Hierarchy Deployment
Icons deploy across standard resolutions to support all DPI scaling configurations:
- `~/.local/share/icons/hicolor/16x16/apps/localsr.png`
- `~/.local/share/icons/hicolor/32x32/apps/localsr.png`
- `~/.local/share/icons/hicolor/48x48/apps/localsr.png`
- `~/.local/share/icons/hicolor/64x64/apps/localsr.png`
- `~/.local/share/icons/hicolor/128x128/apps/localsr.png`
- `~/.local/share/icons/hicolor/256x256/apps/localsr.png`
- `~/.local/share/icons/hicolor/512x512/apps/localsr.png`
- `~/.local/share/icons/hicolor/scalable/apps/localsr.svg`

Post-install cache refresh triggers:
```bash
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor 2>/dev/null || true
update-desktop-database ~/.local/share/applications 2>/dev/null || true
update-mime-database ~/.local/share/mime 2>/dev/null || true
```

---

### 2.2 File Manager Context Actions

Linux distributions feature multiple desktop environments (GNOME, KDE Plasma, Cinnamon, XFCE). LocalSR targets their native file manager extension points.

```
                    [Linux File Manager Right-Click]
                                   │
      ┌────────────────────────────┼────────────────────────────┐
      ▼                            ▼                            ▼
[KDE Dolphin]               [GNOME Nautilus]             [Cinnamon Nemo &
KIO ServiceMenu             Nautilus Scripts &           XFCE Thunar]
localsr.desktop             nautilus-python              Nemo Action & uca.xml
      │                            │                            │
      └────────────────────────────┼────────────────────────────┘
                                   │
                                   ▼
                    [Dynamic Recipe Selector Helper]
               Native Slint Dialog / zenity / kdialog
                                   │
                                   ▼
                        Executes LocalSR binary:
                        --preset / --recipe / --auto-start
```

#### 2.2.1 KDE Plasma / Dolphin ServiceMenus (`~/.local/share/kio/servicemenus/localsr.desktop`)
```ini
[Desktop Entry]
Type=Service
ServiceTypes=KonqPopupMenu/Plugin
MimeType=image/png;image/jpeg;image/webp;image/tiff;image/x-adobe-dng;video/mp4;video/quicktime;video/x-matroska;video/webm;video/x-msvideo;inode/directory;
Actions=ActiveSettings;QuickPreset;BestPreset;CustomRecipe;
X-KDE-Submenu=Upscale with LocalSR
X-KDE-Priority=TopLevel
Icon=localsr

[Desktop Action ActiveSettings]
Name=Upscale with Active Settings
Icon=localsr
Exec=localsr --auto-start %U

[Desktop Action QuickPreset]
Name=Quick Preset (Fast)
Icon=localsr
Exec=localsr --preset quick --auto-start %U

[Desktop Action BestPreset]
Name=Best Quality Preset
Icon=localsr
Exec=localsr --preset best --auto-start %U

[Desktop Action CustomRecipe]
Name=Choose Recipe...
Icon=localsr
Exec=localsr --recipe-dialog %U
```

#### 2.2.2 GNOME Nautilus Python Extension (`~/.local/share/nautilus-python/extensions/localsr_nautilus.py`)
For modern GNOME 43+ Nautilus desktop environments, supporting both file selections and directory background clicks:

```python
"""LocalSR GNOME Nautilus Context Menu Provider."""
import os
import subprocess
from pathlib import Path
from gi.repository import GObject, Nautilus

SUPPORTED_MIMES = {
    "image/png", "image/jpeg", "image/webp", "image/tiff",
    "image/x-adobe-dng", "video/mp4", "video/quicktime",
    "video/x-matroska", "video/webm", "video/x-msvideo",
}

def _extract_safe_path(file_info: Nautilus.FileInfo) -> str | None:
    """Extract local filesystem path safely guarding against None and non-file URIs."""
    try:
        loc = file_info.get_location()
        if loc is None:
            return None
        path = loc.get_path()
        return path if path and os.path.exists(path) else None
    except Exception:
        return None

class LocalSRExtension(GObject.GObject, Nautilus.MenuProvider):
    def get_file_items(self, files: list[Nautilus.FileInfo]) -> list[Nautilus.MenuItem]:
        valid_files = [
            path for f in files
            if (path := _extract_safe_path(f)) and (f.get_mime_type() in SUPPORTED_MIMES or f.is_directory())
        ]
        if not valid_files:
            return []

        top_menu = Nautilus.MenuItem(
            name="LocalSR::UpscaleMenu",
            label="Upscale with LocalSR",
            tip="Upscale media files with LocalSR neural super-resolution",
            icon="localsr"
        )
        submenu = Nautilus.Menu()
        top_menu.set_submenu(submenu)

        item_active = Nautilus.MenuItem(
            name="LocalSR::Active",
            label="Upscale with Active Settings",
            icon="localsr"
        )
        item_active.connect("activate", lambda _: subprocess.Popen(["localsr", "--auto-start"] + valid_files))
        submenu.append_item(item_active)

        item_quick = Nautilus.MenuItem(
            name="LocalSR::Quick",
            label="Quick Preset (Fast)",
            icon="localsr"
        )
        item_quick.connect("activate", lambda _: subprocess.Popen(["localsr", "--preset", "quick", "--auto-start"] + valid_files))
        submenu.append_item(item_quick)

        item_best = Nautilus.MenuItem(
            name="LocalSR::Best",
            label="Best Quality Preset",
            icon="localsr"
        )
        item_best.connect("activate", lambda _: subprocess.Popen(["localsr", "--preset", "best", "--auto-start"] + valid_files))
        submenu.append_item(item_best)

        item_choose = Nautilus.MenuItem(
            name="LocalSR::Choose",
            label="Choose Recipe...",
            icon="localsr"
        )
        item_choose.connect("activate", lambda _: subprocess.Popen(["localsr", "--recipe-dialog"] + valid_files))
        submenu.append_item(item_choose)

        return [top_menu]

    def get_background_items(self, current_folder: Nautilus.FileInfo) -> list[Nautilus.MenuItem]:
        folder_path = _extract_safe_path(current_folder)
        if not folder_path:
            return []

        item_folder = Nautilus.MenuItem(
            name="LocalSR::UpscaleCurrentFolder",
            label="Upscale Current Folder with LocalSR",
            tip="Batch upscale media files in this folder",
            icon="localsr"
        )
        item_folder.connect("activate", lambda _: subprocess.Popen(["localsr", "--auto-start", folder_path]))
        return [item_folder]
```

#### 2.2.3 GNOME Nautilus Script Integration (`~/.local/share/nautilus/scripts/Upscale with LocalSR`)
Fallback script for systems without `nautilus-python`:

```bash
#!/usr/bin/env bash
set -euo pipefail

FILES=()
while IFS= read -r line; do
    [ -n "$line" ] && FILES+=("$line")
done <<< "$NAUTILUS_SCRIPT_SELECTED_FILE_PATHS"

if [ ${#FILES[@]} -eq 0 ]; then
    exit 0
fi

# Query custom recipes from settings.json
SETTINGS_PATH="${XDG_DATA_HOME:-$HOME/.local/share}/LocalSR/settings.json"
RECIPES=("Active App Settings" "Quick Preset (Fast)" "Best Quality Preset")

if [ -f "$SETTINGS_PATH" ]; then
    CUSTOM_NAMES=$(python3 -c "
import json
try:
    with open('$SETTINGS_PATH', 'r', encoding='utf-8') as f:
        data = json.load(f)
    for r in data.get('custom_recipes', []):
        name = str(r.get('name', '')).strip()
        if name:
            print(name)
except Exception:
    pass
" 2>/dev/null || true)
    while IFS= read -r line; do
        [ -n "$line" ] && RECIPES+=("$line")
    done <<< "$CUSTOM_NAMES"
fi

CHOICE=""
if command -v zenity >/dev/null 2>&1; then
    ZENITY_ARGS=(--list --radiolist --title="LocalSR Recipe Selection" --text="Choose recipe for upscaling:" --column="" --column="Preset / Recipe")
    for i in "${!RECIPES[@]}"; do
        if [ "$i" -eq 0 ]; then
            ZENITY_ARGS+=(TRUE "${RECIPES[$i]}")
        else
            ZENITY_ARGS+=(FALSE "${RECIPES[$i]}")
        fi
    done
    CHOICE=$(zenity "${ZENITY_ARGS[@]}" 2>/dev/null || echo "")
elif command -v kdialog >/dev/null 2>&1; then
    KDIALOG_ARGS=(--radiolist "Choose recipe for upscaling:")
    for i in "${!RECIPES[@]}"; do
        STATUS="off"
        [ "$i" -eq 0 ] && STATUS="on"
        KDIALOG_ARGS+=("${RECIPES[$i]}" "${RECIPES[$i]}" "$STATUS")
    done
    CHOICE=$(kdialog "${KDIALOG_ARGS[@]}" 2>/dev/null || echo "")
fi

# Fallback: if dialog cancelled or neither zenity/kdialog present, launch UI directly
if [ -z "$CHOICE" ]; then
    localsr "${FILES[@]}" &
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
ARGS+=("${FILES[@]}")

localsr "${ARGS[@]}" &
```

#### 2.2.4 Cinnamon Nemo Action (`~/.local/share/nemo/actions/localsr.nemo_action`)
```ini
[Nemo Action]
Name=Upscale with LocalSR
Comment=Upscale media files using LocalSR
Exec=localsr --auto-start %F
Icon-Name=localsr
Selection=notnone
Extensions=png;jpg;jpeg;webp;tiff;dng;mp4;mov;m4v;mkv;webm;avi;dir;
Quote=double
Dependencies=localsr;
```

#### 2.2.5 XFCE Thunar Custom Actions (`~/.config/Thunar/uca.xml` snippet)
```xml
<action>
  <icon>localsr</icon>
  <name>Upscale with LocalSR</name>
  <unique-id>localsr-upscale-action</unique-id>
  <command>localsr --auto-start %F</command>
  <description>Upscale selected image and video files</description>
  <patterns>*.png;*.jpg;*.jpeg;*.webp;*.tiff;*.dng;*.mp4;*.mov;*.m4v;*.mkv;*.webm;*.avi</patterns>
  <image-files/>
  <video-files/>
  <directories/>
</action>
```

---

### 2.3 Desktop Notifications (D-Bus Protocol)

Linux desktop notifications communicate over the session D-Bus bus with the `org.freedesktop.Notifications` service.

```
       [LocalSR Notification Request]
                     │
     ┌───────────────┴───────────────┐
     ▼                               ▼
[Direct D-Bus Call]          [CLI notify-send]
org.freedesktop.Notifications Fallback Subprocess
Method: Notify(...)          Formatted CLI Args
     │                               │
     └───────────────┬───────────────┘
                     │
                     ▼
         Native Desktop Notification
       (GNOME Shell / KDE Plasma / Sway)
                     │
                     ▼
    [D-Bus ActionInvoked Signal Listener]
  Dispatches "open" (xdg-open) and "reveal" (file manager)
```

#### 2.3.1 D-Bus Notification Protocol Implementation
Direct communication via `gdbus` or `notify-send` with notification ID parsing:

```python
import re
import shutil
import subprocess
from pathlib import Path


class LinuxDBusNotifier:
    """Delivers FreeDesktop-compliant notifications over D-Bus with action buttons."""

    @staticmethod
    def send(
        title: str,
        message: str,
        output_path: str | None = None,
        preview_image: str | None = None,
        sound: bool = True,
    ) -> tuple[bool, int | None]:
        """Send notification over session D-Bus. Returns (success, notification_id)."""
        gdbus = shutil.which("gdbus")
        if gdbus:
            actions = (
                "['open', 'Open Result', 'reveal', 'Show in Folder']"
                if output_path
                else "[]"
            )
            hints = "{'urgency': <byte 1>}"
            if sound:
                hints = "{'urgency': <byte 1>, 'sound-name': <'message-new-instant'>}"
            if preview_image and Path(preview_image).is_file():
                hints = f"{{'urgency': <byte 1>, 'image-path': <'{preview_image}'>}}"

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
                "LocalSR",  # app_name
                "0",  # replaces_id
                "localsr",  # app_icon
                title,  # summary
                message,  # body
                actions,  # actions array
                hints,  # hints dict
                "-1",  # expire_timeout (default)
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=False)
                if res.returncode == 0:
                    match = re.search(r"\(uint32\s+(\d+),?\)", res.stdout)
                    notif_id = int(match.group(1)) if match else None
                    return True, notif_id
            except Exception:
                pass

        # Fallback to notify-send
        notify_send = shutil.which("notify-send")
        if notify_send:
            cmd = [notify_send, "-a", "LocalSR", "-i", "localsr", "-u", "normal", title, message]
            try:
                res = subprocess.run(cmd, capture_output=True, timeout=3, check=False)
                return res.returncode == 0, None
            except Exception:
                return False, None
        return False, None
```

#### 2.3.2 D-Bus ActionInvoked Signal Listener Loop
When notifications include interactive action buttons ("open", "reveal"), the notification daemon emits an `ActionInvoked` signal on `org.freedesktop.Notifications`. A watchdog timer automatically frees background listener resources after 60 seconds or upon notification dismissal:

```python
"""D-Bus Signal Listener Architecture for Linux Notification Actions."""
import subprocess
import threading
from pathlib import Path


def start_linux_notification_action_listener(
    output_path: str,
    notification_id: int | None,
    timeout_sec: float = 60.0,
) -> None:
    """Listen for ActionInvoked D-Bus signal in background thread to handle button clicks."""
    if not notification_id or not output_path or not shutil.which("gdbus"):
        return

    def _listener_worker():
        proc = None
        try:
            # Monitor session bus for ActionInvoked signal on org.freedesktop.Notifications
            proc = subprocess.Popen(
                [
                    "gdbus", "monitor", "--session",
                    "--dest", "org.freedesktop.Notifications",
                    "--object-path", "/org/freedesktop/Notifications"
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True
            )

            # Watchdog timer to terminate monitor after timeout_sec
            timer = threading.Timer(timeout_sec, lambda: proc.kill() if proc else None)
            timer.daemon = True
            timer.start()

            for line in proc.stdout:
                if "ActionInvoked" in line and str(notification_id) in line:
                    if "'open'" in line:
                        subprocess.Popen(["xdg-open", output_path])
                        break
                    elif "'reveal'" in line:
                        target_dir = str(Path(output_path).parent if Path(output_path).is_file() else output_path)
                        subprocess.Popen(["xdg-open", target_dir])
                        break
                elif "NotificationClosed" in line and str(notification_id) in line:
                    break

            timer.cancel()
        except Exception:
            pass
        finally:
            if proc:
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except Exception:
                    pass

    thread = threading.Thread(target=_listener_worker, daemon=True, name="LinuxDBusActionListener")
    thread.start()
```

---

### 2.4 Distribution Packaging & Sandboxing

#### 2.4.1 AppImage Distribution Architecture
AppImage bundles dependencies into an isolated runtime while preserving GPU hardware access:
- **`AppRun`:** Sets up `LD_LIBRARY_PATH` and fontconfig caches without replacing host Vulkan/GL drivers.
- **Hardware Pass-Through:** Preserves host DRI (`/dev/dri/*`) and NVIDIA device nodes (`/dev/nvidia*`).
- **Desktop Integration:** Integrates with `appimaged` and `AppImageLauncher` for automatic `.desktop` file registration.

```bash
#!/bin/sh
# packaging/linux/AppRun - Robust AppImage Entrypoint with Hardware Pass-Through
set -eu
APPDIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

export PATH="$APPDIR/usr/bin:$PATH"
export LD_LIBRARY_PATH="$APPDIR/usr/lib:$APPDIR/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
export XDG_DATA_DIRS="$APPDIR/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"

# Ensure host GPU drivers (Mesa / NVIDIA) take precedence
export LIBGL_DRIVERS_PATH="/usr/lib/x86_64-linux-gnu/dri:/usr/lib64/dri:${LIBGL_DRIVERS_PATH:-}"

exec "$APPDIR/usr/bin/LocalSR" "$@"
```

#### 2.4.2 Flatpak Packaging Specification (`packaging/flatpak/org.localsr.LocalSR.yml`)
```yaml
app-id: org.localsr.LocalSR
runtime: org.freedesktop.Platform
runtime-version: '23.08'
sdk: org.freedesktop.Sdk
command: LocalSR

finish-args:
  # X11 and Wayland display sockets
  - --socket=wayland
  - --socket=fallback-x11
  # Hardware GPU acceleration (CUDA, Vulkan, VAAPI)
  - --device=all
  # Access to host filesystems for processing user images and videos
  - --filesystem=host
  # IPC and Notifications
  - --socket=session-bus
  - --talk-name=org.freedesktop.Notifications
  - --talk-name=org.freedesktop.portal.Notification
  - --talk-name=org.freedesktop.portal.OpenURI
  - --talk-name=org.freedesktop.portal.FileChooser

modules:
  - name: localsr
    buildsystem: simple
    build-commands:
      - install -D -m 755 LocalSR /app/bin/LocalSR
      - install -D -m 755 LocalSRWorker /app/bin/LocalSRWorker
      - install -D -m 644 packaging/linux/localsr.desktop /app/share/applications/org.localsr.LocalSR.desktop
      - install -D -m 644 packaging/icons/LocalSR.png /app/share/icons/hicolor/256x256/apps/org.localsr.LocalSR.png
```

#### 2.4.3 Standalone Tarball Distribution (`LocalSR-Linux-x86_64.tar.gz`)
Includes automated install/uninstall scripts (`install.sh` / `uninstall.sh`) that copy `.desktop` entries to `~/.local/share/applications/`, icons to `~/.local/share/icons/hicolor/`, and Dolphin ServiceMenus to `~/.local/share/kio/servicemenus/`.

---

## 3. Zero Cross-Platform Interference Architecture (R3)

To ensure maximum codebase stability and prevent platform-specific regressions, LocalSR establishes strict boundaries separating OS-dependent features from core inference and UI logic.

```
                      [Application Core & UI]
                     (Slint App / Worker Bridge)
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │ get_platform_service()│  (Abstract Factory)
                     └───────────┬───────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
   sys.platform ==         sys.platform ==         sys.platform ==
      "darwin"                "win32"                 "linux"
         │                       │                       │
         ▼                       ▼                       ▼
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│  MacOSPlatform   │    │  WindowsPlatform │    │  LinuxPlatform   │
│     Service      │    │     Service      │    │     Service      │
└────────┬─────────┘    └────────┬─────────┘    └────────┬─────────┘
         │                       │                       │
   (osascript, plist)    (winreg, powershell)   (dbus, kdialog, xdg)
```

### 3.1 Dynamic Platform Dispatch & Strict Encapsulation

1. **Strict Interface Encapsulation:** All platform services inherit from `PlatformService` in `src/localsr/platform/base.py`.
2. **Lazy Dynamic Imports:** Foreign OS modules (`winreg`, `ctypes.windll`, `msvcrt`, `osascript`, `pydbus`, `gi`) are NEVER imported at top-level. They are imported exclusively inside platform-specific modules guarded by `sys.platform`.
3. **No Foreign Subprocess Spawns:** Platform modules must never execute foreign binaries (e.g., `osascript` on Windows or `powershell.exe` on Linux).

```python
# src/localsr/platform/__init__.py
import sys

from .base import PlatformService

_PLATFORM_SERVICE: PlatformService | None = None


def get_platform_service() -> PlatformService:
    """Obtain singleton platform integration service for the host operating system."""
    global _PLATFORM_SERVICE
    if _PLATFORM_SERVICE is None:
        if sys.platform == "darwin":
            from .macos import MacOSPlatformService

            _PLATFORM_SERVICE = MacOSPlatformService()
        elif sys.platform == "win32":
            from .windows import WindowsPlatformService

            _PLATFORM_SERVICE = WindowsPlatformService()
        else:
            from .linux import LinuxPlatformService

            _PLATFORM_SERVICE = LinuxPlatformService()
    return _PLATFORM_SERVICE
```

---

### 3.2 Graceful Fallbacks & Headless Environments

LocalSR runs in varied environments: headless cloud servers, CI runners without display servers, and locked-down corporate Windows environments.

#### Resilience Matrix & Invariants
1. **Headless Linux Detection:**
   - When neither `DISPLAY` nor `WAYLAND_DISPLAY` is present, GUI dialogs return empty lists immediately without spawning processes.
   - D-Bus notifications degrade gracefully to standard logger output (`logger.info`).
2. **Enterprise Windows Restrictions:**
   - If PowerShell `ExecutionPolicy` is set to `Restricted` or `powershell.exe` is blocked by AppLocker, dialogs degrade to native Slint UI and notifications log without crashing.
   - If registry write access to `HKLM` is denied, registrations fall back seamlessly to `HKCU`.
3. **100% Crash-Free Guarantee:**
   - Every method in `PlatformService` returns a boolean (`True` on success, `False` on failure) and catches all exceptions internally. Platform integration failures will never crash inference or the primary application UI.

---

### 3.3 Unified CLI & Protocol Parity

All platforms share 100% parity across CLI arguments and IPC messaging:

```bash
# Standard CLI Invocation across macOS, Windows, and Linux:
LocalSR [OPTIONS] [FILES...]

Options:
  --preset [quick|best]      Apply predefined quality/speed preset
  --recipe <NAME>            Apply saved custom recipe by name
  --auto-start               Begin queue processing immediately on launch
  --recipe-dialog            Open native recipe selection dialog for files
  --protocol <URL>           Dispatch custom URL protocol (localsr:action=...)
  --install-integrations     Register OS context menus, shortcuts, and associations
  --uninstall-integrations   Remove all OS integrations cleanly
  --worker                   Launch isolated PyTorch inference worker subprocess
  --smoke-test               Non-interactive component validation for CI packaging
```

---

## 4. Comprehensive Cross-Platform Matrix

| Architectural Dimension | macOS (Darwin) | Windows (Win32 / WinRT) | Linux (X11 / Wayland) |
| :--- | :--- | :--- | :--- |
| **Context Menu Engine** | Finder Quick Actions (`.workflow`) | Windows 10 Registry Verbs & Windows 11 Sparse MSIX `IExplorerCommand` | KDE ServiceMenus (`.desktop`), Nautilus Python/Scripts, Nemo Actions, Thunar |
| **Context Menu Recipe Dialog** | AppleScript `choose from list` / Swift Dialog | Native Slint Dialog / PowerShell WinForms (`recipe_picker.ps1`) | Native Slint Dialog / `zenity --radiolist` / `kdialog --radiolist` |
| **Native Notification Backend** | AppleScript `display notification` & `NSUserNotification` | WinRT Toast Notifications (`Windows.UI.Notifications`) | D-Bus (`org.freedesktop.Notifications`) & `notify-send` |
| **Notification Action Buttons** | macOS Notification Center Actions | WinRT XML Protocol Buttons (`localsr:action=...`) | D-Bus Action Pairs (`open`, `reveal`) & `ActionInvoked` Listener |
| **Process Identity** | Info.plist `CFBundleIdentifier` (`com.localsr.app`) | Explicit AUMID (`LocalSR.Desktop.App`) via `shell32.dll` | `StartupWMClass=localsr` in `.desktop` |
| **File Type Associations** | Info.plist `CFBundleDocumentTypes` & `UTExportedTypeDeclarations` | Registry ProgID (`LocalSR.Image.1`, `LocalSR.Video.1`) under `HKCU` | XDG Shared MIME-info (`localsr.xml`) & `update-mime-database` |
| **Directory Context Actions** | Finder Workflow on Folders (`public.folder`) | `Directory\shell\LocalSR` & `Directory\Background\shell` | `inode/directory` in ServiceMenus & Nautilus Python |
| **Custom URL Protocol** | `CFBundleURLTypes` (`localsr://`) | Registry `HKCU\Software\Classes\localsr` (`URL Protocol`) | XDG MIME `x-scheme-handler/localsr;` in `.desktop` |
| **Config & Settings Directory** | `~/Library/Application Support/LocalSR/settings.json` | `%LOCALAPPDATA%\LocalSR\settings.json` | `${XDG_DATA_HOME:-~/.local/share}/LocalSR/settings.json` |
| **Packaging & Installer** | Signed & Notarized `.dmg` / PyInstaller `.app` | Inno Setup Standalone Installer (`.exe`) / WiX MSI (`.msi`) | Portable `.AppImage`, Flatpak (`.yml`), Standalone `.tar.gz` |
| **Hardware GPU Acceleration** | Apple Silicon Metal Performance Shaders (MPS) | NVIDIA CUDA / DirectML / TensorRT / CPU | NVIDIA CUDA / ROCm / Vulkan / CPU |
| **Headless / CI Mode** | Headless CI Runner (Non-interactive `-smoke-test`) | Windows Server CI Runner (`package-ready` stage) | `xvfb-run` Virtual Framebuffer for Slint Winit backend |
| **Failure Mode Handling** | Service menu cache refresh via `/System/Library/CoreServices/pbs -update` | Non-elevated HKCU fallback, `-ExecutionPolicy Bypass`, Native Slint Fallback | Graceful fallback from D-Bus to `notify-send` to stdout logger |

---

## 5. Implementation Roadmap & Verification Strategy

### Phase 1: Windows Platform Service Implementation
- Implement full `WindowsPlatformService` in `src/localsr/platform/windows.py` with registry installation, URL protocol handler, and WinRT toast generation.
- Add `packaging/windows/recipe_picker.ps1` helper for context menu recipe selection.
- Update `packaging/windows/LocalSR.iss` and `packaging/windows/LocalSR.wxs` with context menu, protocol, and association registration tasks.

### Phase 2: Linux Platform Service Implementation
- Implement full `LinuxPlatformService` in `src/localsr/platform/linux.py` supporting D-Bus notifications, ActionInvoked listener, KDE ServiceMenus, Nautilus Python/scripts, Nemo, and Thunar.
- Deploy XDG `.desktop` file, `localsr.xml` MIME definition, and icon theme directory structure in `packaging/linux/`.
- Create Flatpak manifest `packaging/flatpak/org.localsr.LocalSR.yml` and AppImage packaging script.

### Phase 3: Cross-Platform Automated Verification Suite
- Expand `tests/test_platform_integration.py` with mock-based unit tests for Windows registry generation, PowerShell toast escaping, URL protocol routing, and Linux D-Bus/ServiceMenu payload formatting.
- Validate zero foreign imports on each operating system using static AST analysis in CI:

```python
# CI Foreign Import Verification Checker
import ast
import sys
from pathlib import Path

FORBIDDEN_IMPORTS = {
    "win32": ["osascript", "AppKit", "Foundation", "pydbus", "gi"],
    "linux": ["winreg", "msvcrt", "osascript", "AppKit", "Foundation"],
    "darwin": ["winreg", "msvcrt", "pydbus", "gi"],
}

def verify_no_foreign_imports(platform_name: str, root_dir: Path):
    forbidden = FORBIDDEN_IMPORTS.get(platform_name, [])
    for py_file in (root_dir / "src" / "localsr").rglob("*.py"):
        if "platform" in py_file.parts and not py_file.name.startswith("base"):
            continue # Platform-specific files are isolated
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in forbidden, f"Forbidden import {alias.name} in {py_file}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module not in forbidden, f"Forbidden import {node.module} in {py_file}"
```
