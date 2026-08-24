[CmdletBinding()]
param(
    [UInt64]$MinimumFreeGiB = 20
)

$ErrorActionPreference = "Stop"

function Invoke-ElevatedPartitionResize {
    if ([string]::IsNullOrWhiteSpace($env:WINDOWS_CI_PASSWORD)) {
        throw "Partition expansion requires the masked WINDOWS_CI_PASSWORD repository secret"
    }

    $helper = Join-Path $env:WINDIR "Temp\lsr-resize.ps1"
    $result = Join-Path $env:WINDIR "Temp\lsr-resize-result.txt"
    $taskName = "LocalSR-CI-Partition-Resize-$env:GITHUB_RUN_ID"
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $taskCommand = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $helper -ResultPath $result"
    $startTime = (Get-Date).AddMinutes(1).ToString("HH:mm")
    $taskCreated = $false

    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "resize_windows_partition_elevated.ps1") -Destination $helper -Force
    Remove-Item -LiteralPath $result -Force -ErrorAction SilentlyContinue
    try {
        & schtasks.exe /Create /TN $taskName /TR $taskCommand /SC ONCE /ST $startTime /RU $identity /RP $env:WINDOWS_CI_PASSWORD /RL HIGHEST /F | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Could not create elevated partition-resize task" }
        $taskCreated = $true
        & schtasks.exe /Run /TN $taskName | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Could not start elevated partition-resize task" }

        $deadline = (Get-Date).AddMinutes(3)
        while (-not (Test-Path -LiteralPath $result)) {
            if ((Get-Date) -ge $deadline) { throw "Elevated partition-resize task timed out" }
            Start-Sleep -Seconds 2
        }
        $outcome = (Get-Content -LiteralPath $result -Raw).Trim()
        if ($outcome -ne "PASS") { throw $outcome }
    }
    finally {
        if ($taskCreated) {
            & cmd.exe /c "schtasks.exe /Delete /TN $taskName /F >nul 2>&1"
        }
        Remove-Item -LiteralPath $helper -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $result -Force -ErrorAction SilentlyContinue
    }
}

try {
    $partition = Get-Partition -DriveLetter C
    $supported = Get-PartitionSupportedSize `
        -DiskNumber $partition.DiskNumber `
        -PartitionNumber $partition.PartitionNumber
}
catch [Microsoft.Management.Infrastructure.CimException] {
    Write-Host "The runner token cannot resize partitions directly; using a temporary elevated scheduled task."
    Invoke-ElevatedPartitionResize
    $partition = Get-Partition -DriveLetter C
    $supported = $null
}

if ($null -ne $supported -and $supported.SizeMax -gt ($partition.Size + 1GB)) {
    Write-Host "Extending C: from $([math]::Round($partition.Size / 1GB, 2)) GiB to $([math]::Round($supported.SizeMax / 1GB, 2)) GiB"
    Resize-Partition `
        -DiskNumber $partition.DiskNumber `
        -PartitionNumber $partition.PartitionNumber `
        -Size $supported.SizeMax
}

$volume = Get-Volume -DriveLetter C
$freeGiB = [math]::Round($volume.SizeRemaining / 1GB, 2)
$sizeGiB = [math]::Round($volume.Size / 1GB, 2)
Write-Host "Windows C: size=$sizeGiB GiB free=$freeGiB GiB required-free=$MinimumFreeGiB GiB"

if ($volume.SizeRemaining -lt ($MinimumFreeGiB * 1GB)) {
    throw "Windows scratch has less than $MinimumFreeGiB GiB free"
}
