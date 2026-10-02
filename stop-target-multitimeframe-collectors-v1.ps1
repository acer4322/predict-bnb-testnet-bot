param([switch]$Quiet)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ModuleNeedle = "predict_bot.predict_wallet_maker_book_inference_collector_multitimeframe"
$Specs = @(
    @{ Label="BTC15M"; Port=8801; Pid=".target-mtf-btc15m.pid" },
    @{ Label="ETH15M"; Port=8802; Pid=".target-mtf-eth15m.pid" },
    @{ Label="BTC1H"; Port=8803; Pid=".target-mtf-btc1h.pid" }
)

function Stop-Tree([int]$ProcessId, [string]$Label) {
    if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) { return }
    & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
    if (-not $Quiet) { Write-Host "Stopped $Label collector tree ($ProcessId)." }
}

foreach ($Spec in $Specs) {
    $PidPath = Join-Path $Root ([string]$Spec.Pid)
    if (Test-Path $PidPath) {
        try {
            $Text = (Get-Content $PidPath -Raw).Trim()
            if ($Text -match '^\d+$') { Stop-Tree ([int]$Text) ([string]$Spec.Label) }
        }
        finally { Remove-Item $PidPath -Force -ErrorAction SilentlyContinue }
    }

    $Listeners = @(Get-NetTCPConnection -LocalPort ([int]$Spec.Port) -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($ListenerPid in $Listeners) {
        try { $Command = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ListenerPid" -ErrorAction Stop).CommandLine }
        catch { continue }
        if ($Command.ToLowerInvariant().Contains($ModuleNeedle.ToLowerInvariant())) {
            Stop-Tree ([int]$ListenerPid) ([string]$Spec.Label)
        }
        elseif (-not $Quiet) {
            Write-Warning "Leaving unrecognized $($Spec.Port) listener untouched (PID=$ListenerPid; command=$Command)."
        }
    }
}

if (-not $Quiet) { Write-Host "Multi-timeframe Target collectors stopped." }
