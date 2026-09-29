param([ValidateSet('baseline','candidate')][string]$Variant='baseline')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'invoke_bounded_native_build_v1.ps1')
$taskRepo=Split-Path $PSScriptRoot -Parent
$taskBuild=Join-Path $taskRepo '.tmp/hft244_accounting_build_v1'
if (Test-Path -LiteralPath (Join-Path $taskBuild ($Variant+'.dll'))) { throw 'Native artifact already exists; inspect/test it, do not rebuild' }
$taskSource=Join-Path $taskRepo '.tmp/hft244_accounting_source_v1'
$taskSdk=Join-Path $taskBuild 'sdk'
$taskPackages=@(
    @{name='microsoft.windows.sdk.cpp.x64.10.0.26100.4202.nupkg';sha='fe1de67646c6a6f8580669a04856b89044e08ddcf5e2b11e44cca4b8275562c9';pattern='^c/(um|ucrt)/x64/[^/]+\.[Ll][Ii][Bb]$'},
    @{name='microsoft.windows.sdk.cpp.10.0.26100.4202.nupkg';sha='11f7413c19ea87216a173a53e91e83fcb58f4ee7c4ca5239d4b92053ac2ea835';pattern='^c/Include/10\.0\.26100\.0/(ucrt|shared|um)/.*[^/]$'}
)
if (-not(Test-Path -LiteralPath $taskSdk)) { New-Item -ItemType Directory -Path $taskSdk | Out-Null }
foreach($taskPackage in $taskPackages) {
    $taskArchive=Join-Path $taskBuild $taskPackage.name
    if ((Get-FileHash -LiteralPath $taskArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskPackage.sha) { throw 'SDK archive hash mismatch' }
    $taskZip=[IO.Compression.ZipFile]::OpenRead($taskArchive)
    try {
        $taskEntries=@($taskZip.Entries | Where-Object { $_.FullName -match $taskPackage.pattern })
        if (($taskEntries | Measure-Object Length -Sum).Sum -gt 600MB) { throw 'SDK extraction budget exceeded' }
        foreach($taskEntry in $taskEntries) {
            $taskDest=[IO.Path]::GetFullPath((Join-Path $taskSdk $taskEntry.FullName))
            if (-not $taskDest.StartsWith($taskSdk+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'SDK archive path escape' }
            if (-not(Test-Path -LiteralPath $taskDest)) {
                [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($taskDest)) | Out-Null
                [IO.Compression.ZipFileExtensions]::ExtractToFile($taskEntry,$taskDest,$false)
            }
        }
        Write-Output ('sdkEntries=' + $taskEntries.Count)
    } finally { $taskZip.Dispose() }
}
Import-Module 'C:/Program Files/Microsoft Visual Studio/18/Community/Common7/Tools/Microsoft.VisualStudio.DevShell.dll'
Enter-VsDevShell -VsInstallPath 'C:/Program Files/Microsoft Visual Studio/18/Community' -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64' | Out-Null
$env:LIB=(Join-Path $taskSdk 'c/um/x64')+';'+(Join-Path $taskSdk 'c/ucrt/x64')+';'+$env:LIB
$env:INCLUDE=(Join-Path $taskSdk 'c/Include/10.0.26100.0/ucrt')+';'+(Join-Path $taskSdk 'c/Include/10.0.26100.0/shared')+';'+(Join-Path $taskSdk 'c/Include/10.0.26100.0/um')+';'+$env:INCLUDE
$taskFs=New-Object -ComObject Scripting.FileSystemObject
$taskShortBuild=$taskFs.GetFolder($taskBuild).ShortPath
$taskCargoCache=Join-Path ([IO.Path]::GetTempPath()) 'btc5m-hft244-cargo-v1'
if (-not(Test-Path -LiteralPath $taskCargoCache)) {
    New-Item -ItemType Directory -Path $taskCargoCache | Out-Null
    Copy-Item -LiteralPath (Join-Path $taskBuild 'cargo/registry') -Destination $taskCargoCache -Recurse
}
$env:CARGO_HOME=$taskCargoCache
$env:RUSTUP_HOME=Join-Path $taskShortBuild 'rustup'
$env:CARGO_TARGET_DIR=Join-Path $taskShortBuild 'target'
$env:RUSTFLAGS='--sysroot='+(Join-Path $taskShortBuild 'rustup/toolchains/1.93.1-x86_64-pc-windows-msvc')
$env:CARGO_BUILD_JOBS='2'
$env:PYO3_PYTHON=(Get-Command python.exe).Source
$env:PATH=(Join-Path $taskShortBuild 'cargo/bin')+';'+$env:PATH
$taskCargo=Join-Path $taskShortBuild 'cargo/bin/cargo.exe'
if (-not(Test-Path (Join-Path $taskSdk 'c/um/x64/kernel32.Lib'))) { throw 'kernel32 library missing' }
if (-not(Test-Path (Join-Path $taskSdk 'c/ucrt/x64/ucrt.lib'))) { throw 'UCRT library missing' }
Push-Location $taskSource
try {
    if (-not(Test-Path Cargo.lock)) {
        if ($Variant -ne 'baseline') { throw 'Candidate cannot resolve a new lockfile' }
        & $taskCargo generate-lockfile
        if ($LASTEXITCODE -ne 0) { throw 'Dependency resolution failed' }
    }
    Write-Output ('cargoLockSha256='+(Get-FileHash Cargo.lock -Algorithm SHA256).Hash)
    $taskLog=Join-Path $taskBuild ($Variant+'-build-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
    $taskRun=Invoke-BoundedNativeBuild -FilePath $taskCargo -Arguments @('build','--locked','-p','py-hftbacktest','-j','2') -WorkingDirectory $taskSource -LogPrefix $taskLog -TimeoutSeconds 600
    $taskRun | ConvertTo-Json -Compress
    Get-Content -LiteralPath $taskRun.stderr -Tail 8
    if ($taskRun.timedOut -or -not $taskRun.exited -or $taskRun.exitCode -ne 0) { throw 'Build failed or hit hard 600-second deadline' }
    $taskBuilt=Join-Path $env:CARGO_TARGET_DIR 'debug/hftbacktest.dll'
    $taskOutput=Join-Path $taskBuild ($Variant+'.dll')
    if (Test-Path -LiteralPath $taskOutput) { throw 'Immutable native build already exists' }
    Copy-Item -LiteralPath $taskBuilt -Destination $taskOutput
    Get-FileHash -LiteralPath $taskOutput -Algorithm SHA256 | Format-List Path,Hash
} finally { Pop-Location }
