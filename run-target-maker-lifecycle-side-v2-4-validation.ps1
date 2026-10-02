$ErrorActionPreference = "Stop"
$started = Get-Date
function Stamp([string]$Message) {
    $elapsed = (Get-Date) - $started
    $now = Get-Date -Format "HH:mm:ss"
    Write-Host ("[{0} +{1:mm\:ss}] {2}" -f $now, $elapsed, $Message)
}

Stamp "START Maker lifecycle-side V2.4 chronological validation"

python -m py_compile `
  .\tools\validate_target_maker_lifecycle_side_v2_4.py `
  .\tools\train_target_maker_lifecycle_side_v2_3.py `
  .\tools\build_target_maker_lifecycle_side_v2_3.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Stamp "SYNTAX OK"

python .\tools\validate_target_maker_lifecycle_side_v2_4.py `
  --max-folds 4 `
  --min-train-markets 70 `
  --test-markets 15 `
  --interactions 4 `
  --max-rounds 350 `
  --outer-bags 2
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Stamp "STOP validation complete"
Write-Host "Report: data\research\target_maker_lifecycle_side_v2_4_validation_report.json"
