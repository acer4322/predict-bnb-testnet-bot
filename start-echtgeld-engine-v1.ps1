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

function Import-UserEnvironment([string]$Name) {
    $Value = [Environment]::GetEnvironmentVariable($Name, "User")
    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        Set-Item -Path "Env:$Name" -Value $Value
    }
}

# Credentials belong to this process, not to research observers. Never print values.
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
    "PREDICT_TARGET_TAKER_BINANCE_ACCOUNT_TYPE",
    "PREDICT_TARGET_TAKER_BINANCE_SYMBOL",
    "PREDICT_TARGET_TAKER_BINANCE_BSC_RPC_URL",
    "PREDICT_TARGET_TAKER_BINANCE_USDT_ADDRESS",
    "PREDICT_ECHTGELD_BINANCE_BALANCE_ACCOUNT_TYPE",
    "PREDICT_ECHTGELD_SETTLEMENT_DB"
) | ForEach-Object { Import-UserEnvironment $_ }

$env:PREDICT_ECHTGELD_ENGINE_HOST = "127.0.0.1"
$env:PREDICT_ECHTGELD_ENGINE_PORT = "$EnginePort"
$ReusedExistingEngine = $false

$EnginePid = Get-ListeningProcessId $EnginePort
if ($EnginePid) {
    $Command = Get-ProcessCommandLine $EnginePid
    $Lower = $Command.ToLowerInvariant()
    $IsV2 = $Lower.Contains("predict_bot.echtgeld_engine_v2")
    $IsV1 = $Lower.Contains("predict_bot.echtgeld_engine_v1")
    if (-not $IsV2 -and -not $IsV1) {
        throw "Port $EnginePort is occupied by an unrecognized process. Refusing to terminate it. PID=$EnginePid command=$Command"
    }

    $Healthy = Test-LocalService "$EngineBase/health" 5
    $ExistingHealth = if ($Healthy) { Get-JsonPayload "$EngineBase/health" 5 } else { $null }
    if ($IsV2 -and $Healthy -and ([string]$ExistingHealth.version).Contains("ECHTGELD_ENGINE_V2")) {
        $ReusedExistingEngine = $true
        Write-Host "Echtgeld Engine V2: reusing healthy always-on process PID=$EnginePid without changing runtime state."
    }
    elseif ($IsV1 -and $Healthy -and [bool]$ExistingHealth.armed) {
        throw "A legacy Echtgeld Engine V1 is LIVE ARMED on $EnginePort. It was NOT stopped. Pause it from the control page, then run this launcher again to migrate safely to V2."
    }
    else {
        Write-Host "Echtgeld Engine V2: replacing recognized old/unhealthy engine PID=$EnginePid while not armed."
        & taskkill.exe /PID $EnginePid /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to stop recognized Echtgeld Engine PID=$EnginePid." }
        Start-Sleep -Milliseconds 500
        $EnginePid = $null
    }
}

if (-not $EnginePid) {
    Write-Host "Echtgeld Engine V2: starting independent service on 127.0.0.1:$EnginePort."
    $Process = Start-Process -FilePath "python" `
        -ArgumentList @("-m", "predict_bot.echtgeld_engine_v2") `
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
    throw "$EnginePort is healthy but version is not Echtgeld Engine V2: $($Health.version)"
}
if ([bool]$Health.armed -and -not $ReusedExistingEngine) {
    throw "A newly started Echtgeld Engine unexpectedly reports ARMED. Refusing to continue."
}
if ([bool]$Health.armed -and $ReusedExistingEngine) {
    Write-Warning "Existing Echtgeld Engine is currently LIVE ARMED. It was left completely untouched. Use the dedicated control page to Pause if needed."
}

Write-Host "Echtgeld Engine V2 is running independently."
Write-Host "  Runtime: $($Health.runtimeStatus)"
Write-Host "  Health : $EngineBase/health"
Write-Host "  State  : $EngineBase/state"
Write-Host "  DB     : data/echtgeld_engine_v1.db (existing durable ledger retained)"
Write-Host "  Balance: Binance Prediction payment-options (4310 style) + separate MPC safety balance"
Write-Host "  PnL    : actual reconciled fills + official Target Taker settlements"
Write-Host "  Safety : a new engine starts PAUSED; queued/ambiguous orders are never replayed after restart"
Write-Host "  Note   : restarting strategy observers does NOT stop this process"

if (-not $NoBrowser) {
    Write-Host "Dashboard control page: http://127.0.0.1:4320/echtgeld.html"
}
