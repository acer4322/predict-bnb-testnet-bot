from __future__ import annotations

import json
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import audit_target_controller_prediction_8778_v272 as mod
import audit_target_maker_8778_predict_book_coverage_v2_5b as bookmod


def _z(value) -> bytes:
    return zlib.compress(json.dumps(value).encode("utf-8"))


def _checkpoint(update_id: int, market_id: int, ms: int) -> bookmod.BookUpdate:
    return bookmod.BookUpdate(
        id=update_id,
        market_id=market_id,
        source_timestamp_ms=ms,
        received_at_ms=ms,
        is_checkpoint=True,
        native_bids_z=_z([["0.40", "10"]]),
        native_asks_z=_z([["0.60", "10"]]),
        changes_z=_z({"bids": [], "asks": []}),
    )


def _delta(update_id: int, market_id: int, ms: int) -> bookmod.BookUpdate:
    return bookmod.BookUpdate(
        id=update_id,
        market_id=market_id,
        source_timestamp_ms=ms,
        received_at_ms=ms,
        is_checkpoint=False,
        native_bids_z=None,
        native_asks_z=None,
        changes_z=_z(
            {
                "bids": [
                    {"price": "0.40", "before": "10", "after": "0", "delta": "-10"},
                    {"price": "0.45", "before": "0", "after": "10", "delta": "10"},
                ],
                "asks": [],
            }
        ),
    )


def test_received_strict_excludes_equal_timestamp():
    market = 1396309
    updates = {market: [_checkpoint(1, market, 1000), _delta(2, market, 2000)]}
    rows = mod.audit_rows([{"market_id": market, "sample_ms": 2000}], updates, "receivedStrict")
    assert len(rows) == 1
    row = rows[0]
    assert row["latest_update_id"] == 1
    assert row["latest_age_ms"] == 1000
    assert row["prediction_mid"] == 0.5
    assert row["fresh_2s"] is True


def test_received_strict_applies_prior_delta_and_keeps_market_namespace_direct():
    market = 1396309
    other_market = 6991536
    updates = {
        market: [_checkpoint(1, market, 1000), _delta(2, market, 2000)],
        other_market: [_checkpoint(9, other_market, 1999)],
    }
    rows = mod.audit_rows([{"market_id": market, "sample_ms": 2001}], updates, "receivedStrict")
    row = rows[0]
    assert row["market_id"] == market
    assert row["latest_update_id"] == 2
    assert row["latest_age_ms"] == 1
    assert abs(row["prediction_mid"] - 0.525) < 1e-12


def test_2s_freshness_gate_does_not_turn_stale_book_into_fresh_signal():
    market = 1396279
    updates = {market: [_checkpoint(1, market, 1000)]}
    rows = mod.audit_rows([{"market_id": market, "sample_ms": 4001}], updates, "receivedStrict")
    row = rows[0]
    assert row["reconstructable"] is True
    assert row["valid_mid"] is True
    assert row["latest_age_ms"] == 3001
    assert row["fresh_2s"] is False


def test_missing_target_market_updates_remain_missing_not_cross_joined():
    target_market = 1396279
    updates = {6991509: [_checkpoint(1, 6991509, 1000)]}
    rows = mod.audit_rows([{"market_id": target_market, "sample_ms": 1500}], updates, "receivedStrict")
    row = rows[0]
    assert row["reconstructable"] is False
    assert row["valid_mid"] is False
    assert row["prediction_mid"] is None
    assert row["fresh_2s"] is False


def test_decision_requires_broad_cross_market_2s_coverage():
    ready = {
        "freshValidMidWithinMs": {"2000": {"rate": 0.90}},
        "byTargetMarket": {
            "1": {"freshValidMidWithinMs": {"2000": {"rate": 0.80}}},
            "2": {"freshValidMidWithinMs": {"2000": {"rate": 0.55}}},
        },
    }
    assert mod._decision(ready)["status"] == "READY_FOR_V272_EBM_AB"

    partial = {
        "freshValidMidWithinMs": {"2000": {"rate": 0.70}},
        "byTargetMarket": {
            "1": {"freshValidMidWithinMs": {"2000": {"rate": 0.90}}},
            "2": {"freshValidMidWithinMs": {"2000": {"rate": 0.20}}},
        },
    }
    assert mod._decision(partial)["status"] == "PARTIAL_8778_COVERAGE_DO_NOT_TREAT_MISSING_AS_NEGATIVE"
