param(
    [Parameter(Mandatory = $true)][string]$Artifact,
    [Parameter(Mandatory = $true)][string]$ExpectedThumbprint,
    [Parameter(Mandatory = $true)][string]$Report
)

$ErrorActionPreference = "Stop"
$signature = Get-AuthenticodeSignature -LiteralPath $Artifact
if ($signature.Status -ne "Valid") {
    throw "Authenticode signature is not valid: $($signature.Status) $($signature.StatusMessage)"
}
if ($null -eq $signature.SignerCertificate) {
    throw "Authenticode signer certificate is missing"
}
if ($null -eq $signature.TimeStamperCertificate) {
    throw "Authenticode timestamp certificate is missing"
}
$actualThumbprint = $signature.SignerCertificate.Thumbprint.Replace(" ", "").ToUpperInvariant()
$wantedThumbprint = $ExpectedThumbprint.Replace(" ", "").ToUpperInvariant()
if ($actualThumbprint -ne $wantedThumbprint) {
    throw "Authenticode signer thumbprint does not match the configured certificate"
}

$payload = [ordered]@{
    status = "authenticode-valid"
    signer_subject = $signature.SignerCertificate.Subject
    signer_thumbprint = $actualThumbprint
    timestamp_subject = $signature.TimeStamperCertificate.Subject
    timestamped = $true
}
$payload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $Report -Encoding utf8
$payload | ConvertTo-Json -Depth 4
