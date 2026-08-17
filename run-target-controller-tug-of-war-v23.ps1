param(
    [string]$Bursts = 'data/research/target_controller_hazard_v21_directional_bursts.csv',
    [int]$MaxGapMs = 5000,
    [string]$Report = 'data/research/target_controller_tug_of_war_v23_report.json',
    [string]$Patterns = 'data/research/target_controller_tug_of_war_v23_patterns.csv',
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    $Script = '.\tools\analyze_target_controller_tug_of_war_v23.py'
    $Test = '.\tests\test_target_controller_tug_of_war_v23.py'

    Write-Host 'TARGET CONTROLLER TUG-OF-WAR V2.3'
    Write-Host 'Hypothesis: persistent opportunity/greed pressure can ADD in one exposure direction,'
    Write-Host 'while the risk controller briefly REPAIRs the opposite direction, followed by ADD again.'
    Write-Host 'Input: V2.1 directional bursts only; no SQLite replay.'
    Write-Host 'Strict sequence: consecutive ADD(X) -> opposite REPAIR -> ADD(X).'
    Write-Host 'ASK is treated as reducing the token side, so signed exposure direction is flipped.'
    Write-Host "Each inter-burst gap must be <= $MaxGapMs ms."
    Write-Host 'Research only: no model fit and no strategy promotion.'

    if (-not (Test-Path $Bursts)) { throw "Missing V2.1 directional bursts CSV: $Bursts" }

    python -m py_compile $Script
    if ($LASTEXITCODE -ne 0) { throw 'syntax check failed' }

    if (-not $SkipTests) {
        python -m pytest -q $Test
        if ($LASTEXITCODE -ne 0) { throw 'focused tests failed' }
    }

    python $Script `
        --bursts $Bursts `
        --max-gap-ms $MaxGapMs `
        --report $Report `
        --patterns $Patterns
    if ($LASTEXITCODE -ne 0) { throw 'V2.3 tug-of-war analysis failed' }

    Write-Host "Report  : $Report"
    Write-Host "Patterns: $Patterns"
}
finally {
    Pop-Location
}
