from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAIRS = ROOT / "data" / "research" / "target_maker_taker_complete_set_v2_pairs.csv"
DEFAULT_SETTLEMENTS = ROOT / "data" / "research" / "target_maker_survival_settlements_v3.db"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_directional_tolerance_smoke_v0_report.json"
REPORT_VERSION = "TARGET_DIRECTIONAL_TOLERANCE_SMOKE_V0"
FAVORABLE_PROXY_MIN = 0.60


def _load_settlements(path: Path) -> dict[int, str]:
    db = sqlite3.connect(path)
    try:
        out: dict[int, str] = {}
        for market_id, winner, status in db.execute(
            "SELECT market_id, official_winner, status FROM market_settlements"
        ):
            winner = str(winner or "").upper()
            if str(status or "").upper() == "OFFICIAL" and winner in {"UP", "DOWN"}:
                out[int(market_id)] = winner
        return out
    finally:
        db.close()


def _bucket(p: float) -> str:
    return "GE_080" if p >= 0.80 else "060_080"


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"fragments": 0, "markets": 0, "pairedShares": 0.0}
    shares = sum(r["shares"] for r in rows)
    pair_pnl = sum(r["pair_pnl"] for r in rows)
    hold_pnl = sum(r["hold_pnl"] for r in rows)
    win_shares = sum(r["shares"] for r in rows if r["maker_side_won"])
    return {
        "fragments": len(rows),
        "markets": len({r["market_id"] for r in rows}),
        "pairedShares": shares,
        "makerHeavyWinShareRate": win_shares / shares if shares else None,
        "actualRepairPairRawPnl": pair_pnl,
        "counterfactualHoldRawPnl": hold_pnl,
        "holdMinusRepairPairRawPnl": hold_pnl - pair_pnl,
        "shareWeightedMakerPrice": sum(r["shares"] * r["maker_price"] for r in rows) / shares if shares else None,
        "shareWeightedTakerPrice": sum(r["shares"] * r["taker_price"] for r in rows) / shares if shares else None,
        "buckets": dict(Counter(r["bucket"] for r in rows)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Small directional inventory tolerance architecture smoke test")
    ap.add_argument("--pairs", type=Path, default=DEFAULT_PAIRS)
    ap.add_argument("--settlements", type=Path, default=DEFAULT_SETTLEMENTS)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = ap.parse_args()

    settlements = _load_settlements(args.settlements)
    rows_by_regime: dict[str, list[dict[str, Any]]] = {"ORDINARY_PRE_SPECIAL": [], "SPECIAL": []}
    with args.pairs.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            regime = str(raw.get("regime") or "")
            if regime not in rows_by_regime:
                continue
            if str(raw.get("repair_against_maker_heavy") or "") != "1":
                continue
            # Directional tolerance only challenges paid-insurance repairs.
            # Locked-positive / near-par completions remain allowed.
            if str(raw.get("pair_class_raw") or "") != "INSURANCE_COST":
                continue
            try:
                probability = float(raw["heavy_win_probability"])
                market_id = int(float(raw["market_id"]))
                shares = float(raw["paired_shares"])
                maker_price = float(raw["maker_price"])
                taker_price = float(raw["taker_price"])
                pair_pnl = float(raw["raw_locked_edge_usdt"])
            except (TypeError, ValueError, KeyError):
                continue
            if probability < FAVORABLE_PROXY_MIN or market_id not in settlements:
                continue
            maker_side = str(raw.get("maker_side") or "").upper()
            winner = settlements[market_id]
            maker_side_won = maker_side == winner
            hold_pnl = shares * ((1.0 - maker_price) if maker_side_won else -maker_price)
            rows_by_regime[regime].append({
                "market_id": market_id,
                "bucket": _bucket(probability),
                "heavy_win_probability": probability,
                "shares": shares,
                "maker_price": maker_price,
                "taker_price": taker_price,
                "pair_pnl": pair_pnl,
                "hold_pnl": hold_pnl,
                "maker_side_won": maker_side_won,
            })

    report = {
        "reportVersion": REPORT_VERSION,
        "researchOnly": True,
        "liveChanges": False,
        "parameterSweep": False,
        "purpose": "Architecture smoke test for directional Maker-inventory tolerance before decomposing FAVORABLE/UNFAVORABLE inventory.",
        "coarsePolicy": {
            "favorableProxy": "current Maker-heavy public win probability >= 0.60; inherited from frozen descriptive buckets, not tuned here",
            "keepCompletion": "LOCKED_POSITIVE and NEAR_PAR Maker+Taker completion remain allowed regardless of favorable inventory",
            "testOnly": "when a repair would be INSURANCE_COST and Maker-heavy probability >= 0.60, compare actual repair pair with counterfactual HOLD of the Maker-origin shares to settlement",
        },
        "evaluation": {
            "actualRepairPairRawPnl": "paired_shares * (1 - maker_price - taker_price)",
            "counterfactualHoldRawPnl": "Maker-origin shares held alone to official settlement; winner used only for offline evaluation",
            "fees": "raw economics only; no claim about official Maker rebates/fees",
            "warning": "This is not a deployable rule and does not establish 0.60 as the true favorable threshold.",
        },
        "ordinary": {
            "overall": _summarize(rows_by_regime["ORDINARY_PRE_SPECIAL"]),
            "060_080": _summarize([r for r in rows_by_regime["ORDINARY_PRE_SPECIAL"] if r["bucket"] == "060_080"]),
            "GE_080": _summarize([r for r in rows_by_regime["ORDINARY_PRE_SPECIAL"] if r["bucket"] == "GE_080"]),
        },
        "specialAudit": {
            "overall": _summarize(rows_by_regime["SPECIAL"]),
            "060_080": _summarize([r for r in rows_by_regime["SPECIAL"] if r["bucket"] == "060_080"]),
            "GE_080": _summarize([r for r in rows_by_regime["SPECIAL"] if r["bucket"] == "GE_080"]),
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "version": REPORT_VERSION,
        "ordinary": report["ordinary"]["overall"],
        "special": report["specialAudit"]["overall"],
        "report": str(args.report.resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
