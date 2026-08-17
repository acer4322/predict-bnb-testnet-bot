param(
    [string]$Db = 'data/target_wallet_official_v1.db',
    [string]$PublicDataset = 'data/research/target_taker_action_burst_hazard_v1.csv',
    [string]$Report = 'data/research/target_maker_objective_state_machine_v1_report.json',
    [string]$Transitions = 'data/research/target_maker_objective_state_machine_v1_transitions.csv',
    [string]$StressStart = '2026-08-16T03:40:00+08:00',
    [string]$StressEnd = '2026-08-16T11:35:00+08:00',
    [string]$OrdinaryStart = '',
    [string]$OrdinaryEnd = '',
    [int]$MaxPublicLagMs = 2000
)

$ErrorActionPreference = 'Stop'
$started = Get-Date

function Stamp([string]$Message) {
    $elapsed = (Get-Date) - $started
    Write-Host ("[{0:HH:mm:ss} +{1:mm\:ss}] {2}" -f (Get-Date), $elapsed, $Message)
}

$Script = '.\tools\analyze_target_maker_objective_state_machine_v1.py'

Stamp 'syntax check Maker Objective / State-Machine V1'
python -m py_compile $Script
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$argsList = @(
    $Script,
    '--db', $Db,
    '--public-dataset', $PublicDataset,
    '--report', $Report,
    '--transitions', $Transitions,
    '--stress-start', $StressStart,
    '--stress-end', $StressEnd,
    '--max-public-lag-ms', "$MaxPublicLagMs"
)

if ($OrdinaryStart) {
    $argsList += @('--ordinary-start', $OrdinaryStart)
}
if ($OrdinaryEnd) {
    $argsList += @('--ordinary-end', $OrdinaryEnd)
}

Stamp "compare matched ordinary control vs stress window [$StressStart, $StressEnd)"
python @argsList
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Stamp 'done; research outputs are under data/research'
