from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPORTER = ROOT / "dashboard-v2" / "src" / "wallet-research-export-panel.tsx"
HEALTH_PANEL = ROOT / "dashboard-v2" / "src" / "wallet-lab-service-health-panel.tsx"
TSCONFIG = ROOT / "dashboard-v2" / "tsconfig.wallet-shadow.json"


def test_research_exporter_splits_layers_and_strategy_cohorts() -> None:
    text = EXPORTER.read_text(encoding="utf-8")
    assert "layer:8776-core-state" in text
    assert "layer:8776-health" in text
    assert "layer:8777-taker-signals" in text
    assert "layer:8778-maker-book-btc" in text
    assert "layer:8779-maker-book-eth" in text
    assert "container.variants" in text
    assert "container.cohorts" in text
    assert "strategy:${key}:${cohort}" in text
    assert "8776/state.${key}.cohorts.${cohortKey}" in text
    assert "TARGET_CORE_INTEGRATED_V1" in text
    assert "TARGET_CORE_INTEGRATED_V2_TAKER_HEAVY" in text


def test_research_exporter_redacts_common_secret_fields() -> None:
    text = EXPORTER.read_text(encoding="utf-8")
    assert "api[_-]?key" in text
    assert "authorization" in text
    assert "access[_-]?token" in text
    assert "refresh[_-]?token" in text
    assert "[REDACTED]" in text


def test_research_exporter_is_mounted_and_typechecked() -> None:
    health = HEALTH_PANEL.read_text(encoding="utf-8")
    tsconfig = TSCONFIG.read_text(encoding="utf-8")
    assert "WalletResearchExportPanel" in health
    assert "<WalletResearchExportPanel />" in health
    assert '"src/wallet-research-export-panel.tsx"' in tsconfig
