$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root 'data'
New-Item -ItemType Directory -Force -Path $Data | Out-Null

$Port = 8783
$ExpectedModule = 'predict_bot.public_research_archive_v1'
$HealthUrl = "http://127.0.0.1:$Port/health"
$CurrentUrl = "http://127.0.0.1:$Port/current-public"

function Get-ListeningProcessId([int]$PortNumber) {
    try {
        $c = Get-NetTCPConnection -LocalPort $PortNumber -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($c) { return [int]$c.OwningProcess }
    } catch {}
    return $null
}

function Get-CommandLine([int]$ProcessId) {
    try { return [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop).CommandLine }
    catch { return '' }
}

function Test-CurrentPublic {
    try {
        $r = Invoke-RestMethod -Uri $CurrentUrl -TimeoutSec 5
        $s = $r.snapshot
        return [bool](
            $r.ok -and
            $r.targetBlind -and
            $s -and
            $null -ne $s.spot_price -and
            $null -ne $s.futures_price -and
            $null -ne $s.chainlink_price
        )
    } catch { return $false }
}

if (Test-CurrentPublic) {
    Write-Host 'Gemini public feature source already ready on 8783/current-public.'
    exit 0
}

$pidValue = Get-ListeningProcessId $Port
if ($pidValue) {
    $cmd = Get-CommandLine $pidValue
    if (-not $cmd.ToLowerInvariant().Contains($ExpectedModule)) {
        throw "Port 8783 is occupied by an unexpected process. PID=$pidValue command=$cmd"
    }
    Write-Host "Restarting expected 8783 target-blind archive to expose /current-public. PID=$pidValue"
    Stop-Process -Id $pidValue -Force -ErrorAction Stop
    Start-Sleep -Milliseconds 500
}

python -m py_compile src/predict_bot/public_research_archive_v1.py
if ($LASTEXITCODE -ne 0) { throw 'public_research_archive_v1 syntax check failed.' }

$stdout = Join-Path $Data 'public-research-archive-v1.stdout.log'
$stderr = Join-Path $Data 'public-research-archive-v1.stderr.log'
$p = Start-Process -FilePath 'python' -ArgumentList @('-m','predict_bot.public_research_archive_v1') -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
$p.Id | Set-Content (Join-Path $Root '.public-research-archive-v1.pid')

$deadline = (Get-Date).AddSeconds(25)
do {
    Start-Sleep -Milliseconds 500
    if (Test-CurrentPublic) { break }
} while ((Get-Date) -lt $deadline)

if (-not (Test-CurrentPublic)) {
    if (Test-Path $stderr) { Get-Content $stderr -Tail 80 }
    throw '8783 target-blind current-public feature source did not become ready.'
}

Write-Host 'Gemini public feature source ready:'
Write-Host "  $CurrentUrl"
Write-Host '  targetBlind=true'
Write-Host '  source=live Binance spot/futures microstructure + live Chainlink'
Write-Host '  Predict.fun market frame is NOT required for this endpoint.'
