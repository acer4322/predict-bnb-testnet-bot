$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

$Python = if (Test-Path '.\.venv\Scripts\python.exe') { '.\.venv\Scripts\python.exe' } else { 'python' }
$Db = Join-Path $RepoRoot 'data\microstructure.db'
$Bursts = Join-Path $RepoRoot 'data\research\target_controller_hazard_v21_directional_bursts.csv'

if (-not (Test-Path $Db)) {
    throw "microstructure DB not found: $Db"
}
if (-not (Test-Path $Bursts)) {
    throw "V2.1 directional bursts CSV not found: $Bursts"
}

Write-Host '== py_compile =='
& $Python -m py_compile tools/analyze_target_opportunity_risk_alignment_v24.py

Write-Host '== focused pytest =='
& $Python -m pytest -q tests/test_target_opportunity_risk_alignment_v24.py

Write-Host '== Target Opportunity vs Risk Alignment V2.4 =='
& $Python tools/analyze_target_opportunity_risk_alignment_v24.py

Write-Host ''
Write-Host 'Outputs:'
Write-Host '  data/research/target_opportunity_risk_alignment_v24_report.json'
Write-Host '  data/research/target_opportunity_risk_alignment_v24_actions.csv'
Write-Host '  data/research/target_opportunity_risk_alignment_v24_triplets.csv'
