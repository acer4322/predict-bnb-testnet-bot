$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $Root ".poly-fast-live.pid"
$Port = 8792

$Pid = $null
if (Test-Path $PidFile) {
    try { $Pid = [int]((Get-Content $PidFile -Raw).Trim()) } catch { $Pid = $null }
}
if (-not $Pid) {
    try {
        $row = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -First 1
        if ($row) { $Pid = [int]$row.OwningProcess }
    }
    catch { }
}
if (-not $Pid) {
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Poly Fast Live is already stopped."
    exit 0
}

$CommandLine = ""
try {
    $CommandLine = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$Pid" -ErrorAction Stop).CommandLine
}
catch {
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Poly Fast Live process is already gone."
    exit 0
}

if (-not $CommandLine.ToLowerInvariant().Contains("predict_bot.poly_fast_live")) {
    throw "Refusing to stop PID=$Pid because it is not predict_bot.poly_fast_live*. command=$CommandLine"
}

Stop-Process -Id $Pid -Force -ErrorAction Stop
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue

for ($i = 0; $i -lt 40; $i++) {
    try {
        $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        if (-not $listener) { break }
    }
    catch { break }
    Start-Sleep -Milliseconds 100
}
Write-Host "Poly Fast Live stopped on port $Port."
