param(
    [Parameter(Mandatory = $true)]
    [string]$RegistrationToken,
    [string]$RemovalToken = "",
    [string]$RepositoryUrl = "https://github.com/HerRei/local-upscale",
    [string]$RunnerName = "macmini-windows-x64"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$sharedRoot = $PSScriptRoot
$statusPath = Join-Path $sharedRoot "runner-bootstrap-status.json"
$logPath = Join-Path $sharedRoot "runner-bootstrap.log"
$runnerRoot = "C:\actions-runner"
$pythonRoot = "C:\Python311"
$gitRoot = "C:\Program Files\Git"
$watchdogSource = Join-Path $PSScriptRoot "ensure-runner.ps1"
$watchdogRoot = Join-Path $env:ProgramData "LocalSR-CI"
$watchdogPath = Join-Path $watchdogRoot "ensure-runner.ps1"
$runnerUrl = "https://github.com/actions/runner/releases/download/v2.336.0/actions-runner-win-x64-2.336.0.zip"
$pythonUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
$gitUrl = "https://github.com/git-for-windows/git/releases/download/v2.55.0.windows.5/Git-2.55.0.5-64-bit.exe"

function Invoke-Installer {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Arguments
    )
    $process = Start-Process -FilePath $Path -ArgumentList $Arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Installer $Path failed with exit code $($process.ExitCode)"
    }
}

