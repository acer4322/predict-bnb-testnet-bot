from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import merge_target_controller_source_replays_v2 as merge


def _market(market_id: int, source: str, first_ms: int, last_ms: int) -> dict:
    return {
        "market_id": str(market_id),
        "segment_id": "1",
        "source_version": source,
        "valid_lifecycle": "1",
        "invalid_reason": "",
        "first_event_ms": str(first_ms),
        "last_event_ms": str(last_ms),
        "maker_parents": "1",
        "taker_parents": "1",
        "taker_bursts": "1",
        "final_risk_deficit": "0",
        "final_abs_payoff_gap": "0",
        "final_worst_case_pnl": "0",
    }


def test_cross_cutover_market_is_replaced_by_one_mixed_invalid_row() -> None:
    legacy = [_market(7, "LEGACY", 1000, 2000), _market(8, "LEGACY", 3000, 4000)]
    official = [_market(7, "OFFICIAL", 2100, 2500), _market(9, "OFFICIAL", 5000, 6000)]

    rows, overlap = merge._merge_markets(legacy, official)

    assert overlap == {7}
    seven = [row for row in rows if int(float(row["market_id"])) == 7]
    assert len(seven) == 1
    assert seven[0]["source_version"] == "MIXED"
    assert int(seven[0]["valid_lifecycle"]) == 0
    assert "source-version boundary" in seven[0]["invalid_reason"]
    assert int(seven[0]["first_event_ms"]) == 1000
    assert int(seven[0]["last_event_ms"]) == 2500


def test_boundary_market_rows_are_removed_from_normalized_outputs() -> None:
    rows = [
        {"market_id": "7", "value": "bad"},
        {"market_id": "8", "value": "keep"},
    ]
    filtered = merge._filter_market_rows(rows, {7})
    assert filtered == [{"market_id": "8", "value": "keep"}]
