param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
$HazardModel = Join-Path $Root "data\research\target_maker_direct_models_v1\hazard_5s_compact.joblib"
$LevelModel = Join-Path $Root "data\research\target_maker_direct_models_v1\level_2ticks_compact.joblib"
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
        Get-Content $ErrorLog -Tail 50 | ForEach-Object { Write-Warning $_ }
    }
    throw "$Name did not become healthy at $Url. Check $ErrorLog."
}

$UserPredictKey = [Environment]::GetEnvironmentVariable("PREDICT_FUN_API_KEY", "User")
if ($UserPredictKey) { $env:PREDICT_FUN_API_KEY = $UserPredictKey }
if ([string]::IsNullOrWhiteSpace($env:PREDICT_FUN_API_KEY)) {
    throw "PREDICT_FUN_API_KEY is required. Configure it as a User environment variable first."
}
if (-not (Test-Path $HazardModel) -or -not (Test-Path $LevelModel)) {
    throw "Frozen Maker EBM artifacts are missing. Run: python tools/train_target_maker_direct_runtime_v1.py"
}

# Research children must never inherit an enabled live runtime.
$env:PREDICT_LIVE_ENABLED = "false"
$env:PREDICT_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_WALLET_SHADOW_LEGACY_COHORTS_ENABLED = "false"

if (-not (Test-LocalService "http://127.0.0.1:8771/state")) {
    $ListenerPid = Get-ListeningProcessId 8771
    if ($ListenerPid) {
        $Command = Get-ProcessCommandLine $ListenerPid
        if (-not $Command.ToLowerInvariant().Contains("predict_bot.predict_fun_observer")) {
            throw "Port 8771 is occupied by an unrecognized process. PID=$ListenerPid command=$Command"
        }
    }
    else {
        Write-Host "Maker EBM V1: starting read-only Predict.fun observer on 8771."
        $Process = Start-Process -FilePath "python" `
            -ArgumentList @("-m", "predict_bot.predict_fun_observer") `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "maker-ebm-v1-predict.stdout.log") `
            -RedirectStandardError (Join-Path $Data "maker-ebm-v1-predict.stderr.log") -PassThru
        $Process.Id | Set-Content (Join-Path $Root ".maker-ebm-v1-predict.pid")
    }
    Wait-LocalService "8771 Predict.fun observer" "http://127.0.0.1:8771/state" 45 (Join-Path $Data "maker-ebm-v1-predict.stderr.log")
}
else {
    Write-Host "Maker EBM V1: reusing 8771 Predict.fun observer."
}

if (-not (Test-LocalService "http://127.0.0.1:8777/state")) {
    $ListenerPid = Get-ListeningProcessId 8777
    if ($ListenerPid) {
        $Command = Get-ProcessCommandLine $ListenerPid
        if (-not $Command.ToLowerInvariant().Contains("predict_bot.predict_wallet_taker_signal_collector")) {
            throw "Port 8777 is occupied by an unrecognized process. PID=$ListenerPid command=$Command"
        }
    }
    else {
        Write-Host "Maker EBM V1: starting BTC public signal collector on 8777."
        $Process = Start-Process -FilePath "python" `
            -ArgumentList @("-m", "predict_bot.predict_wallet_taker_signal_collector") `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "maker-ebm-v1-signal.stdout.log") `
            -RedirectStandardError (Join-Path $Data "maker-ebm-v1-signal.stderr.log") -PassThru
        $Process.Id | Set-Content (Join-Path $Root ".maker-ebm-v1-signal.pid")
    }
    Wait-LocalService "8777 BTC public signal collector" "http://127.0.0.1:8777/state" 45 (Join-Path $Data "maker-ebm-v1-signal.stderr.log")
}
else {
    Write-Host "Maker EBM V1: reusing 8777 BTC public signal collector."
}

$ShadowPid = Get-ListeningProcessId 8776
if ($ShadowPid) {
    $Command = Get-ProcessCommandLine $ShadowPid
    $Lower = $Command.ToLowerInvariant()
    if ($Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_20")) {
        Write-Host "Maker EBM V1: reusing v4.20 observer PID=$ShadowPid."
    }
    elseif (
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_16") -or
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_17") -or
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_18") -or
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_19")
    ) {
        Write-Host "Maker EBM V1: replacing older paper observer PID=$ShadowPid with v4.20."
        & taskkill.exe /PID $ShadowPid /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to stop older 8776 Wallet Shadow observer PID=$ShadowPid." }
        Start-Sleep -Milliseconds 500
        $ShadowPid = $null
    }
    else {
        throw "Port 8776 is occupied by an unrecognized process. PID=$ShadowPid command=$Command"
    }
}

if (-not $ShadowPid) {
    Write-Host "Maker EBM V1: starting Wallet Shadow v4.20 on 8776."
    $Process = Start-Process -FilePath "python" `
        -ArgumentList @("-m", "predict_bot.predict_wallet_shadow_observer_v4_20") `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Data "maker-ebm-v1-observer.stdout.log") `
        -RedirectStandardError (Join-Path $Data "maker-ebm-v1-observer.stderr.log") -PassThru
    $Process.Id | Set-Content (Join-Path $Root ".maker-ebm-v1-observer.pid")
}

Wait-LocalService "8776 Wallet Shadow v4.20" "http://127.0.0.1:8776/health" 60 (Join-Path $Data "maker-ebm-v1-observer.stderr.log")
$Health = Get-JsonPayload "http://127.0.0.1:8776/health" 5
$State = if ($Health.state) { $Health.state } else { $Health }
if (-not ([string]$State.version).Contains("V0_25_MAKER_EBM_V1")) {
    throw "8776 is healthy but did not report Maker EBM v4.20. version=$($State.version)"
}
if (-not [bool]$State.makerEbmV1ModelsLoaded) {
    throw "8776 v4.20 started but Maker EBM artifacts failed to load: $($State.makerEbmV1ModelError)"
}

Write-Host "TARGET_MAKER_EBM_V1 forward paper A/B is running."
Write-Host "  MAKER_EBM_HAZARD_V1 = frozen 5s compact_public EBM controls WHEN; fixed 1-tick quote level"
Write-Host "  MAKER_EBM_LEVEL_V1  = level EBM controls WHERE; activity gate bypassed"
Write-Host "  TARGET_MAKER_EBM_V1 = frozen Hazard + Level EBMs combined"
Write-Host "  Side = each cohort's OWN paper inventory; Maker Side EBM is intentionally not promoted"
Write-Host "  Size = fixed 18 shares; strict later ask-touch fill proxy; no live orders"
Write-Host "  8778 is NOT required to run the paper strategy; it is only the forward inference/research collector."
Write-Host "  ETH 8779/8780 are untouched."

if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:8776/state"
}
