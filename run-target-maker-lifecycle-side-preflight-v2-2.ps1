$ErrorActionPreference = "Stop"
$started = Get-Date

function Write-ProgressLine([string]$Message) {
    $now = Get-Date
    $elapsed = $now - $started
    Write-Host ("[{0} +{1:mm\:ss}] {2}" -f $now.ToString("HH:mm:ss"), $elapsed, $Message)
}

Write-ProgressLine "START Maker lifecycle-side V2.2 clean-target preflight"

Write-ProgressLine "STEP 1/2 syntax check"
python -m py_compile .\tools\preflight_target_maker_lifecycle_side_v2_1.py .\tools\preflight_target_maker_lifecycle_side_v2_2.py
if ($LASTEXITCODE -ne 0) { throw "py_compile failed" }
Write-ProgressLine "STEP 1/2 done"

Write-ProgressLine "STEP 2/2 strict-past clean-target descriptive analysis"
python .\tools\preflight_target_maker_lifecycle_side_v2_2.py
if ($LASTEXITCODE -ne 0) { throw "preflight failed" }
Write-ProgressLine "STEP 2/2 done"

Write-ProgressLine "STOP GATE reached. No EBM training was started."
Write-Host "Report: data\research\target_maker_lifecycle_side_preflight_v2_2.json"
