from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from predict_bot import predict_wallet_taker_signal_strategy as candidate_strategy


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WALLET_DB = ROOT / "data" / "predict_wallet_shadow.db"
DEFAULT_SIGNAL_DB = ROOT / "data" / "wallet_taker_signals.db"
DEFAULT_OUTPUT = ROOT / "artifacts" / "wallet_profit_strategy" / "target_taker_signal_forward_report.json"

FEATURES = {
    "predict_up_mid": 1.0,
    "spot_minus_strike_bps": 0.0,
    "chainlink_minus_strike_bps": 0.0,
    "spot_minus_chainlink_bps": 0.0,
    "spot_queue_imbalance": 0.0,
    "spot_taker_imbalance_250ms": 0.0,
    "spot_taker_imbalance_1s": 0.0,
    "spot_return_250ms_bps": 0.0,
    "spot_return_1s_bps": 0.0,
    "spot_return_3s_bps": 0.0,
    "spot_return_5s_bps": 0.0,
    "futures_queue_imbalance": 0.0,
    "futures_taker_imbalance_250ms": 0.0,
    "futures_taker_imbalance_1s": 0.0,
    "futures_return_250ms_bps": 0.0,
    "futures_return_1s_bps": 0.0,
    "futures_return_3s_bps": 0.0,
    "futures_return_5s_bps": 0.0,
    "direction_score": 0.0,
}


def _wilson(successes: int, total: int) -> list[float] | None:
    if total <= 0:
        return None
    z = 1.95996398454
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [center - half, center + half]


def _parents(db: sqlite3.Connection, deployed_at_ms: int) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(
        """SELECT e.wallet,e.market_id,e.order_hash,e.event_ms,e.side,
                  SUM(e.shares) shares,SUM(e.shares*e.price) notional_usdt,
                  SUM(e.shares*e.price)/SUM(e.shares) average_price,
                  r.winner,r.status,r.taker_pnl_usdt
             FROM wallet_shadow_target_events e
             LEFT JOIN wallet_shadow_target_market_results r
               ON r.wallet=e.wallet AND r.market_id=e.market_id
            WHERE e.role='TAKER' AND e.event_ms>=?
            GROUP BY e.wallet,e.market_id,e.order_hash,e.event_ms,e.side
            ORDER BY e.event_ms,e.order_hash""",
        (deployed_at_ms,),
    )]


def _strict_pre_snapshot(
    db: sqlite3.Connection, market_id: int, event_ms: int, *, offset_ms: int = 0
) -> dict[str, Any] | None:
    # Predict executedAt is second-resolution. A 250 ms guard keeps the selected
    # feature strictly before the reported event second instead of leaking from it.
    cutoff_ns = (event_ms - offset_ms - 250) * 1_000_000
    floor_ns = (event_ms - offset_ms - 2_000) * 1_000_000
    row = db.execute(
        """SELECT * FROM wallet_taker_signal_snapshots
            WHERE market_id=? AND timestamp_ns BETWEEN ? AND ?
            ORDER BY timestamp_ns DESC LIMIT 1""",
        (market_id, floor_ns, cutoff_ns),
    ).fetchone()
    return dict(row) if row else None


def _candidate_summary(matched: list[dict[str, Any]]) -> dict[str, Any]:
    samples = []
    per_market: dict[int, list[bool]] = defaultdict(list)
    for item in matched:
        result = candidate_strategy.consensus(item["snapshot"])
        side = result.get("side")
        if side not in {"UP", "DOWN"}:
            continue
        match = side == item["parent"]["side"]
        notional = float(item["parent"]["notional_usdt"])
        market_id = int(item["parent"]["market_id"])
        samples.append((match, notional))
        per_market[market_id].append(match)
    successes = sum(int(match) for match, _ in samples)
    notional = sum(value for _, value in samples)
    matched_notional = sum(value for match, value in samples if match)
    market_rates = [sum(values) / len(values) for values in per_market.values() if values]
    return {
        "rule": f"{candidate_strategy.QUORUM}-of-{len(candidate_strategy.FEATURES)} sign consensus",
        "features": list(candidate_strategy.FEATURES),
        "parents": len(samples),
        "markets": len(per_market),
        "directionMatches": successes,
        "directionMatchRate": successes / len(samples) if samples else None,
        "wilson95": _wilson(successes, len(samples)),
        "notionalWeightedMatchRate": matched_notional / notional if notional else None,
        "equalMarketMeanMatchRate": sum(market_rates) / len(market_rates) if market_rates else None,
        "marketsAboveChance": sum(rate > 0.5 for rate in market_rates),
        "purpose": "frozen forward paper hypothesis; these same observations are discovery data, not validation performance",
    }


