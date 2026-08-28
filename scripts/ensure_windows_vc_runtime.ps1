[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$registryPaths = @(
    "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
)
$runtimeFiles = @(
    (Join-Path $env:WINDIR "System32\msvcp140.dll"),
    (Join-Path $env:WINDIR "System32\vcruntime140.dll"),
    (Join-Path $env:WINDIR "System32\vcruntime140_1.dll")
)

function Test-VcRuntime {
    $registered = $false
    foreach ($path in $registryPaths) {
        if (Test-Path -LiteralPath $path) {
            $properties = Get-ItemProperty -LiteralPath $path
            if ($properties.Installed -eq 1) {
                $registered = $true
                break
            }
        }
    }

    return $registered -and (($runtimeFiles | Where-Object { -not (Test-Path -LiteralPath $_) }).Count -eq 0)
}

if (Test-VcRuntime) {
    Write-Host "Microsoft Visual C++ v14 x64 runtime is already installed."
    exit 0
}

$tempBase = if ([string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
    [IO.Path]::GetTempPath()
} else {
    $env:RUNNER_TEMP
}
$downloadRoot = Join-Path $tempBase "LocalSR-CI\prerequisites"
$installer = Join-Path $downloadRoot "vc_redist.x64.exe"
New-Item -ItemType Directory -Force -Path $downloadRoot | Out-Null

try {
    $url = "https://aka.ms/vc14/vc_redist.x64.exe"
    Write-Host "Downloading the Microsoft Visual C++ v14 x64 runtime."
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $installer

    $signature = Get-AuthenticodeSignature -FilePath $installer
    if ($signature.Status -ne "Valid") {
        throw "VC++ runtime installer signature is not valid: $($signature.Status)"
    }
    if ($signature.SignerCertificate.Subject -notmatch "Microsoft Corporation") {
        throw "VC++ runtime installer is not signed by Microsoft Corporation"
    }

    $process = Start-Process -FilePath $installer -ArgumentList "/install", "/quiet", "/norestart" -Wait -PassThru
    if ($process.ExitCode -notin 0, 1638, 3010) {
        throw "VC++ runtime installer exited with code $($process.ExitCode)"
    }

    if (-not (Test-VcRuntime)) {
        throw "VC++ runtime installation completed, but the x64 runtime is not usable"
    }

    Write-Host "Microsoft Visual C++ v14 x64 runtime is installed and verified."
}
finally {
    if (Test-Path -LiteralPath $installer) {
        Remove-Item -LiteralPath $installer -Force
    }
}
