param(
    [string]$SpecialStart = "2026-08-16T12:00:00+08:00"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunStarted = Get-Date

function Write-ProgressLine {
    param([string]$Message)
    $Now = Get-Date
    $Elapsed = $Now - $RunStarted
    Write-Host ("[{0} +{1:00}:{2:00}] {3}" -f $Now.ToString("HH:mm:ss"), [int]$Elapsed.TotalMinutes, $Elapsed.Seconds, $Message)
}

function Invoke-TimedStep {
    param(
        [string]$Label,
        [scriptblock]$Action
    )
    $StepStarted = Get-Date
    Write-ProgressLine "$Label START"
    & $Action
    $Code = $LASTEXITCODE
    $Elapsed = (Get-Date) - $StepStarted
    if ($Code -ne 0) {
        throw "$Label failed with exit code $Code after $([math]::Round($Elapsed.TotalSeconds, 1))s."
    }
    Write-ProgressLine "$Label DONE ($([math]::Round($Elapsed.TotalSeconds, 1))s)"
}

Push-Location $Root
try {
    Write-ProgressLine "TARGET MAKER LIFECYCLE-SIDE PREFLIGHT V2.1"
    Write-ProgressLine "No EBM training. Strict-past parent first-fill state only."
    Write-ProgressLine "Ordinary evidence is primary; SPECIAL is only reported if the current behavior dataset actually contains it."

    $Behavior = Join-Path $Root "data\research\target_maker_direct_behavior_v1.csv"
    $MakerDb = Join-Path $Root "data\wallet_maker_book_inference.db"
    $Output = Join-Path $Root "data\research\target_maker_lifecycle_side_preflight_v2_1.json"

    if (-not (Test-Path $Behavior)) { throw "Missing behavior dataset: $Behavior" }
    if (-not (Test-Path $MakerDb)) { throw "Missing Maker DB: $MakerDb" }

    Invoke-TimedStep "[1/2] Syntax check" {
        python -m py_compile .\tools\preflight_target_maker_lifecycle_side_v2_1.py
    }

    Invoke-TimedStep "[2/2] Strict-past lifecycle-side statistics" {
        python .\tools\preflight_target_maker_lifecycle_side_v2_1.py `
            --behavior $Behavior `
            --maker-db $MakerDb `
            --output $Output `
            --special-start $SpecialStart
    }

    Write-ProgressLine "STOP GATE reached. No EBM was started."
    Write-Host ""
    Write-Host "Upload/review:"
    Write-Host "  data\research\target_maker_lifecycle_side_preflight_v2_1.json"
    Write-Host ""
    Write-Host "Primary fields:"
    Write-Host "  analyses.ORDINARY_PRE_SPECIAL.BEHAVIOR_COHORT_PRIOR"
    Write-Host "  analyses.ORDINARY_PRE_SPECIAL.DB_ELIGIBLE_PRIOR"
    Write-Host "  sameAsLastRate / towardMinoritySide.rate / rebalanceContrast"
}
finally {
    Pop-Location
}
