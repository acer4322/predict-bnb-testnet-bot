param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dashboard = Join-Path $Root "dashboard-v2"
$Data = Join-Path $Root "data"
New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Test-LocalService([string]$Url, [int]$TimeoutSeconds = 2) {
    try {
        $CodeText = & curl.exe --silent --output NUL `
            --connect-timeout 1 --max-time $TimeoutSeconds `
            --write-out "%{http_code}" $Url 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
        $Code = 0
        if (-not [int]::TryParse(([string]$CodeText).Trim(), [ref]$Code)) { return $false }
        return $Code -eq 200
    }
    catch { return $false }
}

function Get-ListeningProcessId([int]$Port) {
    try {
        $Connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -First 1
        if ($Connection) { return [int]$Connection.OwningProcess }
    }
    catch { }
    return $null
}

function Get-ProcessCommandLine([int]$ProcessId) {
    try {
        return [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop).CommandLine
    }
    catch { return "" }
}

function Get-DashboardV2WebStatus {
    $ListeningPid = Get-ListeningProcessId 4320
    if (-not $ListeningPid) {
        return @{ Status = "MISSING"; Pid = $null; Reason = "no listener" }
    }

    $CommandLine = Get-ProcessCommandLine $ListeningPid
    $CommandNeedle = $CommandLine.ToLowerInvariant()
    $DashboardNeedle = $Dashboard.ToLowerInvariant()
    $LooksLikeThisDashboard = $CommandNeedle.Contains($DashboardNeedle) -and $CommandNeedle.Contains("vite")
    if (-not $LooksLikeThisDashboard) {
        throw "Port 4320 is occupied by an unknown web process. PID=$ListeningPid command=$CommandLine"
    }

    try {
        $Process = Get-Process -Id $ListeningPid -ErrorAction Stop
        $ViteConfig = Get-Item (Join-Path $Dashboard "vite.config.ts") -ErrorAction Stop
        $ServiceManager = Get-Item (Join-Path $Dashboard "service-manager.ts") -ErrorAction SilentlyContinue
        $NewestConfigWrite = $ViteConfig.LastWriteTimeUtc
        if ($ServiceManager -and $ServiceManager.LastWriteTimeUtc -gt $NewestConfigWrite) {
            $NewestConfigWrite = $ServiceManager.LastWriteTimeUtc
        }
        if ($NewestConfigWrite -gt $Process.StartTime.ToUniversalTime()) {
            return @{
                Status = "STALE_CONFIG"
                Pid = $ListeningPid
                Reason = "Dashboard control/config files are newer than the running Vite process"
            }
        }
    }
    catch {
        return @{ Status = "CURRENT"; Pid = $ListeningPid; Reason = "listener identity verified" }
    }

    if (-not (Test-LocalService "http://127.0.0.1:4320" 2)) {
        return @{ Status = "UNRESPONSIVE"; Pid = $ListeningPid; Reason = "verified Vite listener is not answering its root endpoint" }
    }
    return @{ Status = "CURRENT"; Pid = $ListeningPid; Reason = "listener identity and config timestamp verified" }
}

function Stop-DashboardV2Web([string]$Reason) {
    $ListeningPid = Get-ListeningProcessId 4320
    if (-not $ListeningPid) { return }
    Write-Warning "Dashboard V2: restarting Vite listener on 4320 (PID=$ListeningPid): $Reason"
    Stop-Process -Id $ListeningPid -Force -ErrorAction Stop
    Remove-Item (Join-Path $Root ".web-v2.pid") -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 40; $i++) {
        if (-not (Get-ListeningProcessId 4320)) { return }
        Start-Sleep -Milliseconds 100
    }
    if (Get-ListeningProcessId 4320) {
        throw "Dashboard V2 could not release port 4320 after stopping the verified Vite listener."
    }
}

