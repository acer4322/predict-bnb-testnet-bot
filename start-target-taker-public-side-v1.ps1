param(
    [switch]$NoBrowser,
    [ValidateSet("paper", "live", "off")]
    [string]$TakerMode = "paper",
    [ValidateSet("predictfun", "binance")]
    [string]$ExecutionVenue = "predictfun",
    [ValidateRange(0.01, 100.0)]
    [double]$TakerNotionalUsdt = 1.0,
    [ValidateRange(0.0, 0.10)]
    [double]$MaxPriceDrift = 0.02,
    [ValidateSet("SIDE_ONLY", "HAZARD_SIDE")]
    [string]$LiveCohort = "SIDE_ONLY"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
$SideModel = Join-Path $Root "data\research\target_taker_behavior_models_v1\side_up.joblib"
$CohortId = if ($LiveCohort -eq "HAZARD_SIDE") {
    "TARGET_TAKER_PUBLIC_SIDE_V1_HAZARD_SIDE"
} else {
    "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY"
}
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

# Read credentials from User environment without ever printing their values.
@(
    "PREDICT_FUN_API_KEY",
    "PREDICT_FUN_PRIVATE_KEY",
    "PREDICT_FUN_PRIVY_PRIVATE_KEY",
    "PREDICT_FUN_ACCOUNT_ADDRESS",
    "PREDICT_FUN_JWT",
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "PREDICT_TARGET_TAKER_BINANCE_WALLET_ADDRESS",
    "PREDICT_TARGET_TAKER_BINANCE_WALLET_ID",
    "PREDICT_TARGET_TAKER_BINANCE_ACCOUNT_TYPE"
) | ForEach-Object { Import-UserEnvironment $_ }

if ([string]::IsNullOrWhiteSpace($env:PREDICT_FUN_API_KEY)) {
    throw "PREDICT_FUN_API_KEY is required for the public Predict.fun observer. Configure it as a User environment variable first."
}
if (-not (Test-Path $SideModel)) {
    throw "Frozen compact-side EBM is missing: $SideModel. Run: python tools/train_target_taker_behavior_v1.py"
}

if ($TakerMode -eq "live" -and $ExecutionVenue -eq "predictfun") {
    $HasPredictPrivateKey = -not [string]::IsNullOrWhiteSpace($env:PREDICT_FUN_PRIVATE_KEY) -or
        -not [string]::IsNullOrWhiteSpace($env:PREDICT_FUN_PRIVY_PRIVATE_KEY)
    if (-not $HasPredictPrivateKey) {
        throw "Predict.fun live mode requires PREDICT_FUN_PRIVATE_KEY or PREDICT_FUN_PRIVY_PRIVATE_KEY."
    }
}
if ($TakerMode -eq "live" -and $ExecutionVenue -eq "binance") {
    if ([string]::IsNullOrWhiteSpace($env:BINANCE_API_KEY) -or [string]::IsNullOrWhiteSpace($env:BINANCE_API_SECRET)) {
        throw "Binance live mode requires BINANCE_API_KEY and BINANCE_API_SECRET."
    }
    if ([string]::IsNullOrWhiteSpace($env:PREDICT_TARGET_TAKER_BINANCE_WALLET_ADDRESS) -or
        [string]::IsNullOrWhiteSpace($env:PREDICT_TARGET_TAKER_BINANCE_WALLET_ID)) {
        throw "Binance live mode requires PREDICT_TARGET_TAKER_BINANCE_WALLET_ADDRESS and PREDICT_TARGET_TAKER_BINANCE_WALLET_ID. Wallet selection is never guessed for real-money orders."
    }
}

# Keep every legacy/global live path disabled. Target Taker V1 has its own
# dedicated arm switch in v4.21 so enabling it cannot promote other strategies.
$env:PREDICT_LIVE_ENABLED = "false"
$env:PREDICT_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_WALLET_SHADOW_LEGACY_COHORTS_ENABLED = "false"
$env:PREDICT_TARGET_TAKER_LIVE_MODE = $TakerMode.ToLowerInvariant()
$env:PREDICT_TARGET_TAKER_LIVE_VENUE = $ExecutionVenue.ToLowerInvariant()
$env:PREDICT_TARGET_TAKER_LIVE_NOTIONAL_USDT = $TakerNotionalUsdt.ToString([Globalization.CultureInfo]::InvariantCulture)
$env:PREDICT_TARGET_TAKER_LIVE_MAX_PRICE_DRIFT = $MaxPriceDrift.ToString([Globalization.CultureInfo]::InvariantCulture)
$env:PREDICT_TARGET_TAKER_LIVE_COHORT = $CohortId
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
    $CanReuse = $false
    if ($Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_21")) {
        $ExistingHealth = Get-JsonPayload "http://127.0.0.1:8776/health" 5
        $ExistingState = if ($ExistingHealth.state) { $ExistingHealth.state } else { $ExistingHealth }
        $ExistingLive = $ExistingState.targetTakerLiveV1
        if ($ExistingLive) {
            $SameMode = ([string]$ExistingLive.mode).ToLowerInvariant() -eq $TakerMode.ToLowerInvariant()
            $SameVenue = ([string]$ExistingLive.venue).ToLowerInvariant() -eq $ExecutionVenue.ToLowerInvariant()
            $SameCohort = ([string]$ExistingLive.cohort) -eq $CohortId
            $SameNotional = [Math]::Abs(([double]$ExistingLive.notionalUsdt) - $TakerNotionalUsdt) -lt 0.0000001
            $SameDrift = [Math]::Abs(([double]$ExistingLive.maxPriceDrift) - $MaxPriceDrift) -lt 0.0000001
            $CanReuse = $SameMode -and $SameVenue -and $SameCohort -and $SameNotional -and $SameDrift
        }
    }
    if ($CanReuse) {
        Write-Host "Public-side Taker V1: reusing Wallet Shadow v4.21 PID=$ShadowPid with matching execution config."
    }
    else {
        Write-Host "Public-side Taker V1: replacing 8776 observer PID=$ShadowPid so v4.21 execution config is applied."
        & taskkill.exe /PID $ShadowPid /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to stop 8776 Wallet Shadow observer PID=$ShadowPid." }
        Start-Sleep -Milliseconds 500
        $ShadowPid = $null
    }
}

