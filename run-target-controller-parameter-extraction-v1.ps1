param(
    [string]$Db = 'data/target_wallet_official_v1.db',
    [string]$PublicDataset = 'data/research/target_taker_action_burst_hazard_v1.csv',
    [string]$Report = 'data/research/target_controller_parameter_extraction_v1_report.json',
    [string]$Actions = 'data/research/target_controller_parameter_extraction_v1_actions.csv',
    [string]$States = 'data/research/target_controller_parameter_extraction_v1_states.csv',
    [string]$StressStart = '2026-08-16T03:40:00+08:00',
    [string]$StressEnd = '2026-08-16T11:35:00+08:00',
    [string]$OrdinaryStart = '',
    [string]$OrdinaryEnd = '',
    [int]$MaxPublicLagMs = 2000,
    [switch]$RunPrerequisites,
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

    $Script = '.\tools\analyze_target_controller_parameter_extraction_v1.py'
    $Test = '.\tests\test_target_controller_parameter_extraction_v1.py'

    Stamp 'TARGET CONTROLLER PARAMETER EXTRACTION V1'
    Write-Host 'Goal: infer controller boundaries from Target itself; no EBM fitting, no paper/live promotion.'
    Write-Host 'Public context is STRICT-PAST: equal timestamps are excluded.'
    Write-Host 'Ordinary control defaults to the exact same wall-clock span 24h before the frozen 8/16 stress window.'

    if (-not (Test-Path $Db)) { throw "Missing Target DB: $Db" }
    if (-not (Test-Path $PublicDataset)) {
        throw "Missing public dataset: $PublicDataset`nBuild target_taker_action_burst_hazard_v1.csv first."
    }

    Stamp 'syntax check extractor + existing lifecycle dependency'
    python -m py_compile $Script .\tools\analyze_target_maker_taker_inventory_lifecycle_v1.py
    if ($LASTEXITCODE -ne 0) { throw 'syntax check failed' }

    if (-not $SkipTests) {
        Stamp 'run focused accounting / strict-past / threshold tests'
        python -m pytest -q $Test
        if ($LASTEXITCODE -ne 0) { throw 'focused tests failed' }
    }

    if ($RunPrerequisites) {
        Stamp 'run existing inventory lifecycle replay (research prerequisite/audit)'
        & .\run-target-maker-taker-inventory-lifecycle-v1.ps1 -Asset BTC -RunTests
        if ($LASTEXITCODE -ne 0) { throw 'inventory lifecycle prerequisite failed' }

        Stamp 'run existing Maker objective/state-machine stress comparison'
        $objectiveArgs = @(
            '-Db', $Db,
            '-PublicDataset', $PublicDataset,
            '-StressStart', $StressStart,
            '-StressEnd', $StressEnd,
            '-MaxPublicLagMs', $MaxPublicLagMs
        )
        if ($OrdinaryStart) { $objectiveArgs += @('-OrdinaryStart', $OrdinaryStart) }
        if ($OrdinaryEnd) { $objectiveArgs += @('-OrdinaryEnd', $OrdinaryEnd) }
        & .\run-target-maker-objective-state-machine-v1.ps1 @objectiveArgs
        if ($LASTEXITCODE -ne 0) { throw 'objective/state-machine prerequisite failed' }
    }

    $argsList = @(
        $Script,
        '--db', $Db,
        '--public-dataset', $PublicDataset,
        '--report', $Report,
        '--actions', $Actions,
        '--states', $States,
        '--stress-start', $StressStart,
        '--stress-end', $StressEnd,
        '--max-public-lag-ms', "$MaxPublicLagMs"
    )
    if ($OrdinaryStart) { $argsList += @('--ordinary-start', $OrdinaryStart) }
    if ($OrdinaryEnd) { $argsList += @('--ordinary-end', $OrdinaryEnd) }

    Stamp 'extract ordinary controller parameters, freeze them, then evaluate unchanged on 8/16 stress'
    python @argsList
    if ($LASTEXITCODE -ne 0) { throw 'controller parameter extraction failed' }

    Stamp 'done'
    Write-Host "Report : $Report"
    Write-Host "Actions: $Actions"
    Write-Host "States : $States"
    Write-Host 'First run recommendation: .\run-target-controller-parameter-extraction-v1.ps1 -RunPrerequisites'
}
finally {
    Pop-Location
}