function Write-Status {
    param([string]$State, [string]$Message)
    $services = @(Get-Service -Name "actions.runner.*" -ErrorAction SilentlyContinue)
    $pythonVersion = if (Test-Path -LiteralPath "$pythonRoot\python.exe") {
        (& "$pythonRoot\python.exe" --version 2>&1 | Out-String).Trim()
    } else {
        "missing"
    }
    $gitVersion = if (Test-Path -LiteralPath "$gitRoot\cmd\git.exe") {
        (& "$gitRoot\cmd\git.exe" --version 2>&1 | Out-String).Trim()
    } else {
        "missing"
    }
    [ordered]@{
        timestamp = [DateTimeOffset]::UtcNow.ToString("o")
        state = $State
        message = $Message
        runner_name = $RunnerName
        runner_version = "2.336.0"
        services = @($services | ForEach-Object {
            [ordered]@{
                name = $_.Name
                status = $_.Status.ToString()
                start_type = $_.StartType.ToString()
            }
        })
        python = $pythonVersion
        git = $gitVersion
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $statusPath -Encoding UTF8
}

function Write-BootstrapLog {
    param([string]$Message)
    if ((Test-Path -LiteralPath $logPath) -and
        (Get-Item -LiteralPath $logPath).Length -gt 1MB) {
        Move-Item -LiteralPath $logPath -Destination "$logPath.1" -Force
    }
    $timestamp = [DateTimeOffset]::UtcNow.ToString("o")
    Add-Content -LiteralPath $logPath -Value "$timestamp $Message" -Encoding UTF8
}

Write-BootstrapLog "bootstrap started"
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Set-TimeZone -Id "UTC"
    Set-Service -Name "W32Time" -StartupType Automatic
    if ((Get-Service -Name "W32Time").Status -ne "Running") {
        Start-Process -FilePath "$env:SystemRoot\System32\sc.exe" -ArgumentList @("start", "W32Time") -WindowStyle Hidden
    }

    $downloadRoot = Join-Path $env:TEMP "LocalSR-runner-bootstrap"
    New-Item -ItemType Directory -Force -Path $downloadRoot | Out-Null

    if (-not (Test-Path -LiteralPath "$pythonRoot\python.exe")) {
        $pythonInstaller = Join-Path $downloadRoot "python-installer.exe"
        Invoke-WebRequest -Uri $pythonUrl -OutFile $pythonInstaller
        Invoke-Installer -Path $pythonInstaller -Arguments "/quiet InstallAllUsers=1 TargetDir=$pythonRoot PrependPath=1 Include_pip=1"
    }

    if (-not (Test-Path -LiteralPath "$gitRoot\cmd\git.exe")) {
        $gitInstaller = Join-Path $downloadRoot "git-installer.exe"
        Invoke-WebRequest -Uri $gitUrl -OutFile $gitInstaller
        Invoke-Installer -Path $gitInstaller -Arguments "/VERYSILENT /NORESTART /NOCANCEL /SP-"
    }

    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $requiredPaths = @($pythonRoot, "$pythonRoot\Scripts", "$gitRoot\cmd")
    foreach ($path in $requiredPaths) {
        if (($machinePath -split ";") -notcontains $path) {
            $machinePath = "$machinePath;$path"
        }
    }
    [Environment]::SetEnvironmentVariable("Path", $machinePath, "Machine")
    $env:Path = "$machinePath;$([Environment]::GetEnvironmentVariable('Path', 'User'))"

    if (-not (Test-Path -LiteralPath "$runnerRoot\bin\Runner.Listener.exe")) {
        $runnerArchive = Join-Path $downloadRoot "actions-runner.zip"
        Invoke-WebRequest -Uri $runnerUrl -OutFile $runnerArchive
        New-Item -ItemType Directory -Force -Path $runnerRoot | Out-Null
        Expand-Archive -LiteralPath $runnerArchive -DestinationPath $runnerRoot -Force
    }

    $configureRunner = -not (Test-Path -LiteralPath "$runnerRoot\.runner")
    if (-not $configureRunner -and -not [string]::IsNullOrWhiteSpace($RemovalToken)) {
        $existingService = Get-Service -Name "actions.runner.*" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -ne $existingService -and $existingService.Status -ne "Stopped") {
            Stop-Service -Name $existingService.Name -Force
        }

        Push-Location $runnerRoot
        try {
            & .\config.cmd remove --token $RemovalToken
            if ($LASTEXITCODE -ne 0) {
                throw "Runner config.cmd remove failed with exit code $LASTEXITCODE"
            }
        }
        finally {
            Pop-Location
        }
        $configureRunner = $true
    }

    if ($configureRunner) {
        Push-Location $runnerRoot
        try {
            $configArguments = @(
                "--unattended",
                "--url", $RepositoryUrl,
                "--token", $RegistrationToken,
                "--name", $RunnerName,
                "--labels", "self-hosted,Windows,X64",
                "--work", "_work",
                "--replace",
                "--runasservice"
            )
            & .\config.cmd @configArguments
            if ($LASTEXITCODE -ne 0) {
                throw "Runner config.cmd failed with exit code $LASTEXITCODE"
            }
        }
        finally {
            Pop-Location
        }
    }

    $service = Get-Service -Name "actions.runner.*" -ErrorAction Stop | Select-Object -First 1
    Set-Service -Name $service.Name -StartupType Automatic
    $serviceRegistryPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$($service.Name)"
    Set-ItemProperty -LiteralPath $serviceRegistryPath -Name "DelayedAutoStart" -Type DWord -Value 0

    if (-not (Test-Path -LiteralPath $watchdogSource)) {
        throw "Runner watchdog source is missing: $watchdogSource"
    }
    New-Item -ItemType Directory -Force -Path $watchdogRoot | Out-Null
    Copy-Item -LiteralPath $watchdogSource -Destination $watchdogPath -Force
    $taskAction = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$watchdogPath`""
    $startupTrigger = New-ScheduledTaskTrigger -AtStartup
    $periodicTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName "LocalSR-Runner-Watchdog" -Action $taskAction -Trigger @($startupTrigger, $periodicTrigger) -Principal $taskPrincipal -Settings $taskSettings -Force | Out-Null

    if ($service.Status -ne "Running") {
        Start-Service -Name $service.Name
    }
    Write-Status -State "ready" -Message "Windows runner installed and started"
    Write-BootstrapLog "bootstrap completed"
}
catch {
    Write-Status -State "failed" -Message $_.Exception.Message
    Write-BootstrapLog "ERROR: $($_.Exception.Message)"
    throw
}
