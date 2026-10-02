$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root 'data'
New-Item -ItemType Directory -Force -Path $Data | Out-Null
Start-Sleep -Seconds 8

function Log([string]$Message) {
    Add-Content -LiteralPath (Join-Path $Data 'post-reboot-collector-recovery.log') -Value ("{0} {1}" -f (Get-Date -Format 's'), $Message)
}

Log 'BEGIN post-reboot collector recovery'

# 8776 is now a strategy-free Target Official V2 collector.  Do not start the
# retired Wallet Shadow / public-side paper service here because it owns the
# same port and prevents the official BTC/ETH/BNB Target ledger from advancing.
try {
    & (Join-Path $Root 'start-target-taker-echtgeld-producer-v1.ps1') -NoBrowser
    Log '8776 Target Official V2 requested'
} catch { Log ("8776 Target Official V2 failed: " + $_.Exception.Message) }

try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8776/health' -TimeoutSec 5
    if ([string]$health.version -ne 'TARGET_WALLET_OFFICIAL_V2_LEGACY_HISTORY') {
        throw "unexpected 8776 version=$($health.version)"
    }
    if (-not [bool]$health.ok -or [bool]$health.liveOrdersAffected -or [bool]$health.strategyLogic) {
        throw "8776 health invariant failed: ok=$($health.ok) liveOrdersAffected=$($health.liveOrdersAffected) strategyLogic=$($health.strategyLogic)"
    }
    Log '8776 Target Official V2 health verified'
} catch { Log ("8776 Target Official V2 health verify failed: " + $_.Exception.Message) }

try {
    $key = [Environment]::GetEnvironmentVariable('PREDICT_FUN_API_KEY','User')
    if ($key) { $env:PREDICT_FUN_API_KEY = $key }
    $env:PREDICT_LIVE_ENABLED='false'
    $env:PREDICT_POLY_GAP_LIVE_ENABLED='false'
    $env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED='false'
    $env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED='false'
    $env:PREDICT_MICRO_RAW_RETENTION_HOURS='6'
    $env:PREDICT_MICRO_SNAPSHOT_RETENTION_HOURS='72'
    $env:PREDICT_MICRO_LIQUIDITY_RETENTION_HOURS='72'
    $env:PREDICT_MICRO_SNAPSHOT_INTERVAL_MS='250'
    if (-not (Get-NetTCPConnection -LocalPort 8778 -State Listen -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath 'python' -ArgumentList @('-m','predict_bot.predict_wallet_maker_book_inference_collector_v2_1') -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $Data 'recovery-btc8778.stdout.log') -RedirectStandardError (Join-Path $Data 'recovery-btc8778.stderr.log') | Out-Null
    }
    Log '8778 BTC5M requested'
} catch { Log ("8778 failed: " + $_.Exception.Message) }

foreach ($item in @(
    @{Script='start-wallet-eth-taker-research.ps1'; Args=@('-NoBrowser'); Label='ETH'},
    @{Script='start-target-multitimeframe-collectors-v1.ps1'; Args=@('-Quiet'); Label='MTF'},
    @{Script='start-target-bnb5m-collectors-v1.ps1'; Args=@('-NoBrowser'); Label='BNB'},
    @{Script='start-public-research-archive-v1.ps1'; Args=@(); Label='PUBLIC8783'},
    @{Script='start-predict-own-wallet-lifecycle-v1.ps1'; Args=@(); Label='OWN8795'},
    @{Script='start-polymarket-btc5m-external-hft-v1.ps1'; Args=@(); Label='POLY8811'}
)) {
    try {
        & (Join-Path $Root $item.Script) @($item.Args)
        Log ($item.Label + ' requested')
    } catch {
        Log ($item.Label + ' failed: ' + $_.Exception.Message)
    }
}

Log 'END post-reboot collector recovery; 8781 and 8810 intentionally not started'
