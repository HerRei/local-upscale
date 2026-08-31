[CmdletBinding()]
param(
    [string]$BootstrapperUri = "https://aka.ms/vs/17/release/vs_BuildTools.exe",
    [Int64]$MinimumFreeBytes = 15GB
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Set-StrictMode -Version Latest

function Find-VcVarsAll {
    $programFilesX86 = [Environment]::GetFolderPath("ProgramFilesX86")
    $vswhere = Join-Path $programFilesX86 "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path -LiteralPath $vswhere) {
        $installationPath = (& $vswhere `
            -products * `
            -latest `
            -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
            -property installationPath 2>$null | Select-Object -First 1)
        if (-not [string]::IsNullOrWhiteSpace($installationPath)) {
            $candidate = Join-Path $installationPath "VC\Auxiliary\Build\vcvarsall.bat"
            if (Test-Path -LiteralPath $candidate) {
                return $candidate
            }
        }
    }

    foreach ($root in @($programFilesX86, $env:ProgramFiles)) {
        foreach ($edition in @("BuildTools", "Community", "Professional", "Enterprise")) {
            $candidate = Join-Path $root "Microsoft Visual Studio\2022\$edition\VC\Auxiliary\Build\vcvarsall.bat"
            if (Test-Path -LiteralPath $candidate) {
                return $candidate
            }
        }
    }
    return $null
}

$existing = Find-VcVarsAll
if ($null -ne $existing) {
    Write-Host "MSVC build tools are already available at $existing"
    return
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Installing Microsoft C++ Build Tools is a one-time elevated runner provisioning step. Re-run this script as Administrator."
}

$systemDriveName = ([IO.Path]::GetPathRoot($env:SystemRoot)).TrimEnd("\").TrimEnd(":")
$systemDrive = Get-PSDrive -Name $systemDriveName
if ($systemDrive.Free -lt $MinimumFreeBytes) {
    $availableGiB = [Math]::Round($systemDrive.Free / 1GB, 1)
    $requiredGiB = [Math]::Round($MinimumFreeBytes / 1GB, 1)
    throw "MSVC provisioning requires at least $requiredGiB GiB free on $systemDriveName`:; only $availableGiB GiB is available."
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$downloadRoot = Join-Path $env:TEMP "LocalSR-build-tools"
$bootstrapper = Join-Path $downloadRoot "vs_BuildTools.exe"
New-Item -ItemType Directory -Force -Path $downloadRoot | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri $BootstrapperUri -OutFile $bootstrapper

$signature = Get-AuthenticodeSignature -LiteralPath $bootstrapper
if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
    $null -eq $signature.SignerCertificate -or
    $signature.SignerCertificate.Subject -notmatch "Microsoft Corporation") {
    Remove-Item -LiteralPath $bootstrapper -Force -ErrorAction SilentlyContinue
    throw "The Visual Studio Build Tools bootstrapper does not have a valid Microsoft Authenticode signature."
}

$installPath = Join-Path ([Environment]::GetFolderPath("ProgramFilesX86")) "Microsoft Visual Studio\2022\BuildTools"
$arguments = @(
    "--quiet",
    "--wait",
    "--norestart",
    "--nocache",
    "--installPath",
    "`"$installPath`"",
    "--add",
    "Microsoft.VisualStudio.Workload.VCTools",
    "--includeRecommended"
)
$process = Start-Process -FilePath $bootstrapper -ArgumentList $arguments -Wait -PassThru
if ($process.ExitCode -notin @(0, 3010)) {
    throw "Visual Studio Build Tools failed with exit code $($process.ExitCode)."
}

$installed = Find-VcVarsAll
if ($null -eq $installed) {
    throw "Visual Studio completed without exposing the required x64 C++ build environment."
}

Remove-Item -LiteralPath $bootstrapper -Force -ErrorAction SilentlyContinue
Write-Host "MSVC build tools are ready at $installed"

