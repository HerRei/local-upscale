# LocalSR verified installer for Windows 10 and 11.
[CmdletBinding()]
param(
    [string]$Tag,
    [ValidateSet(
        "Windows-CPU-x86_64",
        "Windows-DirectML-x86_64",
        "Windows-CUDA-x86_64"
    )]
    [string]$Flavor,
    [string]$InstallDir = "$env:LOCALAPPDATA\LocalSR",
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Repo = "HerRei/local-upscale"
$DefaultReleaseTag = "@LOCALSR_RELEASE_TAG@"
$TempDir = Join-Path $env:TEMP ("localsr-install-" + [guid]::NewGuid().ToString("N"))
$BackupDir = $null
$InstallCommitted = $false

function Test-SafeName {
    param([Parameter(Mandatory)][string]$Name)
    return $Name -match '^[A-Za-z0-9][A-Za-z0-9._+-]*$'
}

function Test-GitHubCli {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        return $false
    }
    & gh auth status *> $null
    return $LASTEXITCODE -eq 0
}

function Get-ReleaseAsset {
    param([Parameter(Mandatory)][string]$Name)
    if (-not (Test-SafeName $Name)) {
        throw "Unsafe release asset name: $Name"
    }
    $Destination = Join-Path $TempDir $Name
    if (Test-GitHubCli) {
        & gh release download $Tag --repo $Repo --pattern $Name --dir $TempDir --clobber
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Destination -PathType Leaf)) {
            throw "GitHub did not provide $Name"
        }
        return $Destination
    }

    $Headers = @{"User-Agent" = "LocalSR-Installer"}
    $Uri = "https://github.com/$Repo/releases/download/$Tag/$Name"
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Uri -Headers $Headers -OutFile $Destination
    }
    catch {
        throw "Could not download $Name. For a private release, install GitHub CLI and run 'gh auth login'. $($_.Exception.Message)"
    }
    return $Destination
}

if (-not $Tag -and $DefaultReleaseTag.StartsWith("@")) {
    if (-not (Test-GitHubCli)) {
        throw "This source-tree installer needs -Tag TAG, or an authenticated GitHub CLI."
    }
    $ReleaseList = & gh release list --repo $Repo --limit 20 --json tagName,isDraft,publishedAt
    if ($LASTEXITCODE -ne 0) {
        throw "Could not list LocalSR releases."
    }
    $Tag = ($ReleaseList | ConvertFrom-Json |
        Where-Object { -not $_.isDraft } |
        Sort-Object { [datetime]$_.publishedAt } -Descending |
        Select-Object -First 1).tagName
}
elseif (-not $Tag) {
    $Tag = $DefaultReleaseTag
}
if (-not (Test-SafeName $Tag)) {
    throw "Unsafe release tag: $Tag"
}

$Architecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
if ($Architecture -ne "X64") {
    throw "This alpha publishes Windows x86_64 bundles only; detected $Architecture."
}

if (-not $Flavor) {
    $GpuNames = (Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Name) -join ", "
    if ($GpuNames -match "NVIDIA") {
        $Flavor = "Windows-CUDA-x86_64"
    }
    elseif ($GpuNames -match "Intel.*(Arc|Iris|Ultra|Xe|Graphics)|AMD|Radeon|Snapdragon|Qualcomm|Adreno") {
        $Flavor = "Windows-DirectML-x86_64"
    }
    else {
        $Flavor = "Windows-CPU-x86_64"
    }
}

$BundleName = switch ($Flavor) {
    "Windows-CPU-x86_64" { "LocalSR-Windows-CPU-x86_64.zip" }
    "Windows-DirectML-x86_64" { "LocalSR-Windows-DirectML-x86_64.zip" }
    "Windows-CUDA-x86_64" { "LocalSR-Windows-CUDA-x86_64.zip" }
}

Write-Host "LocalSR verified installer" -ForegroundColor Cyan
Write-Host "Release: $Tag"
Write-Host "Selected backend: $Flavor"
New-Item -ItemType Directory -Path $TempDir -Force | Out-Null

