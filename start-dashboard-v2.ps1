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

$MultiOwned = $false
$MultiReady = (Test-LocalService "http://127.0.0.1:8770/state") -and `
              (Test-LocalService "http://127.0.0.1:8772/state") -and `
              (Test-LocalService "http://127.0.0.1:8773/state")
if (-not $MultiReady) {
    Write-Host "Dashboard V2: starting BTC/ETH/BNB observer + isolated ETH/BNB live engines."
    $Multi = Start-Process -FilePath "python" -ArgumentList @("-m", "predict_bot.multi_asset_live_supervisor") `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Data "multi-live.stdout.log") `
        -RedirectStandardError (Join-Path $Data "multi-live.stderr.log") -PassThru
    $Multi.Id | Set-Content (Join-Path $Root ".multi-live.pid")
    $MultiOwned = $true
}
else {
    Write-Host "Dashboard V2: existing 8770/8772/8773 services detected."
}

$WebOwned = $false
if (-not (Test-LocalService "http://127.0.0.1:4320")) {
    $Web = Start-Process -FilePath "npm.cmd" -ArgumentList @("run", "dev") `
        -WorkingDirectory $Dashboard -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Data "web-v2.stdout.log") `
        -RedirectStandardError (Join-Path $Data "web-v2.stderr.log") -PassThru
    $Web.Id | Set-Content (Join-Path $Root ".web-v2.pid")
    $WebOwned = $true
}
else {
    Write-Host "Dashboard V2: existing web server detected on 4320."
}

$Deadline = (Get-Date).AddSeconds(120)
$Required = @(
    @{ Name = "8766"; Url = "http://127.0.0.1:8766/api/realtime" },
    @{ Name = "8769 BTC"; Url = "http://127.0.0.1:8769/state" },
    @{ Name = "8770 observer"; Url = "http://127.0.0.1:8770/state" },
    @{ Name = "8772 ETH"; Url = "http://127.0.0.1:8772/state" },
    @{ Name = "8773 BNB"; Url = "http://127.0.0.1:8773/state" },
    @{ Name = "4320 Dashboard V2"; Url = "http://127.0.0.1:4320" }
)
while ((Get-Date) -lt $Deadline) {
    $Missing = @($Required | Where-Object { -not (Test-LocalService $_.Url) })
    if ($Missing.Count -eq 0) { break }
    Start-Sleep -Milliseconds 500
}
$Missing = @($Required | Where-Object { -not (Test-LocalService $_.Url) })
if ($Missing.Count -gt 0) {
    $Names = ($Missing | ForEach-Object { $_.Name }) -join ", "
    throw "Dashboard V2 startup incomplete: $Names. Check data\api-v2.stderr.log, data\multi-live.stderr.log, data\web-v2.stderr.log."
}

Write-Host "Dashboard V2 ready: http://localhost:4320"
Write-Host "BTC live state: http://127.0.0.1:8769/state"
Write-Host "ETH live state: http://127.0.0.1:8772/state"
Write-Host "BNB live state: http://127.0.0.1:8773/state"
Write-Host "Echtgeld WRITE controls are localhost-only and require the current Vite session token."

$EthMaster = $env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED -match '^(1|true|yes|on)$'
$BnbMaster = $env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED -match '^(1|true|yes|on)$'
if (-not $EthMaster) { Write-Warning "ETH master is OFF: set user env PREDICT_ETH_POLY_GAP_LIVE_ENABLED=true before Echtgeld can Resume." }
if (-not $BnbMaster) { Write-Warning "BNB master is OFF: set user env PREDICT_BNB_POLY_GAP_LIVE_ENABLED=true before Echtgeld can Resume." }

if (-not $NoBrowser) { Start-Process "http://localhost:4320/live-markets" }
Write-Host "READY: Dashboard V2 is running in the background."
