$ErrorActionPreference = "Stop"

$started = Get-Date
function Stamp([string]$Message) {
    $elapsed = (Get-Date) - $started
    Write-Host ("[{0} +{1:00}:{2:00}] {3}" -f (Get-Date -Format "HH:mm:ss"), [int]$elapsed.TotalMinutes, $elapsed.Seconds, $Message)
}

Stamp "START Maker lifecycle-side V2.3 quick"
Stamp "Syntax check"
python -m py_compile `
  .\tools\build_target_maker_lifecycle_side_v2_3.py `
  .\tools\train_target_maker_lifecycle_side_v2_3.py
if ($LASTEXITCODE -ne 0) { throw "py_compile failed" }

Stamp "Build clean strict-past lifecycle dataset"
python .\tools\build_target_maker_lifecycle_side_v2_3.py
if ($LASTEXITCODE -ne 0) { throw "dataset build failed" }

Stamp "Run 2-fold quick EBM: PUBLIC_ONLY vs LAST_MAKER vs LIFECYCLE"
python .\tools\train_target_maker_lifecycle_side_v2_3.py `
  --min-train-markets 70 `
  --test-markets 15 `
  --max-folds 2 `
  --interactions 4 `
  --max-rounds 350 `
  --outer-bags 2
if ($LASTEXITCODE -ne 0) { throw "quick EBM failed" }

Stamp "DONE"
Write-Host "Report: data\research\target_maker_lifecycle_side_v2_3_report.json"
