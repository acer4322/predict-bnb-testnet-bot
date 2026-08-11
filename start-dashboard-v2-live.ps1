param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

# Real-money ETH/BNB/Wallet-Clone executors intentionally require the dedicated
# BINANCE_LIVE_* pair.  Do not silently reuse the read-only BINANCE_API_* pair.
# Credentials entered here live only in this PowerShell process and are inherited
# by the Dashboard V2 child processes; they are not written to disk or User env.
if ([string]::IsNullOrWhiteSpace($env:BINANCE_LIVE_API_KEY) -or
    [string]::IsNullOrWhiteSpace($env:BINANCE_LIVE_API_SECRET)) {
    Write-Host "Enter Binance LIVE HMAC credentials for this session only."
    Write-Host "These are required by ETH/BNB Echtgeld and Wallet Maker Clone; they are not saved to disk."
    $env:BINANCE_LIVE_API_KEY = Read-Host "BINANCE_LIVE_API_KEY"
    if ([string]::IsNullOrWhiteSpace($env:BINANCE_LIVE_API_KEY)) {
        throw "BINANCE_LIVE_API_KEY is required for Echtgeld/Wallet Maker Clone."
    }

    $SecureSecret = Read-Host "BINANCE_LIVE_API_SECRET" -AsSecureString
    $SecretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureSecret)
    try {
        $env:BINANCE_LIVE_API_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($SecretPtr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($SecretPtr)
    }
    if ([string]::IsNullOrWhiteSpace($env:BINANCE_LIVE_API_SECRET)) {
        throw "BINANCE_LIVE_API_SECRET is required for Echtgeld/Wallet Maker Clone."
    }
}

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
