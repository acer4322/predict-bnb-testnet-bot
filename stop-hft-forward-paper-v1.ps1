$ErrorActionPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $Root '.hft-forward-paper-v1.pid'
if (Test-Path $PidFile) {
    $pidText = (Get-Content $PidFile -Raw).Trim()
    $pidValue = 0
    if ([int]::TryParse($pidText, [ref]$pidValue) -and $pidValue -gt 0) {
        $p = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        if ($p) { Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}
