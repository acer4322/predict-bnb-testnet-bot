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

    $CompatScript = '.\tools\analyze_target_controller_complete_history_v2_compat.py'
    $SourceScript = '.\tools\analyze_target_controller_source_replay_v2.py'
    $MergeScript = '.\tools\merge_target_controller_source_replays_v2.py'
    $Tests = @(
        '.\tests\test_target_controller_complete_history_v2.py',
        '.\tests\test_target_controller_complete_history_v2_compat.py',
        '.\tests\test_merge_target_controller_source_replays_v2.py'
    )

    $LegacyStageReport = 'data/research/target_controller_complete_history_v2_legacy_report.json'
    $LegacyStageBursts = 'data/research/target_controller_complete_history_v2_legacy_bursts.csv'
    $LegacyStageTransitions = 'data/research/target_controller_complete_history_v2_legacy_transitions.csv'
    $LegacyStageMarkets = 'data/research/target_controller_complete_history_v2_legacy_markets.csv'
    $OfficialStageReport = 'data/research/target_controller_complete_history_v2_official_report.json'
    $OfficialStageBursts = 'data/research/target_controller_complete_history_v2_official_bursts.csv'
    $OfficialStageTransitions = 'data/research/target_controller_complete_history_v2_official_transitions.csv'
    $OfficialStageMarkets = 'data/research/target_controller_complete_history_v2_official_markets.csv'

    Stamp 'TARGET CONTROLLER COMPLETE HISTORY V2 - SPLIT SOURCE PIPELINE'
    Write-Host 'Pipeline:'
    Write-Host "  [1] LEGACY  < $Cutover  -> isolated replay process"
    Write-Host "  [2] OFFICIAL >= $Cutover -> isolated replay process"
    Write-Host '  [3] Merge normalized CSV/report outputs only; merge stage does NOT open SQLite.'
    Write-Host '  Legacy schemas without asset column inject BTC; official schemas retain asset filtering.'
    Write-Host '  Post-cutover legacy fallback is DISABLED; missing collector windows remain hard gaps.'
    Write-Host '  Cross-cutover market IDs are invalidated instead of stitching inventory state.'
    Write-Host "Burst candidate: Taker idle gap <= ${IdleGapMs}ms, onset cap <= ${BurstCapMs}ms, Maker breaks burst."
    Write-Host 'Research only: no EBM fitting, no paper/live promotion.'

    if (-not (Test-Path $LegacyDb)) { throw "Missing legacy DB: $LegacyDb" }
    if (-not (Test-Path $OfficialDb)) { throw "Missing official DB: $OfficialDb" }

    Stamp 'syntax check source adapter + isolated replay + merge'
    python -m py_compile $CompatScript $SourceScript $MergeScript
    if ($LASTEXITCODE -ne 0) { throw 'syntax check failed' }

    if (-not $SkipTests) {
        Stamp 'run focused source/gap/burst/accounting/schema/merge invariants'
        python -m pytest -q @Tests
        if ($LASTEXITCODE -ne 0) { throw 'focused tests failed' }
    }

    Stamp 'stage 1/3: replay LEGACY source only'
    python $SourceScript `
        --db $LegacyDb `
        --source-version LEGACY `
        --cutover $Cutover `
        --gap-minutes $GapMinutes `
        --idle-gap-ms $IdleGapMs `
        --burst-cap-ms $BurstCapMs `
        --report $LegacyStageReport `
        --bursts $LegacyStageBursts `
        --transitions $LegacyStageTransitions `
        --markets $LegacyStageMarkets
    if ($LASTEXITCODE -ne 0) { throw 'LEGACY source replay failed' }

    Stamp 'stage 2/3: replay OFFICIAL source only'
    python $SourceScript `
        --db $OfficialDb `
        --source-version OFFICIAL `
        --cutover $Cutover `
        --gap-minutes $GapMinutes `
        --idle-gap-ms $IdleGapMs `
        --burst-cap-ms $BurstCapMs `
        --report $OfficialStageReport `
        --bursts $OfficialStageBursts `
        --transitions $OfficialStageTransitions `
        --markets $OfficialStageMarkets
    if ($LASTEXITCODE -ne 0) { throw 'OFFICIAL source replay failed' }

    Stamp 'stage 3/3: merge normalized source outputs (no SQLite)'
    python $MergeScript `
        --legacy-report $LegacyStageReport `
        --legacy-bursts $LegacyStageBursts `
        --legacy-transitions $LegacyStageTransitions `
        --legacy-markets $LegacyStageMarkets `
        --official-report $OfficialStageReport `
        --official-bursts $OfficialStageBursts `
        --official-transitions $OfficialStageTransitions `
        --official-markets $OfficialStageMarkets `
        --report $Report `
        --bursts $Bursts `
        --transitions $Transitions `
        --markets $Markets
    if ($LASTEXITCODE -ne 0) { throw 'normalized source merge failed' }

    Stamp 'done'
    Write-Host "Combined report : $Report"
    Write-Host "Combined bursts : $Bursts"
    Write-Host "Combined states : $Transitions"
    Write-Host "Combined markets: $Markets"
    Write-Host "Legacy report   : $LegacyStageReport"
    Write-Host "Official report : $OfficialStageReport"
    Write-Host 'Primary file to review first: target_controller_complete_history_v2_report.json'
}
finally {
    Pop-Location
}
