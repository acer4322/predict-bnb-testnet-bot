from __future__ import annotations

from pathlib import Path

from predict_bot import multi_asset_live_supervisor as supervisor


def test_eth_child_environment_is_isolated(monkeypatch) -> None:
    monkeypatch.setenv("PREDICT_ETH_POLY_GAP_LIVE_ENABLED", "true")
    monkeypatch.setenv("BINANCE_API_KEY", "read-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "read-secret")
    monkeypatch.setenv("BINANCE_LIVE_API_KEY", "live-key")
    monkeypatch.setenv("BINANCE_LIVE_API_SECRET", "live-secret")

    env = supervisor._asset_environment("ETH")

    assert env["PREDICT_POLY_GAP_LIVE_ENABLED"] == "true"
    assert env["PREDICT_POLY_GAP_LIVE_ASSET"] == "ETH"
    assert env["PREDICT_POLY_GAP_LIVE_SYMBOL"] == "ETHUSDT"
    assert env["PREDICT_POLY_GAP_LIVE_PORT"] == "8772"
    assert Path(env["PREDICT_POLY_GAP_LIVE_DB"]).name == "poly_gap_live_eth.db"
    assert env["PREDICT_MULTI_PREDICTION_STATE_URL"] == "http://127.0.0.1:8770/state"
    assert "BINANCE_API_KEY" not in env
    assert "BINANCE_API_SECRET" not in env
    assert env["BINANCE_LIVE_API_KEY"] == "live-key"
    assert env["BINANCE_LIVE_API_SECRET"] == "live-secret"


def test_bnb_child_environment_uses_own_master_and_db(monkeypatch) -> None:
    monkeypatch.setenv("PREDICT_ETH_POLY_GAP_LIVE_ENABLED", "true")
    monkeypatch.setenv("PREDICT_BNB_POLY_GAP_LIVE_ENABLED", "false")

    eth = supervisor._asset_environment("ETH")
    bnb = supervisor._asset_environment("BNB")

    assert eth["PREDICT_POLY_GAP_LIVE_ENABLED"] == "true"
    assert bnb["PREDICT_POLY_GAP_LIVE_ENABLED"] == "false"
    assert bnb["PREDICT_POLY_GAP_LIVE_ASSET"] == "BNB"
    assert bnb["PREDICT_POLY_GAP_LIVE_SYMBOL"] == "BNBUSDT"
    assert bnb["PREDICT_POLY_GAP_LIVE_PORT"] == "8773"
    assert Path(bnb["PREDICT_POLY_GAP_LIVE_DB"]).name == "poly_gap_live_bnb.db"
    assert eth["PREDICT_POLY_GAP_LIVE_DB"] != bnb["PREDICT_POLY_GAP_LIVE_DB"]


def test_assets_use_distinct_ports_and_databases() -> None:
    assert supervisor.ASSETS["ETH"]["port"] == 8772
    assert supervisor.ASSETS["BNB"]["port"] == 8773
    assert supervisor.ASSETS["ETH"]["db"] != supervisor.ASSETS["BNB"]["db"]