def build_report(wallet_db_path: Path, signal_db_path: Path) -> dict[str, Any]:
    signal_db = sqlite3.connect(signal_db_path)
    signal_db.row_factory = sqlite3.Row
    meta = signal_db.execute("SELECT value FROM wallet_taker_signal_meta WHERE key='deployed_at_ms'").fetchone()
    deployed_at_ms = int(meta[0]) if meta else 0
    wallet_db = sqlite3.connect(wallet_db_path)
    wallet_db.row_factory = sqlite3.Row
    parents = _parents(wallet_db, deployed_at_ms)
    matched = []
    for parent in parents:
        snapshot = _strict_pre_snapshot(signal_db, int(parent["market_id"]), int(parent["event_ms"]))
        if snapshot:
            matched.append({"parent": parent, "snapshot": snapshot})

    feature_results: dict[str, Any] = {}
    for feature, neutral in FEATURES.items():
        samples = []
        for item in matched:
            value = item["snapshot"].get(feature)
            if value is None:
                continue
            value = float(value)
            if feature == "predict_up_mid":
                side = "UP" if value > 0.5 else "DOWN" if value < 0.5 else None
                strength = abs(value - 0.5) * 2
            else:
                side = "UP" if value > neutral else "DOWN" if value < neutral else None
                strength = abs(value - neutral)
            if side:
                samples.append((side == item["parent"]["side"], float(item["parent"]["notional_usdt"]), strength))
        successes = sum(int(match) for match, _, _ in samples)
        notional = sum(amount for _, amount, _ in samples)
        matched_notional = sum(amount for match, amount, _ in samples if match)
        # A positive strength-size association supports confidence-weighted sizing.
        strength_size = None
        if len(samples) >= 3:
            sx = [math.log1p(strength) for _, _, strength in samples]
            sy = [math.log1p(amount) for _, amount, _ in samples]
            mx, my = sum(sx) / len(sx), sum(sy) / len(sy)
            numerator = sum((x - mx) * (y - my) for x, y in zip(sx, sy))
            denominator = math.sqrt(sum((x - mx) ** 2 for x in sx) * sum((y - my) ** 2 for y in sy))
            strength_size = numerator / denominator if denominator else None
        feature_results[feature] = {
            "parents": len(samples),
            "markets": len({item["parent"]["market_id"] for item in matched if item["snapshot"].get(feature) is not None}),
            "directionMatches": successes,
            "directionMatchRate": successes / len(samples) if samples else None,
            "wilson95": _wilson(successes, len(samples)),
            "notionalWeightedMatchRate": matched_notional / notional if notional else None,
            "logStrengthVsLogNotionalCorrelation": strength_size,
        }

    settled = [item for item in matched if item["parent"].get("winner") in {"UP", "DOWN"}]
    timing_controls: dict[str, Any] = {}
    for offset_seconds in (0, 5, 15, 30):
        offset_matched = []
        for parent in parents:
            snapshot = _strict_pre_snapshot(
                signal_db, int(parent["market_id"]), int(parent["event_ms"]), offset_ms=offset_seconds * 1_000
            )
            if snapshot:
                offset_matched.append({"parent": parent, "snapshot": snapshot})
        timing_controls[f"minus{offset_seconds}s"] = _candidate_summary(offset_matched)
    ages = sorted(
        (int(item["parent"]["event_ms"]) * 1_000_000 - int(item["snapshot"]["timestamp_ns"])) / 1_000_000
        for item in matched
    )
    signal_db.close()
    wallet_db.close()
    distinct_markets = len({item["parent"]["market_id"] for item in matched})
    return {
        "generatedAt": datetime.now().astimezone().isoformat(),
        "cohort": {
            "deployedAtMs": deployed_at_ms,
            "forwardOnly": True,
            "paperOnly": True,
            "historicalBackfill": False,
            "eventTimePolicy": "latest same-market signal snapshot at least 250 ms before second-resolution target executedAt; maximum age 2 seconds",
        },
        "coverage": {
            "targetTakerParents": len(parents),
            "matchedParents": len(matched),
            "matchedMarkets": distinct_markets,
            "settledMatchedParents": len(settled),
            "pairingRate": len(matched) / len(parents) if parents else None,
            "snapshotAgeMs": {
                "minimum": ages[0] if ages else None,
                "median": ages[len(ages) // 2] if ages else None,
                "p90": ages[min(len(ages) - 1, math.floor(len(ages) * 0.9))] if ages else None,
                "maximum": ages[-1] if ages else None,
            },
        },
        "features": feature_results,
        "candidateHypothesis": _candidate_summary(matched),
        "timingNegativeControls": timing_controls,
        "researchGate": {
            "minimumDistinctMarketsForInitialInference": 30,
            "ready": distinct_markets >= 30,
            "warning": "Do not select or tune a rule from the same forward outcomes. Freeze candidate hypotheses, then validate on a later market holdout.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Causally compare pre-event market signals with target Taker direction and sizing")
    parser.add_argument("--wallet-db", type=Path, default=DEFAULT_WALLET_DB)
    parser.add_argument("--signal-db", type=Path, default=DEFAULT_SIGNAL_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_report(args.wallet_db, args.signal_db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
