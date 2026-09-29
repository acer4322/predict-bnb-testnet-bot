$ErrorActionPreference = 'Stop'
$taskBuildRoot = Join-Path (Split-Path $PSScriptRoot -Parent) '.tmp/hft244_accounting_build_v1'
if (-not (Test-Path -LiteralPath $taskBuildRoot)) {
    New-Item -ItemType Directory -Path $taskBuildRoot | Out-Null
}
$taskBuildRoot = (Resolve-Path -LiteralPath $taskBuildRoot).Path
$env:CARGO_HOME = Join-Path $taskBuildRoot 'cargo'
$env:RUSTUP_HOME = Join-Path $taskBuildRoot 'rustup'
$env:RUSTUP_IO_THREADS = '2'
$taskInitPath = Join-Path $taskBuildRoot 'rustup-init.exe'
$taskRustupUrl = 'https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe'
$taskShaResponse = Invoke-WebRequest -Uri ($taskRustupUrl + '.sha256') -TimeoutSec 30
$taskShaText = if ($taskShaResponse.Content -is [byte[]]) { [Text.Encoding]::UTF8.GetString($taskShaResponse.Content) } else { [string]$taskShaResponse.Content }
$taskExpectedSha = ($taskShaText.Trim() -split '\s+')[0]
if ($taskExpectedSha -notmatch '^[0-9a-f]{64}$') { throw 'Invalid official rustup SHA256' }
if (-not (Test-Path -LiteralPath $taskInitPath)) {
    Invoke-WebRequest -Uri $taskRustupUrl -OutFile $taskInitPath -TimeoutSec 120
}
if ((Get-FileHash -LiteralPath $taskInitPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskExpectedSha) { throw 'Rustup hash mismatch' }
Write-Output ('verifiedRustupSha256=' + $taskExpectedSha)
# Child-process environment only. No global PATH, registry, or profile modification.
& $taskInitPath -y --no-modify-path --profile minimal --default-host x86_64-pc-windows-msvc --default-toolchain 1.93.1
if ($LASTEXITCODE -ne 0) { throw ('Isolated rustup failed: ' + $LASTEXITCODE) }
& (Join-Path $env:CARGO_HOME 'bin/rustc.exe') --version
& (Join-Path $env:CARGO_HOME 'bin/cargo.exe') --version
