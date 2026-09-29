[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ToolsDir = Join-Path $ProjectRoot ".tools\tunnel-client"
New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null

$headers = @{ "User-Agent" = "Codex-local-tunnel-client-setup" }
$release = Invoke-RestMethod -Uri "https://api.github.com/repos/openai/tunnel-client/releases/latest" -Headers $headers
$assetName = "tunnel-client-{0}-windows-amd64.zip" -f $release.tag_name
$asset = $release.assets | Where-Object { $_.name -eq $assetName } | Select-Object -First 1
$checksumsAsset = $release.assets | Where-Object { $_.name -eq "SHA256SUMS.txt" } | Select-Object -First 1

if (-not $asset -or -not $checksumsAsset) {
    throw "Latest release $($release.tag_name) does not contain the expected Windows amd64 asset and SHA256SUMS.txt."
}

$zipPath = Join-Path $ToolsDir $asset.name
$checksumsPath = Join-Path $ToolsDir "SHA256SUMS.txt"
Invoke-WebRequest -Uri $asset.browser_download_url -Headers $headers -OutFile $zipPath
Invoke-WebRequest -Uri $checksumsAsset.browser_download_url -Headers $headers -OutFile $checksumsPath

$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant()
$line = Get-Content -LiteralPath $checksumsPath | Where-Object { $_ -match [regex]::Escape($asset.name) } | Select-Object -First 1
if (-not $line -or $line -notmatch '^([0-9a-fA-F]{64})\s+') {
    throw "No SHA256 entry found for $($asset.name)."
}
$expected = $Matches[1].ToLowerInvariant()
if ($actual -ne $expected) {
    throw "SHA256 mismatch for $($asset.name). Expected $expected, got $actual."
}

Expand-Archive -LiteralPath $zipPath -DestinationPath $ToolsDir -Force
$binary = Join-Path $ToolsDir "tunnel-client.exe"
if (-not (Test-Path -LiteralPath $binary)) {
    throw "The release archive did not contain tunnel-client.exe."
}

Write-Host "Installed tunnel-client $($release.tag_name) to $binary"
Write-Host "SHA256 verified: $actual"
