from __future__ import annotations

import pytest

from src.predict_bot import predict_wallet_taker_signal_collector as module


def test_signal_collector_builds_and_persists_causal_feature_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(module, "DB_PATH", tmp_path / "signals.db")
    monkeypatch.setattr(module, "MICRO_DB_PATH", tmp_path / "micro.db")
    monkeypatch.setattr(module, "PRIVATE_ARCHIVE_DB_PATH", tmp_path / "private-archive.db")
    collector = module.TakerSignalCollector()
    try:
        collector.current_market_id = 123
        collector.current_predict = {
            "bucketStartSec": 100,
            "windowEndMs": 400_000,
            "secondsLeft": 42.5,
            "sourceAgeMs": 10,
            "receiptAgeMs": 20,
            "market": {"id": 123, "bucketStartSec": 100, "windowEndMs": 400_000},
            "up": {"bid": 0.55, "ask": 0.56, "mid": 0.555},
            "down": {"bid": 0.44, "ask": 0.45, "mid": 0.445},
        }
        collector.current_market_detail = {"variantData": {"startPrice": 100.0}}
        with collector.micro.state_lock:
            collector.micro.latest_snapshot = {
                "spot_price": 101.0,
                "spot_microprice": 101.1,
                "spot_queue_imbalance": 0.25,
                "spot_taker_imbalance_250ms": 0.5,
                "spot_taker_imbalance_1s": 0.4,
                "futures_price": 101.2,
                "futures_microprice": 101.3,
                "futures_queue_imbalance": 0.2,
                "futures_taker_imbalance_250ms": 0.6,
                "futures_taker_imbalance_1s": 0.3,
                "perp_spot_basis_bps": 19.8,
                "direction_score": 0.44,
                "direction_bias": "UP",
                "volatility_alert": "NORMAL",
            }
        item = collector._snapshot()
        collector._persist(item)
        assert item["marketId"] == 123
        assert item["spotMinusStrikeBps"] == pytest.approx(100.0)
        row = collector.db.execute("SELECT * FROM wallet_taker_signal_snapshots").fetchone()
        assert row is not None
        assert row["spot_price"] == 101.0
        assert row["direction_bias"] == "UP"
        archived = collector.private_archive_db.execute("SELECT * FROM wallet_taker_private_signal_archive").fetchone()
        assert archived is not None
        assert archived["market_id"] == 123
    finally:
        collector.stop_event.set()
        collector.micro.stop()
        collector.client.close()
        collector.predict_client.close()
        collector.db.close()
        collector.private_archive_db.close()
