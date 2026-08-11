param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dashboard = Join-Path $Root "dashboard-v2"
$Data = Join-Path $Root "data"
New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Test-LocalService([string]$Url) {
    try {
        $Response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return $Response.StatusCode -eq 200
    }
    catch { return $false }
}

function Test-JsonService([string]$Url) {
    try {
        $Response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        if ($Response.StatusCode -ne 200) { return $false }
        $ContentType = [string]$Response.Headers["Content-Type"]
        if ($ContentType -notmatch "application/json") { return $false }
        $null = $Response.Content | ConvertFrom-Json -ErrorAction Stop
        return $true
    }
    catch { return $false }
}

function Get-ServiceVersion([int]$Port) {
    try {
        $Payload = Invoke-RestMethod -Uri "http://127.0.0.1:${Port}/state" -TimeoutSec 2
        if ($Payload.state -and $Payload.state.version) { return [string]$Payload.state.version }
        if ($Payload.version) { return [string]$Payload.version }
    }
    catch { }
    return $null
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
        $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        return [string]$Process.CommandLine
    }
    catch { return "" }
}

function Stop-StaleDashboardV2Web {
    $ListeningPid = Get-ListeningProcessId 4320
    if (-not $ListeningPid) { return $true }

    $CommandLine = Get-ProcessCommandLine $ListeningPid
    $DashboardNeedle = ($Dashboard -replace "\\", "\\").ToLowerInvariant()
    $CommandNeedle = ($CommandLine -replace "\\", "\\").ToLowerInvariant()
    $LooksLikeThisDashboard = $CommandNeedle.Contains($DashboardNeedle) -and $CommandNeedle.Contains("vite")

    if (-not $LooksLikeThisDashboard) {
        throw "Port 4320 is occupied by an unknown web process (PID=$ListeningPid; command=$CommandLine). Stop it manually before starting Dashboard V2."
    }

    Write-Warning "Dashboard V2: stale Vite listener detected on 4320 (PID=$ListeningPid); restarting it so the current vite.config.ts proxy is loaded."
    Stop-Process -Id $ListeningPid -Force -ErrorAction Stop
    Remove-Item (Join-Path $Root ".web-v2.pid") -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 30; $i++) {
        if (-not (Get-ListeningProcessId 4320)) { return $true }
        Start-Sleep -Milliseconds 100
    }
    return (-not (Get-ListeningProcessId 4320))
}

function Test-DashboardV2Proxy {
    return (Test-LocalService "http://127.0.0.1:4320") -and `
           (Test-JsonService "http://127.0.0.1:4320/bridge/realtime") -and `
           (Test-JsonService "http://127.0.0.1:4320/bridge/multi-market")
}

# Import only named user-scoped settings. Secrets remain environment variables;
# this script never writes credentials into config files or Dashboard storage.
$UserEnvironment = @(
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_LIVE_API_KEY",
    "BINANCE_LIVE_API_SECRET",
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
    if ($Value) { Set-Item -LiteralPath "Env:$Name" -Value $Value }
}

# ETH/BNB are now first-class Dashboard V2 Echtgeld markets.  Capability is ON
# by default unless the operator explicitly persisted false.  The V3 engine has
# a one-time safe-pause migration, so this never means automatic new BUYs.
if (-not $env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED) { $env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED = "true" }
if (-not $env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED) { $env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED = "true" }

if (-not $env:BINANCE_API_KEY -or -not $env:BINANCE_API_SECRET) {
    Write-Host "Enter the read-only Binance HMAC credentials for this session only."
    $env:BINANCE_API_KEY = Read-Host "BINANCE_API_KEY"
    $SecureSecret = Read-Host "BINANCE_API_SECRET" -AsSecureString
    $SecretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureSecret)
    try { $env:BINANCE_API_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($SecretPtr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($SecretPtr) }
}

