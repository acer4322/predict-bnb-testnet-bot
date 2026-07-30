param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dashboard = Join-Path $Root "dashboard"
$ApiPort = 8766
$WebPort = 4310

function Get-LanIPv4 {
    $Routes = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix "0.0.0.0/0" `
        -ErrorAction SilentlyContinue | Sort-Object RouteMetric
    foreach ($Route in $Routes) {
        $Address = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $Route.InterfaceIndex `
            -ErrorAction SilentlyContinue | Where-Object {
                $_.IPAddress -ne "127.0.0.1" -and
                -not $_.IPAddress.StartsWith("169.254.")
            } | Select-Object -First 1
        if ($Address) {
            return $Address.IPAddress
        }
    }
    return $null
}

# Always clear previous copies first so an old five-second collector cannot
# share the same port with the current one.
& (Join-Path $Root "stop-local.ps1") -Quiet

$FirewallRuleName = "BTC 5M Lab - Local Subnet"
$FirewallRule = Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue
$FirewallPorts = if ($FirewallRule) {
    @($FirewallRule | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue).LocalPort -join ","
}
$FirewallReady = $FirewallRule -and $FirewallPorts -match "(^|,)4310(,|$)" -and `
    $FirewallPorts -match "(^|,)8766(,|$)"
if (-not $FirewallReady) {
    Write-Host "One-time Windows permission: allowing phones on your local network."
    $FirewallScript = Join-Path $Root "enable-lan-access.ps1"
    $Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$FirewallScript`""
    try {
        $FirewallSetup = Start-Process powershell.exe -Verb RunAs -ArgumentList $Arguments -Wait -PassThru
        if ($FirewallSetup.ExitCode -ne 0) {
            Write-Warning "LAN firewall access was not enabled. Localhost will still work."
        }
    }
    catch {
        Write-Warning "LAN firewall access was not enabled. Localhost will still work."
    }
}

# Use a dedicated API port. Port 8765 is intentionally left untouched because
# another local application may already own it.
$env:PREDICT_SIM_PORT = [string]$ApiPort

$UserApiKey = [Environment]::GetEnvironmentVariable("BINANCE_API_KEY", "User")
$UserApiSecret = [Environment]::GetEnvironmentVariable("BINANCE_API_SECRET", "User")
if ($UserApiKey) {
    $env:BINANCE_API_KEY = $UserApiKey
}
if ($UserApiSecret) {
    $env:BINANCE_API_SECRET = $UserApiSecret
}
$OptionalUserEnvironment = @(
    "BINANCE_LIVE_API_KEY",
    "BINANCE_LIVE_API_SECRET",
    "PREDICT_LIVE_ENABLED",
    "PREDICT_LIVE_ACCOUNT_TYPE",
    "PREDICT_AUTO_REDEEM_ENABLED",
    "PREDICT_API_RESTART_ERROR_THRESHOLD",
    "PREDICT_API_MAX_RESTARTS",
    "PREDICT_API_RESTART_WINDOW_SECONDS",
    "PREDICT_API_RESTART_DELAY_SECONDS",
    "PREDICT_MICRO_SNAPSHOT_RETENTION_HOURS"
)
foreach ($Name in $OptionalUserEnvironment) {
    $Value = [Environment]::GetEnvironmentVariable($Name, "User")
    if ($Value) {
        Set-Item -LiteralPath "Env:$Name" -Value $Value
    }
}
if (-not $env:BINANCE_API_KEY -or -not $env:BINANCE_API_SECRET) {
    Write-Host "Enter the read-only Binance HMAC credentials for this session only."
    $env:BINANCE_API_KEY = Read-Host "BINANCE_API_KEY"
    $SecureSecret = Read-Host "BINANCE_API_SECRET" -AsSecureString
    $SecretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureSecret)
    try {
        $env:BINANCE_API_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($SecretPtr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($SecretPtr)
    }
}

