from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import backfill_target_maker_survival_settlements_v3_2 as mod


def _payload(*, resolution=None, outcomes=None, start=100.0, end=90.0):
    return {
        "success": True,
        "data": {
            "resolution": resolution,
            "outcomes": outcomes if outcomes is not None else [],
            "variantData": {
                "type": "CRYPTO_UP_DOWN",
                "startPrice": start,
                "endPrice": end,
            },
        },
    }


def test_resolution_winner_overrides_conflicting_price_direction():
    parsed, detail = mod._parse_canonical_detail(
        _payload(
            resolution={"name": "Up", "status": "WON"},
            outcomes=[],
            start=100.0,
            end=90.0,
        )
    )
    assert parsed is not None
    assert parsed["official_winner"] == "UP"
    assert parsed["winner_source"] == "RESOLUTION"
    assert detail["winnerSource"] == "RESOLUTION"


def test_outcomes_won_overrides_conflicting_price_direction():
    parsed, detail = mod._parse_canonical_detail(
        _payload(
            resolution=None,
            outcomes=[
                {"name": "Up", "status": "LOST"},
                {"name": "Down", "status": "WON"},
            ],
            start=100.0,
            end=110.0,
        )
    )
    assert parsed is not None
    assert parsed["official_winner"] == "DOWN"
    assert parsed["winner_source"] == "OUTCOMES_WON"
    assert detail["winnerSource"] == "OUTCOMES_WON"


def test_resolution_and_outcomes_must_not_disagree():
    parsed, detail = mod._parse_canonical_detail(
        _payload(
            resolution={"name": "Up", "status": "WON"},
            outcomes=[{"name": "Down", "status": "WON"}],
        )
    )
    assert parsed is None
    assert detail["kind"] == "CANONICAL_CONFLICT"


def test_price_direction_is_fallback_only_without_canonical_outcome():
    parsed, detail = mod._parse_canonical_detail(
        _payload(resolution=None, outcomes=[], start=100.0, end=101.0)
    )
    assert parsed is not None
    assert parsed["official_winner"] == "UP"
    assert parsed["winner_source"] == "PRICE_FALLBACK"
    assert detail["winnerSource"] == "PRICE_FALLBACK"


def test_only_legacy_unsuffixed_predict_source_is_invalidated():
    assert mod._is_legacy_inferred_source("PREDICT_MARKET_DETAIL_API") is True
    assert mod._is_legacy_inferred_source(
        "PREDICT_MARKET_DETAIL_API_RESOLUTION"
    ) is False
    assert mod._is_legacy_inferred_source(
        "PREDICT_MARKET_DETAIL_API_PRICE_FALLBACK"
    ) is False
    assert mod._is_legacy_inferred_source("SIMULATION_DB_MARKET_ID") is False


def test_concordance_preview_reports_winners_and_source(tmp_path: Path):
    risk = tmp_path / "risk.csv"
    risk.write_text(
        "market_id,maker_heavy_side,heavy_side_won\n"
        "1,UP,1\n"
        "2,DOWN,1\n",
        encoding="utf-8",
    )
    settlements = {
        1: {"winner": "DOWN", "source": "PREDICT_MARKET_DETAIL_API_RESOLUTION"},
        2: {"winner": "DOWN", "source": "PREDICT_MARKET_DETAIL_API_OUTCOMES_WON"},
    }
    audit = mod._winner_concordance_preview(risk, settlements)
    assert audit["overlapMarkets"] == 2
    assert audit["mismatchedWinnerMarkets"] == 1
    assert audit["mismatchDetailsFirst100"] == [
        {
            "marketId": 1,
            "v2RiskWinner": "UP",
            "settlementWinner": "DOWN",
            "settlementSource": "PREDICT_MARKET_DETAIL_API_RESOLUTION",
        }
    ]
    assert audit["passed"] is False
