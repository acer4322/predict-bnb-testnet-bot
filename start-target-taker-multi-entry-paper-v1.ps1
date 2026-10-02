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

# This launcher is deliberately paper-only. Every live path stays disabled.
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
$env:PREDICT_MICRO_RAW_RETENTION_HOURS = "6"
$env:PREDICT_MICRO_SNAPSHOT_RETENTION_HOURS = "72"
$env:PREDICT_MICRO_LIQUIDITY_RETENTION_HOURS = "72"
$env:PREDICT_MICRO_SNAPSHOT_INTERVAL_MS = "250"

if (-not (Test-LocalService "http://127.0.0.1:8771/state")) {
    $Pid8771 = Get-ListeningProcessId 8771
    if ($Pid8771) {
        $Command = Get-ProcessCommandLine $Pid8771
        if (-not $Command.ToLowerInvariant().Contains("predict_bot.predict_fun_observer")) {
            throw "Port 8771 is occupied by an unrecognized process. PID=$Pid8771 command=$Command"
        }
    }
    else {
        Write-Host "Multi-entry lab: starting read-only Predict.fun observer on 8771."
        $Process = Start-Process -FilePath "python" `
            -ArgumentList @("-m", "predict_bot.predict_fun_observer") `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "target-taker-multi-entry-predict.stdout.log") `
            -RedirectStandardError (Join-Path $Data "target-taker-multi-entry-predict.stderr.log") -PassThru
        $Process.Id | Set-Content (Join-Path $Root ".target-taker-multi-entry-predict.pid")
    }
    Wait-LocalService "8771 Predict.fun observer" "http://127.0.0.1:8771/state" 45 (Join-Path $Data "target-taker-multi-entry-predict.stderr.log")
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
        Write-Host "Multi-entry lab: starting BTC public Taker signal collector on 8777."
        $Process = Start-Process -FilePath "python" `
            -ArgumentList @("-m", "predict_bot.predict_wallet_taker_signal_collector") `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "target-taker-multi-entry-signal.stdout.log") `
            -RedirectStandardError (Join-Path $Data "target-taker-multi-entry-signal.stderr.log") -PassThru
        $Process.Id | Set-Content (Join-Path $Root ".target-taker-multi-entry-signal.pid")
    }
    Wait-LocalService "8777 BTC public signal collector" "http://127.0.0.1:8777/state" 45 (Join-Path $Data "target-taker-multi-entry-signal.stderr.log")
}

$ShadowPid = Get-ListeningProcessId 8776
if ($ShadowPid) {
    $Command = Get-ProcessCommandLine $ShadowPid
    $Lower = $Command.ToLowerInvariant()
    $KnownWalletShadow = (
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_16") -or
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_17") -or
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_18") -or
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_19") -or
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_20") -or
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_21") -or
        $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_22")
    )
    if (-not $KnownWalletShadow) {
        throw "Port 8776 is occupied by an unrecognized process. Refusing to terminate it. PID=$ShadowPid command=$Command"
    }
    if ($Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_22") -and (Test-LocalService "http://127.0.0.1:8776/health" 5)) {
        Write-Host "Multi-entry lab: reusing Wallet Shadow v4.22 PID=$ShadowPid."
    }
    else {
        Write-Host "Multi-entry lab: replacing known Wallet Shadow PID=$ShadowPid with paper-only v4.22."
        & taskkill.exe /PID $ShadowPid /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to stop 8776 Wallet Shadow observer PID=$ShadowPid." }
        Start-Sleep -Milliseconds 500
        $ShadowPid = $null
    }
}

if (-not $ShadowPid) {
    Write-Host "Multi-entry lab: starting Wallet Shadow v4.22 on 8776."
    $Process = Start-Process -FilePath "python" `
        -ArgumentList @("-m", "predict_bot.predict_wallet_shadow_observer_v4_22") `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Data "target-taker-multi-entry-observer.stdout.log") `
        -RedirectStandardError (Join-Path $Data "target-taker-multi-entry-observer.stderr.log") -PassThru
    $Process.Id | Set-Content (Join-Path $Root ".target-taker-multi-entry-observer.pid")
}

Wait-LocalService "8776 Wallet Shadow v4.22" "http://127.0.0.1:8776/health" 60 (Join-Path $Data "target-taker-multi-entry-observer.stderr.log")
$Health = Get-JsonPayload "http://127.0.0.1:8776/health" 5
$State = if ($Health.state) { $Health.state } else { $Health }
$Version = [string]$State.version
if (-not $Version.Contains("V0_27_TARGET_TAKER_MULTI_ENTRY_PAPER_V1")) {
    throw "8776 is healthy but did not report the multi-entry paper observer. version=$Version"
}
if (-not [bool]$State.targetTakerPublicSideModelLoaded) {
    throw "8776 started but the frozen compact-side EBM did not load: $($State.targetTakerPublicSideModelError)"
}
if (-not $State.targetTakerMultiEntryPaperV1) {
    throw "8776 v4.22 did not expose targetTakerMultiEntryPaperV1 health state."
}
if (-not [bool]$State.paperOnly -or [bool]$State.liveOrdersAffected) {
    throw "Multi-entry experiment unexpectedly exposed live execution. Refusing to continue."
}

Write-Host "TARGET_TAKER_PUBLIC_SIDE_V1 multi-entry experiment is running PAPER ONLY."
Write-Host "  CONTROL     = SIDE_ONLY, first valid EBM entry only"
Write-Host "  EXPERIMENT  = every new public snapshot that remains EBM TRADE -> another fixed `$1 paper entry"
Write-Host "  Cooldown    = none"
Write-Host "  Side flip   = not required"
Write-Host "  Echtgeld    = disabled; experiment cohort is not in the live allowlist"
Write-Host "  State       = http://127.0.0.1:8776/state"
