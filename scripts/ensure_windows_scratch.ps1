[CmdletBinding()]
param(
    [UInt64]$MinimumFreeGiB = 20
)

$ErrorActionPreference = "Stop"

$partition = Get-Partition -DriveLetter C
$supported = Get-PartitionSupportedSize `
    -DiskNumber $partition.DiskNumber `
    -PartitionNumber $partition.PartitionNumber

if ($supported.SizeMax -gt ($partition.Size + 1GB)) {
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
