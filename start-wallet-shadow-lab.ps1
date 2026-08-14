param(
    [switch]$NoBrowser,
    [switch]$KeepExistingStack
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dashboard = Join-Path $Root "dashboard-v2"
$Data = Join-Path $Root "data"
New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Test-LocalService([string]$Url, [int]$TimeoutSeconds = 2) {
    try {
        $CodeText = & curl.exe --silent --output NUL `
            --connect-timeout 1 --max-time $TimeoutSeconds `
            --write-out "%{http_code}" $Url 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
        $Code = 0
        if (-not [int]::TryParse(([string]$CodeText).Trim(), [ref]$Code)) { return $false }
        return $Code -eq 200
    }
    catch { return $false }
}

function Get-JsonPayload([string]$Url, [int]$TimeoutSeconds = 3) {
    try {
        $Body = & curl.exe --silent --fail `
            --connect-timeout 1 --max-time $TimeoutSeconds `
            --header "Accept: application/json" $Url 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        $Text = ($Body -join "`n")
        if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
        return $Text | ConvertFrom-Json -ErrorAction Stop
    }
    catch { return $null }
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

function Get-ProcessCommandLine([int]$ProcessId) {
    try {
        return [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop).CommandLine
    }
    catch { return "" }
}

function Assert-PausedLiveServices {
    foreach ($Port in @(8769, 8772, 8773, 8774, 8775)) {
        if (-not (Test-LocalService "http://127.0.0.1:${Port}/state")) { continue }
        $Payload = Get-JsonPayload "http://127.0.0.1:${Port}/state"
        if ($null -eq $Payload) {
            throw "Port $Port is running but its state cannot be verified. Refusing to stop the full stack."
        }
        $State = if ($Payload.state) { $Payload.state } else { $Payload }
        $RuntimeEnabled = $false
        if ($State.settings -and $null -ne $State.settings.runtimeEnabled) {
            $RuntimeEnabled = [bool]$State.settings.runtimeEnabled
        }
        if ([string]$State.status -ne "PAUSED" -or $RuntimeEnabled) {
            throw "Port $Port is not safely paused (status=$($State.status), runtimeEnabled=$RuntimeEnabled). Pause it before switching to Wallet Shadow Lab."
        }
    }
}

function Assert-KnownListener([int]$Port, [string]$Needle, [string]$Label) {
    $ListenerPid = Get-ListeningProcessId $Port
    if (-not $ListenerPid) { return }
    $CommandLine = Get-ProcessCommandLine $ListenerPid
    if (-not $CommandLine.ToLowerInvariant().Contains($Needle.ToLowerInvariant())) {
        throw "Port $Port is occupied by an unrecognized process. $Label was not started. PID=$ListenerPid command=$CommandLine"
    }
}

function Stop-RemainingHeavyListeners {
    $KnownModules = @{
        8766 = "predict_bot.api"
        8767 = "predict_bot.cross_oracle_dedicated"
        8768 = "predict_bot.cross_oracle_strategy_dedicated"
        8769 = "predict_bot.poly_gap_live_v"
        8770 = "predict_bot.multi_prediction_observer"
        8772 = "predict_bot.poly_gap_multi_asset_live_v"
        8773 = "predict_bot.poly_gap_multi_asset_live_v"
        8774 = "predict_bot.wallet_maker_clone_live_v"
        8775 = "predict_bot.wallet_maker_clone_live_v"
    }
    foreach ($Port in @($KnownModules.Keys)) {
        $ListenerPid = Get-ListeningProcessId ([int]$Port)
        if (-not $ListenerPid) { continue }
        $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$ListenerPid" -ErrorAction Stop
        $CommandLine = [string]$Process.CommandLine
        $Expected = [string]$KnownModules[$Port]
        if (-not $CommandLine.ToLowerInvariant().Contains($Expected.ToLowerInvariant())) {
            throw "Heavy port $Port is still occupied by an unrecognized process after full-stack stop. PID=$ListenerPid command=$CommandLine"
        }

        $TargetPid = $ListenerPid
        $ParentCommand = Get-ProcessCommandLine ([int]$Process.ParentProcessId)
        if ($ParentCommand.ToLowerInvariant().Contains("predict_bot.supervisor") -or
            $ParentCommand.ToLowerInvariant().Contains("predict_bot.multi_asset_live_supervisor")) {
            $TargetPid = [int]$Process.ParentProcessId
        }
        Write-Warning "Wallet Shadow Lab: removing verified paused heavy listener on $Port (PID=$ListenerPid, tree=$TargetPid)."
        & taskkill.exe /PID $TargetPid /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to stop verified paused heavy listener on port $Port (tree PID=$TargetPid)."
        }
    }

    Start-Sleep -Milliseconds 500
    $Remaining = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -in @($KnownModules.Keys) })
    if ($Remaining.Count -gt 0) {
        $Description = ($Remaining | ForEach-Object { "$($_.LocalPort):PID$($_.OwningProcess)" }) -join ", "
        throw "Heavy listeners remain after safe cleanup: $Description"
    }
}

