param(
    [string]$SpecialStart = "2026-08-16T12:00:00+08:00",
    [int]$FitStrideSeconds = 2,
    [int]$MinTrainMarkets = 180,
    [int]$TestMarkets = 50,
    [int]$MaxFolds = 7,
    [int]$Interactions = 6,
    [int]$MaxRounds = 500,
    [int]$OuterBags = 4,
    [double]$MinOrdinarySettlementCoverage = 0.95,
    [double]$MinSpecialSettlementCoverage = 0.95,
    [double]$MinCanonicalOutcomeCoverage = 0.95,
    [double]$MinTargetPnlCoverage = 0.95,
    [int]$SettlementApiRetries = 8,
    [int]$SettlementRetryBaseDelayMs = 1000,
    [int]$SettlementBackfillPasses = 3,
    [int]$SettlementPassCooldownSeconds = 10,
    [switch]$RunTests,
    [switch]$SkipSettlementBackfill
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET MAKER CONTROLLER V3.3 PORTFOLIO-PNL SEMANTICS"
    Write-Host "Target WIN/LOSS: combined target_market_results.net_pnl_usdt >0 / <0; zero is FLAT."
    Write-Host "Official UP/DOWN: market outcome only, used for settlement and heavy-side survival probability."
    Write-Host "Fit: ordinary only, chronological market walk-forward; special cohort never used for fit/calibration."
    Write-Host "Compare: raw Predict + past-only calibrated Predict vs PREDICT_CONTEXT / LEAN_SPOT / FULL_PUBLIC EBM."
    Write-Host "Integrity: market-outcome coverage, Target portfolio-PnL coverage, exact timestamp repair joins."
    Write-Host "Legacy V2 heavy_side_won concordance is diagnostic only and is re-derived canonically for survival audit."
    Write-Host "Settlement API: transient 429/5xx uses Retry-After or exponential backoff; cached passes retry only when diagnostics show a transient cause."
    Write-Host "Research only. No cutoff or live rule promotion."

    $PublicDataset = Join-Path $Root "data\research\target_taker_action_burst_hazard_v1.csv"
    $RiskCsv = Join-Path $Root "data\research\target_maker_taker_repair_hazard_v2_risk.csv"
    $SettlementDb = Join-Path $Root "data\research\target_maker_survival_settlements_v3.db"
    $SettlementReport = Join-Path $Root "data\research\target_maker_survival_settlements_v3_report.json"
    $TargetDb = Join-Path $Root "data\target_wallet_official_v1.db"

    if (-not (Test-Path $PublicDataset)) { throw "Missing frozen public dataset: $PublicDataset" }
    if (-not (Test-Path $RiskCsv)) {
        throw "Missing V2 repair risk-set: $RiskCsv`nRun .\run-target-maker-taker-repair-hazard-complete-set-v2.ps1 first."
    }
    if (-not (Test-Path $TargetDb)) { throw "Missing official Target accounting DB: $TargetDb" }

    Write-Host "`n[1/4] Validate V3/V3.1/V3.2/V3.3 scripts..."
    python -m py_compile `
        .\tools\backfill_target_maker_survival_settlements_v3.py `
        .\tools\backfill_target_maker_survival_settlements_v3_2.py `
        .\tools\backfill_target_maker_survival_settlements_v3_3.py `
        .\tools\analyze_target_maker_heavy_survival_v3.py `
        .\tools\analyze_target_maker_heavy_survival_v3_1.py `
        .\tools\analyze_target_maker_heavy_survival_v3_3.py
    if ($LASTEXITCODE -ne 0) { throw "V3.3 syntax check failed." }

    if ($RunTests) {
        Write-Host "`n[2/4] Run V3 + V3.1 + V3.2 + V3.3 semantic/leakage/integrity tests..."
        python -m pytest -q `
            .\tests\test_target_maker_heavy_survival_v3.py `
            .\tests\test_target_maker_heavy_survival_v3_1.py `
            .\tests\test_target_maker_survival_settlement_resolution_v3_2.py `
            .\tests\test_target_maker_portfolio_pnl_semantics_v3_3.py
        if ($LASTEXITCODE -ne 0) { throw "V3.3 tests failed." }
    }
    else {
        Write-Host "`n[2/4] Tests skipped (use -RunTests to enable)."
    }

    if (-not $SkipSettlementBackfill) {
        Write-Host "`n[3/4] Refresh canonical market outcomes; legacy V2 outcome mismatch is diagnostic only..."
        $settlementIntegrityPassed = $false
        $passes = [Math]::Max(1, $SettlementBackfillPasses)
        for ($pass = 1; $pass -le $passes; $pass++) {
            Write-Host "  canonical settlement pass $pass / $passes"
            python .\tools\backfill_target_maker_survival_settlements_v3_3.py `
                --public-dataset $PublicDataset `
                --output-db $SettlementDb `
                --report $SettlementReport `
                --risk-csv $RiskCsv `
                --special-start $SpecialStart `
                --min-ordinary-coverage $MinOrdinarySettlementCoverage `
                --min-special-coverage $MinSpecialSettlementCoverage `
                --min-canonical-outcome-coverage $MinCanonicalOutcomeCoverage `
                --api-retries $SettlementApiRetries `
                --retry-base-delay-ms $SettlementRetryBaseDelayMs
            if ($LASTEXITCODE -eq 0) {
                $settlementIntegrityPassed = $true
                break
            }

            $reportPayload = $null
            if (Test-Path $SettlementReport) {
                try {
                    $reportPayload = Get-Content -Raw $SettlementReport | ConvertFrom-Json
                }
                catch {
                    Write-Host "  warning: could not parse settlement diagnostics: $($_.Exception.Message)"
                }
            }

            $statusSummary = "none"
            $http429 = 0
            $http5xx = 0
            $exceptions = 0
            $unresolvedKinds = @{}
            if ($null -ne $reportPayload -and $null -ne $reportPayload.apiDiagnostics) {
                $diag = $reportPayload.apiDiagnostics
                if ($null -ne $diag.httpStatusCounts) {
                    $pairs = @()
                    foreach ($property in $diag.httpStatusCounts.PSObject.Properties) {
                        $pairs += "$($property.Name)=$($property.Value)"
                        $code = 0
                        if ([int]::TryParse([string]$property.Name, [ref]$code)) {
                            if ($code -eq 429) { $http429 += [int]$property.Value }
                            if ($code -ge 500 -and $code -le 599) { $http5xx += [int]$property.Value }
                        }
                    }
                    if ($pairs.Count -gt 0) { $statusSummary = ($pairs -join ", ") }
                }
                if ($null -ne $diag.exceptionCounts) {
                    foreach ($property in $diag.exceptionCounts.PSObject.Properties) {
                        $exceptions += [int]$property.Value
                    }
                }
                if ($null -ne $diag.unresolvedDetailsFirst100) {
                    foreach ($item in $diag.unresolvedDetailsFirst100) {
                        $kind = [string]$item.kind
                        if ([string]::IsNullOrWhiteSpace($kind)) { $kind = "UNKNOWN" }
                        if (-not $unresolvedKinds.ContainsKey($kind)) { $unresolvedKinds[$kind] = 0 }
                        $unresolvedKinds[$kind] += 1
                    }
                }
            }

            $kindSummary = if ($unresolvedKinds.Count -gt 0) {
                (($unresolvedKinds.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join ", ")
            } else { "none" }
            Write-Host "  settlement diagnostics: HTTP[$statusSummary] exceptions=$exceptions unresolvedKinds[$kindSummary]"

            if ($pass -lt $passes) {
                $hasTransient = ($http429 -gt 0 -or $http5xx -gt 0 -or $exceptions -gt 0)
                if (-not $hasTransient) {
                    Write-Host "  no transient API failure is visible; further cached passes would repeat the same unresolved markets."
                    break
                }

                $baseCooldown = [Math]::Max(0, $SettlementPassCooldownSeconds)
                if ($http429 -gt 0) {
                    $adaptiveCooldown = [Math]::Min(300, [Math]::Max(60, $baseCooldown * [Math]::Pow(2, $pass - 1)))
                }
                elseif ($http5xx -gt 0 -or $exceptions -gt 0) {
                    $adaptiveCooldown = [Math]::Min(120, [Math]::Max(15, $baseCooldown * [Math]::Pow(2, $pass - 1)))
                }
                else {
                    $adaptiveCooldown = $baseCooldown
                }
                $cooldown = [int][Math]::Ceiling($adaptiveCooldown)
                Write-Host "  transient settlement failure detected; keeping canonical cache and retrying unresolved markets after ${cooldown}s..."
                if ($cooldown -gt 0) { Start-Sleep -Seconds $cooldown }
            }
        }
        if (-not $settlementIntegrityPassed) {
            throw "V3.3 settlement integrity failed. Inspect the printed diagnostics and data\research\target_maker_survival_settlements_v3_report.json."
        }
    }
    else {
        Write-Host "`n[3/4] Settlement refresh skipped; V3.3 analyzer still enforces market-outcome + Target-PnL coverage."
        if (-not (Test-Path $SettlementDb)) { throw "Settlement DB missing: $SettlementDb" }
    }

    Write-Host "`n[4/4] Train heavy-side survival submodels + audit repairs against Target portfolio PnL..."
    python .\tools\analyze_target_maker_heavy_survival_v3_3.py `
        --public-dataset $PublicDataset `
        --settlement-db $SettlementDb `
        --risk-csv $RiskCsv `
        --target-db $TargetDb `
        --min-target-pnl-coverage $MinTargetPnlCoverage `
        --special-start $SpecialStart `
        --fit-stride-seconds $FitStrideSeconds `
        --min-train-markets $MinTrainMarkets `
        --test-markets $TestMarkets `
        --max-folds $MaxFolds `
        --interactions $Interactions `
        --max-rounds $MaxRounds `
        --outer-bags $OuterBags
    if ($LASTEXITCODE -ne 0) { throw "V3.3 survival/PnL analysis integrity preflight failed." }

    Write-Host "`nDone. No survival cutoff or repair rule was promoted."
    Write-Host "Target WIN means combined net PnL > 0; official UP/DOWN is never reported as Target win/loss."
    Write-Host "Settlement report: data\research\target_maker_survival_settlements_v3_report.json"
    Write-Host "V3.3 report: data\research\target_maker_heavy_survival_v3_report.json"
    Write-Host "Scores: data\research\target_maker_heavy_survival_v3_scores.csv"
}
finally {
    Pop-Location
}
