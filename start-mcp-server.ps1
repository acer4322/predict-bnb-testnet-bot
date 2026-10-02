[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$LocalPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (Test-Path -LiteralPath $LocalPython) {
    $Python = $LocalPython
} else {
    $PythonCommand = Get-Command python -ErrorAction Stop
    $Python = $PythonCommand.Source
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MCP_PORT = $Port.ToString()

Write-Host "Starting project-scoped filesystem MCP on http://127.0.0.1:$Port/mcp"
Write-Host "Scope: $ProjectRoot"
& $Python -m predict_bot.mcp_server
exit $LASTEXITCODE