$Api = Start-Process -FilePath "python" -ArgumentList @("-m", "predict_bot.supervisor") `
    -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Root "data\api.stdout.log") `
    -RedirectStandardError (Join-Path $Root "data\api.stderr.log") -PassThru
$Api.Id | Set-Content (Join-Path $Root ".api.pid")

$Web = Start-Process -FilePath "npm.cmd" -ArgumentList @("run", "dev") `
    -WorkingDirectory $Dashboard -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Root "data\web.stdout.log") `
    -RedirectStandardError (Join-Path $Root "data\web.stderr.log") -PassThru
$Web.Id | Set-Content (Join-Path $Root ".web.pid")

$ApiUrl = "http://127.0.0.1:${ApiPort}/api/realtime"
$ApiStateUrl = "http://127.0.0.1:${ApiPort}/api/state"
$LocalUrl = "http://localhost:${WebPort}"
$ApiReady = $false
$WebReady = $false
$StartupDeadline = (Get-Date).AddSeconds(40)
while ((Get-Date) -lt $StartupDeadline -and (-not $ApiReady -or -not $WebReady)) {
    if (-not $ApiReady) {
        try {
            $ApiResponse = Invoke-WebRequest -Uri $ApiUrl -UseBasicParsing -TimeoutSec 3
            $ApiReady = $ApiResponse.StatusCode -eq 200
        }
        catch {
            if ($Api.HasExited) {
                $ApiError = Get-Content (Join-Path $Root "data\api.stderr.log") -Raw `
                    -ErrorAction SilentlyContinue
                throw "BTC 5M API failed to start. $ApiError"
            }
        }
    }
    if (-not $WebReady) {
        try {
            $WebResponse = Invoke-WebRequest -Uri $LocalUrl -UseBasicParsing -TimeoutSec 3
            $WebReady = $WebResponse.StatusCode -eq 200
        }
        catch {
            if ($Web.HasExited) {
                $WebError = Get-Content (Join-Path $Root "data\web.stderr.log") -Raw `
                    -ErrorAction SilentlyContinue
                throw "BTC 5M dashboard failed to start. $WebError"
            }
        }
    }
    if (-not $ApiReady -or -not $WebReady) {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $ApiReady -or -not $WebReady) {
    throw "BTC 5M Lab did not become ready within 40 seconds (API=$ApiReady, dashboard=$WebReady)."
}

$LanIp = Get-LanIPv4
Write-Host "BTC 5M Lab ready at $LocalUrl (API port $ApiPort)"
if ($LanIp) {
    $PhoneUrl = "http://${LanIp}:4310"
    $PhoneUrl | Set-Content (Join-Path $Root "data\phone-url.txt")
    Write-Host "PHONE (same Wi-Fi/LAN): $PhoneUrl"
}
else {
    Write-Warning "No LAN IPv4 address was found. Localhost is still available."
}
if (-not $NoBrowser) {
    Start-Process $LocalUrl
}
Write-Host "READY: the website is running in the background. You can type another command now."
if ($env:PREDICT_LIVE_ENABLED -match '^(1|true|yes|on)$') {
    try {
        $ReadyState = Invoke-RestMethod -Uri $ApiStateUrl -TimeoutSec 30
        $LiveState = $ReadyState.liveM0W
        Write-Warning (
            "REAL MONEY: status {0}; strategy {1}; per-market cap {2} USDT." -f `
                $LiveState.status, $LiveState.rules.strategy, $LiveState.rules.maxStakeUsdt
        )
    }
    catch {
        Write-Warning "REAL MONEY is configured, but its current state could not be read."
    }
}
if ($env:PREDICT_AUTO_REDEEM_ENABLED -notmatch '^(0|false|no|off)$') {
    Write-Host "AUTO REDEEM ENABLED: claimable winners are redeemed 60 seconds after settlement."
}
Write-Host "Keyboard: Ctrl+C cancels a foreground command; Esc clears the current input line."
