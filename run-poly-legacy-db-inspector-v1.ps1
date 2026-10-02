param(
    [string[]]$Db = @('data/microstructure.db', 'data/cross_oracle.db'),
    [string]$Report = 'data/research/poly_legacy_db_inspector_v1_report.json',
    [int]$SampleLimit = 3,
    [int]$CountCap = 25000,
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

    $Script = '.\tools\inspect_poly_legacy_databases_v1.py'
    $Test = '.\tests\test_inspect_poly_legacy_databases_v1.py'

    Stamp 'POLY LEGACY DB INSPECTOR V1'
    Write-Host 'Read-only / low-load: schema + tiny samples; time-window queries only on indexed timestamps.'
    Write-Host 'Default windows: 8/16 03:40-11:35 +08 stress and 8/17 03:40-11:35 +08 ordinary.'

    $existing = @($Db | Where-Object { Test-Path $_ })
    if ($existing.Count -eq 0) {
        throw "None of the requested DBs exist: $($Db -join ', ')"
    }
    foreach ($path in $Db) {
        if (Test-Path $path) { Write-Host "DB found  : $path" }
        else { Write-Host "DB missing: $path (will be recorded as MISSING if passed directly)" }
    }

    Stamp 'syntax check inspector'
    python -m py_compile $Script
    if ($LASTEXITCODE -ne 0) { throw 'syntax check failed' }

    if (-not $SkipTests) {
        Stamp 'run focused read-only / timestamp / indexed-window tests'
        python -m pytest -q $Test
        if ($LASTEXITCODE -ne 0) { throw 'focused tests failed' }
    }

    $argsList = @(
        $Script,
        '--report', $Report,
        '--sample-limit', "$SampleLimit",
        '--count-cap', "$CountCap"
    )
    foreach ($path in $Db) {
        $argsList += @('--db', $path)
    }

    Stamp 'inspect legacy DB schemas and safely bounded 8/16 + 8/17 coverage'
    python @argsList
    if ($LASTEXITCODE -ne 0) { throw 'legacy DB inspection failed' }

    Stamp 'done'
    Write-Host "Report: $Report"
    Write-Host 'No database was copied or modified. Unindexed timestamp columns were not range-scanned.'
}
finally {
    Pop-Location
}
