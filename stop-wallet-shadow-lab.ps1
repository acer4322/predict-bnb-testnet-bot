param([switch]$Quiet)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Stop-OwnedTree([string]$PidFile, [string]$Label) {
    $Path = Join-Path $Root $PidFile
    if (-not (Test-Path $Path)) { return }
    try {
        $Text = (Get-Content $Path -Raw).Trim()
        if ($Text -match '^\d+$') {
            $ProcessId = [int]$Text
            if (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
                & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
                if (-not $Quiet) { Write-Host "Stopped $Label process tree ($ProcessId)." }
            }
        }
    }
    finally {
        Remove-Item $Path -Force -ErrorAction SilentlyContinue
    }
}

function Get-ListeningProcessId([int]$Port) {
    try {
        $Connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -First 1
        if ($Connection) { return [int]$Connection.OwningProcess }
    }
    catch { }
    return $null
}

function Stop-VerifiedDashboardVite {
    $ListenerPid = Get-ListeningProcessId 4320
    if (-not $ListenerPid) { return }
    try {
        $CommandLine = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ListenerPid" -ErrorAction Stop).CommandLine
    }
    catch { return }
    $Dashboard = (Join-Path $Root "dashboard-v2").ToLowerInvariant()
    $CommandNeedle = $CommandLine.ToLowerInvariant()
    if (-not ($CommandNeedle.Contains($Dashboard) -and $CommandNeedle.Contains("vite"))) {
        if (-not $Quiet) {
            Write-Warning "Leaving unrecognized 4320 listener untouched (PID=$ListenerPid; command=$CommandLine)."
        }
        return
    }
    & taskkill.exe /PID $ListenerPid /T /F 2>$null | Out-Null
    if (-not $Quiet) { Write-Host "Stopped verified Wallet Shadow Dashboard listener ($ListenerPid)." }
}

function Stop-VerifiedResearchListeners {
    $ExpectedByPort = @{
        8771 = "predict_bot.predict_fun_observer"
        8776 = "predict_bot.predict_wallet_shadow_observer_v4_14"
        8777 = "predict_bot.predict_wallet_taker_signal_collector"
        8778 = "predict_bot.predict_wallet_maker_book_inference_collector"
        8779 = "predict_bot.predict_wallet_maker_book_inference_collector_eth5m"
    }
    foreach ($Port in @($ExpectedByPort.Keys)) {
        $Listeners = @(Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique)
        foreach ($ListenerPid in $Listeners) {
            try {
                $CommandLine = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ListenerPid" -ErrorAction Stop).CommandLine
            }
            catch { continue }
            $Expected = [string]$ExpectedByPort[$Port]
            if (-not $CommandLine.ToLowerInvariant().Contains($Expected.ToLowerInvariant())) {
                if (-not $Quiet) {
                    Write-Warning "Leaving unrecognized $Port listener untouched (PID=$ListenerPid; command=$CommandLine)."
                }
                continue
            }
            & taskkill.exe /PID $ListenerPid /T /F 2>$null | Out-Null
            if (-not $Quiet) { Write-Host "Stopped verified stale research listener on $Port ($ListenerPid)." }
        }
    }
}

Stop-OwnedTree ".wallet-shadow-lab-web.pid" "Wallet Shadow Dashboard"
Stop-VerifiedDashboardVite
Stop-OwnedTree ".wallet-shadow-lab-observer.pid" "Wallet Shadow observer"
Stop-OwnedTree ".wallet-shadow-lab-taker-signal.pid" "Taker signal collector"
Stop-OwnedTree ".wallet-shadow-lab-maker-book.pid" "Maker book inference collector"
Stop-OwnedTree ".wallet-shadow-lab-maker-book-eth5m.pid" "ETH 5M Maker book inference collector"
Stop-OwnedTree ".wallet-shadow-lab-predict.pid" "Predict.fun observer"
Stop-VerifiedResearchListeners

if (-not $Quiet) {
    Write-Host "Wallet Shadow Lab stopped. Services not owned by the dedicated launcher were left untouched."
}
