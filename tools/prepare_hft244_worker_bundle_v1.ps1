$ErrorActionPreference='Stop'
$taskRepo=Split-Path $PSScriptRoot -Parent
$taskBundle=Join-Path $taskRepo '.lan_worker_v1/hft244_worker_repair_20260910_v1'
if (Test-Path -LiteralPath $taskBundle) { throw 'Immutable bundle exists' }
New-Item -ItemType Directory -Path $taskBundle | Out-Null
function New-TaskTar([string]$Name,[string]$Base,[string[]]$Entries) {
    $taskDest=Join-Path $taskBundle $Name
    & tar.exe -cf $taskDest --exclude='*.pdb' --exclude='__pycache__' -C $Base @Entries
    if ($LASTEXITCODE -ne 0) { throw "Archive failed: $Name" }
}
$taskBuild=Join-Path $taskRepo '.tmp/hft244_accounting_build_v1'
New-TaskTar 'rust.tar' (Join-Path $taskBuild 'rustup/toolchains/1.93.1-x86_64-pc-windows-msvc') @('bin','lib')
New-TaskTar 'registry.tar' (Join-Path ([IO.Path]::GetTempPath()) 'btc5m-hft244-cargo-v1') @('registry')
New-TaskTar 'source.tar' (Join-Path $taskRepo '.tmp/hft244_accounting_source_v1') @('Cargo.toml','Cargo.lock','LICENSE','hftbacktest','hftbacktest-derive','py-hftbacktest')
$taskVc='C:/Program Files/Microsoft Visual Studio/18/Community/VC/Tools/MSVC/14.51.36231'
New-TaskTar 'msvc.tar' $taskVc @('bin/Hostx64/x64','include','lib/x64/msvcrt.lib','lib/x64/vcruntime.lib','lib/x64/oldnames.lib','lib/x64/libcmt.lib','lib/x64/libvcruntime.lib','lib/x64/msvcprt.lib','lib/x64/delayimp.lib','lib/x64/legacy_stdio_definitions.lib','lib/x64/legacy_stdio_wide_specifiers.lib','lib/x64/iso_stdio_wide_specifiers.lib','lib/x64/aligned_new.lib','lib/x64/concrt.lib','lib/x64/chkstk.obj')
New-TaskTar 'support.tar' $taskRepo @('tools/probe_hft244_partial_receipt_accounting_v1.py','tools/check_hft244_rebuilt_smoke_v1.py','tools/check_hft244_extended_receipts_v1.py','data/research/r4_v0/p0_provenance_v1/HFT244_PARTIAL_RECEIPT_ACCOUNTING_COMPACT_V1_20260910.json')
$taskOriginal=Join-Path $taskRepo '.tmp/hftbacktest_244/hftbacktest'
$taskWrapperNames=@(Get-ChildItem -LiteralPath $taskOriginal -Filter '*.py' -File | ForEach-Object Name)
New-TaskTar 'wrappers.tar' $taskOriginal $taskWrapperNames
foreach($taskSdkName in @('microsoft.windows.sdk.cpp.x64.10.0.26100.4202.nupkg','microsoft.windows.sdk.cpp.10.0.26100.4202.nupkg')) {
    Copy-Item -LiteralPath (Join-Path $taskBuild $taskSdkName) -Destination $taskBundle
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'run_hft244_worker_repair_v1.py') -Destination $taskBundle
$taskFiles=@(Get-ChildItem -LiteralPath $taskBundle -File | ForEach-Object {
    @{name=$_.Name;bytes=$_.Length;sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
})
$taskManifest=@{version='HFT244_WORKER_REPAIR_BUNDLE_V1';files=$taskFiles;marketBE=0;maxBuildSeconds=600;maxCargoJobs=4;incremental=$false}
$taskManifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $taskBundle 'MANIFEST.json') -Encoding UTF8
$taskFiles | Select-Object name,bytes | Format-Table
Write-Output ('totalBytes='+($taskFiles | Measure-Object -Property bytes -Sum).Sum)
