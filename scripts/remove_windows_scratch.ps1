param(
    [Parameter(Mandatory = $true)]
    [string]$Target,
    [string]$ManagedRoot = "C:\lsr-ci",
    [int]$TimeoutSeconds = 900
)

$base = [IO.Path]::GetFullPath($ManagedRoot).TrimEnd('\') + '\'
$resolved = [IO.Path]::GetFullPath($Target).TrimEnd('\')
if (-not $resolved.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe cleanup target: $resolved"
}
if (-not (Test-Path -LiteralPath $resolved)) {
    Write-Host "Scratch path is already absent: $resolved"
    exit 0
}

# cmd.exe's rd handles deep Windows trees more reliably than Remove-Item. Run it
# out of process so antivirus or filesystem delays cannot occupy the runner forever.
$process = Start-Process -FilePath $env:ComSpec -ArgumentList @('/d', '/c', 'rd', '/s', '/q', $resolved) -PassThru -WindowStyle Hidden
if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    Write-Warning "Scratch cleanup exceeded ${TimeoutSeconds}s; bounded maintenance will retry: $resolved"
    exit 0
}
if ($process.ExitCode -ne 0 -or (Test-Path -LiteralPath $resolved)) {
    Write-Warning "Scratch cleanup was incomplete; bounded maintenance will retry: $resolved"
}
