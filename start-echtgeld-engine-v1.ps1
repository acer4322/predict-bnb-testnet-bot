param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
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

function Import-PersistentEnvironment([string]$Name) {
    $Value = [Environment]::GetEnvironmentVariable($Name, "User")
    if ([string]::IsNullOrWhiteSpace($Value)) {
        $Value = [Environment]::GetEnvironmentVariable($Name, "Machine")
    }
    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        Set-Item -Path "Env:$Name" -Value $Value
    }
}

function Test-EnvPair([string]$First, [string]$Second) {
    $FirstValue = [Environment]::GetEnvironmentVariable($First, "Process")
    $SecondValue = [Environment]::GetEnvironmentVariable($Second, "Process")
    return -not [string]::IsNullOrWhiteSpace($FirstValue) -and -not [string]::IsNullOrWhiteSpace($SecondValue)
}

@(
    "PREDICT_FUN_API_KEY",
    "PREDICT_FUN_PRIVATE_KEY",
    "PREDICT_FUN_PRIVY_PRIVATE_KEY",
    "PREDICT_FUN_ACCOUNT_ADDRESS",
    "PREDICT_FUN_JWT",
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "PREDICT_TARGET_TAKER_BINANCE_ACCOUNT_TYPE",
    "PREDICT_TARGET_TAKER_BINANCE_SYMBOL",
    "PREDICT_TARGET_TAKER_BINANCE_BSC_RPC_URL",
    "PREDICT_TARGET_TAKER_BINANCE_USDT_ADDRESS",
    "PREDICT_ECHTGELD_BINANCE_BALANCE_ACCOUNT_TYPE",
    "PREDICT_ECHTGELD_SETTLEMENT_DB",
    "PREDICT_ECHTGELD_AUTO_REDEEM"
) | ForEach-Object { Import-PersistentEnvironment $_ }

$PredictApiReady = -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("PREDICT_FUN_API_KEY", "Process"))
$PredictPrivateReady = -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("PREDICT_FUN_PRIVATE_KEY", "Process")) -or -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("PREDICT_FUN_PRIVY_PRIVATE_KEY", "Process"))
$BinanceApiReady = Test-EnvPair "BINANCE_API_KEY" "BINANCE_API_SECRET"
Write-Host "Echtgeld credential load: Predict=$($PredictApiReady -and $PredictPrivateReady) BinanceAPI=$BinanceApiReady BinanceWallet=API_DISCOVERY(wallet/list) (values hidden)"

$env:PREDICT_ECHTGELD_ENGINE_HOST = "127.0.0.1"
$env:PREDICT_ECHTGELD_ENGINE_PORT = "$EnginePort"
$ReusedExistingEngine = $false

$EnginePid = Get-ListeningProcessId $EnginePort
if ($EnginePid) {
    $Command = Get-ProcessCommandLine $EnginePid
    $Lower = $Command.ToLowerInvariant()
    $IsV4 = $Lower.Contains("predict_bot.echtgeld_engine_v4")
    $IsV3 = $Lower.Contains("predict_bot.echtgeld_engine_v3")
    $IsV2 = $Lower.Contains("predict_bot.echtgeld_engine_v2")
    $IsV1 = $Lower.Contains("predict_bot.echtgeld_engine_v1")
    if (-not $IsV4 -and -not $IsV3 -and -not $IsV2 -and -not $IsV1) {
        throw "Port $EnginePort is occupied by an unrecognized process. Refusing to terminate it. PID=$EnginePid command=$Command"
    }

    $Healthy = Test-LocalService "$EngineBase/health" 5
    $ExistingHealth = if ($Healthy) { Get-JsonPayload "$EngineBase/health" 5 } else { $null }
    if ($IsV4 -and $Healthy -and ([string]$ExistingHealth.version).Contains("POLY_FAST_V1")) {
        if ([bool]$ExistingHealth.armed) {
            $ReusedExistingEngine = $true
            Write-Warning "Echtgeld Engine V4 is LIVE ARMED on PID=$EnginePid. It was left untouched."
        }
        else {
            Write-Host "Echtgeld Engine V4: healthy but PAUSED; restarting PID=$EnginePid to reload credentials/environment."
            & taskkill.exe /PID $EnginePid /T /F | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Failed to stop PAUSED Echtgeld Engine PID=$EnginePid." }
            Start-Sleep -Milliseconds 500
            $EnginePid = $null
        }
    }
    elseif (($IsV3 -or $IsV2) -and $Healthy -and [bool]$ExistingHealth.armed) {
        throw "Existing 8781 Echtgeld engine is LIVE ARMED. Pause it before migrating to Poly Fast gateway V4."
    }
    elseif ($IsV1 -and $Healthy -and [bool]$ExistingHealth.armed) {
        throw "A legacy Echtgeld Engine V1 is LIVE ARMED. Pause it before migration."
    }
    else {
        Write-Host "Echtgeld Engine: replacing recognized old/unhealthy engine PID=$EnginePid while not armed."
        & taskkill.exe /PID $EnginePid /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to stop recognized Echtgeld Engine PID=$EnginePid." }
        Start-Sleep -Milliseconds 500
        $EnginePid = $null
    }
}

