param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
$Expected8776Version = "TARGET_WALLET_OFFICIAL_V2_LEGACY_HISTORY"
New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Test-LocalService([string]$Url, [int]$TimeoutSeconds = 2) {
    try {
        $Code = & curl.exe --silent --output NUL --connect-timeout 1 --max-time $TimeoutSeconds --write-out "%{http_code}" $Url 2>$null
        return $LASTEXITCODE -eq 0 -and ([string]$Code).Trim() -eq "200"
    }
    catch { return $false }
}

function Get-JsonPayload([string]$Url, [int]$TimeoutSeconds = 3) {
    try {
        $Body = & curl.exe --silent --fail --connect-timeout 1 --max-time $TimeoutSeconds --header "Accept: application/json" $Url 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        $Text = ($Body -join "`n")
        if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
        return $Text | ConvertFrom-Json -ErrorAction Stop
    }
    catch { return $null }
}

function Get-ListeningProcessId([int]$Port) {
    try {
        $Connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($Connection) { return [int]$Connection.OwningProcess }
    }
    catch { }
    return $null
}

function Get-ProcessCommandLine([int]$ProcessId) {
    try { return [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop).CommandLine }
    catch { return "" }
}

function Wait-LocalService([string]$Name, [string]$Url, [int]$Seconds, [string]$ErrorLog) {
    $Deadline = (Get-Date).AddSeconds($Seconds)
    do {
        if (Test-LocalService $Url 3) { return }
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $Deadline)
    if (Test-Path $ErrorLog) {
        Write-Warning "$Name failed. Last stderr lines:"
        Get-Content $ErrorLog -Tail 80 | ForEach-Object { Write-Warning $_ }
    }
    throw "$Name did not become healthy at $Url. Check $ErrorLog."
}

function Import-UserEnvironment([string]$Name) {
    $Value = [Environment]::GetEnvironmentVariable($Name, "User")
    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        Set-Item -Path "Env:$Name" -Value $Value
    }
}

Import-UserEnvironment "PREDICT_FUN_API_KEY"
if ([string]::IsNullOrWhiteSpace($env:PREDICT_FUN_API_KEY)) {
    throw "PREDICT_FUN_API_KEY is required for Target Wallet Official collection."
}

# Historical script name retained for Dashboard V2 compatibility only.
# 8776 no longer loads EBM models, sends TradeIntent, touches 8781, or enables
# any live trading path.
$env:PREDICT_LIVE_ENABLED = "false"
$env:PREDICT_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED = "false"

if (-not (Test-LocalService "http://127.0.0.1:8771/state")) {
    $Pid8771 = Get-ListeningProcessId 8771
    if ($Pid8771) {
        $Command = Get-ProcessCommandLine $Pid8771
        if (-not $Command.ToLowerInvariant().Contains("predict_bot.predict_fun_observer")) {
            throw "Port 8771 is occupied by an unrecognized process. PID=$Pid8771 command=$Command"
        }
    }
    else {
        Write-Host "Target Official: starting shared Predict.fun observer on 8771."
        $Process = Start-Process -FilePath "python" `
            -ArgumentList @("-m", "predict_bot.predict_fun_observer") `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "target-official-predict.stdout.log") `
            -RedirectStandardError (Join-Path $Data "target-official-predict.stderr.log") -PassThru
        $Process.Id | Set-Content (Join-Path $Root ".target-taker-echtgeld-predict.pid")
    }
    Wait-LocalService "8771 Predict.fun observer" "http://127.0.0.1:8771/state" 45 (Join-Path $Data "target-official-predict.stderr.log")
}

# Temporary compatibility only: the current Dashboard service definition still
# expects 8777 as a second member. New 8776 does not consume anything from 8777.
if (-not (Test-LocalService "http://127.0.0.1:8777/state")) {
    $Pid8777 = Get-ListeningProcessId 8777
    if ($Pid8777) {
        $Command = Get-ProcessCommandLine $Pid8777
        if (-not $Command.ToLowerInvariant().Contains("predict_bot.predict_wallet_taker_signal_collector")) {
            throw "Port 8777 is occupied by an unrecognized process. PID=$Pid8777 command=$Command"
        }
    }
    else {
        Write-Host "Target Official: starting temporary 8777 compatibility collector."
        $Process = Start-Process -FilePath "python" `
            -ArgumentList @("-m", "predict_bot.predict_wallet_taker_signal_collector") `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "target-official-8777-compat.stdout.log") `
            -RedirectStandardError (Join-Path $Data "target-official-8777-compat.stderr.log") -PassThru
        $Process.Id | Set-Content (Join-Path $Root ".target-taker-echtgeld-signal.pid")
    }
    Wait-LocalService "8777 compatibility collector" "http://127.0.0.1:8777/state" 45 (Join-Path $Data "target-official-8777-compat.stderr.log")
}

$Existing = Get-ListeningProcessId 8776
if ($Existing) {
    $Health = Get-JsonPayload "http://127.0.0.1:8776/health" 4
    if ($Health -and ([string]$Health.version) -eq $Expected8776Version) {
        Write-Host "Target Official: reusing healthy 8776 V2 PID=$Existing."
        Write-Host "  DB = $($Health.dbPath)"
        return
    }
    $Command = Get-ProcessCommandLine $Existing
    $ObservedVersion = if ($Health) { [string]$Health.version } else { "UNKNOWN" }
    throw "Port 8776 is still occupied by an older process (version=$ObservedVersion). Use Dashboard Diagnostics -> Restart Target Wallet Official, then retry. PID=$Existing command=$Command"
}

Write-Host "Target Official: starting clean 8776 V2 collector."
# The old module token is intentionally kept in the command line for safe
# Dashboard process ownership during the cutover. The module itself is now only
# a thin compatibility launcher into target_wallet_official_v2.
$Process = Start-Process -FilePath "python" `
    -ArgumentList @("-m", "predict_bot.predict_wallet_shadow_observer_v4_23") `
    -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Data "target-wallet-official.stdout.log") `
    -RedirectStandardError (Join-Path $Data "target-wallet-official.stderr.log") -PassThru
$Process.Id | Set-Content (Join-Path $Root ".target-taker-echtgeld-producer.pid")

Wait-LocalService "8776 Target Wallet Official V2" "http://127.0.0.1:8776/health" 60 (Join-Path $Data "target-wallet-official.stderr.log")
$Health = Get-JsonPayload "http://127.0.0.1:8776/health" 5
if (-not $Health -or ([string]$Health.version) -ne $Expected8776Version) {
    $ObservedVersion = if ($Health) { [string]$Health.version } else { "NO_HEALTH" }
    throw "8776 responded but reported version=$ObservedVersion instead of $Expected8776Version."
}
if ([bool]$Health.liveOrdersAffected -or [bool]$Health.strategyLogic) {
    throw "8776 unexpectedly reports strategy/live behavior. Refusing to continue."
}

Write-Host "Target Wallet Official V2 is running on 8776."
Write-Host "  BTC5M + ETH5M target fills = enabled"
Write-Host "  Parent Orders / Inventory / Official Performance = enabled"
Write-Host "  Legacy Target win/loss history = read-only display source"
Write-Host "  DB = $($Health.dbPath)"
Write-Host "  Retention = permanent unless manually archived"
Write-Host "  Strategy / EBM / Echtgeld / TradeIntent = removed from 8776"
