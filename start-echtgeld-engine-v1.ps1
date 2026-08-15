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
    "PREDICT_TARGET_TAKER_BINANCE_USDT_CONTRACT"
) | ForEach-Object { Import-UserEnvironment $_ }

$env:PREDICT_ECHTGELD_ENGINE_HOST = "127.0.0.1"
$env:PREDICT_ECHTGELD_ENGINE_PORT = "8780"

$EnginePid = Get-ListeningProcessId 8780
if ($EnginePid) {
    $Command = Get-ProcessCommandLine $EnginePid
    $Lower = $Command.ToLowerInvariant()
    if (-not $Lower.Contains("predict_bot.echtgeld_engine_v1")) {
        throw "Port 8780 is occupied by an unrecognized process. Refusing to terminate it. PID=$EnginePid command=$Command"
    }
    if (Test-LocalService "http://127.0.0.1:8780/health" 5) {
        Write-Host "Echtgeld Engine V1: reusing healthy always-on process PID=$EnginePid."
    }
    else {
        Write-Host "Echtgeld Engine V1: replacing recognized but unhealthy process PID=$EnginePid."
        & taskkill.exe /PID $EnginePid /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to stop unhealthy Echtgeld Engine PID=$EnginePid." }
        Start-Sleep -Milliseconds 500
        $EnginePid = $null
    }
}

if (-not $EnginePid) {
    Write-Host "Echtgeld Engine V1: starting independent service on 127.0.0.1:8780."
    $Process = Start-Process -FilePath "python" `
        -ArgumentList @("-m", "predict_bot.echtgeld_engine_v1") `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Data "echtgeld-engine-v1.stdout.log") `
        -RedirectStandardError (Join-Path $Data "echtgeld-engine-v1.stderr.log") -PassThru
    $Process.Id | Set-Content (Join-Path $Root ".echtgeld-engine-v1.pid")
}

Wait-LocalService "8780 Echtgeld Engine" "http://127.0.0.1:8780/health" 45 (Join-Path $Data "echtgeld-engine-v1.stderr.log")
$Health = Get-JsonPayload "http://127.0.0.1:8780/health" 5
if (-not $Health -or -not [bool]$Health.ok) {
    throw "8780 responded but did not report a healthy Echtgeld Engine."
}
if (-not ([string]$Health.version).Contains("ECHTGELD_ENGINE_V1")) {
    throw "8780 is healthy but version is not Echtgeld Engine V1: $($Health.version)"
}
if ([bool]$Health.armed) {
    throw "Fresh/reused Echtgeld Engine unexpectedly reports ARMED. Open the control page and inspect it before continuing."
}

Write-Host "Echtgeld Engine V1 is running independently and PAUSED."
Write-Host "  Health : http://127.0.0.1:8780/health"
Write-Host "  State  : http://127.0.0.1:8780/state"
Write-Host "  DB     : data/echtgeld_engine_v1.db"
Write-Host "  Safety : engine restart never auto-arms or replays queued/ambiguous orders"
Write-Host "  Note   : restarting strategy observers does NOT stop this process"

if (-not $NoBrowser) {
    Write-Host "Open Dashboard V2 /echtgeld after the V2 frontend is running."
}
