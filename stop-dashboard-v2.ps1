param([switch]$Quiet)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dashboard = Join-Path $Root "dashboard-v2"

function Get-ListeningProcessId([int]$Port) {
    try {
        $Connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -First 1
        if ($Connection) { return [int]$Connection.OwningProcess }
    }
    catch { }
    return $null
}

function Get-ProcessCommandLine([int]$ProcessId) {
    try {
        return [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop).CommandLine
    }
    catch { return "" }
}

$ListeningPid = Get-ListeningProcessId 4320
if (-not $ListeningPid) {
    Remove-Item (Join-Path $Root ".web-v2.pid") -Force -ErrorAction SilentlyContinue
    if (-not $Quiet) {
        Write-Host "Dashboard V2 is already stopped. Managed backend services were left untouched."
    }
    return
}

$CommandLine = Get-ProcessCommandLine $ListeningPid
$CommandNeedle = $CommandLine.ToLowerInvariant()
$DashboardNeedle = $Dashboard.ToLowerInvariant()
if (-not ($CommandNeedle.Contains($DashboardNeedle) -and $CommandNeedle.Contains("vite"))) {
    throw "Port 4320 is occupied by an unrecognized process. Refusing to stop it. PID=$ListeningPid command=$CommandLine"
}

Stop-Process -Id $ListeningPid -Force -ErrorAction Stop
for ($i = 0; $i -lt 40; $i++) {
    if (-not (Get-ListeningProcessId 4320)) { break }
    Start-Sleep -Milliseconds 100
}
if (Get-ListeningProcessId 4320) {
    throw "Verified Dashboard V2 listener PID=$ListeningPid did not release port 4320."
}

Remove-Item (Join-Path $Root ".web-v2.pid") -Force -ErrorAction SilentlyContinue
if (-not $Quiet) {
    Write-Host "Stopped Dashboard V2 web/control host (4320)."
    Write-Host "Managed backend services (8766-8781) were intentionally left running."
}
