param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
$SideModel = Join-Path $Root "data\research\target_taker_behavior_models_v1\side_up.joblib"
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
        Get-Content $ErrorLog -Tail 40 | ForEach-Object { Write-Warning $_ }
    }
    throw "$Name did not become healthy at $Url. Check $ErrorLog."
}

$UserPredictKey = [Environment]::GetEnvironmentVariable("PREDICT_FUN_API_KEY", "User")
if ($UserPredictKey) { $env:PREDICT_FUN_API_KEY = $UserPredictKey }
if ([string]::IsNullOrWhiteSpace($env:PREDICT_FUN_API_KEY)) {
    throw "PREDICT_FUN_API_KEY is required. Configure it as a User environment variable first."
}
if (-not (Test-Path $SideModel)) {
    throw "Frozen compact-side EBM is missing: $SideModel. Run: python tools/train_target_taker_behavior_v1.py"
}

# Paper research children must never inherit an enabled live runtime.
$env:PREDICT_LIVE_ENABLED = "false"
$env:PREDICT_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_WALLET_SHADOW_LEGACY_COHORTS_ENABLED = "false"
$env:PREDICT_MICRO_RAW_RETENTION_HOURS = "6"
$env:PREDICT_MICRO_SNAPSHOT_RETENTION_HOURS = "72"
$env:PREDICT_MICRO_LIQUIDITY_RETENTION_HOURS = "72"
$env:PREDICT_MICRO_SNAPSHOT_INTERVAL_MS = "250"

if (-not (Test-LocalService "http://127.0.0.1:8771/state")) {
    $ListenerPid = Get-ListeningProcessId 8771
    if ($ListenerPid) {
        $Command = Get-ProcessCommandLine $ListenerPid
        if (-not $Command.ToLowerInvariant().Contains("predict_bot.predict_fun_observer")) {
            throw "Port 8771 is occupied by an unrecognized process. PID=$ListenerPid command=$Command"
        }
    }
    else {
        Write-Host "Public-side Taker V1: starting read-only Predict.fun observer on 8771."
        $Process = Start-Process -FilePath "python" `
            -ArgumentList @("-m", "predict_bot.predict_fun_observer") `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "target-taker-public-side-predict.stdout.log") `
            -RedirectStandardError (Join-Path $Data "target-taker-public-side-predict.stderr.log") -PassThru
        $Process.Id | Set-Content (Join-Path $Root ".target-taker-public-side-predict.pid")
    }
    Wait-LocalService "8771 Predict.fun observer" "http://127.0.0.1:8771/state" 45 (Join-Path $Data "target-taker-public-side-predict.stderr.log")
}
else {
    Write-Host "Public-side Taker V1: reusing 8771 Predict.fun observer."
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
        Write-Host "Public-side Taker V1: starting BTC public Taker signal collector on 8777."
        $Process = Start-Process -FilePath "python" `
            -ArgumentList @("-m", "predict_bot.predict_wallet_taker_signal_collector") `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "target-taker-public-side-signal.stdout.log") `
            -RedirectStandardError (Join-Path $Data "target-taker-public-side-signal.stderr.log") -PassThru
        $Process.Id | Set-Content (Join-Path $Root ".target-taker-public-side-signal.pid")
    }
    Wait-LocalService "8777 BTC public Taker signal collector" "http://127.0.0.1:8777/state" 45 (Join-Path $Data "target-taker-public-side-signal.stderr.log")
}
else {
    Write-Host "Public-side Taker V1: reusing 8777 BTC public signal collector."
}

$ShadowPid = Get-ListeningProcessId 8776
if ($ShadowPid) {
    $Command = Get-ProcessCommandLine $ShadowPid
    $Lower = $Command.ToLowerInvariant()
    if ($Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_19")) {
        Write-Host "Public-side Taker V1: reusing v4.19 observer PID=$ShadowPid."
    }
    elseif (
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_16") -or
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_17") -or
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_18")
    ) {
        Write-Host "Public-side Taker V1: replacing older paper observer PID=$ShadowPid with v4.19."
        & taskkill.exe /PID $ShadowPid /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to stop the older 8776 Wallet Shadow observer PID=$ShadowPid." }
        Start-Sleep -Milliseconds 500
        $ShadowPid = $null
    }
    else {
        throw "Port 8776 is occupied by an unrecognized process. PID=$ShadowPid command=$Command"
    }
}

if (-not $ShadowPid) {
    Write-Host "Public-side Taker V1: starting Wallet Shadow v4.19 on 8776."
    $Process = Start-Process -FilePath "python" `
        -ArgumentList @("-m", "predict_bot.predict_wallet_shadow_observer_v4_19") `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Data "target-taker-public-side-observer.stdout.log") `
        -RedirectStandardError (Join-Path $Data "target-taker-public-side-observer.stderr.log") -PassThru
    $Process.Id | Set-Content (Join-Path $Root ".target-taker-public-side-observer.pid")
}

Wait-LocalService "8776 Wallet Shadow v4.19" "http://127.0.0.1:8776/health" 60 (Join-Path $Data "target-taker-public-side-observer.stderr.log")
$Health = Get-JsonPayload "http://127.0.0.1:8776/health" 5
$State = if ($Health.state) { $Health.state } else { $Health }
if (-not ([string]$State.version).Contains("V0_24_TARGET_TAKER_PUBLIC_SIDE_V1")) {
    throw "8776 is healthy but did not report the expected public-side V1 version. version=$($State.version)"
}
if (-not [bool]$State.targetTakerPublicSideModelLoaded) {
    throw "8776 v4.19 started but the frozen compact-side EBM did not load: $($State.targetTakerPublicSideModelError)"
}

Write-Host "TARGET_TAKER_PUBLIC_SIDE_V1 paper simulation is running."
Write-Host "  SIDE_ONLY   = frozen compact-side EBM, selected-side score >= 0.60, fixed `$1 max one entry per market"
Write-Host "  HAZARD_SIDE = same frozen EBM + Lifecycle V3 own-Maker-fill 5s time/price gate"
Write-Host "  Side artifact = data/research/target_taker_behavior_models_v1/side_up.joblib"
Write-Host "  8771/8777 are public-data dependencies; 8778 is intentionally NOT required."
Write-Host "  ETH 8779/8780 are untouched and can continue collecting."
Write-Host "No live trading runtime was enabled."

if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:8776/state"
}
