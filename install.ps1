# ==============================================================================
# LocalSR Smart Universal Installer (Windows 10 & 11)
# Automatically probes GPU hardware (NVIDIA CUDA, Intel Arc/Iris, AMD Radeon, DirectML, CPU)
# and installs the optimal standalone binary from GitHub Releases.
# ==============================================================================

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = "HerRei/local-upscale"
$InstallDir = "$env:LOCALAPPDATA\LocalSR"
$DesktopShortcut = "$env:USERPROFILE\Desktop\LocalSR.lnk"
$StartMenuDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\LocalSR"

Write-Host "  _                     _  ____  ____  " -ForegroundColor Cyan
Write-Host " | |    ___   ___ __ _| |/ ___||  _ \ " -ForegroundColor Cyan
Write-Host " | |   / _ \ / __/ _\` | |\___ \| |_) |" -ForegroundColor Cyan
Write-Host " | |__| (_) | (_| (_| | | ___) |  _ < " -ForegroundColor Cyan
Write-Host " |_____\___/ \___\__,_|_||____/|_| \_\" -ForegroundColor Cyan
Write-Host ""
Write-Host "LocalSR Smart Hardware Prober & Installer for Windows" -ForegroundColor White
Write-Host ""

# ------------------------------------------------------------------------------
# 1. Probe Hardware & Detect GPU Flavor
# ------------------------------------------------------------------------------
Write-Host "🔍 Probing system hardware..." -ForegroundColor Yellow

$GpuControllers = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue
$GpuNames = ($GpuControllers | Select-Object -ExpandProperty Name) -join ", "
$Flavor = "Windows-CPU"

# Detect Architecture
$Arch = $env:PROCESSOR_ARCHITECTURE
if ($Arch -eq "ARM64") {
    $ArchSuffix = "arm64"
} else {
    $ArchSuffix = "x86_64"
}

if ($GpuNames -match "NVIDIA") {
    Write-Host "   Detected GPU: $GpuNames (NVIDIA CUDA 12.x Acceleration)" -ForegroundColor Green
    $Flavor = "Windows-CUDA-$ArchSuffix"
} elseif ($GpuNames -match "Intel.*(Arc|Iris|Ultra|Xe|Graphics)") {
    Write-Host "   Detected GPU: $GpuNames (Intel DirectML / XPU Acceleration)" -ForegroundColor Green
    $Flavor = "Windows-DirectML-$ArchSuffix"
} elseif ($GpuNames -match "AMD|Radeon") {
    Write-Host "   Detected GPU: $GpuNames (AMD DirectML Acceleration)" -ForegroundColor Green
    $Flavor = "Windows-DirectML-$ArchSuffix"
} elseif ($GpuNames -match "Snapdragon|Qualcomm|Adreno") {
    Write-Host "   Detected GPU: $GpuNames (Snapdragon DirectML / QNN NPU)" -ForegroundColor Green
    $Flavor = "Windows-DirectML-$ArchSuffix"
} else {
    Write-Host "   Hardware Acceleration: Universal CPU (Multi-threaded Intel MKL)" -ForegroundColor DarkYellow
    $Flavor = "Windows-CPU-$ArchSuffix"
}

Write-Host "   Selected Backend Flavor: $Flavor" -ForegroundColor Cyan
Write-Host ""

# ------------------------------------------------------------------------------
# 2. Fetch Latest Matching Release Asset from GitHub
# ------------------------------------------------------------------------------
Write-Host "📡 Fetching latest release asset for $Flavor from GitHub..." -ForegroundColor Yellow

$ReleasesUrl = "https://api.github.com/repos/$Repo/releases"
$Releases = Invoke-RestMethod -Uri $ReleasesUrl -Headers @{"User-Agent"="LocalSR-Installer"}

$DownloadAsset = $null
$SelectedRelease = $null
foreach ($rel in $Releases) {
    foreach ($asset in $rel.assets) {
        if ($asset.name -like "*.zip" -and $asset.name -match [regex]::Escape($Flavor)) {
            $DownloadAsset = $asset
            $SelectedRelease = $rel
            break
        }
    }
    if ($DownloadAsset) { break }
}

