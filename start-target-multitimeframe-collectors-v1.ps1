param([switch]$Quiet)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Import-UserEnvironment([string]$Name) {
    $Value = [Environment]::GetEnvironmentVariable($Name, "User")
    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        Set-Item -Path "Env:$Name" -Value $Value
    }
}

function Test-LocalService([string]$Url, [int]$TimeoutSeconds = 2) {
    try {
        $CodeText = & curl.exe --silent --output NUL --connect-timeout 1 --max-time $TimeoutSeconds --write-out "%{http_code}" $Url 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
        $Code = 0
        if (-not [int]::TryParse(([string]$CodeText).Trim(), [ref]$Code)) { return $false }
        return $Code -eq 200
    }
    catch { return $false }
}

function Get-ListeningProcessId([int]$Port) {
    try {
        $Connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($Connection) { return [int]$Connection.OwningProcess }
    }
    catch { }
    return $null
}

function Get-CommandLine([int]$ProcessId) {
    try { return [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop).CommandLine }
    catch { return "" }
}

@("PREDICT_FUN_API_KEY") | ForEach-Object { Import-UserEnvironment $_ }
if ([string]::IsNullOrWhiteSpace($env:PREDICT_FUN_API_KEY)) {
    throw "PREDICT_FUN_API_KEY is required for multi-timeframe Target collectors."
}

$Retention = if ($env:PREDICT_TARGET_MTF_RETENTION_HOURS) { $env:PREDICT_TARGET_MTF_RETENTION_HOURS } else { "72" }
$LiveLifecycle = if ($env:PREDICT_TARGET_MTF_LIVE_LIFECYCLE_ENABLED) { $env:PREDICT_TARGET_MTF_LIVE_LIFECYCLE_ENABLED } else { "false" }
$DiscoveryIdle = if ($env:PREDICT_TARGET_MTF_DISCOVERY_IDLE_SECONDS) { $env:PREDICT_TARGET_MTF_DISCOVERY_IDLE_SECONDS } else { "30" }
$ModuleNeedle = "predict_bot.predict_wallet_maker_book_inference_collector_multitimeframe"
$Specs = @(
    @{ Label="BTC15M"; Asset="BTC"; Timeframe="15M"; Port=8801; Db="wallet_maker_book_inference_btc15m.db"; Pid=".target-mtf-btc15m.pid" },
    @{ Label="ETH15M"; Asset="ETH"; Timeframe="15M"; Port=8802; Db="wallet_maker_book_inference_eth15m.db"; Pid=".target-mtf-eth15m.pid" },
    @{ Label="BTC1H"; Asset="BTC"; Timeframe="1H"; Port=8803; Db="wallet_maker_book_inference_btc1h.db"; Pid=".target-mtf-btc1h.pid" }
)

foreach ($Spec in $Specs) {
    $Existing = Get-ListeningProcessId ([int]$Spec.Port)
    if ($Existing) {
        $Command = Get-CommandLine $Existing
        if (-not $Command.ToLowerInvariant().Contains($ModuleNeedle.ToLowerInvariant())) {
            throw "Port $($Spec.Port) is occupied by an unrecognized process. $($Spec.Label) not started. PID=$Existing command=$Command"
        }
        if (-not $Quiet) { Write-Host "Reusing $($Spec.Label) collector on $($Spec.Port)." }
        continue
    }

    $env:PREDICT_TARGET_MTF_ASSET = [string]$Spec.Asset
    $env:PREDICT_TARGET_MTF_TIMEFRAME = [string]$Spec.Timeframe
    $env:PREDICT_TARGET_MTF_PORT = [string]$Spec.Port
    $env:PREDICT_TARGET_MTF_DB = Join-Path $Data ([string]$Spec.Db)
    $env:PREDICT_TARGET_MTF_RETENTION_HOURS = [string]$Retention
    $env:PREDICT_TARGET_MTF_LIVE_LIFECYCLE_ENABLED = [string]$LiveLifecycle
    $env:PREDICT_TARGET_MTF_DISCOVERY_IDLE_SECONDS = [string]$DiscoveryIdle
    $env:PREDICT_WALLET_MAKER_BOOK_STATE_CACHE_REFRESH_MS = "30000"
    $env:PREDICT_WALLET_MAKER_TARGET_INFERENCE_ENABLED = [string]$LiveLifecycle

    $Stdout = Join-Path $Data ("target-mtf-" + $Spec.Label.ToLowerInvariant() + ".stdout.log")
    $Stderr = Join-Path $Data ("target-mtf-" + $Spec.Label.ToLowerInvariant() + ".stderr.log")
    $Process = Start-Process -FilePath "python" `
        -ArgumentList @("-m", $ModuleNeedle) `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
    $Process.Id | Set-Content (Join-Path $Root ([string]$Spec.Pid))
    if (-not $Quiet) { Write-Host "Started $($Spec.Label) Target collector on $($Spec.Port), PID=$($Process.Id), retention=${Retention}h." }
}

$Deadline = (Get-Date).AddSeconds(40)
do {
    $Missing = @($Specs | Where-Object { -not (Test-LocalService ("http://127.0.0.1:" + $_.Port + "/health") 3) })
    if ($Missing.Count -eq 0) { break }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $Deadline)

if ($Missing.Count -gt 0) {
    $Names = ($Missing | ForEach-Object { $_.Label }) -join ", "
    throw "Multi-timeframe Target collector startup incomplete: $Names. Check data/target-mtf-*.stderr.log"
}

foreach ($Spec in $Specs) {
    try {
        $Payload = Invoke-RestMethod -Uri ("http://127.0.0.1:" + $Spec.Port + "/health") -TimeoutSec 5
        $State = if ($Payload.state) { $Payload.state } else { $Payload }
        if ([string]$State.asset -ne [string]$Spec.Asset -or [string]$State.timeframe -ne [string]$Spec.Timeframe) {
            throw "$($Spec.Label) identity mismatch: asset=$($State.asset) timeframe=$($State.timeframe)"
        }
        if (-not $Quiet) {
            Write-Host "$($Spec.Label): status=$($State.status), market=$($State.current.marketId), timeframe=$($State.timeframe), retention=$($State.retentionHours)h"
        }
    }
    catch {
        throw "$($Spec.Label) health identity validation failed: $_"
    }
}

if (-not $Quiet) { Write-Host "BTC15M + ETH15M + BTC1H Target data collectors are online." }
