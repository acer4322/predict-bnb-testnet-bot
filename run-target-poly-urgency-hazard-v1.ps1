param(
    [string]$Transitions = 'data/research/target_controller_complete_history_v2_official_transitions.csv',
    [string]$Bursts = 'data/research/target_controller_complete_history_v2_official_bursts.csv',
    [string]$CrossDb = 'data/cross_oracle.db',
    [string]$Report = 'data/research/target_poly_urgency_hazard_v1_report.json',
    [string]$States = 'data/research/target_poly_urgency_hazard_v1_states.csv',
    [string]$Start = '2026-08-16T03:40:00+08:00',
    [string]$End = '2026-08-16T11:35:00+08:00',
    [int]$MaxPolyLagMs = 2000,
    [int]$GridMs = 1000,
    [int]$LookbackMs = 30000,
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

    $Script = '.\tools\analyze_target_poly_urgency_hazard_v1.py'
    $Test = '.\tests\test_target_poly_urgency_hazard_v1.py'

    Stamp 'TARGET POLY URGENCY HAZARD V1'
    Write-Host 'Hypothesis: stronger Poly directional price state raises Target Taker urgency / shrinks inventory tolerance.'
    Write-Host 'Target states come from normalized OFFICIAL V2 CSVs; this runner does NOT reopen the Target wallet DB.'
    Write-Host 'Poly is STRICT-PAST: source_timestamp_ms equal to the Maker timestamp is excluded.'
    Write-Host 'Hazard is next Taker burst within 1/3/5/15s even if additional Maker parents occur in between.'
    Write-Host "Maker-state density control: keep latest state per market every ${GridMs}ms."
    Write-Host "Poly freshness requirement: lag <= ${MaxPolyLagMs}ms."
    Write-Host 'Research only: no EBM fitting and no paper/live promotion.'

    foreach ($required in @($Transitions, $Bursts, $CrossDb)) {
        if (-not (Test-Path $required)) { throw "Missing required input: $required" }
    }

    Stamp 'syntax check'
    python -m py_compile $Script
    if ($LASTEXITCODE -ne 0) { throw 'syntax check failed' }

    if (-not $SkipTests) {
        Stamp 'run strict-past / burst-hazard / grid / read-only invariants'
        python -m pytest -q $Test
        if ($LASTEXITCODE -ne 0) { throw 'focused tests failed' }
    }

    Stamp 'join raw Poly price state to OFFICIAL Maker states inside actual Poly overlap'
    python $Script `
        --transitions $Transitions `
        --bursts $Bursts `
        --cross-db $CrossDb `
        --report $Report `
        --states $States `
        --start $Start `
        --end $End `
        --max-poly-lag-ms $MaxPolyLagMs `
        --grid-ms $GridMs `
        --lookback-ms $LookbackMs
    if ($LASTEXITCODE -ne 0) { throw 'Poly urgency hazard analysis failed' }

    Stamp 'done'
    Write-Host "Report: $Report"
    Write-Host "States: $States"
}
finally {
    Pop-Location
}
