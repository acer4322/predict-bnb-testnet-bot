param(
    [switch]$SkipNpmCi
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$branch = (git branch --show-current).Trim()
if ($branch -ne "feature/decision-strategy-native-v2") {
    throw "Expected branch feature/decision-strategy-native-v2, found $branch"
}

Write-Host "[1/8] Apply guarded native React source patch"
python scripts/apply_decision_strategy_native_tab.py
python scripts/apply_decision_strategy_native_tab.py --check

Write-Host "[2/8] Verify no startup/runtime integration hooks were added"
$package = Get-Content dashboard/package.json -Raw
if ($package -match 'integrate:decision|predev.*decision|prebuild.*decision|prelint.*decision') {
    throw "dashboard/package.json contains a forbidden startup/build decision-tab hook"
}
if (Select-String -Path start-local.ps1 -Pattern 'decision_strategy|decision-strategy' -Quiet) {
    throw "start-local.ps1 must not invoke decision strategy integration"
}

Write-Host "[3/8] Verify page diff does not touch the existing data loader"
$diff = git diff -- dashboard/app/page.tsx
$forbidden = @(
    'loadStatistics',
    '/api/state',
    'setStatisticsDown',
    'fetchDashboardJson',
    'STATISTICS_REFRESH_MS',
    'REALTIME_REFRESH_MS'
)
foreach ($token in $forbidden) {
    $changed = $diff | Select-String -Pattern "^[+-].*$([regex]::Escape($token))"
    if ($changed) {
        throw "Native page patch touched forbidden data-loader token: $token"
    }
}

Write-Host "[4/8] Install/import Python package"
python -m pip install -e ".[dev]"
python -c "import predict_bot; from predict_bot.decision_strategy_rules import STRATEGIES; from predict_bot.research_forward import RESEARCH_STRATEGIES, GENERIC_SIGNAL_RESEARCH_STRATEGIES; from predict_bot import live_trading, m_realtime; assert set(STRATEGIES) <= set(RESEARCH_STRATEGIES); assert not (set(STRATEGIES) & set(GENERIC_SIGNAL_RESEARCH_STRATEGIES)); assert set(STRATEGIES) <= set(live_trading.LIVE_SUPPORTED_STRATEGIES); assert set(STRATEGIES) <= set(m_realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES)"

Write-Host "[5/8] Run focused backend and existing integration tests"
python -m pytest `
    tests/test_decision_strategy_native_v2.py `
    tests/test_strong_trend_guard_shadows.py `
    tests/test_calibrated_value_confirmation_variants.py `
    tests/test_live_trading.py `
    -q

Write-Host "[6/8] Install dashboard dependencies"
Push-Location dashboard
try {
    if (-not $SkipNpmCi) {
        npm ci
    }

    Write-Host "[7/8] Run dashboard lint/tests/build"
    npm run lint
    npm test
    npm run build
}
finally {
    Pop-Location
}

Write-Host "[8/8] Final source checks"
git diff --check
python scripts/apply_decision_strategy_native_tab.py --check

Write-Host ""
Write-Host "Decision Strategy Native V2 validation passed." -ForegroundColor Green
Write-Host "The native page source is now modified in your working tree." -ForegroundColor Yellow
Write-Host "Review with: git diff -- dashboard/app/page.tsx" -ForegroundColor Yellow
Write-Host "Do not start the branch if any command above failed." -ForegroundColor Yellow
