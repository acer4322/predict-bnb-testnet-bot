param(
    [switch]$NoBrowser,
    [ValidateSet("BINANCE", "PREDICT", "PREDICT_DIRECT")]
    [string]$CloneVenue = ""
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

function Normalize-CloneVenue {
    param([string]$Value)
    $Normalized = ([string]$Value).Trim().ToUpperInvariant().Replace("-", "_")
    if ($Normalized -eq "PREDICT") { $Normalized = "PREDICT_DIRECT" }
    if ([string]::IsNullOrWhiteSpace($Normalized)) { return "BINANCE" }
    if ($Normalized -notin @("BINANCE", "PREDICT_DIRECT")) {
        throw "Invalid Wallet Maker Clone venue '$Value'. Expected BINANCE or PREDICT_DIRECT."
    }
    return $Normalized
}

# Echtgeld ETH/BNB normal-live engines still use BINANCE_LIVE_* even when the
# Wallet Maker Clone itself is switched to Predict Direct. Credentials are read
# only from Process/User/Machine environment variables; this launcher never
# prompts for or writes secrets.
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

# Venue can be selected globally with -CloneVenue, or persisted via
# PREDICT_WALLET_MAKER_CLONE_VENUE. Per-asset environment overrides remain
# available to the supervisor for mixed ETH/BNB A/B tests.
$PersistedGlobalVenue = Import-PersistedEnvironmentVariable -Name "PREDICT_WALLET_MAKER_CLONE_VENUE"
$EthVenueRaw = Import-PersistedEnvironmentVariable -Name "PREDICT_ETH_WALLET_MAKER_CLONE_VENUE"
$BnbVenueRaw = Import-PersistedEnvironmentVariable -Name "PREDICT_BNB_WALLET_MAKER_CLONE_VENUE"
if (-not [string]::IsNullOrWhiteSpace($CloneVenue)) {
    $ResolvedGlobalVenue = Normalize-CloneVenue $CloneVenue
    [Environment]::SetEnvironmentVariable("PREDICT_WALLET_MAKER_CLONE_VENUE", $ResolvedGlobalVenue, "Process")
}
else {
    $ResolvedGlobalVenue = Normalize-CloneVenue $PersistedGlobalVenue
    [Environment]::SetEnvironmentVariable("PREDICT_WALLET_MAKER_CLONE_VENUE", $ResolvedGlobalVenue, "Process")
}

$EthVenue = if ([string]::IsNullOrWhiteSpace($EthVenueRaw)) { $ResolvedGlobalVenue } else { Normalize-CloneVenue $EthVenueRaw }
$BnbVenue = if ([string]::IsNullOrWhiteSpace($BnbVenueRaw)) { $ResolvedGlobalVenue } else { Normalize-CloneVenue $BnbVenueRaw }
$PredictDirectRequested = ($EthVenue -eq "PREDICT_DIRECT") -or ($BnbVenue -eq "PREDICT_DIRECT")

if ($PredictDirectRequested) {
    $PredictApiKey = Import-PersistedEnvironmentVariable -Name "PREDICT_FUN_API_KEY"
    $PredictPrivateKey = Import-PersistedEnvironmentVariable -Name "PREDICT_FUN_PRIVATE_KEY"
    if ([string]::IsNullOrWhiteSpace($PredictPrivateKey)) {
        $PredictPrivateKey = Import-PersistedEnvironmentVariable -Name "PREDICT_FUN_PRIVY_PRIVATE_KEY"
    }
    $null = Import-PersistedEnvironmentVariable -Name "PREDICT_FUN_ACCOUNT_ADDRESS"
    $null = Import-PersistedEnvironmentVariable -Name "PREDICT_FUN_JWT"

    if ([string]::IsNullOrWhiteSpace($PredictApiKey) -or
        [string]::IsNullOrWhiteSpace($PredictPrivateKey)) {
        throw @"
Predict Direct V8 was selected but its trading credentials are incomplete.
Required:
  PREDICT_FUN_API_KEY
  PREDICT_FUN_PRIVATE_KEY   (or PREDICT_FUN_PRIVY_PRIVATE_KEY)
For a Predict web-app Smart Wallet also set:
  PREDICT_FUN_ACCOUNT_ADDRESS   (deposit / Predict Account address)
PREDICT_FUN_JWT is optional; the engine can generate/refresh JWT from the signing key.
"@
    }
    Write-Host "Predict Direct V8 credentials loaded from environment. ETH venue=$EthVenue; BNB venue=$BnbVenue."
}
else {
    Write-Host "Wallet Maker Clone V8 venue: BINANCE for ETH and BNB."
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
