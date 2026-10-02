param(
    [string]$Db = 'data/target_wallet_official_v1.db',
    [string]$StressStart = '2026-08-16T05:20:00+08:00',
    [string]$StressEnd = '2026-08-16T11:35:00+08:00',
    [string]$OrdinaryStart = '2026-08-17T05:20:00+08:00',
    [string]$OrdinaryEnd = '2026-08-17T11:35:00+08:00',
    [double]$GapMinutes = 30,
    [int]$IdleGapMs = 1000,
    [int]$BurstCapMs = 3000,
    [string]$Report = 'data/research/target_controller_hazard_v21_report.json',
    [string]$DirectionalBursts = 'data/research/target_controller_hazard_v21_directional_bursts.csv',
    [string]$ExecutionBursts = 'data/research/target_controller_hazard_v21_execution_bursts.csv',
    [string]$States = 'data/research/target_controller_hazard_v21_states.csv',
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

    $Script = '.\tools\analyze_target_controller_hazard_v21.py'
    $Test = '.\tests\test_target_controller_hazard_v21.py'

    Stamp 'TARGET CONTROLLER HAZARD V2.1 - OFFICIAL ONLY'
    Write-Host 'Research-only diagnostic. No EBM fit and no strategy/live changes.'
    Write-Host "Stress   : $StressStart -> $StressEnd"
    Write-Host "Ordinary : $OrdinaryStart -> $OrdinaryEnd"
    Write-Host 'Fixed grid: one strict-past controller state per market-second after first Maker.'
    Write-Host 'Directional burst: Maker OR side flip breaks the Taker burst.'

    if (-not (Test-Path $Db)) { throw "Missing OFFICIAL DB: $Db" }

    Stamp 'syntax check'
    python -m py_compile $Script
    if ($LASTEXITCODE -ne 0) { throw 'syntax check failed' }

    if (-not $SkipTests) {
        Stamp 'run focused strict-past / directional-burst / purpose / lifecycle tests'
        python -m pytest -q $Test
        if ($LASTEXITCODE -ne 0) { throw 'focused tests failed' }
    }

    Stamp 'replay OFFICIAL Target lifecycle and build V2.1 hazard states'
    python $Script `
        --db $Db `
        --gap-minutes $GapMinutes `
        --idle-gap-ms $IdleGapMs `
        --burst-cap-ms $BurstCapMs `
        --stress-start $StressStart `
        --stress-end $StressEnd `
        --ordinary-start $OrdinaryStart `
        --ordinary-end $OrdinaryEnd `
        --report $Report `
        --directional-bursts $DirectionalBursts `
        --execution-bursts $ExecutionBursts `
        --states $States
    if ($LASTEXITCODE -ne 0) { throw 'V2.1 analysis failed' }

    Stamp 'done'
    Write-Host "Report             : $Report"
    Write-Host "Directional bursts : $DirectionalBursts"
    Write-Host "Execution bursts   : $ExecutionBursts"
    Write-Host "Fixed-grid states  : $States"
    Write-Host 'Review the JSON report first.'
}
finally {
    Pop-Location
}