if (-not $EnginePid) {
    Write-Host "Echtgeld Engine V4 + Poly Fast gateway: starting independent service on 127.0.0.1:$EnginePort."
    $Process = Start-Process -FilePath "python" `
        -ArgumentList @("-m", "predict_bot.echtgeld_engine_v4", "predict_bot.echtgeld_engine_v3", "predict_bot.echtgeld_engine_v2") `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Data "echtgeld-engine-v2.stdout.log") `
        -RedirectStandardError (Join-Path $Data "echtgeld-engine-v2.stderr.log") -PassThru
    $Process.Id | Set-Content (Join-Path $Root ".echtgeld-engine-v2.pid")
}

Wait-LocalService "$EnginePort Echtgeld Engine" "$EngineBase/health" 45 (Join-Path $Data "echtgeld-engine-v2.stderr.log")
$Health = Get-JsonPayload "$EngineBase/health" 5
if (-not $Health -or -not [bool]$Health.ok) {
    throw "$EnginePort responded but did not report a healthy Echtgeld Engine."
}
if (-not ([string]$Health.version).Contains("ECHTGELD_ENGINE_V2")) {
    throw "$EnginePort is healthy but version is not Echtgeld Engine V2-compatible: $($Health.version)"
}
if (-not [bool]$Health.polyFastGatewayEnabled) {
    throw "$EnginePort is healthy but Poly Fast gateway is not enabled. version=$($Health.version)"
}
if ([bool]$Health.armed -and -not $ReusedExistingEngine) {
    throw "A newly started Echtgeld Engine unexpectedly reports ARMED. Refusing to continue."
}
if ([bool]$Health.armed -and $ReusedExistingEngine) {
    Write-Warning "Existing Echtgeld Engine is currently LIVE ARMED and was left untouched."
}

Write-Host "Echtgeld Engine V4 is running independently."
Write-Host "  Runtime : $($Health.runtimeStatus)"
Write-Host "  Health  : $EngineBase/health"
Write-Host "  State   : $EngineBase/state"
Write-Host "  Poly    : POST $EngineBase/poly-intent (8792 producer only)"
Write-Host "  DB      : data/echtgeld_engine_v1.db (existing durable ledger retained)"
Write-Host "  Balance : Binance Prediction payment-options + MPC safety balance"
Write-Host "  Risk    : existing Pause/Resume + durable stop-loss admission/pre-venue fences"
Write-Host "  Orders  : 8781 is the only real-money venue owner; signed MARKET/FOK + reconciliation"
Write-Host "  Redeem  : existing 4310 durable no-blind-retry lifecycle continues while PAUSED"
Write-Host "  Safety  : a new engine starts PAUSED; queued/ambiguous orders are never replayed after restart"
Write-Host "  Assets  : Poly Fast BTC/ETH/BNB symbols are selected per intent inside 8781"

if (-not $NoBrowser) {
    Write-Host "Dashboard control page: http://127.0.0.1:4320/echtgeld.html"
}