$ApiOwned = $false
if (-not (Test-LocalService "http://127.0.0.1:8766/api/realtime")) {
    Write-Host "Dashboard V2: starting core supervisor (8766-8769)."
    $Api = Start-Process -FilePath "python" -ArgumentList @("-m", "predict_bot.supervisor") `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Data "api-v2.stdout.log") `
        -RedirectStandardError (Join-Path $Data "api-v2.stderr.log") -PassThru
    $Api.Id | Set-Content (Join-Path $Root ".api-v2.pid")
    $ApiOwned = $true
}
else {
    Write-Host "Dashboard V2: existing core API detected; not starting a duplicate."
}

if (Test-LocalService "http://127.0.0.1:8770/state") {
    $ExistingObserverVersion = Get-ServiceVersion 8770
    if ($ExistingObserverVersion -ne "MULTI_PREDICTION_OBSERVER_V2") {
        throw "Port 8770 is occupied by $ExistingObserverVersion. Stop the old/manual multi_prediction_observer first; ETH/BNB Echtgeld requires MULTI_PREDICTION_OBSERVER_V2."
    }
}

foreach ($AssetCheck in @(
    @{ Asset = "ETH"; Port = 8772 },
    @{ Asset = "BNB"; Port = 8773 }
)) {
    if (Test-LocalService "http://127.0.0.1:$($AssetCheck.Port)/state") {
        $ExistingVersion = Get-ServiceVersion $AssetCheck.Port
        if ($ExistingVersion -ne "POLY_GAP_MULTI_ASSET_LIVE_V3") {
            throw "Port $($AssetCheck.Port) is occupied by $ExistingVersion. Run .\stop-dashboard-v2.ps1 (or stop the old external multi-asset supervisor) before upgrading $($AssetCheck.Asset) to POLY_GAP_MULTI_ASSET_LIVE_V3."
        }
    }
}

$MultiOwned = $false
$MultiReady = (Test-LocalService "http://127.0.0.1:8770/state") -and `
              (Test-LocalService "http://127.0.0.1:8772/state") -and `
              (Test-LocalService "http://127.0.0.1:8773/state")
if (-not $MultiReady) {
    Write-Host "Dashboard V2: starting BTC/ETH/BNB observer V2 + isolated ETH/BNB live engines V3."
    $Multi = Start-Process -FilePath "python" -ArgumentList @("-m", "predict_bot.multi_asset_live_supervisor") `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Data "multi-live.stdout.log") `
        -RedirectStandardError (Join-Path $Data "multi-live.stderr.log") -PassThru
    $Multi.Id | Set-Content (Join-Path $Root ".multi-live.pid")
    $MultiOwned = $true
}
else {
    Write-Host "Dashboard V2: existing current 8770/8772/8773 services detected."
}

$WebOwned = $false
$WebRootReady = Test-LocalService "http://127.0.0.1:4320"
if ($WebRootReady -and -not (Test-DashboardV2Proxy)) {
    if (-not (Stop-StaleDashboardV2Web)) {
        throw "Dashboard V2 could not stop the stale Vite listener on 4320."
    }
    $WebRootReady = $false
}

if (-not $WebRootReady) {
    $Web = Start-Process -FilePath "npm.cmd" -ArgumentList @("run", "dev") `
        -WorkingDirectory $Dashboard -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Data "web-v2.stdout.log") `
        -RedirectStandardError (Join-Path $Data "web-v2.stderr.log") -PassThru
    $Web.Id | Set-Content (Join-Path $Root ".web-v2.pid")
    $WebOwned = $true
}
else {
    Write-Host "Dashboard V2: existing current web/proxy server detected on 4320."
}

