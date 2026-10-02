param(
    [switch]$Quiet,
    [switch]$ElevatedCleanup
)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectPorts = @(8766, 8767, 8768, 4310)
foreach ($Name in @("api", "web")) {
    $PidFile = Join-Path $Root ".$Name.pid"
    if (Test-Path $PidFile) {
        $ProcessId = (Get-Content $PidFile -Raw).Trim()
        if ($ProcessId -match '^\d+$' -and (
            Get-Process -Id ([int]$ProcessId) -ErrorAction SilentlyContinue
        )) {
            & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
        }
        Remove-Item $PidFile -Force
    }
}

# PID files can be replaced by a later launch while an older listener remains.
# Clear every listener on this project's dedicated local ports. Port 8765 is
# deliberately excluded because it belongs to another local application.
$ListenerPids = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in $ProjectPorts } |
    Select-Object -ExpandProperty OwningProcess -Unique
foreach ($ProcessId in $ListenerPids) {
    if ($ProcessId -and $ProcessId -ne $PID) {
        & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
    }
}

Start-Sleep -Milliseconds 300
$Remaining = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in $ProjectPorts }

if ($Remaining -and -not $ElevatedCleanup) {
    Write-Host "An older elevated BTC 5M Lab process needs administrator cleanup."
    Write-Host "Please accept the Windows permission prompt once."
    $Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -ElevatedCleanup -Quiet"
    Start-Process powershell.exe -Verb RunAs -ArgumentList $Arguments -Wait
    Start-Sleep -Milliseconds 300
    $Remaining = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -in $ProjectPorts }
}

if ($Remaining) {
    throw "Could not clear local ports 8766/8767/8768/4310. Close the older PowerShell window or run Stop BTC 5M Lab as administrator."
}

if (-not $Quiet) {
    Write-Host "BTC 5M Lab stopped. All stale local listeners were cleared."
}