if (-not $DownloadAsset) {
    Write-Host "❌ Error: Could not resolve a release package for $Flavor." -ForegroundColor Red
    Write-Host "Please check: https://github.com/$Repo/releases"
    exit 1
}

$DownloadUrl = $DownloadAsset.browser_download_url
$FileName = Split-Path $DownloadUrl -Leaf
$TempZip = "$env:TEMP\$FileName"
$ChecksumName = "$FileName.sha256"
$TempChecksum = "$env:TEMP\$ChecksumName"
$ChecksumAsset = $SelectedRelease.assets |
    Where-Object { $_.name -eq $ChecksumName } |
    Select-Object -First 1

if (-not $ChecksumAsset) {
    throw "Release checksum asset is missing: $ChecksumName"
}

Write-Host "⬇️  Downloading $FileName..." -ForegroundColor Cyan
Invoke-WebRequest -Uri $DownloadUrl -OutFile $TempZip
Invoke-WebRequest -Uri $ChecksumAsset.browser_download_url -OutFile $TempChecksum

Write-Host "🔐 Verifying SHA-256 checksum..." -ForegroundColor Cyan
$ChecksumLine = (Get-Content -LiteralPath $TempChecksum -Raw).Trim()
if ($ChecksumLine -notmatch '^([0-9A-Fa-f]{64})\s+\*?(.+)$') {
    throw "Release checksum file has an invalid format: $ChecksumName"
}
$ExpectedHash = $Matches[1]
$ExpectedName = Split-Path $Matches[2].Trim() -Leaf
if ($ExpectedName -ne $FileName) {
    throw "Release checksum names $ExpectedName instead of $FileName"
}
$ActualHash = (Get-FileHash -LiteralPath $TempZip -Algorithm SHA256).Hash
if (-not $ActualHash.Equals($ExpectedHash, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Release checksum verification failed; refusing to install $FileName"
}

# ------------------------------------------------------------------------------
# 3. Extract & Provision Application
# ------------------------------------------------------------------------------
Write-Host "📦 Installing LocalSR to $InstallDir..." -ForegroundColor Yellow

if (Test-Path $InstallDir) {
    Remove-Item -Path $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

Expand-Archive -Path $TempZip -DestinationPath $InstallDir -Force

$ExePath = Get-ChildItem -Path $InstallDir -Filter "LocalSR.exe" -Recurse | Select-Object -ExpandProperty FullName -First 1

if (-not $ExePath) {
    Write-Host "❌ Error: LocalSR.exe was not found inside the extracted package." -ForegroundColor Red
    exit 1
}

# ------------------------------------------------------------------------------
# 4. Create Desktop & Start Menu Shortcuts
# ------------------------------------------------------------------------------
$WshShell = New-Object -ComObject WScript.Shell

# Desktop Shortcut
$Shortcut = $WshShell.CreateShortcut($DesktopShortcut)
$Shortcut.TargetPath = $ExePath
$Shortcut.WorkingDirectory = (Split-Path $ExePath)
$Shortcut.Description = "LocalSR - High Performance Super-Resolution"
$Shortcut.Save()

# Start Menu Shortcut
New-Item -ItemType Directory -Path $StartMenuDir -Force | Out-Null
$StartMenuShortcut = "$StartMenuDir\LocalSR.lnk"
$Shortcut = $WshShell.CreateShortcut($StartMenuShortcut)
$Shortcut.TargetPath = $ExePath
$Shortcut.WorkingDirectory = (Split-Path $ExePath)
$Shortcut.Description = "LocalSR - High Performance Super-Resolution"
$Shortcut.Save()

# Cleanup
Remove-Item -Path $TempZip -Force -ErrorAction SilentlyContinue
Remove-Item -Path $TempChecksum -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "🎉 Installation complete!" -ForegroundColor Green
Write-Host "   Application: $ExePath" -ForegroundColor Cyan
Write-Host "   Shortcuts created on Desktop and in Start Menu." -ForegroundColor White
Write-Host ""
Write-Host "Launching LocalSR..." -ForegroundColor Green
Start-Process -FilePath $ExePath
