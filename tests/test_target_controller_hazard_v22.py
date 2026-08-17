from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_controller_hazard_v22 as mod


def _row(regime: str, risk: float, gap: float, maker_gap: float, sec: float, y: int) -> dict:
    row = {
        "regime": regime,
        "market_id": "1",
        "sample_ms": "1",
        "risk_deficit": str(risk),
        "abs_payoff_gap": str(gap),
        "maker_abs_payoff_gap": str(maker_gap),
        "seconds_left": str(sec),
        "time_since_last_taker_ms": "1000",
    }
    for h in mod.HORIZONS:
        for kind in mod.KINDS:
            row[f"{kind}_within_{h}s"] = str(y if kind == "taker" else 0)
    return row


def test_pooled_edges_are_shared_not_regime_specific():
    rows = [
        _row(mod.STRESS, 0, 1, 1, 10, 1),
        _row(mod.STRESS, 1, 1, 1, 10, 1),
        _row(mod.ORDINARY, 100, 1, 1, 10, 0),
        _row(mod.ORDINARY, 101, 1, 1, 10, 0),
    ]
    edges = mod._pooled_edges(rows, "risk_deficit", 2)
    assert len(edges) == 1
    assert 1 < edges[0] < 100


def test_common_support_weight_balances_regimes_within_cells():
    rows = []
    for _ in range(20):
        rows.append(_row(mod.STRESS, 10, 20, 30, 100, 1))
    for _ in range(12):
        rows.append(_row(mod.ORDINARY, 10, 20, 30, 100, 0))
    report, cells = mod._matched_spec(
        rows,
        name="TEST",
        features=mod.CORE_FEATURES,
        bins=2,
        min_per_regime=5,
    )
    assert report["matchedPairsWeight"] == 12
    assert len(cells) == 1
    h5 = report["hazard"]["5s"]["taker"]
    assert h5["stress"] == 1.0
    assert h5["ordinary"] == 0.0
    assert h5["difference"] == 1.0
    assert h5["ratio"] is None


def test_policy_dependent_features_are_not_core_match_controls():
    assert "time_since_last_taker_ms" in mod.POLICY_DEPENDENT_FEATURES
    assert "time_since_last_taker_ms" not in mod.CORE_FEATURES
    assert "time_since_last_taker_ms" not in mod.CORE_PLUS_MAKER_GAP
    assert "maker_shares_since_last_taker" not in mod.CORE_PLUS_MAKER_GAP


def test_common_surface_uses_same_bin_boundaries_for_both_regimes():
    rows = []
    for value in range(10):
        rows.append(_row(mod.STRESS, value, 10, 10, 100, 1))
        rows.append(_row(mod.ORDINARY, value, 10, 10, 100, 0))
    surface = mod._common_surface(rows, "risk_deficit", 5)
    assert len(surface["bins"]) == 5
    assert all(row["stressN"] > 0 and row["ordinaryN"] > 0 for row in surface["bins"])
    assert all(row["takerStress5s"] == 1.0 for row in surface["bins"])
    assert all(row["takerOrdinary5s"] == 0.0 for row in surface["bins"])
