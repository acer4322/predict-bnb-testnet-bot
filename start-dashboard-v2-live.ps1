param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Import-PersistedEnvironmentVariable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $current = [Environment]::GetEnvironmentVariable($Name, "Process")
    if (-not [string]::IsNullOrWhiteSpace($current)) {
        return $current
    }

    $userValue = [Environment]::GetEnvironmentVariable($Name, "User")
    if (-not [string]::IsNullOrWhiteSpace($userValue)) {
        [Environment]::SetEnvironmentVariable($Name, $userValue, "Process")
        return $userValue
    }

    $machineValue = [Environment]::GetEnvironmentVariable($Name, "Machine")
    if (-not [string]::IsNullOrWhiteSpace($machineValue)) {
        [Environment]::SetEnvironmentVariable($Name, $machineValue, "Process")
        return $machineValue
    }

    return $null
}

# Echtgeld ETH/BNB and Wallet Maker Clone intentionally use the dedicated
# BINANCE_LIVE_* credential pair.  Credentials are now expected to be stored in
# the Windows Process/User/Machine environment; this launcher never prompts for
# or writes credentials.  User/Machine values are copied into this process so
# the Dashboard V2 child processes inherit them even when this PowerShell window
# was opened before the variables were saved.
$LiveApiKey = Import-PersistedEnvironmentVariable -Name "BINANCE_LIVE_API_KEY"
$LiveApiSecret = Import-PersistedEnvironmentVariable -Name "BINANCE_LIVE_API_SECRET"

if ([string]::IsNullOrWhiteSpace($LiveApiKey) -or
    [string]::IsNullOrWhiteSpace($LiveApiSecret)) {
    throw @"
Binance LIVE credentials were not found in the environment.
Expected variables:
  BINANCE_LIVE_API_KEY
  BINANCE_LIVE_API_SECRET
Set them as Windows User or Machine environment variables, then run this launcher again.
"@
}

Write-Host "Binance LIVE credentials loaded from environment; interactive credential input skipped."

$Launcher = Join-Path $Root "start-dashboard-v2.ps1"
if (-not (Test-Path $Launcher)) {
    throw "start-dashboard-v2.ps1 not found at $Launcher"
}

if ($NoBrowser) {
    & $Launcher -NoBrowser
}
else {
    & $Launcher
}
