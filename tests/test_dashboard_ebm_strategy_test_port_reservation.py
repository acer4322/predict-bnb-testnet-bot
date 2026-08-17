from __future__ import annotations

from pathlib import Path

from predict_bot import target_taker_public_side_test_v2 as ebm_v2


ROOT = Path(__file__).resolve().parents[1]


def test_8780_remains_reserved_for_eth_taker_collector() -> None:
    source = (ROOT / "src" / "predict_bot" / "predict_wallet_eth_taker_signal_collector.py").read_text(encoding="utf-8")
    assert 'PORT = 8780' in source
    assert ebm_v2.PORT == 8782


def test_dashboard_ebm_proxy_and_lifecycle_use_8782() -> None:
    vite = (ROOT / "dashboard-v2" / "vite.config.ts").read_text(encoding="utf-8")
    control = (ROOT / "dashboard-v2" / "strategy-test-control.ts").read_text(encoding="utf-8")
    page = (ROOT / "dashboard-v2" / "src" / "ebm-strategy-test-page.tsx").read_text(encoding="utf-8")

    assert vite.count("target: 'http://127.0.0.1:8782'") >= 2
    assert "const PORT = 8782" in control
    assert "EBM 策略測試" in page
    assert "Start 8782" in page
    assert "獨立 8782 forward test" in page


def test_target_official_launcher_requires_v2_history_collector() -> None:
    launcher = (ROOT / "start-target-taker-echtgeld-producer-v1.ps1").read_text(encoding="utf-8")
    compatibility = (ROOT / "src" / "predict_bot" / "predict_wallet_shadow_observer_v4_23.py").read_text(encoding="utf-8")

    assert '$Expected8776Version = "TARGET_WALLET_OFFICIAL_V2_LEGACY_HISTORY"' in launcher
    assert "target_wallet_official_v2" in compatibility
