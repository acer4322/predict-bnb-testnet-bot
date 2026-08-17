param(
    [string]$Db = 'data/cross_oracle.db',
    [string]$Report = 'data/research/poly_slug_window_integrity_v1_report.json',
    [string]$Start = '2026-08-16T03:40:00+08:00',
    [string]$End = '2026-08-16T11:35:00+08:00',
    [int]$MaxRows = 200000,
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

    $Script = '.\tools\audit_poly_slug_window_integrity_v1.py'
    $Test = '.\tests\test_audit_poly_slug_window_integrity_v1.py'

    Stamp 'POLY SLUG WINDOW INTEGRITY V1'
    Write-Host 'Goal: determine whether raw Polymarket events rotated with the BTC 5m market encoded by market_slug.'
    Write-Host 'Eligibility rule: an event is clean only when slug_start <= source_timestamp_ms < slug_start + 5m.'
    Write-Host 'Market discovery is checked by primary-key slug point lookups; raw events use the source_timestamp_ms index.'
    Write-Host 'Guardrails: SQLite read-only/query-only, no DB copies, no writes, no full-table scans.'
    Write-Host 'Research only: this does not fit a model or promote a strategy.'

    if (-not (Test-Path $Db)) { throw "Missing cross oracle DB: $Db" }

    Stamp 'syntax check'
    python -m py_compile $Script
    if ($LASTEXITCODE -ne 0) { throw 'syntax check failed' }

    if (-not $SkipTests) {
        Stamp 'run slug-boundary / index / read-only invariants'
        python -m pytest -q $Test
        if ($LASTEXITCODE -ne 0) { throw 'focused tests failed' }
    }

    Stamp 'audit raw Poly event slugs against their encoded five-minute windows'
    python $Script `
        --db $Db `
        --report $Report `
        --start $Start `
        --end $End `
        --max-rows $MaxRows
    if ($LASTEXITCODE -ne 0) { throw 'Poly slug-window integrity audit failed' }

    Stamp 'done'
    Write-Host "Report: $Report"
    Write-Host 'Primary fields: summary.diagnosis, slugs[*].insideWindowRows/afterWindowRows, expectedMarketPointLookups, feedGapsInObservedEnvelope.'
}
finally {
    Pop-Location
}
