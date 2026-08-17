param(
    [string]$States = 'data/research/target_controller_hazard_v21_states.csv',
    [string]$Report = 'data/research/target_controller_hazard_v22_matched_report.json',
    [string]$Cells = 'data/research/target_controller_hazard_v22_matched_cells.csv',
    [int]$Bins = 4,
    [int]$SurfaceBins = 5,
    [int]$MinPerRegime = 10,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    $Script = '.\tools\analyze_target_controller_hazard_v22.py'
    $Test = '.\tests\test_target_controller_hazard_v22.py'

    Write-Host 'TARGET CONTROLLER HAZARD V2.2 - COMMON SUPPORT MATCH'
    Write-Host 'Input: V2.1 fixed-grid strict-past states only; no SQLite replay.'
    Write-Host 'Core match: risk deficit + abs payoff gap + seconds left.'
    Write-Host 'Second match adds maker abs payoff gap.'
    Write-Host 'Prior-Taker reset/age variables are diagnostic only, not matching controls.'
    Write-Host 'Research only: no model fit and no strategy promotion.'

    if (-not (Test-Path $States)) { throw "Missing V2.1 states CSV: $States" }

    python -m py_compile $Script
    if ($LASTEXITCODE -ne 0) { throw 'syntax check failed' }

    if (-not $SkipTests) {
        python -m pytest -q $Test
        if ($LASTEXITCODE -ne 0) { throw 'focused tests failed' }
    }

    python $Script `
        --states $States `
        --report $Report `
        --cells $Cells `
        --bins $Bins `
        --surface-bins $SurfaceBins `
        --min-per-regime $MinPerRegime
    if ($LASTEXITCODE -ne 0) { throw 'V2.2 matched analysis failed' }

    Write-Host "Report: $Report"
    Write-Host "Cells : $Cells"
}
finally {
    Pop-Location
}
