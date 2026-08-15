param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
$SideModel = Join-Path $Root "data\research\target_taker_behavior_models_v1\side_up.joblib"
$EnginePort = 8781
$EngineBase = "http://127.0.0.1:$EnginePort"
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

function Unwrap-State($Payload) {
    if ($null -eq $Payload) { return $null }
    if ($null -ne $Payload.state) { return $Payload.state }
    return $Payload
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

function Import-UserEnvironment([string]$Name) {
    $Value = [Environment]::GetEnvironmentVariable($Name, "User")
    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        Set-Item -Path "Env:$Name" -Value $Value
    }
}

Import-UserEnvironment "PREDICT_FUN_API_KEY"
if ([string]::IsNullOrWhiteSpace($env:PREDICT_FUN_API_KEY)) {
    throw "PREDICT_FUN_API_KEY is required for the public Predict.fun observer."
}
if (-not (Test-Path $SideModel)) {
    throw "Frozen compact-side EBM is missing: $SideModel. Run: python tools/train_target_taker_behavior_v1.py"
}

# Producer/research process is permanently paper-only. All venue writes belong to the independent engine.
$env:PREDICT_LIVE_ENABLED = "false"
$env:PREDICT_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_WALLET_SHADOW_LEGACY_COHORTS_ENABLED = "false"
$env:PREDICT_TARGET_TAKER_LIVE_MODE = "paper"
$env:PREDICT_TARGET_TAKER_LIVE_VENUE = "predictfun"
$env:PREDICT_TARGET_TAKER_LIVE_NOTIONAL_USDT = "1"
$env:PREDICT_TARGET_TAKER_LIVE_MAX_PRICE_DRIFT = "0.02"
$env:PREDICT_TARGET_TAKER_LIVE_COHORT = "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY"
$env:PREDICT_ECHTGELD_ENGINE_URL = $EngineBase
$env:PREDICT_MICRO_RAW_RETENTION_HOURS = "6"
$env:PREDICT_MICRO_SNAPSHOT_RETENTION_HOURS = "72"
$env:PREDICT_MICRO_LIQUIDITY_RETENTION_HOURS = "72"
$env:PREDICT_MICRO_SNAPSHOT_INTERVAL_MS = "250"

# Start/reuse only the read-only dependencies needed by v4.23. Do not bootstrap v4.22 first.
if (-not (Test-LocalService "http://127.0.0.1:8771/state")) {
    $Pid8771 = Get-ListeningProcessId 8771
    if ($Pid8771) {
        $Command = Get-ProcessCommandLine $Pid8771
        if (-not $Command.ToLowerInvariant().Contains("predict_bot.predict_fun_observer")) {
            throw "Port 8771 is occupied by an unrecognized process. PID=$Pid8771 command=$Command"
        }
    }
    else {
        Write-Host "Target Taker producer: starting read-only Predict.fun observer on 8771."
        $Process = Start-Process -FilePath "python" `
            -ArgumentList @("-m", "predict_bot.predict_fun_observer") `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "target-taker-echtgeld-predict.stdout.log") `
            -RedirectStandardError (Join-Path $Data "target-taker-echtgeld-predict.stderr.log") -PassThru
        $Process.Id | Set-Content (Join-Path $Root ".target-taker-echtgeld-predict.pid")
    }
    Wait-LocalService "8771 Predict.fun observer" "http://127.0.0.1:8771/state" 45 (Join-Path $Data "target-taker-echtgeld-predict.stderr.log")
}

if (-not (Test-LocalService "http://127.0.0.1:8777/state")) {
    $Pid8777 = Get-ListeningProcessId 8777
    if ($Pid8777) {
        $Command = Get-ProcessCommandLine $Pid8777
        if (-not $Command.ToLowerInvariant().Contains("predict_bot.predict_wallet_taker_signal_collector")) {
            throw "Port 8777 is occupied by an unrecognized process. PID=$Pid8777 command=$Command"
        }
    }
    else {
        Write-Host "Target Taker producer: starting BTC public Taker signal collector on 8777."
        $Process = Start-Process -FilePath "python" `
            -ArgumentList @("-m", "predict_bot.predict_wallet_taker_signal_collector") `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "target-taker-echtgeld-signal.stdout.log") `
            -RedirectStandardError (Join-Path $Data "target-taker-echtgeld-signal.stderr.log") -PassThru
        $Process.Id | Set-Content (Join-Path $Root ".target-taker-echtgeld-signal.pid")
    }
    Wait-LocalService "8777 BTC public signal collector" "http://127.0.0.1:8777/state" 45 (Join-Path $Data "target-taker-echtgeld-signal.stderr.log")
}

