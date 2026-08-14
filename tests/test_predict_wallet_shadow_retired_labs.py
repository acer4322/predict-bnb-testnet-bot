from pathlib import Path

from predict_bot import predict_wallet_maker_grid_strategy as maker_grid
from predict_bot import predict_wallet_shadow_observer_v4_2 as spot
from predict_bot import predict_wallet_wide_maker_flow_strategy as wide
from predict_bot.predict_wallet_shadow_observer_v4_16 import (
    LEGACY_BASE_COHORT,
    RETIRED_COHORTS,
    VERSION,
    WalletShadowObserver,
)


ROOT = Path(__file__).resolve().parents[1]


def test_retired_cohort_registry_covers_requested_obsolete_labs() -> None:
    assert VERSION == "PREDICT_WALLET_SHADOW_V0_21_PERSISTENT_WARM_REPORT_CACHE"
    assert LEGACY_BASE_COHORT in RETIRED_COHORTS
    assert spot.COHORT in RETIRED_COHORTS
    assert {item["cohort"] for item in maker_grid.COHORTS}.issubset(RETIRED_COHORTS)
    assert {item["cohort"] for item in wide.COHORTS}.issubset(RETIRED_COHORTS)


def test_runtime_kill_switch_helpers_are_noops_without_state() -> None:
    observer = object.__new__(WalletShadowObserver)
    assert observer._append_shadow(event_type="MAKER_FILL_PROXY") is None
    assert observer._advance_spot_strike({}) is None
    assert observer._advance_maker_grids() is None
    assert observer._advance_wide_cohorts() is None


def test_retired_dashboard_panels_are_compatibility_stubs() -> None:
    files = [
        ROOT / "dashboard-v2" / "src" / "wallet-shadow-maker-grid-depth-panel.tsx",
        ROOT / "dashboard-v2" / "src" / "wallet-shadow-wide-maker-flow-panel.tsx",
        ROOT / "dashboard-v2" / "src" / "wallet-shadow-spot-strike-panel.tsx",
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "RETIRED 2026-08-14" in text
        assert "return null" in text


def test_launcher_uses_v21_8778_and_latest_v4_19_8776() -> None:
    text = (ROOT / "start-wallet-shadow-lab.ps1").read_text(encoding="utf-8")
    assert "predict_bot.predict_wallet_maker_book_inference_collector_v2_1" in text
    assert "predict_bot.predict_wallet_shadow_observer_v4_19" in text
    assert "predict_bot.predict_wallet_shadow_observer_v4_16" not in text
    stop_text = (ROOT / "stop-wallet-shadow-lab.ps1").read_text(encoding="utf-8")
    assert "Save-WalletShadowWarmCache" in stop_text
    assert "wallet-shadow-last-good-state.json" in stop_text
