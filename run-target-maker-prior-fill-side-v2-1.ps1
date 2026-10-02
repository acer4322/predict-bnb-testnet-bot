param(
    [string]$BehaviorDataset = ".\data\research\target_maker_direct_behavior_v1.csv"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Started = Get-Date

function Stamp([string]$Message) {
    $Now = Get-Date
    $Elapsed = $Now - $Started
    Write-Host ("[{0} +{1:00}:{2:00}] {3}" -f $Now.ToString("HH:mm:ss"), [int]$Elapsed.TotalMinutes, $Elapsed.Seconds, $Message)
}

Push-Location $Root
try {
    Stamp "TARGET MAKER PRIOR-FILL SIDE V2.1 EMPIRICAL PREFLIGHT"
    Stamp "No EBM. No SPECIAL audit. Same-period V1 Maker behavior only."
    Stamp "Leakage guard: a prior parent enters state only if first_fill_ms < current placement_first_ms; current parent can never seed itself."

    $Report = Join-Path $Root "data\research\target_maker_prior_fill_side_v2_1_preflight.json"
    if (-not (Test-Path $BehaviorDataset)) { throw "Missing Maker behavior dataset: $BehaviorDataset" }

    Stamp "[1/2] Syntax check START"
    python -m py_compile .\tools\preflight_target_maker_prior_fill_side_v2_1.py
    if ($LASTEXITCODE -ne 0) { throw "V2.1 preflight syntax check failed." }
    Stamp "[1/2] Syntax check DONE"

    Stamp "[2/2] Empirical prior-fill state scan START"
    python .\tools\preflight_target_maker_prior_fill_side_v2_1.py `
        --behavior-dataset $BehaviorDataset `
        --report $Report
    if ($LASTEXITCODE -ne 0) { throw "V2.1 empirical preflight failed." }
    Stamp "[2/2] Empirical prior-fill state scan DONE"

    Stamp "STOP GATE reached. No EBM training was started."
    Write-Host "Report: data\research\target_maker_prior_fill_side_v2_1_preflight.json"
}
finally {
    Pop-Location
}
