param(
    [string]$MicroDb = 'data/microstructure.db',
    [string]$CrossDb = 'data/cross_oracle.db',
    [string]$Report = 'data/research/poly_market_indexed_history_v2_report.json',
    [string]$Start = '2026-08-16T03:40:00+08:00',
    [string]$End = '2026-08-16T11:35:00+08:00',
    [int]$CountCapPerMarket = 10000,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$started = Get-Date
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    function Stamp([string]$Message) {
        $elapsed = (Get-Date) - $started
        Write-Host ("[{0:HH:mm:ss} +{1:mm\:ss}] {2}" -f (Get-Date), $elapsed, $Message)
    }

    $Script = '.\tools\probe_poly_market_indexed_history_v2.py'
    $Test = '.\tests\test_probe_poly_market_indexed_history_v2.py'

    Stamp 'POLY MARKET INDEXED HISTORY V2'
    Write-Host 'Goal: recover safely-queryable historical Poly mid samples through the existing (binance_market_id, observed_at_ms) index.'
    Write-Host 'Guardrails: SQLite read-only/query-only, no DB copies, no full-table scan, bounded per-market counts.'

    if (-not (Test-Path $MicroDb)) { throw "Missing microstructure DB: $MicroDb" }
    if (-not (Test-Path $CrossDb)) { throw "Missing cross oracle DB: $CrossDb" }

    Stamp 'syntax check'
    python -m py_compile $Script
    if ($LASTEXITCODE -ne 0) { throw 'syntax check failed' }

    if (-not $SkipTests) {
        Stamp 'run focused composite-index / read-only tests'
        python -m pytest -q $Test
        if ($LASTEXITCODE -ne 0) { throw 'focused tests failed' }
    }

    Stamp 'probe stress-window market IDs from microstructure, then query Poly mids by composite index'
    python $Script `
        --micro-db $MicroDb `
        --cross-db $CrossDb `
        --report $Report `
        --start $Start `
        --end $End `
        --count-cap-per-market $CountCapPerMarket
    if ($LASTEXITCODE -ne 0) { throw 'indexed Poly history probe failed' }

    Stamp 'done'
    Write-Host "Report: $Report"
}
finally {
    Pop-Location
}
