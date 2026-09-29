[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$McpPort = 8000,
    [ValidateRange(1024, 65535)]
    [int]$HealthPort = 8081,
    [string]$McpConnectionMaxTtl = "24h",
    [ValidateRange(1, 1000)]
    [int]$McpMaxConcurrentRequests = 4,
    [ValidateSet("debug", "info", "warn")]
    [string]$LogLevel = "debug",
    [string]$LogFile = "data\mcp-tunnel.log"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$TunnelClient = Join-Path $ProjectRoot ".tools\tunnel-client\tunnel-client.exe"

function Get-SecretEnvironmentValue {
    param([Parameter(Mandatory)][string]$Name)

    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        $value = [Environment]::GetEnvironmentVariable($Name, "User")
    }
    return $value
}

if (-not (Test-Path -LiteralPath $TunnelClient)) {
    throw "Missing $TunnelClient. Install the official tunnel-client release first."
}
$env:CONTROL_PLANE_TUNNEL_ID = Get-SecretEnvironmentValue -Name "CONTROL_PLANE_TUNNEL_ID"
$env:CONTROL_PLANE_API_KEY = Get-SecretEnvironmentValue -Name "CONTROL_PLANE_API_KEY"
if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_TUNNEL_ID)) {
    throw "Set CONTROL_PLANE_TUNNEL_ID in the Process or User environment first."
}
if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)) {
    throw "Set CONTROL_PLANE_API_KEY in the Process or User environment first."
}
if ([string]::IsNullOrWhiteSpace($McpConnectionMaxTtl)) {
    throw "McpConnectionMaxTtl must be a valid duration such as 24h or 30m."
}

$ResolvedLogFile = if ([System.IO.Path]::IsPathRooted($LogFile)) {
    $LogFile
} else {
    Join-Path $ProjectRoot $LogFile
}
$LogDirectory = Split-Path -Parent $ResolvedLogFile
if (-not [string]::IsNullOrWhiteSpace($LogDirectory)) {
    New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
}

$McpUrl = "http://127.0.0.1:$McpPort/mcp"
$HealthAddr = "127.0.0.1:$HealthPort"
Write-Host "Forwarding $McpUrl through OpenAI Secure MCP Tunnel"
Write-Host "Tunnel-client health UI: http://$HealthAddr/ui"
Write-Host "MCP connection max TTL: $McpConnectionMaxTtl"
Write-Host "MCP max concurrent requests: $McpMaxConcurrentRequests"
Write-Host "Tunnel log: $ResolvedLogFile ($LogLevel)"
Write-Host "Credentials are read from environment variables and are not written to argv or files."

& $TunnelClient run `
    --control-plane.tunnel-id $env:CONTROL_PLANE_TUNNEL_ID `
    --control-plane.api-key env:CONTROL_PLANE_API_KEY `
    --mcp.server-url $McpUrl `
    --mcp.connection-max-ttl $McpConnectionMaxTtl `
    --mcp.max-concurrent-requests $McpMaxConcurrentRequests `
    --health.listen-addr $HealthAddr `
    --log.level $LogLevel `
    --log.file $ResolvedLogFile
exit $LASTEXITCODE