# The Vite process is now the localhost service-control host. Services started
# from Diagnostics / Services inherit this process environment, so import known
# user-scoped settings before launching Vite. Values are never printed or saved.
$UserEnvironment = @(
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_LIVE_API_KEY",
    "BINANCE_LIVE_API_SECRET",
    "PREDICT_FUN_API_KEY",
    "PREDICT_FUN_PRIVATE_KEY",
    "PREDICT_FUN_PRIVY_PRIVATE_KEY",
    "PREDICT_FUN_ACCOUNT_ADDRESS",
    "PREDICT_FUN_JWT",
    "PREDICT_TARGET_TAKER_BINANCE_WALLET_ADDRESS",
    "PREDICT_TARGET_TAKER_BINANCE_WALLET_ID",
    "PREDICT_TARGET_TAKER_BINANCE_ACCOUNT_TYPE",
    "PREDICT_TARGET_TAKER_BINANCE_SYMBOL",
    "PREDICT_TARGET_TAKER_BINANCE_BSC_RPC_URL",
    "PREDICT_TARGET_TAKER_BINANCE_USDT_ADDRESS",
    "PREDICT_ECHTGELD_BINANCE_BALANCE_ACCOUNT_TYPE",
    "PREDICT_LIVE_ENABLED",
    "PREDICT_POLY_GAP_LIVE_ENABLED",
    "PREDICT_ETH_POLY_GAP_LIVE_ENABLED",
    "PREDICT_BNB_POLY_GAP_LIVE_ENABLED",
    "PREDICT_MULTI_PREDICTION_ENABLED",
    "PREDICT_CROSS_ORACLE_STRATEGIES_ENABLED",
    "PREDICT_AUTO_REDEEM_ENABLED"
)
foreach ($Name in $UserEnvironment) {
    $Value = [Environment]::GetEnvironmentVariable($Name, "User")
    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        Set-Item -LiteralPath "Env:$Name" -Value $Value
    }
}

$WebStatus = Get-DashboardV2WebStatus
if ($WebStatus.Status -eq "STALE_CONFIG" -or $WebStatus.Status -eq "UNRESPONSIVE") {
    Stop-DashboardV2Web $WebStatus.Reason
    $WebStatus = @{ Status = "MISSING"; Pid = $null; Reason = "restart requested" }
}

if ($WebStatus.Status -eq "MISSING") {
    Write-Host "Dashboard V2: starting web/control host on 4320."
    $Web = Start-Process -FilePath "npm.cmd" -ArgumentList @("run", "dev") `
        -WorkingDirectory $Dashboard -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Data "web-v2.stdout.log") `
        -RedirectStandardError (Join-Path $Data "web-v2.stderr.log") -PassThru
    $Web.Id | Set-Content (Join-Path $Root ".web-v2.pid")
}
else {
    Write-Host "Dashboard V2: reusing current web/control host on 4320 (PID=$($WebStatus.Pid))."
}

$Deadline = (Get-Date).AddSeconds(30)
do {
    if (Test-LocalService "http://127.0.0.1:4320" 2) { break }
    Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $Deadline)

if (-not (Test-LocalService "http://127.0.0.1:4320" 3)) {
    if (Test-Path (Join-Path $Data "web-v2.stderr.log")) {
        Write-Warning "Dashboard V2 web failed. Last stderr lines:"
        Get-Content (Join-Path $Data "web-v2.stderr.log") -Tail 60 | ForEach-Object { Write-Warning $_ }
    }
    throw "Dashboard V2 web/control host did not become healthy on http://127.0.0.1:4320."
}

Write-Host ""
Write-Host "Dashboard V2 is ready: http://127.0.0.1:4320"
Write-Host "Backend services are intentionally NOT required at Dashboard startup."
Write-Host "Open Diagnostics / Services to start, stop, or restart managed services (8766-8781)."
Write-Host "Echtgeld 8781 remains PAUSED/DISARMED after process startup; Resume is still a separate action."

if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:4320/diagnostics"
}
