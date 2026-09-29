param([switch]$NoBrowser)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Get-ListeningProcessId([int]$Port) {
    try { return [int](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1).OwningProcess }
    catch { return $null }
}
function Get-ProcessCommandLine([int]$ProcessId) {
    try { return [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop).CommandLine }
    catch { return "" }
}
function Test-Url([string]$Url) {
    try { return (Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3).StatusCode -eq 200 }
    catch { return $false }
}
function Wait-Url([string]$Name,[string]$Url,[int]$Seconds,[string]$Log) {
    $deadline=(Get-Date).AddSeconds($Seconds)
    while((Get-Date)-lt $deadline) {
        if(Test-Url $Url){ return }
        Start-Sleep -Milliseconds 400
    }
    if(Test-Path $Log){ Get-Content $Log -Tail 60 | ForEach-Object { Write-Warning $_ } }
    throw "$Name failed to become healthy: $Url"
}
function Start-Known([int]$Port,[string]$Token,[string]$Module,[string]$PidFile,[string]$Out,[string]$Err) {
    $existing=Get-ListeningProcessId $Port
    if($existing) {
        $cmd=Get-ProcessCommandLine $existing
        if(-not $cmd.ToLowerInvariant().Contains($Token.ToLowerInvariant())) {
            throw "Port $Port occupied by unrecognized process PID=$existing command=$cmd"
        }
        Write-Host "BNB collector: reusing $Port PID=$existing"
        return
    }
    $p=Start-Process -FilePath "python" -ArgumentList @("-m",$Module) -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Data $Out) -RedirectStandardError (Join-Path $Data $Err) -PassThru
    $p.Id | Set-Content (Join-Path $Root $PidFile)
    Write-Host "BNB collector: started $Module PID=$($p.Id) port=$Port"
}

$UserKey=[Environment]::GetEnvironmentVariable("PREDICT_FUN_API_KEY","User")
if($UserKey){$env:PREDICT_FUN_API_KEY=$UserKey}
if([string]::IsNullOrWhiteSpace($env:PREDICT_FUN_API_KEY)){ throw "PREDICT_FUN_API_KEY required" }

# Research-only children must never inherit enabled trading flags.
$env:PREDICT_LIVE_ENABLED="false"
$env:PREDICT_POLY_GAP_LIVE_ENABLED="false"
$env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED="false"
$env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED="false"
$env:PREDICT_WALLET_MAKER_TARGET_INFERENCE_ENABLED="true"
$env:PREDICT_WALLET_MAKER_BOOK_RETENTION_HOURS="72"
$env:PREDICT_BNB_EXECUTION_TAPE_RETENTION_DAYS="30"
$env:PREDICT_BNB_PUBLIC_INTERVAL_MS="500"
$env:PREDICT_BNB_PUBLIC_RETENTION_DAYS="30"

if(-not (Test-Url "http://127.0.0.1:8771/state")){ throw "Shared Predict.fun observer 8771 must be online first" }
if(-not (Test-Url "http://127.0.0.1:8776/health")){ throw "Target Official 8776 must be online first" }

Start-Known 8788 "predict_wallet_maker_book_inference_collector_bnb5m" "predict_bot.predict_wallet_maker_book_inference_collector_bnb5m" ".target-bnb5m-maker-book.pid" "target-bnb5m-maker-book.stdout.log" "target-bnb5m-maker-book.stderr.log"
Wait-Url "BNB full-book collector" "http://127.0.0.1:8788/health" 45 (Join-Path $Data "target-bnb5m-maker-book.stderr.log")

Start-Known 8797 "public_research_archive_bnb5m_v1" "predict_bot.public_research_archive_bnb5m_v1" ".target-bnb5m-public.pid" "target-bnb5m-public.stdout.log" "target-bnb5m-public.stderr.log"
Wait-Url "BNB public archive" "http://127.0.0.1:8797/health" 45 (Join-Path $Data "target-bnb5m-public.stderr.log")

Write-Host "BNB5M target research collection is online."
Write-Host "  8776 = shared Target Official BTC/ETH/BNB fills + parents + settlement"
Write-Host "  8788 = BNB full-book / lifecycle / Execution Tape (live DB 72h; tape 30d)"
Write-Host "  8797 = BNB public fair-value compact archive (500ms; 30d; no raw websocket persistence)"
Write-Host "  Live trading = disabled for these child processes"
if(-not $NoBrowser){ Start-Process "http://127.0.0.1:8788/state" }
