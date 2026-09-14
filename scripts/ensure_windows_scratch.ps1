[CmdletBinding()]
param(
    [UInt64]$MinimumFreeGiB = 20
)

$ErrorActionPreference = "Stop"

$volume = Get-Volume -DriveLetter C
$freeGiB = [math]::Round($volume.SizeRemaining / 1GB, 2)
$sizeGiB = [math]::Round($volume.Size / 1GB, 2)
Write-Host "Windows C: size=$sizeGiB GiB free=$freeGiB GiB required-free=$MinimumFreeGiB GiB"

if ($volume.SizeRemaining -lt ($MinimumFreeGiB * 1GB)) {
    throw "Windows scratch has less than $MinimumFreeGiB GiB free"
}

try {
    Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name 'LongPathsEnabled' -Value 1 -ErrorAction SilentlyContinue
} catch {}

try {
    # Prevent Windows sleep, hibernation, and screen/disk turn-off during long builds
    powercfg /change standby-timeout-ac 0
    powercfg /change standby-timeout-dc 0
    powercfg /change monitor-timeout-ac 0
    powercfg /change monitor-timeout-dc 0
    powercfg /change hibernate-timeout-ac 0
    powercfg /change hibernate-timeout-dc 0
    powercfg /change disk-timeout-ac 0
    powercfg /change disk-timeout-dc 0
} catch {}