try {
    $IndexPath = Get-ReleaseAsset "release-index.json"
    $SumsPath = Get-ReleaseAsset "SHA256SUMS"
    $Index = Get-Content -LiteralPath $IndexPath -Raw | ConvertFrom-Json
    if ($Index.schema_version -ne 2) {
        throw "Unsupported release-index schema: $($Index.schema_version)"
    }
    if ($Index.release_tag -ne $Tag -or $Index.checksum_file -ne "SHA256SUMS") {
        throw "Release index header does not match the requested release."
    }
    $Bundles = @($Index.bundles | Where-Object { $_.filename -eq $BundleName })
    if ($Bundles.Count -ne 1) {
        throw "Expected one bundle named $BundleName; found $($Bundles.Count)"
    }
    $Bundle = $Bundles[0]
    if ($Bundle.sha256 -notmatch '^[0-9A-Fa-f]{64}$') {
        throw "Release index contains an invalid bundle digest."
    }

    $ChecksumEntries = @{}
    foreach ($Line in Get-Content -LiteralPath $SumsPath) {
        if ($Line -notmatch '^([0-9A-Fa-f]{64})  ([A-Za-z0-9][A-Za-z0-9._+-]*)$') {
            throw "SHA256SUMS contains a nonstandard or unsafe line."
        }
        if ($ChecksumEntries.ContainsKey($Matches[2])) {
            throw "SHA256SUMS contains a duplicate entry for $($Matches[2])"
        }
        $ChecksumEntries[$Matches[2]] = $Matches[1]
    }

    $StoredAssets = @($Bundle.assets)
    if ($StoredAssets.Count -eq 0) {
        throw "Release index contains no assets for $BundleName"
    }
    foreach ($AssetName in $StoredAssets) {
        if (-not (Test-SafeName $AssetName)) {
            throw "Unsafe bundle asset name: $AssetName"
        }
        $AssetPath = Get-ReleaseAsset $AssetName
        if (-not $ChecksumEntries.ContainsKey($AssetName)) {
            throw "SHA256SUMS does not contain $AssetName"
        }
        $ActualPartHash = (Get-FileHash -LiteralPath $AssetPath -Algorithm SHA256).Hash
        if (-not $ActualPartHash.Equals($ChecksumEntries[$AssetName], [StringComparison]::OrdinalIgnoreCase)) {
            throw "Checksum verification failed for $AssetName"
        }
    }

    $ArchivePath = Join-Path $TempDir $BundleName
    if ($StoredAssets.Count -eq 1 -and $StoredAssets[0] -eq $BundleName) {
        $ArchivePath = Join-Path $TempDir $StoredAssets[0]
    }
    else {
        $OutputStream = [IO.File]::Open($ArchivePath, [IO.FileMode]::Create, [IO.FileAccess]::Write)
        try {
            foreach ($AssetName in $StoredAssets) {
                $InputStream = [IO.File]::OpenRead((Join-Path $TempDir $AssetName))
                try {
                    $InputStream.CopyTo($OutputStream)
                }
                finally {
                    $InputStream.Dispose()
                }
            }
        }
        finally {
            $OutputStream.Dispose()
        }
    }

    $ActualBundleHash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash
    if (-not $ActualBundleHash.Equals($Bundle.sha256, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Assembled bundle checksum verification failed."
    }
    Write-Host "Verified $BundleName." -ForegroundColor Green

    $UnpackedDir = Join-Path $TempDir "unpacked"
    Expand-Archive -LiteralPath $ArchivePath -DestinationPath $UnpackedDir -Force
    $SourceExe = Get-ChildItem -LiteralPath $UnpackedDir -Filter "LocalSR.exe" -File -Recurse |
        Select-Object -First 1
    if (-not $SourceExe) {
        throw "Verified archive does not contain LocalSR.exe."
    }
    $SourceRoot = $SourceExe.Directory.FullName
    $InstallParent = Split-Path -Parent $InstallDir
    New-Item -ItemType Directory -Path $InstallParent -Force | Out-Null
    $NewDir = Join-Path $InstallParent (".localsr.new." + [guid]::NewGuid().ToString("N"))
    $BackupDir = Join-Path $InstallParent (".localsr.previous." + [guid]::NewGuid().ToString("N"))
    Copy-Item -LiteralPath $SourceRoot -Destination $NewDir -Recurse

    if (Test-Path -LiteralPath $InstallDir) {
        Move-Item -LiteralPath $InstallDir -Destination $BackupDir
    }
    try {
        Move-Item -LiteralPath $NewDir -Destination $InstallDir
        $InstalledExe = Get-ChildItem -LiteralPath $InstallDir -Filter "LocalSR.exe" -File -Recurse |
            Select-Object -ExpandProperty FullName -First 1
        if (-not $InstalledExe) {
            throw "LocalSR.exe was not found after installation."
        }
        $InstallCommitted = $true
    }
    catch {
        if (Test-Path -LiteralPath $InstallDir) {
            Remove-Item -LiteralPath $InstallDir -Recurse -Force
        }
        if (Test-Path -LiteralPath $BackupDir) {
            Move-Item -LiteralPath $BackupDir -Destination $InstallDir
        }
        throw
    }
    if (Test-Path -LiteralPath $BackupDir) {
        Remove-Item -LiteralPath $BackupDir -Recurse -Force
        $BackupDir = $null
    }

    $WshShell = New-Object -ComObject WScript.Shell
    $ShortcutTargets = @(
        "$env:USERPROFILE\Desktop\LocalSR.lnk",
        "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\LocalSR\LocalSR.lnk"
    )
    foreach ($ShortcutPath in $ShortcutTargets) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $ShortcutPath) -Force | Out-Null
        $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
        $Shortcut.TargetPath = $InstalledExe
        $Shortcut.WorkingDirectory = Split-Path -Parent $InstalledExe
        $Shortcut.Description = "LocalSR - private local super-resolution"
        $Shortcut.Save()
    }

    Write-Host "Installed LocalSR to $InstallDir" -ForegroundColor Green
    if (-not $NoLaunch) {
        Start-Process -FilePath $InstalledExe
    }
}
finally {
    if (-not $InstallCommitted -and $BackupDir -and (Test-Path -LiteralPath $BackupDir) -and -not (Test-Path -LiteralPath $InstallDir)) {
        Move-Item -LiteralPath $BackupDir -Destination $InstallDir
    }
    if (Test-Path -LiteralPath $TempDir) {
        Remove-Item -LiteralPath $TempDir -Recurse -Force
    }
}
