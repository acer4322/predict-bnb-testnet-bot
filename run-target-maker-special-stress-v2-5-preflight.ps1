$ErrorActionPreference = 'Stop'

$started = Get-Date
function Stamp([string]$Message) {
    $elapsed = (Get-Date) - $started
    Write-Host ("[{0:HH:mm:ss} +{1:mm\:ss}] {2}" -f (Get-Date), $elapsed, $Message)
}

Stamp 'syntax check'
python -m py_compile `
  .\tools\preflight_target_maker_special_stress_v2_5.py `
  .\tools\build_target_maker_lifecycle_side_v2_3.py `
  .\src\predict_bot\target_maker_direct_placement_v1.py

Stamp 'rebuild special Maker behavior + clean lifecycle preflight'
python .\tools\preflight_target_maker_special_stress_v2_5.py

Stamp 'done'
