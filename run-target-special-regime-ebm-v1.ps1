param(
    [string]$SpecialStart = "2026-08-16T00:00:00+08:00",
    [string]$SpecialEnd = "",
    [switch]$SkipBuild,
    [switch]$SkipMaker
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET_TAKER_DIRECT_ELIGIBILITY_SPECIAL_REGIME_V1"
    Write-Host "Special window start: $SpecialStart"
    if ($SpecialEnd) {
        Write-Host "Special window end:   $SpecialEnd"
    } else {
        Write-Host "Special window end:   latest available data"
    }

    python -c "import sys, joblib, pandas, sklearn, interpret; print(f'Python {sys.version.split()[0]} | joblib {joblib.__version__} | interpret {interpret.__version__} | pandas {pandas.__version__} | sklearn {sklearn.__version__}')"
    if ($LASTEXITCODE -ne 0) {
        throw 'Research dependencies missing. Run: pip install -e ".[research]"'
    }
    Write-Host "Special-regime EBM backend: joblib threading/sharedmem (avoids loky resource_tracker on Windows/Python 3.13)"

    $LegacySignalDb = Join-Path $Root "data\wallet_taker_signals.db"
    $PublicArchiveDb = Join-Path $Root "data\public_research_archive_v1.db"
    $OfficialTargetDb = Join-Path $Root "data\target_wallet_official_v1.db"
    Write-Host "`nSignal archives:"
    if (Test-Path $LegacySignalDb) {
        Write-Host "  [legacy public] $LegacySignalDb"
    } else {
        Write-Warning "Legacy signal DB missing: $LegacySignalDb"
    }
    if (Test-Path $PublicArchiveDb) {
        Write-Host "  [target-blind public] $PublicArchiveDb"
    } else {
        Write-Warning "PUBLIC_RESEARCH_ARCHIVE_V1 missing: $PublicArchiveDb"
    }
    Write-Host "Target label sources:"
    Write-Host "  [legacy history] data\predict_wallet_shadow.db"
    if (Test-Path $OfficialTargetDb) {
        Write-Host "  [official observed truth] $OfficialTargetDb"
    } else {
        Write-Warning "TARGET_WALLET_OFFICIAL_V1 missing: $OfficialTargetDb"
        Write-Warning "The preflight will refuse training if legacy Target Taker labels do not cover the special window."
    }

    $labelArgs = @(
        ".\tools\build_target_taker_direct_eligibility_special_regime_official_v2.py",
        "--special-start", $SpecialStart
    )
    if ($SpecialEnd) { $labelArgs += @("--special-end", $SpecialEnd) }

    if (-not $SkipBuild) {
        Write-Host "`n[1/4] Build merged direct Target Taker eligibility dataset..."
        Write-Host "      public state: legacy 8777 + PUBLIC_RESEARCH_ARCHIVE_V1"
        Write-Host "      Target truth: legacy mirror history + TARGET_WALLET_OFFICIAL_V1 BTC/TAKER parents"
        Write-Host "      Official parent first_event_ms wins exact parent/order overlap."
        python @labelArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Taker eligibility dataset build or official-label preflight failed. Do not train stale/all-negative labels."
        }
    } else {
        Write-Host "`n[preflight] Verify the existing dataset against current official Target Taker truth..."
        $preflightArgs = @($labelArgs + "--preflight-only")
        python @preflightArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Target Taker official-label preflight failed. Rebuild before training."
        }
    }

    Write-Host "`n[2/4] Train/stress-test Target Taker direct eligibility EBM..."
    $takerArgs = @(
        ".\tools\run_with_joblib_threading.py",
        ".\tools\train_target_taker_direct_eligibility_special_regime_v1.py",
        "--special-start", $SpecialStart
    )
    if ($SpecialEnd) { $takerArgs += @("--special-end", $SpecialEnd) }
    python @takerArgs
    if ($LASTEXITCODE -ne 0) { throw "Taker special-regime EBM test failed." }

    if (-not $SkipMaker) {
        if (-not $SkipBuild) {
            Write-Host "`n[3/4] Build merged direct Target Maker datasets..."
            Write-Host "      legacy 8777 history + PUBLIC_RESEARCH_ARCHIVE_V1 when available"
            python .\tools\build_target_maker_direct_special_regime_multisource_v1.py
            if ($LASTEXITCODE -ne 0) { throw "Maker direct dataset build failed." }
        }

        Write-Host "`n[4/4] Stress-test Target Maker on the same special regime..."
        $makerArgs = @(
            ".\tools\run_with_joblib_threading.py",
            ".\tools\train_target_maker_special_regime_v1.py",
            "--special-start", $SpecialStart
        )
        if ($SpecialEnd) { $makerArgs += @("--special-end", $SpecialEnd) }
        python @makerArgs
        if ($LASTEXITCODE -ne 0) { throw "Maker special-regime EBM test failed." }
    }

    Write-Host "`nDone."
    Write-Host "Taker report: .\data\research\target_taker_direct_eligibility_special_regime_v1_report.json"
    Write-Host "Taker coverage meta: .\data\research\target_taker_direct_eligibility_special_regime_v1.meta.json"
    if (-not $SkipMaker) {
        Write-Host "Maker report: .\data\research\target_maker_special_regime_v1_report.json"
        Write-Host "Maker coverage meta: .\data\research\target_maker_direct_placement_v1.meta.json"
    }
}
finally {
    Pop-Location
}
