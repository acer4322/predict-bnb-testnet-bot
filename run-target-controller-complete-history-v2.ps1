param(
    [string]$LegacyDb = 'data/predict_wallet_shadow.db',
    [string]$OfficialDb = 'data/target_wallet_official_v1.db',
    [string]$Cutover = '2026-08-15T00:00:00+08:00',
    [double]$GapMinutes = 30,
    [int]$IdleGapMs = 1000,
    [int]$BurstCapMs = 3000,
    [string]$Report = 'data/research/target_controller_complete_history_v2_report.json',
    [string]$Bursts = 'data/research/target_controller_complete_history_v2_bursts.csv',
    [string]$Transitions = 'data/research/target_controller_complete_history_v2_transitions.csv',
    [string]$Markets = 'data/research/target_controller_complete_history_v2_markets.csv',
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

    $Script = '.\tools\analyze_target_controller_complete_history_v2.py'
    $Test = '.\tests\test_target_controller_complete_history_v2.py'

    Stamp 'TARGET CONTROLLER COMPLETE HISTORY V2'
    Write-Host 'Source contract:'
    Write-Host "  < $Cutover  => LEGACY only ($LegacyDb)"
    Write-Host "  >= $Cutover => OFFICIAL only ($OfficialDb)"
    Write-Host '  Post-cutover legacy fallback is DISABLED; missing collector windows remain hard gaps.'
    Write-Host '  Markets crossing a source boundary or detected hard gap are excluded from lifecycle replay.'
    Write-Host "Burst candidate: Taker idle gap <= ${IdleGapMs}ms, onset cap <= ${BurstCapMs}ms, Maker breaks burst."
    Write-Host 'Research only: no EBM fitting, no paper/live promotion.'

    if (-not (Test-Path $LegacyDb)) { throw "Missing legacy DB: $LegacyDb" }
    if (-not (Test-Path $OfficialDb)) { throw "Missing official DB: $OfficialDb" }

    Stamp 'syntax check version-aware controller analyzer'
    python -m py_compile $Script
    if ($LASTEXITCODE -ne 0) { throw 'syntax check failed' }

    if (-not $SkipTests) {
        Stamp 'run focused source/gap/burst/accounting invariants'
        python -m pytest -q $Test
        if ($LASTEXITCODE -ne 0) { throw 'focused tests failed' }
    }

    Stamp 'replay all complete lifecycle segments across legacy + official history'
    python $Script `
        --legacy-db $LegacyDb `
        --official-db $OfficialDb `
        --cutover $Cutover `
        --gap-minutes $GapMinutes `
        --idle-gap-ms $IdleGapMs `
        --burst-cap-ms $BurstCapMs `
        --report $Report `
        --bursts $Bursts `
        --transitions $Transitions `
        --markets $Markets
    if ($LASTEXITCODE -ne 0) { throw 'complete-history controller replay failed' }

    Stamp 'done'
    Write-Host "Report      : $Report"
    Write-Host "Bursts      : $Bursts"
    Write-Host "Transitions : $Transitions"
    Write-Host "Markets     : $Markets"
    Write-Host 'Primary file to review first: target_controller_complete_history_v2_report.json'
}
finally {
    Pop-Location
}
