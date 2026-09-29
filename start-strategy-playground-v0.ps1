param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$Python = (Get-Command python -ErrorAction Stop).Source
$ArgsList = @('-X','utf8','tools/strategy_playground_v0/launch.py','start')
if ($NoBrowser) { $ArgsList += '--no-browser' }
& $Python @ArgsList
if ($LASTEXITCODE -ne 0) { throw 'Playground did not confirm readiness. Other services were not changed.' }
