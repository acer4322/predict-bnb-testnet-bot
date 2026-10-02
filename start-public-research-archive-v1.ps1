$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
$ExpectedVersion = "PUBLIC_RESEARCH_ARCHIVE_V1"
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

if (-not (Test-LocalService "http://127.0.0.1:8771/state")) {
    $Pid8771 = Get-ListeningProcessId 8771
    if ($Pid8771) {
        $Command = Get-ProcessCommandLine $Pid8771
        if (-not $Command.ToLowerInvariant().Contains("predict_bot.predict_fun_observer")) {
            throw "Port 8771 is occupied by an unrecognized process. PID=$Pid8771 command=$Command"
        }
    }
    else {
        if ([string]::IsNullOrWhiteSpace($env:PREDICT_FUN_API_KEY)) {
            throw "8771 is offline and PREDICT_FUN_API_KEY is unavailable. Start Predict.fun Observer first or set the user environment variable."
        }
        Write-Host "Public Research Archive: starting shared Predict.fun observer on 8771."
        $Predict = Start-Process -FilePath "python" `
            -ArgumentList @("-m", "predict_bot.predict_fun_observer") `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Data "public-research-predict.stdout.log") `
            -RedirectStandardError (Join-Path $Data "public-research-predict.stderr.log") -PassThru
        $Predict.Id | Set-Content (Join-Path $Root ".public-research-predict.pid")
    }
    Wait-LocalService "8771 Predict.fun observer" "http://127.0.0.1:8771/state" 45 (Join-Path $Data "public-research-predict.stderr.log")
}

$Existing = Get-ListeningProcessId 8783
if ($Existing) {
    $Health = Get-JsonPayload "http://127.0.0.1:8783/health" 4
    if ($Health -and ([string]$Health.version) -eq $ExpectedVersion) {
        Write-Host "Public Research Archive: reusing healthy 8783 PID=$Existing."
        Write-Host "  rows = $($Health.rows)"
        Write-Host "  dbBytes = $($Health.dbBytes)"
        Write-Host "  retentionDays = $($Health.retentionDays)"
        return
    }
    $Command = Get-ProcessCommandLine $Existing
    $ObservedVersion = if ($Health) { [string]$Health.version } else { "UNKNOWN" }
    throw "Port 8783 is occupied by an unexpected process (version=$ObservedVersion). PID=$Existing command=$Command"
}

Write-Host "Public Research Archive: starting target-blind collector on 8783."
$Process = Start-Process -FilePath "python" `
    -ArgumentList @("-m", "predict_bot.public_research_archive_v1") `
    -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Data "public-research-archive-v1.stdout.log") `
    -RedirectStandardError (Join-Path $Data "public-research-archive-v1.stderr.log") -PassThru
$Process.Id | Set-Content (Join-Path $Root ".public-research-archive-v1.pid")

Wait-LocalService "8783 Public Research Archive V1" "http://127.0.0.1:8783/health" 60 (Join-Path $Data "public-research-archive-v1.stderr.log")
$Health = Get-JsonPayload "http://127.0.0.1:8783/health" 5
if (-not $Health -or ([string]$Health.version) -ne $ExpectedVersion) {
    $ObservedVersion = if ($Health) { [string]$Health.version } else { "NO_HEALTH" }
    throw "8783 responded but reported version=$ObservedVersion instead of $ExpectedVersion."
}
if (-not [bool]$Health.targetBlind) {
    throw "8783 did not report targetBlind=true. Refusing to accept the collector as the public research archive."
}

Write-Host "PUBLIC_RESEARCH_ARCHIVE_V1 is running on 8783."
Write-Host "  Predict source = 8771"
Write-Host "  Binance Spot/Futures = direct public websocket"
Write-Host "  Chainlink = direct public websocket"
Write-Host "  Raw websocket archive = disabled"
Write-Host "  Compact snapshot interval = 250 ms (default)"
Write-Host "  Retention = 30 days (default, configurable by env)"
Write-Host "  DB = data/public_research_archive_v1.db"
Write-Host "  Target Wallet / 8776 inputs = none"