$Deadline = (Get-Date).AddSeconds(120)
$Required = @(
    @{ Name = "8766"; Url = "http://127.0.0.1:8766/api/realtime" },
    @{ Name = "8767 Poly"; Url = "http://127.0.0.1:8767/state" },
    @{ Name = "8768 Strategies"; Url = "http://127.0.0.1:8768/state" },
    @{ Name = "8769 BTC"; Url = "http://127.0.0.1:8769/state" },
    @{ Name = "8770 observer"; Url = "http://127.0.0.1:8770/state" },
    @{ Name = "8772 ETH"; Url = "http://127.0.0.1:8772/state" },
    @{ Name = "8773 BNB"; Url = "http://127.0.0.1:8773/state" },
    @{ Name = "4320 Dashboard V2"; Url = "http://127.0.0.1:4320" }
)
while ((Get-Date) -lt $Deadline) {
    $Missing = @($Required | Where-Object { -not (Test-LocalService $_.Url) })
    if ($Missing.Count -eq 0 -and (Test-DashboardV2Proxy)) { break }
    Start-Sleep -Milliseconds 500
}
$Missing = @($Required | Where-Object { -not (Test-LocalService $_.Url) })
if ($Missing.Count -gt 0) {
    $Names = ($Missing | ForEach-Object { $_.Name }) -join ", "
    throw "Dashboard V2 startup incomplete: $Names. Check data\api-v2.stderr.log, data\multi-live.stderr.log, data\web-v2.stderr.log."
}

$BridgeUrls = @(
    @{ Name = "8766"; Url = "http://127.0.0.1:4320/bridge/realtime" },
    @{ Name = "8767"; Url = "http://127.0.0.1:4320/bridge/cross-oracle" },
    @{ Name = "8768"; Url = "http://127.0.0.1:4320/bridge/strategies" },
    @{ Name = "8769"; Url = "http://127.0.0.1:4320/bridge/poly-gap" },
    @{ Name = "8770"; Url = "http://127.0.0.1:4320/bridge/multi-market" },
    @{ Name = "8772"; Url = "http://127.0.0.1:4320/bridge/eth-live" },
    @{ Name = "8773"; Url = "http://127.0.0.1:4320/bridge/bnb-live" }
)
$BadBridges = @($BridgeUrls | Where-Object { -not (Test-JsonService $_.Url) })
if ($BadBridges.Count -gt 0) {
    $Names = ($BadBridges | ForEach-Object { $_.Name }) -join ", "
    throw "Dashboard V2 proxy returned non-JSON/unhealthy responses for: $Names. The web server is not running the current vite.config.ts."
}

$ObserverVersion = Get-ServiceVersion 8770
$EthVersion = Get-ServiceVersion 8772
$BnbVersion = Get-ServiceVersion 8773
if ($ObserverVersion -ne "MULTI_PREDICTION_OBSERVER_V2") {
    throw "8770 became ready as $ObserverVersion, but live trading requires MULTI_PREDICTION_OBSERVER_V2."
}
if ($EthVersion -ne "POLY_GAP_MULTI_ASSET_LIVE_V3" -or $BnbVersion -ne "POLY_GAP_MULTI_ASSET_LIVE_V3") {
    throw "ETH/BNB engine version mismatch after startup: ETH=$EthVersion BNB=$BnbVersion; expected POLY_GAP_MULTI_ASSET_LIVE_V3."
}

Write-Host "Dashboard V2 ready: http://localhost:4320"
Write-Host "BTC live state: http://127.0.0.1:8769/state"
Write-Host "ETH live state: http://127.0.0.1:8772/state"
Write-Host "BNB live state: http://127.0.0.1:8773/state"
Write-Host "Dashboard V2 JSON bridges verified: 8766/8767/8768/8769/8770/8772/8773."
Write-Host "Echtgeld WRITE controls are localhost-only and require the current Vite session token."
Write-Host "ETH/BNB Echtgeld capability is available; V3 first startup force-pauses new entries until explicit Dashboard Resume."

$EthMaster = $env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED -match '^(1|true|yes|on)$'
$BnbMaster = $env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED -match '^(1|true|yes|on)$'
if (-not $EthMaster) { Write-Warning "ETH master explicitly OFF: set PREDICT_ETH_POLY_GAP_LIVE_ENABLED=true before Echtgeld can Resume." }
if (-not $BnbMaster) { Write-Warning "BNB master explicitly OFF: set PREDICT_BNB_POLY_GAP_LIVE_ENABLED=true before Echtgeld can Resume." }

if (-not $NoBrowser) { Start-Process "http://localhost:4320/live-markets" }
Write-Host "READY: Dashboard V2 is running in the background."
