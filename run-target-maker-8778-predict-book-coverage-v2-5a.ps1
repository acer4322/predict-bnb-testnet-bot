$ErrorActionPreference = 'Stop'

$started = Get-Date
function Stamp([string]$Message) {
    $elapsed = (Get-Date) - $started
    Write-Host ("[{0:HH:mm:ss} +{1:mm\:ss}] {2}" -f (Get-Date), $elapsed, $Message)
}

Stamp 'syntax check'
python -m py_compile .\tools\audit_target_maker_8778_predict_book_coverage_v2_5a.py

Stamp 'audit 8/16+ special Maker parents against retained 8778 Predict book checkpoints+deltas'
python .\tools\audit_target_maker_8778_predict_book_coverage_v2_5a.py

Stamp 'done'
