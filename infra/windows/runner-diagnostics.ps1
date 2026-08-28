$ErrorActionPreference = "Stop"

$runnerRoot = "C:\actions-runner"
$services = @(Get-Service -Name "actions.runner.*" -ErrorAction SilentlyContinue)
$processes = @(Get-Process -Name "Runner.Listener", "Runner.Worker" -ErrorAction SilentlyContinue)
$result = [ordered]@{
    timestamp = [DateTimeOffset]::UtcNow.ToString("o")
    computer_name = $env:COMPUTERNAME
    user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    architecture = $env:PROCESSOR_ARCHITECTURE
    runner_root_exists = Test-Path -LiteralPath $runnerRoot
    runner_config_exists = Test-Path -LiteralPath (Join-Path $runnerRoot ".runner")
    runner_listener_exists = Test-Path -LiteralPath (Join-Path $runnerRoot "bin\Runner.Listener.exe")
    services = @($services | ForEach-Object {
        [ordered]@{
            name = $_.Name
            status = $_.Status.ToString()
            start_type = $_.StartType.ToString()
        }
    })
    processes = @($processes | ForEach-Object {
        [ordered]@{
            name = $_.ProcessName
            id = $_.Id
            path = $_.Path
        }
    })
    python = (& python --version 2>&1 | Out-String).Trim()
    git = (& git --version 2>&1 | Out-String).Trim()
}

$output = Join-Path $PSScriptRoot "runner-diagnostics.json"
$result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $output -Encoding UTF8
Write-Host "Wrote $output"
