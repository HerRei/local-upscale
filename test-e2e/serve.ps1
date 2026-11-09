# serves C:\e2e\feed at https://localhost:8443/updates/ ; log -> C:\e2e\serve.log
$root = 'C:\e2e\feed'
$l = New-Object System.Net.HttpListener
$l.Prefixes.Add('https://localhost:8443/')
$l.Start()
while ($l.IsListening) {
  $ctx = $l.GetContext(); $req = $ctx.Request; $res = $ctx.Response
  $rel = $req.Url.AbsolutePath.TrimStart('/') -replace '^updates/',''
  $path = Join-Path $root ($rel -replace '/','\')
  "$(Get-Date -f s) $($req.HttpMethod) $($req.Url.AbsolutePath)" | Add-Content C:\e2e\serve.log
  if ($rel -notmatch '\.\.' -and (Test-Path -LiteralPath $path -PathType Leaf)) {
    $res.ContentLength64 = (Get-Item -LiteralPath $path).Length
    $fs = [IO.File]::OpenRead($path); try { $fs.CopyTo($res.OutputStream) } catch {} finally { $fs.Close() }
  } else { $res.StatusCode = 404 }
  try { $res.Close() } catch {}
}
