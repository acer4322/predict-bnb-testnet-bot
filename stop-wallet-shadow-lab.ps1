param([switch]$Quiet)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
New-Item -ItemType Directory -Force -Path $Data | Out-Null

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

function Save-WalletShadowWarmCache {
    $ListenerPid = Get-ListeningProcessId 8776
    if (-not $ListenerPid) { return }
    try {
        $CommandLine = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ListenerPid" -ErrorAction Stop).CommandLine
    }
    catch { return }
    if (-not $CommandLine.ToLowerInvariant().Contains("predict_bot.predict_wallet_shadow_observer_v4_")) { return }

    $CachePath = Join-Path $Data "wallet-shadow-last-good-state.json"
    $TempPath = Join-Path $Data "wallet-shadow-last-good-state.stop.tmp"
    Remove-Item $TempPath -Force -ErrorAction SilentlyContinue
    try {
        & curl.exe --silent --fail --connect-timeout 1 --max-time 12 `
            --header "Accept: application/json" `
            --output $TempPath "http://127.0.0.1:8776/state" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $TempPath)) { return }
        $Info = Get-Item $TempPath
        if ($Info.Length -lt 1024 -or $Info.Length -gt 67108864) { return }
        $Raw = Get-Content $TempPath -Raw
        if ($Raw -match '"reportOnlyState"\s*:\s*true') { return }
        $HasSubstantialState = (
            $Raw -match '"makerInventoryTakerSharedLab"\s*:' -or
            $Raw -match '"targetTakerMirrorAudit"\s*:' -or
            $Raw -match '"targetAccounting"\s*:' -or
            $Raw -match '"reconstructedMakerRulesLab"\s*:'
        )
        if (-not $HasSubstantialState) { return }
        Move-Item $TempPath $CachePath -Force
        if (-not $Quiet) { Write-Host "Saved Wallet Shadow last-good state for warm restart." }
    }
    catch {
        if (-not $Quiet) { Write-Warning "Could not save Wallet Shadow warm cache: $_" }
    }
    finally {
        Remove-Item $TempPath -Force -ErrorAction SilentlyContinue
    }
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
        8776 = "predict_bot.predict_wallet_shadow_observer_v4_"
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

Save-WalletShadowWarmCache
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
