# Test only: update end-to-end on a hosted Windows runner. Fails on any assertion.
$ErrorActionPreference = 'Stop'
$R = 'C:\e2e'; $out = "$R\out"; New-Item -ItemType Directory -Force $out, "$R\feed" | Out-Null
Start-Transcript -Path "$out\transcript.txt" | Out-Null
$failures = New-Object System.Collections.Generic.List[string]
function Assert($ok, $msg) { if ($ok) { "PASS: $msg" } else { "FAIL: $msg"; $script:failures.Add($msg) } }
function Info($m) { "[$(Get-Date -f T)] $m" }

# --- files
$v1 = (Get-ChildItem $R\art1 -Filter 'LocalSR-v0.3.7-beta-Windows-CPU-x86_64.exe').FullName
$v2 = (Get-ChildItem $R\art2 -Filter 'LocalSR-v0.3.8-beta-Windows-CPU-x86_64.exe').FullName
Copy-Item $v2 "$R\feed\LocalSR-new.exe"
Copy-Item test-e2e\feed\LocalSR-fail.exe "$R\feed\LocalSR-fail.exe"
Copy-Item test-e2e\serve.ps1 "$R\serve.ps1"

# --- trusted localhost certificate bound to 8443 (v4 and v6)
$c = New-SelfSignedCertificate -DnsName localhost -CertStoreLocation Cert:\LocalMachine\My -NotAfter (Get-Date).AddDays(1)
Export-Certificate -Cert $c -FilePath "$R\e2e.cer" | Out-Null
Import-Certificate -FilePath "$R\e2e.cer" -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
$app = '{5a1b2c3d-0000-4000-8000-0123456789ab}'
netsh http add sslcert ipport=127.0.0.1:8443 certhash=$($c.Thumbprint) appid=$app
netsh http add sslcert 'ipport=[::1]:8443' certhash=$($c.Thumbprint) appid=$app
Set-Content "$R\feed\t.txt" hello
$server = Start-Process pwsh -ArgumentList '-NoProfile','-File',"$R\serve.ps1" -PassThru -WindowStyle Hidden
Start-Sleep 5
Assert ((curl.exe -s --max-time 10 https://localhost:8443/updates/t.txt) -eq 'hello') 'HTTPS feed server reachable with the trusted certificate'

# --- install v1 silently
$p = Start-Process $v1 -ArgumentList '/S' -Wait -PassThru
Assert ($p.ExitCode -eq 0) "v1 silent install exit code $($p.ExitCode)"
$reg = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LocalSR' -ErrorAction SilentlyContinue
$dir = if ($reg) { $reg.InstallLocation.Trim('"') } else { (Get-ChildItem $env:LOCALAPPDATA\Programs -Filter localsr-next.exe -Recurse | Select-Object -First 1).DirectoryName }
$exe = "$dir\localsr-next.exe"
Info "install dir $dir"
function Ver { (Get-Item $exe).VersionInfo.FileVersion }
$next = "$env:LOCALAPPDATA\LocalSR\next"
Assert ((Ver) -like '0.3.7*') "installed version is $(Ver)"
Set-Content "$dir\engine\e2e-marker.txt" kept
$engineBefore = Get-ChildItem "$dir\engine" -Recurse -File | % { $_.FullName.Substring($dir.Length) } | Sort-Object
Info "engine files before: $($engineBefore.Count)"
$bak = "$out\profile-state"
function Dump($tag) {
  $f = "$out\state-$tag.txt"
  @("version: $(Ver)", "dir:") + (Get-ChildItem $dir | % Name) + @("next:") + (Get-ChildItem $next -ErrorAction SilentlyContinue | % Name) +
    @("pending: " + (Get-Content "$next\update-pending.json" -Raw -ErrorAction SilentlyContinue)) +
    @("backups:") + (Get-ChildItem "$next\backups" -ErrorAction SilentlyContinue | % { "$($_.Name): " + (Get-Content "$($_.FullName)\installed-version.txt" -ErrorAction SilentlyContinue) }) +
    @("updates: " + ((Get-ChildItem "$next\updates" -ErrorAction SilentlyContinue | % { "$($_.Name)($($_.Length))" }) -join ', ')) +
    @("engines: " + ((Get-ChildItem "$next\engines" -ErrorAction SilentlyContinue | % Name) -join ', ')) +
    @("active-engine: " + (Get-Content "$next\active-engine.txt" -ErrorAction SilentlyContinue)) | Set-Content $f
  Get-Content $f
}
function Run-App($steps, $tag) {
  Set-Content "$R\steps.txt" $steps
  $env:LOCALSR_E2E_STEPS = "$R\steps.txt"
  $pr = Start-Process $exe -PassThru -RedirectStandardError "$out\app-$tag.err" -RedirectStandardOutput "$out\app-$tag.out"
  Remove-Item Env:\LOCALSR_E2E_STEPS
  $pr
}
function Kill-App { Get-Process localsr-next -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep 2 }
function Wait-Until([scriptblock]$cond, [int]$sec) { $t = [Diagnostics.Stopwatch]::StartNew(); while ($t.Elapsed.TotalSeconds -lt $sec) { if (& $cond) { return $true }; Start-Sleep 1 }; return $false }
function Feed($name) { Copy-Item "test-e2e\feed\beta$name.json" "$R\feed\beta.json" -Force }
Remove-Item "$R\serve.log" -ErrorAction SilentlyContinue

# ===== B: free-space refusal
Info '=== B: free-space refusal'
Feed 'B'; $pr = Run-App 'check download install' 'B'
$done = Wait-Until { (Get-Content "$out\app-B.err" -Raw -ErrorAction SilentlyContinue) -match 'step install:' } 420
Kill-App
Get-Content "$out\app-B.err" | Select-String 'E2E' | % { $_.Line }
$errB = (Get-Content "$out\app-B.err" -Raw -ErrorAction SilentlyContinue)
Assert $done 'B: install step finished'
Assert ($errB -match 'step install: Err') 'B: install refused'
Assert ($errB -match '(?i)space|disk|Speicher') 'B: refusal message mentions space'
Assert ((Ver) -like '0.3.7*') 'B: nothing installed (still 0.3.7)'
Assert (-not (Test-Path "$next\update-pending.json")) 'B: no pending marker written'
Dump 'B'

# ===== C: installer that fails
Info '=== C: failed installer'
Feed 'C'; $pr = Run-App 'check download install' 'C'
$exited = $pr.WaitForExit(420000)
Start-Sleep 10
Get-Content "$out\app-C.err" | Select-String 'E2E' | % { $_.Line }
Assert $exited 'C: app exited after handing over to the installer'
Assert ((Ver) -like '0.3.7*') 'C: old version still installed'
$pend = Get-Content "$next\update-pending.json" -Raw -ErrorAction SilentlyContinue
Assert ($pend -match '0.3.8-beta') 'C: pending marker present for 0.3.8-beta'
Assert (Test-Path "$next\updates") 'C: verified downloads kept for a retry'
Dump 'C-after'
$pr = Start-Process $exe -PassThru -RedirectStandardError "$out\app-C2.err"
Start-Sleep 30
Assert (-not $pr.HasExited) 'C: the old version starts and stays running'
Assert (Test-Path "$next\update-pending.json") 'C: marker stays after starting the old version (retry possible)'
Kill-App
Dump 'C-restart'

# ===== A: retry with a real installer (unchanged engine id)
Info '=== A: normal update'
Remove-Item "$R\serve.log" -ErrorAction SilentlyContinue
Feed 'A'; $pr = Run-App 'check download install' 'A'
$seen = $false
$ok = Wait-Until { if (Test-Path "$next\update-pending.json") { $script:seen = $true }; (Ver) -like '0.3.8*' } 600
Assert $ok 'A: installed version became 0.3.8-beta'
Assert $seen 'A: pending marker was written before the installer ran'
Start-Sleep 30
$running = Get-Process localsr-next -ErrorAction SilentlyContinue
Info "after installer: running=$(@($running).Count)"
if (-not $running) { Info 'installer did not restart the app; starting the new version once'; $pr2 = Start-Process $exe -PassThru -RedirectStandardError "$out\app-A2.err" } else { Info "installer restarted the app (pid $($running.Id -join ','))" }
$gone = Wait-Until { -not (Test-Path "$next\update-pending.json") } 120
Assert $gone 'A: pending marker confirmed and removed by the next start'
Start-Sleep 5
Assert ((Ver) -like '0.3.8*') "A: version $(Ver)"
Assert ((Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LocalSR').DisplayVersion -like '0.3.8*') 'A: registry DisplayVersion 0.3.8'
Assert (-not (Test-Path "$next\updates")) 'A: downloads removed after confirmation'
$iv = Get-ChildItem "$next\backups" | % { Get-Content "$($_.FullName)\installed-version.txt" -ErrorAction SilentlyContinue } | ? { $_ }
Assert ($iv -contains '0.3.8-beta') 'A: backup records installed-version 0.3.8-beta'
Assert (Test-Path "$dir\engine\e2e-marker.txt") 'A: engine folder kept (marker file survives)'
$engineAfter = Get-ChildItem "$dir\engine" -Recurse -File | ? { $_.Name -ne 'e2e-marker.txt' } | % { $_.FullName.Substring($dir.Length) } | Sort-Object
$engineBefore2 = $engineBefore | ? { $_ -notlike '*e2e-marker.txt' }
Assert (-not (Compare-Object $engineBefore2 $engineAfter)) "A: engine file list unchanged ($($engineAfter.Count) files)"
$log = Get-Content "$R\serve.log" -ErrorAction SilentlyContinue
$log | Set-Content "$out\serve-A.log"
Assert (-not ($log -match 'engine')) 'A: no engine download in the feed log'
Assert (($log -match 'LocalSR-new.exe').Count -ge 1) 'A: application package was downloaded'
Dump 'A'
Get-Content "$out\app-A.err" | Select-String 'E2E' | % { $_.Line }

# --- collect
Kill-App
Copy-Item $R\serve.log $out\serve-all.log -ErrorAction SilentlyContinue
$noise = Get-ChildItem $out -Filter 'app-*.err' | Select-String 'could not roll back an interrupted update'
Info ("false alarm 'could not roll back an interrupted update' seen in: " + (($noise | % { $_.Filename } | Sort-Object -Unique) -join ', '))
Get-ChildItem $next -Recurse -ErrorAction SilentlyContinue | Select-Object -First 200 FullName | Out-File "$out\profile-tree.txt"
Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
Stop-Transcript | Out-Null
if ($failures.Count) { "FAILED ASSERTIONS:"; $failures; exit 1 }
"ALL ASSERTIONS PASSED"
