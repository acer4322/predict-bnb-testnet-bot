param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$Python = (Get-Command python -ErrorAction Stop).Source
$ArgsList = @('-X','utf8','tools/strategy_playground_v1/launch.py','start')
if ($NoBrowser) { $ArgsList += '--no-browser' }
& $Python @ArgsList
if ($LASTEXITCODE -ne 0) { throw 'V1 readiness not confirmed. Other services are preserved.' }