if (-not $ShadowPid) {
    Write-Host "Public-side Taker V1: starting Wallet Shadow v4.21 on 8776."
    $Process = Start-Process -FilePath "python" `
        -ArgumentList @("-m", "predict_bot.predict_wallet_shadow_observer_v4_21") `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Data "target-taker-public-side-observer.stdout.log") `
        -RedirectStandardError (Join-Path $Data "target-taker-public-side-observer.stderr.log") -PassThru
    $Process.Id | Set-Content (Join-Path $Root ".target-taker-public-side-observer.pid")
}

Wait-LocalService "8776 Wallet Shadow" "http://127.0.0.1:8776/health" 60 (Join-Path $Data "target-taker-public-side-observer.stderr.log")
$Health = Get-JsonPayload "http://127.0.0.1:8776/health" 5
$State = if ($Health.state) { $Health.state } else { $Health }
$Version = [string]$State.version
if (-not $Version.Contains("V0_26_TARGET_TAKER_LIVE_V1")) {
    throw "8776 is healthy but did not report Target Taker live-capable v4.21. version=$Version"
}
if (-not [bool]$State.targetTakerPublicSideModelLoaded) {
    throw "8776 started but the frozen compact-side EBM did not load: $($State.targetTakerPublicSideModelError)"
}
$LiveState = $State.targetTakerLiveV1
if (-not $LiveState) {
    throw "8776 v4.21 did not expose targetTakerLiveV1 health state."
}
if (([string]$LiveState.mode).ToLowerInvariant() -ne $TakerMode.ToLowerInvariant() -or
    ([string]$LiveState.venue).ToLowerInvariant() -ne $ExecutionVenue.ToLowerInvariant()) {
    throw "8776 Target Taker execution config mismatch after startup."
}

Write-Host "TARGET_TAKER_PUBLIC_SIDE_V1_EBM_FORWARD is running."
Write-Host "  SIDE_ONLY   = frozen compact-side EBM, selected-side score >= 0.60, max one entry per market"
Write-Host "  HAZARD_SIDE = same frozen EBM + Lifecycle V3 own-Maker-fill 5s time/price gate"
Write-Host "  Side artifact = data/research/target_taker_behavior_models_v1/side_up.joblib"
Write-Host "  Mode          = $($TakerMode.ToUpperInvariant())"
Write-Host "  Live cohort   = $CohortId"
Write-Host "  Venue         = $ExecutionVenue"
Write-Host "  Notional      = $($TakerNotionalUsdt.ToString('0.00', [Globalization.CultureInfo]::InvariantCulture)) USDT max target"
Write-Host "  Price drift   = $($MaxPriceDrift.ToString('0.000', [Globalization.CultureInfo]::InvariantCulture)) absolute max from signal ask"
Write-Host "  Legacy/global live strategy switches remain disabled."
Write-Host "  8771/8777 are public-data dependencies; 8778 is intentionally NOT required."
if ($TakerMode -eq "live") {
    Write-Warning "REAL-MONEY TARGET TAKER EXECUTION IS ARMED for one configured cohort only."
} else {
    Write-Host "No Target Taker real-money order will be submitted in this mode."
}

if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:8776/state"
}
