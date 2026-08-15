from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_echtgeld_does_not_reuse_eth_taker_port_8780() -> None:
    eth_collector = _text("src/predict_bot/predict_wallet_eth_taker_signal_collector.py")
    engine_launcher = _text("start-echtgeld-engine-v1.ps1")
    producer_launcher = _text("start-target-taker-echtgeld-producer-v1.ps1")
    vite = _text("dashboard-v2/vite.config.ts")

    assert "PORT = 8780" in eth_collector
    assert '$EnginePort = 8781' in engine_launcher
    assert '$EnginePort = 8781' in producer_launcher
    assert "http://127.0.0.1:8781" in vite
    assert "http://127.0.0.1:8780" not in vite


def test_producer_launcher_does_not_bootstrap_v422_and_unwraps_health_state() -> None:
    producer_launcher = _text("start-target-taker-echtgeld-producer-v1.ps1")

    assert "start-target-taker-multi-entry-paper-v1.ps1" not in producer_launcher
    assert "function Unwrap-State" in producer_launcher
    assert 'Unwrap-State (Get-JsonPayload "http://127.0.0.1:8776/health" 5)' in producer_launcher
    assert "predict_bot.predict_wallet_shadow_observer_v4_23" in producer_launcher


def test_existing_v423_is_reused_only_when_engine_url_matches() -> None:
    producer_launcher = _text("start-target-taker-echtgeld-producer-v1.ps1")

    assert "$ReportedEngineUrl.TrimEnd('/') -eq $EngineBase" in producer_launcher
    assert "existing v4.23 points to stale engine URL" in producer_launcher
    assert "points to the wrong Echtgeld Engine URL" in producer_launcher


def test_v423_default_handoff_points_to_8781() -> None:
    producer = _text("src/predict_bot/predict_wallet_shadow_observer_v4_23.py")
    assert 'http://127.0.0.1:8781' in producer
    assert 'Use the standalone Echtgeld Engine on port 8781.' in producer
