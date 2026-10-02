from __future__ import annotations

from pathlib import Path

from predict_bot import multi_asset_live_supervisor as supervisor


def test_eth_child_environment_is_isolated_and_execution_retired(monkeypatch) -> None:
    monkeypatch.setenv("PREDICT_ETH_POLY_GAP_LIVE_ENABLED", "true")
    monkeypatch.setenv("BINANCE_API_KEY", "read-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "read-secret")
    monkeypatch.setenv("BINANCE_LIVE_API_KEY", "live-key")
    monkeypatch.setenv("BINANCE_LIVE_API_SECRET", "live-secret")

    env = supervisor._asset_environment("ETH")

    assert env["PREDICT_POLY_GAP_LIVE_ENABLED"] == "false"
    assert env["PREDICT_LEGACY_EXECUTION_RETIRED_TO_8781"] == "true"
    assert env["PREDICT_POLY_GAP_LIVE_ASSET"] == "ETH"
    assert env["PREDICT_POLY_GAP_LIVE_SYMBOL"] == "ETHUSDT"
    assert env["PREDICT_POLY_GAP_LIVE_PORT"] == "8772"
    assert Path(env["PREDICT_POLY_GAP_LIVE_DB"]).name == "poly_gap_live_eth.db"
    assert env["PREDICT_MULTI_PREDICTION_STATE_URL"] == "http://127.0.0.1:8770/state"
    # Credentials remain available for read/reconciliation compatibility, but the
    # legacy process cannot acquire execution authority or resume runtime.
    assert env["BINANCE_LIVE_API_KEY"] == "live-key"
    assert env["BINANCE_LIVE_API_SECRET"] == "live-secret"


def test_bnb_and_eth_legacy_master_is_always_off(monkeypatch) -> None:
    monkeypatch.setenv("PREDICT_ETH_POLY_GAP_LIVE_ENABLED", "true")
    monkeypatch.setenv("PREDICT_BNB_POLY_GAP_LIVE_ENABLED", "true")

    eth = supervisor._asset_environment("ETH")
    bnb = supervisor._asset_environment("BNB")

    assert eth["PREDICT_POLY_GAP_LIVE_ENABLED"] == "false"
    assert bnb["PREDICT_POLY_GAP_LIVE_ENABLED"] == "false"
    assert eth["PREDICT_LEGACY_EXECUTION_RETIRED_TO_8781"] == "true"
    assert bnb["PREDICT_LEGACY_EXECUTION_RETIRED_TO_8781"] == "true"
    assert bnb["PREDICT_POLY_GAP_LIVE_ASSET"] == "BNB"
    assert bnb["PREDICT_POLY_GAP_LIVE_SYMBOL"] == "BNBUSDT"
    assert bnb["PREDICT_POLY_GAP_LIVE_PORT"] == "8773"
    assert Path(bnb["PREDICT_POLY_GAP_LIVE_DB"]).name == "poly_gap_live_bnb.db"
    assert eth["PREDICT_POLY_GAP_LIVE_DB"] != bnb["PREDICT_POLY_GAP_LIVE_DB"]


def test_asset_master_cannot_be_reenabled_by_old_environment(monkeypatch) -> None:
    for asset in ("ETH", "BNB"):
        name = f"PREDICT_{asset}_POLY_GAP_LIVE_ENABLED"
        monkeypatch.delenv(name, raising=False)
        assert supervisor._asset_environment(asset)["PREDICT_POLY_GAP_LIVE_ENABLED"] == "false"
        monkeypatch.setenv(name, "true")
        assert supervisor._asset_environment(asset)["PREDICT_POLY_GAP_LIVE_ENABLED"] == "false"
        monkeypatch.setenv(name, "false")
        assert supervisor._asset_environment(asset)["PREDICT_POLY_GAP_LIVE_ENABLED"] == "false"


def test_clone_master_is_always_off_for_both_venues(monkeypatch) -> None:
    for asset in ("ETH", "BNB"):
        monkeypatch.setenv(f"PREDICT_{asset}_WALLET_MAKER_CLONE_ENABLED", "true")
        for venue in ("BINANCE", "PREDICT_DIRECT"):
            env = supervisor._clone_environment(asset, venue)
            assert env["PREDICT_WALLET_MAKER_CLONE_ENABLED"] == "false"
            assert env["PREDICT_LEGACY_EXECUTION_RETIRED_TO_8781"] == "true"


def test_assets_use_distinct_ports_and_databases() -> None:
    assert supervisor.ASSETS["ETH"]["port"] == 8772
    assert supervisor.ASSETS["BNB"]["port"] == 8773
    assert supervisor.ASSETS["ETH"]["db"] != supervisor.ASSETS["BNB"]["db"]
