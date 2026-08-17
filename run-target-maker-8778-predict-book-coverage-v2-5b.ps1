param(
    [string]$Db = 'data/wallet_maker_book_inference.db',
    [string]$BookDb = '',
    [string]$Out = 'target_maker_8778_predict_book_coverage_v2_5b.json',
    [string]$SpecialStart = '2026-08-16T00:00:00+08:00',
    [string]$SpecialEnd = '2026-08-16T12:00:00+08:00'
)

$ErrorActionPreference = 'Stop'

$started = Get-Date
function Stamp([string]$Message) {
    $elapsed = (Get-Date) - $started
    Write-Host ("[{0:HH:mm:ss} +{1:mm\:ss}] {2}" -f (Get-Date), $elapsed, $Message)
}

$CoreAudit = '.\tools\audit_target_maker_8778_predict_book_coverage_v2_5b.py'
$V21Audit = '.\tools\audit_target_maker_8778_predict_book_coverage_v2_5b_v21.py'

Stamp 'syntax check V2.5b core + V2.1 parent adapter'
python -m py_compile $CoreAudit $V21Audit
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$argsList = @(
    $V21Audit,
    '--db', $Db,
    '--out', $Out,
    '--special-start', $SpecialStart,
    '--special-end', $SpecialEnd
)
if ($BookDb) {
    $argsList += @('--book-db', $BookDb)
}

Stamp "audit bounded pre-noon 8/16 window against live 8778 checkpoint+changes_z with V2.1 parent schema: [$SpecialStart, $SpecialEnd)"
python @argsList
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Stamp 'done'
