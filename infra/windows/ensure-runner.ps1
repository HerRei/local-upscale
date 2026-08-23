$ErrorActionPreference = "Stop"

$stateRoot = Join-Path $env:ProgramData "LocalSR-CI"
$logPath = Join-Path $stateRoot "runner-watchdog.log"
New-Item -ItemType Directory -Force -Path $stateRoot | Out-Null

if ((Test-Path -LiteralPath $logPath) -and
    (Get-Item -LiteralPath $logPath).Length -gt 1MB) {
    Move-Item -LiteralPath $logPath -Destination "$logPath.1" -Force
}

function Write-WatchdogLog {
    param([string]$Message)
    $timestamp = [DateTimeOffset]::UtcNow.ToString("o")
    Add-Content -LiteralPath $logPath -Value "$timestamp $Message" -Encoding UTF8
}

try {
    Set-TimeZone -Id "UTC"
    $service = Get-Service -Name "actions.runner.*" -ErrorAction Stop |
        Select-Object -First 1
    if ($service.Status -ne "Running") {
        Start-Process -FilePath "$env:SystemRoot\System32\sc.exe" -ArgumentList @("start", $service.Name) -WindowStyle Hidden
        Write-WatchdogLog "runner service start requested ($($service.Name))"
    }
    else {
        Write-WatchdogLog "runner service running ($($service.Name))"
    }

    Set-Service -Name "W32Time" -StartupType Automatic
    if ((Get-Service -Name "W32Time").Status -ne "Running") {
        Start-Process -FilePath "$env:SystemRoot\System32\sc.exe" -ArgumentList @("start", "W32Time") -WindowStyle Hidden
    }
    Start-Process -FilePath "$env:SystemRoot\System32\w32tm.exe" -ArgumentList @("/resync", "/force", "/nowait") -WindowStyle Hidden
    Write-WatchdogLog "time resync requested"
}
catch {
    Write-WatchdogLog "ERROR: $($_.Exception.Message)"
    exit 1
}