if (-not $KeepExistingStack) {
    $HeavyPorts = @(8766, 8767, 8768, 8769, 8770, 8772, 8773, 8774, 8775)
    $HeavyListeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -in $HeavyPorts })
    if ($HeavyListeners.Count -gt 0) {
        Assert-PausedLiveServices
        Write-Host "Wallet Shadow Lab: verified Echtgeld services are paused; stopping the full Dashboard V2 stack."
        & (Join-Path $Root "stop-dashboard-v2.ps1") -Quiet
        Start-Sleep -Milliseconds 500
        Stop-RemainingHeavyListeners
    }
}

$UserPredictKey = [Environment]::GetEnvironmentVariable("PREDICT_FUN_API_KEY", "User")
if ($UserPredictKey) { $env:PREDICT_FUN_API_KEY = $UserPredictKey }
if ([string]::IsNullOrWhiteSpace($env:PREDICT_FUN_API_KEY)) {
    throw "PREDICT_FUN_API_KEY is required for the read-only 8771 market feed. Configure it as a User environment variable first."
}

# These child processes are research-only. They never inherit an enabled live runtime.
$env:PREDICT_LIVE_ENABLED = "false"
$env:PREDICT_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_WALLET_SHADOW_LEGACY_COHORTS_ENABLED = "false"
$env:PREDICT_MICRO_RAW_RETENTION_HOURS = "6"
$env:PREDICT_MICRO_SNAPSHOT_RETENTION_HOURS = "72"
$env:PREDICT_MICRO_LIQUIDITY_RETENTION_HOURS = "72"
$env:PREDICT_MICRO_SNAPSHOT_INTERVAL_MS = "250"

