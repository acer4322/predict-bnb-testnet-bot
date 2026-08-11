param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dashboard = Join-Path $Root "dashboard-v2"
$Data = Join-Path $Root "data"
New-Item -ItemType Directory -Force -Path $Data | Out-Null
$script:PredictFunEnabled = $false

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

function Stop-StaleCoreSupervisor([string]$Version) {
    $ListenerProcessId = Get-ListeningProcessId 8769
    if (-not $ListenerProcessId) { return $false }

    try {
        $ListenerProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$ListenerProcessId" -ErrorAction Stop
    }
    catch {
        return $false
    }

    $SupervisorProcessId = [int]$ListenerProcess.ParentProcessId
    $ListenerCommand = [string]$ListenerProcess.CommandLine
    $SupervisorCommand = Get-ProcessCommandLine $SupervisorProcessId
    $LooksLikePolyGap = $ListenerCommand.ToLowerInvariant().Contains("predict_bot.poly_gap_live_v")
    $LooksLikeSupervisor = $SupervisorCommand.ToLowerInvariant().Contains("predict_bot.supervisor")

    if (-not ($LooksLikePolyGap -and $LooksLikeSupervisor)) {
        Write-Warning "Dashboard V2 found stale BTC version $Version on 8769, but its process tree is not a recognized predict_bot.supervisor tree. Listener PID=$ListenerProcessId command=$ListenerCommand; parent PID=$SupervisorProcessId command=$SupervisorCommand"
        return $false
    }

    Write-Warning "Dashboard V2: stale core supervisor detected ($Version on 8769; supervisor PID=$SupervisorProcessId). Stopping its process tree before starting the current V45 core."
    & taskkill.exe /PID $SupervisorProcessId /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Dashboard V2 failed to stop stale core supervisor PID=$SupervisorProcessId with taskkill /T /F."
    }
    Remove-Item (Join-Path $Root ".api-v2.pid") -Force -ErrorAction SilentlyContinue

    for ($i = 0; $i -lt 50; $i++) {
        $CoreStillListening = (Get-ListeningProcessId 8766) -or `
                              (Get-ListeningProcessId 8767) -or `
                              (Get-ListeningProcessId 8768) -or `
                              (Get-ListeningProcessId 8769)
        if (-not $CoreStillListening) { return $true }
        Start-Sleep -Milliseconds 100
    }
    return $false
}

function Stop-StaleMultiAssetSupervisor([int]$Port, [string]$Asset, [string]$Version) {
    $ListenerProcessId = Get-ListeningProcessId $Port
    if (-not $ListenerProcessId) { return $false }

    try {
        $ListenerProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$ListenerProcessId" -ErrorAction Stop
    }
    catch {
        return $false
    }

    $SupervisorProcessId = [int]$ListenerProcess.ParentProcessId
    $ListenerCommand = [string]$ListenerProcess.CommandLine
    $SupervisorCommand = Get-ProcessCommandLine $SupervisorProcessId
    $LooksLikeAssetEngine = $ListenerCommand.ToLowerInvariant().Contains("predict_bot.poly_gap_multi_asset_live_v")
    $LooksLikeSupervisor = $SupervisorCommand.ToLowerInvariant().Contains("predict_bot.multi_asset_live_supervisor")
    $ParentMissing = [string]::IsNullOrWhiteSpace($SupervisorCommand)

    if (-not $LooksLikeAssetEngine) {
        Write-Warning "Dashboard V2 found stale $Asset version $Version on $Port, but the listener is not a recognized predict_bot.poly_gap_multi_asset_live_v* process. Listener PID=$ListenerProcessId command=$ListenerCommand"
        return $false
    }

    if ($LooksLikeSupervisor) {
        Write-Warning "Dashboard V2: stale multi-asset supervisor detected ($Asset $Version on $Port; supervisor PID=$SupervisorProcessId). Stopping its process tree before starting the current V3 engines."
        & taskkill.exe /PID $SupervisorProcessId /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Dashboard V2 failed to stop stale multi-asset supervisor PID=$SupervisorProcessId with taskkill /T /F."
        }
        Remove-Item (Join-Path $Root ".multi-live.pid") -Force -ErrorAction SilentlyContinue
    }
    elseif ($ParentMissing) {
        # A previous supervisor can exit/crash while its Python child remains alive.
        # The listener command itself is enough to identify this as our stale asset
        # engine, so kill only that orphan instead of touching an unrelated process.
        Write-Warning "Dashboard V2: orphaned stale $Asset engine detected ($Version on $Port; listener PID=$ListenerProcessId; missing parent PID=$SupervisorProcessId). Stopping only the verified stale listener."
        & taskkill.exe /PID $ListenerProcessId /F | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Dashboard V2 failed to stop orphaned stale $Asset listener PID=$ListenerProcessId with taskkill /F."
        }
    }
    else {
        Write-Warning "Dashboard V2 found stale $Asset version $Version on $Port, but its live parent is not predict_bot.multi_asset_live_supervisor. Listener PID=$ListenerProcessId command=$ListenerCommand; parent PID=$SupervisorProcessId command=$SupervisorCommand"
        return $false
    }

    for ($i = 0; $i -lt 50; $i++) {
        if (-not (Get-ListeningProcessId $Port)) { return $true }
        Start-Sleep -Milliseconds 100
    }
    return $false
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
    $Ready = (Test-LocalService "http://127.0.0.1:4320") -and `
             (Test-JsonService "http://127.0.0.1:4320/bridge/realtime") -and `
             (Test-JsonService "http://127.0.0.1:4320/bridge/multi-market")
    if (-not $Ready) { return $false }
    if ($script:PredictFunEnabled) {
        return Test-JsonService "http://127.0.0.1:4320/bridge/predict-fun"
    }
    return $true
}

# Import only named user-scoped settings. Secrets remain environment variables;
# this script never writes credentials into config files or Dashboard storage.
$UserEnvironment = @(
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_LIVE_API_KEY",
    "BINANCE_LIVE_API_SECRET",
    "PREDICT_FUN_API_KEY",
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

$script:PredictFunEnabled = -not [string]::IsNullOrWhiteSpace($env:PREDICT_FUN_API_KEY)
if (-not $script:PredictFunEnabled) {
    Write-Warning "PREDICT_FUN_API_KEY is not configured; Predict.fun observer 8771 will remain disabled."
}

# ETH/BNB are now first-class Dashboard V2 Echtgeld markets. Capability is ON
# by default unless the operator explicitly persisted false. The V3 engine has
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

# If an older core is still alive from a previous launcher/manual session, only
# replace it automatically when 8769 is a recognized poly_gap child whose parent
# is predict_bot.supervisor. Unknown processes are never killed automatically.
if (Test-LocalService "http://127.0.0.1:8769/state") {
    $ExistingBtcVersion = Get-ServiceVersion 8769
    if ($ExistingBtcVersion -and $ExistingBtcVersion -ne "POLY_GAP_DEDICATED_LIVE_V45") {
        if (-not (Stop-StaleCoreSupervisor $ExistingBtcVersion)) {
            throw "Port 8769 is occupied by $ExistingBtcVersion and Dashboard V2 could not safely replace its process tree. Stop that process manually before upgrading BTC to POLY_GAP_DEDICATED_LIVE_V45."
        }
    }
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
    if (Test-LocalService "http://127.0.0.1:8769/state") {
        $ExistingBtcVersion = Get-ServiceVersion 8769
        if ($ExistingBtcVersion -ne "POLY_GAP_DEDICATED_LIVE_V45") {
            throw "Port 8769 is occupied by $ExistingBtcVersion. Stop the old core supervisor before upgrading BTC to POLY_GAP_DEDICATED_LIVE_V45."
        }
    }
}

# Upgrade stale ETH/BNB V2 process trees before validating the shared observer.
# A verified live supervisor is stopped as a tree; a verified orphaned V2/Vx child
# with no surviving parent is stopped by listener PID only.
foreach ($AssetCheck in @(
    @{ Asset = "ETH"; Port = 8772 },
    @{ Asset = "BNB"; Port = 8773 }
)) {
    if (Test-LocalService "http://127.0.0.1:$($AssetCheck.Port)/state") {
        $ExistingVersion = Get-ServiceVersion $AssetCheck.Port
        if ($ExistingVersion -and $ExistingVersion -ne "POLY_GAP_MULTI_ASSET_LIVE_V3") {
            if (-not (Stop-StaleMultiAssetSupervisor $AssetCheck.Port $AssetCheck.Asset $ExistingVersion)) {
                throw "Port $($AssetCheck.Port) is occupied by $ExistingVersion and Dashboard V2 could not safely replace its process tree. Stop that process manually before upgrading $($AssetCheck.Asset) to POLY_GAP_MULTI_ASSET_LIVE_V3."
            }
        }
    }
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
            throw "Port $($AssetCheck.Port) is still occupied by $ExistingVersion after stale-process cleanup; expected POLY_GAP_MULTI_ASSET_LIVE_V3."
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

$PredictFunOwned = $false
if ($script:PredictFunEnabled) {
    if (Test-LocalService "http://127.0.0.1:8771/state") {
        $ExistingPredictFunVersion = Get-ServiceVersion 8771
        if ($ExistingPredictFunVersion -ne "PREDICT_FUN_MULTI_OBSERVER_V1") {
            throw "Port 8771 is occupied by $ExistingPredictFunVersion. Stop the stale/manual Predict.fun observer before starting Dashboard V2."
        }
        Write-Host "Dashboard V2: existing Predict.fun observer detected on 8771."
    }
    else {
        Write-Host "Dashboard V2: starting read-only Predict.fun observer (8771)."
        $PredictFun = Start-Process -FilePath "python" -ArgumentList @("-m", "predict_bot.predict_fun_observer") `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "predict-fun-v2.stdout.log") `
            -RedirectStandardError (Join-Path $Data "predict-fun-v2.stderr.log") -PassThru
        $PredictFun.Id | Set-Content (Join-Path $Root ".predict-fun-v2.pid")
        $PredictFunOwned = $true
    }
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
if ($script:PredictFunEnabled) {
    $Required += @{ Name = "8771 Predict.fun"; Url = "http://127.0.0.1:8771/state" }
}
while ((Get-Date) -lt $Deadline) {
    $Missing = @($Required | Where-Object { -not (Test-LocalService $_.Url) })
    if ($Missing.Count -eq 0 -and (Test-DashboardV2Proxy)) { break }
    Start-Sleep -Milliseconds 500
}
$Missing = @($Required | Where-Object { -not (Test-LocalService $_.Url) })
if ($Missing.Count -gt 0) {
    $Names = ($Missing | ForEach-Object { $_.Name }) -join ", "
    throw "Dashboard V2 startup incomplete: $Names. Check data\api-v2.stderr.log, data\multi-live.stderr.log, data\predict-fun-v2.stderr.log, data\web-v2.stderr.log."
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
if ($script:PredictFunEnabled) {
    $BridgeUrls += @{ Name = "8771"; Url = "http://127.0.0.1:4320/bridge/predict-fun" }
}
$BadBridges = @($BridgeUrls | Where-Object { -not (Test-JsonService $_.Url) })
if ($BadBridges.Count -gt 0) {
    $Names = ($BadBridges | ForEach-Object { $_.Name }) -join ", "
    throw "Dashboard V2 proxy returned non-JSON/unhealthy responses for: $Names. The web server is not running the current vite.config.ts."
}

$BtcVersion = Get-ServiceVersion 8769
$ObserverVersion = Get-ServiceVersion 8770
$EthVersion = Get-ServiceVersion 8772
$BnbVersion = Get-ServiceVersion 8773
$PredictFunVersion = $null
if ($script:PredictFunEnabled) {
    $PredictFunVersion = Get-ServiceVersion 8771
}
if ($BtcVersion -ne "POLY_GAP_DEDICATED_LIVE_V45") {
    throw "BTC 8769 became ready as $BtcVersion; expected POLY_GAP_DEDICATED_LIVE_V45. Stop the stale core supervisor and restart Dashboard V2."
}
if ($ObserverVersion -ne "MULTI_PREDICTION_OBSERVER_V2") {
    throw "8770 became ready as $ObserverVersion, but live trading requires MULTI_PREDICTION_OBSERVER_V2."
}
if ($EthVersion -ne "POLY_GAP_MULTI_ASSET_LIVE_V3" -or $BnbVersion -ne "POLY_GAP_MULTI_ASSET_LIVE_V3") {
    throw "ETH/BNB engine version mismatch after startup: ETH=$EthVersion BNB=$BnbVersion; expected POLY_GAP_MULTI_ASSET_LIVE_V3."
}
if ($script:PredictFunEnabled -and $PredictFunVersion -ne "PREDICT_FUN_MULTI_OBSERVER_V1") {
    throw "Predict.fun 8771 became ready as $PredictFunVersion; expected PREDICT_FUN_MULTI_OBSERVER_V1."
}

Write-Host "Dashboard V2 ready: http://localhost:4320"
Write-Host "BTC live state: http://127.0.0.1:8769/state"
Write-Host "ETH live state: http://127.0.0.1:8772/state"
Write-Host "BNB live state: http://127.0.0.1:8773/state"
if ($script:PredictFunEnabled) {
    Write-Host "Predict.fun observer state: http://127.0.0.1:8771/state"
    Write-Host "Dashboard V2 JSON bridges verified: 8766/8767/8768/8769/8770/8771/8772/8773."
}
else {
    Write-Host "Dashboard V2 JSON bridges verified: 8766/8767/8768/8769/8770/8772/8773 (Predict.fun disabled: no API key)."
}
Write-Host "Echtgeld WRITE controls are localhost-only and require the current Vite session token."
Write-Host "ETH/BNB Echtgeld capability is available; V3 first startup force-pauses new entries until explicit Dashboard Resume."
Write-Host "Pinned Divergence strategy page: http://localhost:4320/pinned-divergence"

$EthMaster = $env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED -match '^(1|true|yes|on)$'
$BnbMaster = $env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED -match '^(1|true|yes|on)$'
if (-not $EthMaster) { Write-Warning "ETH master explicitly OFF: set PREDICT_ETH_POLY_GAP_LIVE_ENABLED=true before Echtgeld can Resume." }
if (-not $BnbMaster) { Write-Warning "BNB master explicitly OFF: set PREDICT_BNB_POLY_GAP_LIVE_ENABLED=true before Echtgeld can Resume." }

if (-not $NoBrowser) { Start-Process "http://localhost:4320/live-markets" }
Write-Host "READY: Dashboard V2 is running in the background."
