param(
    [string]$BehaviorDataset = ".\data\research\target_maker_direct_behavior_v1.csv",
    [string]$ShadowDb = ".\data\predict_wallet_shadow.db"
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
    Stamp "TARGET MAKER LEGACY SHADOW SOURCE V2 PREFLIGHT"
    Stamp "No EBM. Read-only scan of old data\predict_wallet_shadow.db."
    Stamp "Gate: candidate table must overlap Maker V1 by both exact market_id and time."

    if (-not (Test-Path $BehaviorDataset)) { throw "Missing Maker behavior dataset: $BehaviorDataset" }
    if (-not (Test-Path $ShadowDb)) { throw "Missing legacy shadow DB: $ShadowDb" }

    $Report = Join-Path $Root "data\research\target_maker_shadow_source_v2_preflight.json"

    Stamp "[1/2] Syntax check START"
    python -m py_compile .\tools\preflight_target_maker_shadow_source_v2.py
    if ($LASTEXITCODE -ne 0) { throw "Legacy shadow source preflight syntax check failed." }
    Stamp "[1/2] Syntax check DONE"

    Stamp "[2/2] Schema + market/time overlap scan START"
    python .\tools\preflight_target_maker_shadow_source_v2.py `
        --behavior-dataset $BehaviorDataset `
        --shadow-db $ShadowDb `
        --report $Report
    if ($LASTEXITCODE -ne 0) { throw "Legacy shadow source preflight failed." }
    Stamp "[2/2] Schema + market/time overlap scan DONE"

    Stamp "STOP GATE reached. No dataset rebuild or EBM training was started."
    Write-Host "Report: data\research\target_maker_shadow_source_v2_preflight.json"
}
finally {
    Pop-Location
}
