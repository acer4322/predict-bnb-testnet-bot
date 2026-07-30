$ErrorActionPreference = "Stop"

$Principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Administrator permission is required to add the local-subnet firewall rule."
}

$RuleName = "BTC 5M Lab - Local Subnet"
$Existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($Existing) {
    $Existing | Set-NetFirewallRule -Enabled True -Action Allow -Profile Any
    $Existing | Get-NetFirewallPortFilter |
        Set-NetFirewallPortFilter -Protocol TCP -LocalPort 4310,8766
}
else {
    New-NetFirewallRule -DisplayName $RuleName `
        -Description "Allow BTC 5M Lab dashboard and protected API from the local subnet." `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort 4310,8766 `
        -Profile Any -RemoteAddress LocalSubnet | Out-Null
}

Write-Host "BTC 5M Lab LAN access enabled for TCP ports 4310 and 8766 (LocalSubnet only)."
