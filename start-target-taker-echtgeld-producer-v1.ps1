param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Test-LocalService([string]$Url, [int]$TimeoutSeconds = 2) {
    try {
        $Code = & curl.exe --silent --output NUL --connect-timeout 1 --max-time $TimeoutSeconds --write-out "%{http_code}" $Url 2>$null
        return $LASTEXITCODE -eq 0 -and ([string]$Code).Trim() -eq "200"
    }
    catch { return $false }
}

function Get-JsonPayload([string]$Url, [int]$TimeoutSeconds = 3) {
    try {
        $Body = & curl.exe --silent --fail --connect-timeout 1 --max-time $TimeoutSeconds --header "Accept: application/json" $Url 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        $Text = ($Body -join "`n")
        if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
        return $Text | ConvertFrom-Json -ErrorAction Stop
    }
    catch { return $null }
}

function Get-ListeningProcessId([int]$Port) {
    try {
        $Connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($Connection) { return [int]$Connection.OwningProcess }
    }
    catch { }
    return $null
}

function Get-ProcessCommandLine([int]$ProcessId) {
    try { return [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop).CommandLine }
    catch { return "" }
}

function Wait-LocalService([string]$Name, [string]$Url, [int]$Seconds, [string]$ErrorLog) {
    $Deadline = (Get-Date).AddSeconds($Seconds)
    do {
        if (Test-LocalService $Url 3) { return }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $Deadline)
    if (Test-Path $ErrorLog) {
        Write-Warning "$Name failed. Last stderr lines:"
        Get-Content $ErrorLog -Tail 60 | ForEach-Object { Write-Warning $_ }
    }
    throw "$Name did not become healthy at $Url. Check $ErrorLog."
}

$Existing = Get-ListeningProcessId 8776
if ($Existing) {
    $Command = Get-ProcessCommandLine $Existing
    $Lower = $Command.ToLowerInvariant()
    if ($Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_23") -and (Test-LocalService "http://127.0.0.1:8776/health" 5)) {
        Write-Host "Target Taker producer: reusing healthy v4.23 PID=$Existing."
        $Health = Get-JsonPayload "http://127.0.0.1:8776/health" 5
        if (-not $Health.targetTakerEchtgeldProducerV1) {
            throw "8776 v4.23 is healthy but producer diagnostics are missing."
        }
        Write-Host "  Engine handoff = http://127.0.0.1:8780/intent"
        Write-Host "  Embedded Echtgeld = disabled"
        return
    }
    if ($Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_23")) {
        Write-Host "Target Taker producer: stopping recognized unhealthy v4.23 PID=$Existing."
        & taskkill.exe /PID $Existing /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to stop unhealthy v4.23 PID=$Existing." }
        Start-Sleep -Milliseconds 500
    }
}

# Bootstrap the exact same paper research dependencies and v4.22 schema chain.
# This script is deliberately paper-only and never starts/stops port 8780.
& (Join-Path $Root "start-target-taker-multi-entry-paper-v1.ps1") -NoBrowser

$ShadowPid = Get-ListeningProcessId 8776
if (-not $ShadowPid) {
    throw "Paper bootstrap completed but 8776 is not listening."
}
$Command = Get-ProcessCommandLine $ShadowPid
$Lower = $Command.ToLowerInvariant()
if (-not $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_22")) {
    throw "Expected paper v4.22 bootstrap on 8776 before producer swap. PID=$ShadowPid command=$Command"
}

Write-Host "Target Taker producer: replacing paper v4.22 with v4.23 intent producer."
& taskkill.exe /PID $ShadowPid /T /F | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to stop v4.22 PID=$ShadowPid." }
Start-Sleep -Milliseconds 500

# Keep every embedded/global live path disabled. Real orders belong only to 8780.
$env:PREDICT_LIVE_ENABLED = "false"
$env:PREDICT_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_TARGET_TAKER_LIVE_MODE = "paper"
$env:PREDICT_ECHTGELD_ENGINE_URL = "http://127.0.0.1:8780"

$Process = Start-Process -FilePath "python" `
    -ArgumentList @("-m", "predict_bot.predict_wallet_shadow_observer_v4_23") `
    -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Data "target-taker-echtgeld-producer.stdout.log") `
    -RedirectStandardError (Join-Path $Data "target-taker-echtgeld-producer.stderr.log") -PassThru
$Process.Id | Set-Content (Join-Path $Root ".target-taker-echtgeld-producer.pid")

Wait-LocalService "8776 Target Taker Echtgeld producer" "http://127.0.0.1:8776/health" 60 (Join-Path $Data "target-taker-echtgeld-producer.stderr.log")
$Health = Get-JsonPayload "http://127.0.0.1:8776/health" 5
$Version = [string]$Health.version
if (-not $Version.Contains("V0_28_ECHTGELD_INTENT_PRODUCER_V1")) {
    throw "8776 is healthy but is not the v4.23 Echtgeld producer. version=$Version"
}
if (-not [bool]$Health.paperOnly -or [bool]$Health.liveOrdersAffected) {
    throw "v4.23 unexpectedly reports embedded live execution. Refusing to continue."
}
if (-not $Health.targetTakerEchtgeldProducerV1 -or -not [bool]$Health.targetTakerEchtgeldProducerV1.embeddedLiveDisabled) {
    throw "v4.23 did not confirm embedded-live isolation."
}

$EngineOnline = Test-LocalService "http://127.0.0.1:8780/health" 3
Write-Host "Target Taker v4.23 producer is running."
Write-Host "  Research/Paper = 8776 (safe to restart independently)"
Write-Host "  Echtgeld Engine = 8780 ($(if ($EngineOnline) { 'ONLINE' } else { 'OFFLINE' }))"
Write-Host "  Embedded Echtgeld = disabled"
Write-Host "  Handoff = new SIDE_ONLY/HAZARD_SIDE paper event -> one localhost TradeIntent"
Write-Host "  Handoff retry = none; stale/lost signals are never replayed"
if (-not $EngineOnline) {
    Write-Warning "8780 Echtgeld Engine is offline. Paper research continues normally; live intents cannot execute until the independent engine is started."
}
