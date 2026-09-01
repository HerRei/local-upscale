[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$RustupVersion = '1.29.0',

    [ValidatePattern('^[0-9a-fA-F]{64}$')]
    [string]$ExpectedSha256 = '86478e53f769379d7f0ebfa7c9aa97cb76ca92233f79aa2cc0dbee2efaac73c7'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$cargoHome = if ($env:CARGO_HOME) {
    $env:CARGO_HOME
} else {
    Join-Path $env:USERPROFILE '.cargo'
}
$cargoBin = Join-Path $cargoHome 'bin'
$env:CARGO_HOME = $cargoHome

$rustupCommand = Get-Command rustup.exe -ErrorAction SilentlyContinue
if ($rustupCommand) {
    $rustup = $rustupCommand.Source
} else {
    New-Item -ItemType Directory -Force -Path $cargoBin | Out-Null
    $installer = Join-Path $env:RUNNER_TEMP "rustup-init-$RustupVersion.exe"
    $uri = "https://static.rust-lang.org/rustup/archive/$RustupVersion/x86_64-pc-windows-msvc/rustup-init.exe"
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $uri -OutFile $installer
        $actualSha256 = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualSha256 -ne $ExpectedSha256.ToLowerInvariant()) {
            throw "rustup-init checksum mismatch: expected $ExpectedSha256, got $actualSha256"
        }
        & $installer --default-toolchain none --no-modify-path -y
        if ($LASTEXITCODE -ne 0) {
            throw "rustup-init failed with exit code $LASTEXITCODE"
        }
    } finally {
        if (Test-Path -LiteralPath $installer) {
            Remove-Item -LiteralPath $installer -Force
        }
    }
    $rustup = Join-Path $cargoBin 'rustup.exe'
}

if (-not (Test-Path -LiteralPath $rustup)) {
    throw "rustup.exe was not found after installation: $rustup"
}

& $rustup toolchain install stable --profile minimal --component clippy --component rustfmt --no-self-update
if ($LASTEXITCODE -ne 0) {
    throw "rustup toolchain install failed with exit code $LASTEXITCODE"
}
& $rustup default stable
if ($LASTEXITCODE -ne 0) {
    throw "rustup default failed with exit code $LASTEXITCODE"
}

if ($env:GITHUB_ENV) {
    "CARGO_HOME=$cargoHome" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
}
if ($env:GITHUB_PATH) {
    $cargoBin | Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append
}

& (Join-Path $cargoBin 'rustc.exe') --version --verbose
if ($LASTEXITCODE -ne 0) {
    throw "rustc verification failed with exit code $LASTEXITCODE"
}
