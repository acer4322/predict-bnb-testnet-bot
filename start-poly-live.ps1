param(
    [switch]$NoBrowser
)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Start = Join-Path $Root "start-local.ps1"
& $Start -Profile POLY_LIVE -NoBrowser:$NoBrowser