try {
    Assert-KnownListener 8771 "predict_bot.predict_fun_observer" "Predict.fun observer"
    if (-not (Test-LocalService "http://127.0.0.1:8771/state")) {
        Write-Host "Wallet Shadow Lab: starting read-only Predict.fun observer on 8771."
        $Predict = Start-Process -FilePath "python" `
            -ArgumentList @("-m", "predict_bot.predict_fun_observer") `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "wallet-shadow-lab-predict.stdout.log") `
            -RedirectStandardError (Join-Path $Data "wallet-shadow-lab-predict.stderr.log") -PassThru
        $Predict.Id | Set-Content (Join-Path $Root ".wallet-shadow-lab-predict.pid")
    }
    else { Write-Host "Wallet Shadow Lab: reusing the current read-only 8771 observer." }

    Assert-KnownListener 8778 "predict_bot.predict_wallet_maker_book_inference_collector_v2_1" "Maker book consumable lifecycle inference collector"
    if (-not (Test-LocalService "http://127.0.0.1:8778/state")) {
        Write-Host "Wallet Shadow Lab: starting consumable-quantity Maker lifecycle inference collector on 8778."
        $MakerBook = Start-Process -FilePath "python" `
            -ArgumentList @("-m", "predict_bot.predict_wallet_maker_book_inference_collector_v2_1") `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "wallet-shadow-lab-maker-book.stdout.log") `
            -RedirectStandardError (Join-Path $Data "wallet-shadow-lab-maker-book.stderr.log") -PassThru
        $MakerBook.Id | Set-Content (Join-Path $Root ".wallet-shadow-lab-maker-book.pid")
    }
    else { Write-Host "Wallet Shadow Lab: reusing the current 8778 consumable lifecycle collector." }

    Assert-KnownListener 8779 "predict_bot.predict_wallet_maker_book_inference_collector_eth5m" "ETH 5M Maker book inference collector"
    if (-not (Test-LocalService "http://127.0.0.1:8779/state")) {
        Write-Host "Wallet Shadow Lab: starting forward-only ETH 5M Maker full-book inference collector on 8779."
        $EthMakerBook = Start-Process -FilePath "python" `
            -ArgumentList @("-m", "predict_bot.predict_wallet_maker_book_inference_collector_eth5m") `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "wallet-shadow-lab-maker-book-eth5m.stdout.log") `
            -RedirectStandardError (Join-Path $Data "wallet-shadow-lab-maker-book-eth5m.stderr.log") -PassThru
        $EthMakerBook.Id | Set-Content (Join-Path $Root ".wallet-shadow-lab-maker-book-eth5m.pid")
    }
    else { Write-Host "Wallet Shadow Lab: reusing the current forward-only 8779 ETH 5M Maker book collector." }

    Assert-KnownListener 8777 "predict_bot.predict_wallet_taker_signal_collector" "Taker signal collector"
    if (-not (Test-LocalService "http://127.0.0.1:8777/state")) {
        Write-Host "Wallet Shadow Lab: starting public-data Taker signal collector on 8777."
        $TakerSignal = Start-Process -FilePath "python" `
            -ArgumentList @("-m", "predict_bot.predict_wallet_taker_signal_collector") `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "wallet-shadow-lab-taker-signal.stdout.log") `
            -RedirectStandardError (Join-Path $Data "wallet-shadow-lab-taker-signal.stderr.log") -PassThru
        $TakerSignal.Id | Set-Content (Join-Path $Root ".wallet-shadow-lab-taker-signal.pid")
    }
    else { Write-Host "Wallet Shadow Lab: reusing the current paper-only 8777 Taker signal collector." }

    Assert-KnownListener 8776 "predict_bot.predict_wallet_shadow_observer_v4_19" "Wallet Shadow observer"
    if (-not (Test-LocalService "http://127.0.0.1:8776/health" 5)) {
        Write-Host "Wallet Shadow Lab: starting latest paper-only Wallet Shadow observer v4.19 on 8776."
        $Shadow = Start-Process -FilePath "python" `
            -ArgumentList @("-m", "predict_bot.predict_wallet_shadow_observer_v4_19") `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "wallet-shadow-lab-observer.stdout.log") `
            -RedirectStandardError (Join-Path $Data "wallet-shadow-lab-observer.stderr.log") -PassThru
        $Shadow.Id | Set-Content (Join-Path $Root ".wallet-shadow-lab-observer.pid")
    }
    else { Write-Host "Wallet Shadow Lab: reusing the latest paper-only 8776 observer." }

    $WebPid = Get-ListeningProcessId 4320
    if ($WebPid) {
        $WebCommand = Get-ProcessCommandLine $WebPid
        if (-not ($WebCommand.ToLowerInvariant().Contains($Dashboard.ToLowerInvariant()) -and
                  $WebCommand.ToLowerInvariant().Contains("vite"))) {
            throw "Port 4320 is occupied by an unrecognized process. PID=$WebPid command=$WebCommand"
        }
    }
    if (-not (Test-LocalService "http://127.0.0.1:4320")) {
        Write-Host "Wallet Shadow Lab: starting Dashboard V2 on 4320."
        $Web = Start-Process -FilePath "npm.cmd" -ArgumentList @("run", "dev") `
            -WorkingDirectory $Dashboard -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "wallet-shadow-lab-web.stdout.log") `
            -RedirectStandardError (Join-Path $Data "wallet-shadow-lab-web.stderr.log") -PassThru
        $Web.Id | Set-Content (Join-Path $Root ".wallet-shadow-lab-web.pid")
    }
    else { Write-Host "Wallet Shadow Lab: reusing the current Dashboard V2 web process." }

    $Required = @(
        @{ Name = "8771 Predict.fun"; Url = "http://127.0.0.1:8771/state" },
        @{ Name = "8778 Maker book inference"; Url = "http://127.0.0.1:8778/state" },
        @{ Name = "8779 ETH 5M Maker book inference"; Url = "http://127.0.0.1:8779/state" },
        @{ Name = "8777 Taker signals"; Url = "http://127.0.0.1:8777/state" },
        @{ Name = "8776 Wallet Shadow"; Url = "http://127.0.0.1:8776/health" },
        @{ Name = "4320 Dashboard"; Url = "http://127.0.0.1:4320" },
        @{ Name = "Wallet Shadow bridge"; Url = "http://127.0.0.1:4320/bridge/wallet-shadow-health" }
    )
    $Deadline = (Get-Date).AddSeconds(60)
    do {
        $Missing = @($Required | Where-Object { -not (Test-LocalService $_.Url 3) })
        if ($Missing.Count -eq 0) { break }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $Deadline)

    if ($Missing.Count -gt 0) {
        $Names = ($Missing | ForEach-Object { $_.Name }) -join ", "
        throw "Wallet Shadow Lab startup incomplete: $Names. Check data/wallet-shadow-lab-*.log."
    }

    $ShadowPayload = Get-JsonPayload "http://127.0.0.1:8776/health" 5
    $ShadowState = if ($ShadowPayload.state) { $ShadowPayload.state } else { $ShadowPayload }
    if (-not ([string]$ShadowState.version).StartsWith("PREDICT_WALLET_SHADOW_")) {
        throw "8776 returned an unexpected version: $($ShadowState.version)"
    }

    Write-Host "Wallet Shadow Lab ready: http://localhost:4320/wallet-shadow"
    Write-Host "Started research path: 8771 + 8778 BTC lifecycle inference + 8779 ETH book + 8777 public Taker signals + 8776 v4.19 + 4320."
    Write-Host "8776 version=$($ShadowState.version); status=$($ShadowState.status); report=$($ShadowState.reportStatus); public-side Taker A/B is paper-only."
    if (-not $NoBrowser) { Start-Process "http://localhost:4320/wallet-shadow" }
}
catch {
    $Failure = $_
    try { & (Join-Path $Root "stop-wallet-shadow-lab.ps1") -Quiet }
    catch { Write-Warning "Startup failed and partial-process cleanup also reported: $_" }
    throw $Failure
}
