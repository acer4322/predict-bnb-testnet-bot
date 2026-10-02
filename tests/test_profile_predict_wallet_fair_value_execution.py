from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "profile_predict_wallet_fair_value_execution.py"
SPEC = importlib.util.spec_from_file_location("profile_predict_wallet_fair_value_execution", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def test_market_descriptor_supports_btc_5m_and_15m() -> None:
    five = m.market_descriptor("Bitcoin Up or Down - August 12, 10:00AM-10:05AM ET")
    fifteen = m.market_descriptor("BTC Up or Down - August 12, 10:00AM-10:15AM ET")
    assert five is not None
    assert five["asset"] == "BTC"
    assert five["durationMinutes"] == 5
    assert fifteen is not None
    assert fifteen["asset"] == "BTC"
    assert fifteen["durationMinutes"] == 15


def test_trajectory_index_dynamic_columns_and_alignment(tmp_path: Path) -> None:
    db_path = tmp_path / "observer.db"
    db = sqlite3.connect(db_path)
    db.execute(
        """CREATE TABLE multi_prediction_trajectory (
            asset TEXT NOT NULL,
            market_bucket INTEGER NOT NULL,
            sampled_at_ms INTEGER NOT NULL,
            seconds_left REAL,
            poly_up_mid REAL,
            poly_down_mid REAL,
            binance_up_mid REAL,
            binance_down_mid REAL,
            binance_up_ask REAL,
            binance_down_ask REAL
        )"""
    )
    db.executemany(
        "INSERT INTO multi_prediction_trajectory VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            ("BTC", 1000, 1_000_000, 200.0, 0.60, 0.40, 0.54, 0.46, 0.55, 0.47),
            ("BTC", 1000, 1_002_000, 198.0, 0.61, 0.39, 0.57, 0.43, 0.58, 0.44),
        ],
    )
    db.commit()
    db.close()

    index = m.TrajectoryIndex(db_path, {"BTC"})
    try:
        event = {
            "role": "TAKER",
            "kind": "TAKER_BUY",
            "asset": "BTC",
            "durationMinutes": 5,
            "marketBucket": 1000,
            "marketId": 1,
            "marketTitle": "Bitcoin Up or Down",
            "side": "UP",
            "price": 0.55,
            "shares": 2.0,
            "costUsdtApprox": 1.10,
            "eventMs": 1_001_000,
            "eventAt": "x",
        }
        aligned = m.align_event(event, index, offsets=(0.0, 1.0), max_age_ms=1500)
    finally:
        index.close()

    pre = aligned["before"]["0s"]
    post = aligned["after"]["1s"]
    assert pre is not None
    assert pre["polyProbabilityForSide"] == 0.60
    assert pre["binancePredictionProbabilityForSide"] == 0.54
    assert abs(pre["polyEdgeVsExecutionPrice"] - 0.05) < 1e-12
    assert post is not None
    assert post["binancePredictionProbabilityForSide"] == 0.57


def test_maker_repricing_tracks_poly_and_target() -> None:
    def event(ms: int, price: float, poly: float, target: float, order_hash: str) -> dict:
        return {
            "role": "MAKER",
            "asset": "BTC",
            "durationMinutes": 5,
            "marketId": 1,
            "side": "UP",
            "eventMs": ms,
            "price": price,
            "orderHash": order_hash,
            "before": {
                "1s": {
                    "polyProbabilityForSide": poly,
                    "binancePredictionProbabilityForSide": target,
                }
            },
        }

    rows = [
        event(1000, 0.40, 0.42, 0.41, "a"),
        event(2000, 0.45, 0.47, 0.46, "b"),
        event(3000, 0.50, 0.52, 0.51, "c"),
    ]
    result = m.maker_repricing(rows, 1.0)
    assert result["transitionsWithSnapshots"] == 2
    assert result["vsPoly"]["sameDirectionShare"] == 1.0
    assert result["vsBinancePrediction"]["sameDirectionShare"] == 1.0
    assert result["vsPoly"]["deltaCorrelation"] is not None


def test_taker_stale_quote_detects_poly_edge_and_catchup() -> None:
    event = {
        "asset": "BTC",
        "durationMinutes": 5,
        "marketId": 1,
        "side": "UP",
        "eventAt": "2026-08-12T00:00:00Z",
        "price": 0.55,
        "before": {
            "1s": {
                "polyProbabilityForSide": 0.62,
                "binancePredictionProbabilityForSide": 0.56,
            }
        },
        "after": {
            "2s": {
                "polyProbabilityForSide": 0.63,
                "binancePredictionProbabilityForSide": 0.60,
            }
        },
    }
    result = m.taker_stale_quote_tests([event], 1.0, (2.0,))
    summary = result["byPostOffset"]["2s"]
    assert summary["prePolyPositiveEdgeShare"] == 1.0
    assert summary["prePolyAtLeast1CentEdgeShare"] == 1.0
    assert summary["binancePredictionMovedTowardPrePolyShare"] == 1.0
    assert summary["polyBinanceGapReducedShare"] == 1.0
