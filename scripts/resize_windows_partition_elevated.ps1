[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ResultPath
)

$ErrorActionPreference = "Stop"

try {
    $partition = Get-Partition -DriveLetter C
    $supported = Get-PartitionSupportedSize `
        -DiskNumber $partition.DiskNumber `
        -PartitionNumber $partition.PartitionNumber
    if ($supported.SizeMax -gt ($partition.Size + 1GB)) {
        Resize-Partition `
            -DiskNumber $partition.DiskNumber `
            -PartitionNumber $partition.PartitionNumber `
            -Size $supported.SizeMax
    }
    "PASS" | Out-File -LiteralPath $ResultPath -Encoding ascii -Force
}
catch {
    "FAIL: $($_.Exception.Message)" | Out-File -LiteralPath $ResultPath -Encoding utf8 -Force
    exit 1
}