$Existing = Get-ListeningProcessId 8776
if ($Existing) {
    $Command = Get-ProcessCommandLine $Existing
    $Lower = $Command.ToLowerInvariant()
    $IsV423 = $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_23")
    $KnownWalletShadow = (
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_16") -or
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_17") -or
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_18") -or
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_19") -or
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_20") -or
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_21") -or
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_22") -or
        $IsV423
    )
    if (-not $KnownWalletShadow) {
        throw "Port 8776 is occupied by an unrecognized process. Refusing to terminate it. PID=$Existing command=$Command"
    }

    if ($IsV423 -and (Test-LocalService "http://127.0.0.1:8776/health" 5)) {
        $Health = Unwrap-State (Get-JsonPayload "http://127.0.0.1:8776/health" 5)
        $Producer = $Health.targetTakerEchtgeldProducerV1
        $ReportedEngineUrl = if ($Producer) { [string]$Producer.engineUrl } else { "" }
        if (
            $Health -and
            ([string]$Health.version).Contains("V0_28_ECHTGELD_INTENT_PRODUCER_V1") -and
            $Producer -and
            [bool]$Producer.embeddedLiveDisabled -and
            $ReportedEngineUrl.TrimEnd('/') -eq $EngineBase
        ) {
            Write-Host "Target Taker producer: reusing healthy v4.23 PID=$Existing."
            Write-Host "  Engine handoff = $EngineBase/intent"
            Write-Host "  Embedded Echtgeld = disabled"
            return
        }
        if ($ReportedEngineUrl -and $ReportedEngineUrl.TrimEnd('/') -ne $EngineBase) {
            Write-Host "Target Taker producer: existing v4.23 points to stale engine URL $ReportedEngineUrl; replacing it."
        }
    }

    Write-Host "Target Taker producer: replacing recognized Wallet Shadow PID=$Existing with v4.23 intent producer."
    & taskkill.exe /PID $Existing /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to stop Wallet Shadow PID=$Existing." }
    Start-Sleep -Milliseconds 500
}

Write-Host "Target Taker producer: starting v4.23 intent producer on 8776."
$Process = Start-Process -FilePath "python" `
    -ArgumentList @("-m", "predict_bot.predict_wallet_shadow_observer_v4_23") `
    -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Data "target-taker-echtgeld-producer.stdout.log") `
    -RedirectStandardError (Join-Path $Data "target-taker-echtgeld-producer.stderr.log") -PassThru
$Process.Id | Set-Content (Join-Path $Root ".target-taker-echtgeld-producer.pid")

Wait-LocalService "8776 Target Taker Echtgeld producer" "http://127.0.0.1:8776/health" 60 (Join-Path $Data "target-taker-echtgeld-producer.stderr.log")
$Health = Unwrap-State (Get-JsonPayload "http://127.0.0.1:8776/health" 5)
$Version = [string]$Health.version
if (-not $Version.Contains("V0_28_ECHTGELD_INTENT_PRODUCER_V1")) {
    throw "8776 is healthy but is not the v4.23 Echtgeld producer. version=$Version"
}
if (-not [bool]$Health.paperOnly -or [bool]$Health.liveOrdersAffected) {
    throw "v4.23 unexpectedly reports embedded live execution. Refusing to continue."
}
$Producer = $Health.targetTakerEchtgeldProducerV1
if (-not $Producer -or -not [bool]$Producer.embeddedLiveDisabled) {
    throw "v4.23 did not confirm embedded-live isolation."
}
if (([string]$Producer.engineUrl).TrimEnd('/') -ne $EngineBase) {
    throw "v4.23 is healthy but points to the wrong Echtgeld Engine URL: $($Producer.engineUrl)"
}

$EngineOnline = Test-LocalService "$EngineBase/health" 3
Write-Host "Target Taker v4.23 producer is running."
Write-Host "  Research/Paper = 8776 (safe to restart independently)"
Write-Host "  Echtgeld Engine = $EnginePort ($(if ($EngineOnline) { 'ONLINE' } else { 'OFFLINE' }))"
Write-Host "  Embedded Echtgeld = disabled"
Write-Host "  Handoff = new SIDE_ONLY/HAZARD_SIDE paper event -> one localhost TradeIntent"
Write-Host "  Handoff retry = none; stale/lost signals are never replayed"
if (-not $EngineOnline) {
    Write-Warning "$EnginePort Echtgeld Engine is offline. Paper research continues normally; live intents cannot execute until the independent engine is started."
}
