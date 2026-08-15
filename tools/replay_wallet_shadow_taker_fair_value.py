from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.evaluate_wallet_shadow_sync import (  # noqa: E402
    DEFAULT_DB,
    Event,
    _parent_targets,
    eligible_markets,
    one_to_one_matches,
    score,
)

DEFAULT_OBSERVER_DB = Path("data/multi_prediction_observer.db")


def fair_value_candidate(
    conn: sqlite3.Connection,
    market_id: int,
    start_ms: int,
    *,
    minimum_edge: float,
    cooldown_ms: int,
) -> list[Event]:
    """Generate Taker events from independent Poly/Binance trajectory rows only."""
    market_bucket = start_ms // 300_000 * 300
    rows = conn.execute(
        """
        SELECT sampled_at_ms,executable_edge_up,executable_edge_down,
               binance_up_ask,binance_down_ask
          FROM multi_prediction_trajectory
         WHERE asset='BTC' AND market_bucket=? AND sampled_at_ms>=?
         ORDER BY sampled_at_ms
        """,
        (market_bucket, start_ms),
    ).fetchall()
    last_emit: dict[str, int] = {}
    events: list[Event] = []
    for row in rows:
        at_ms = int(row["sampled_at_ms"])
        for side, edge_key, price_key in (
            ("UP", "executable_edge_up", "binance_up_ask"),
            ("DOWN", "executable_edge_down", "binance_down_ask"),
        ):
            edge = row[edge_key]
            price = row[price_key]
            if edge is None or price is None or float(edge) < minimum_edge:
                continue
            if at_ms - last_emit.get(side, -10**15) < cooldown_ms:
                continue
            last_emit[side] = at_ms
            events.append(Event(at_ms, side, float(price), f"FAIR_VALUE:{market_id}:{at_ms}:{side}"))
    return events


def evaluate(
    wallet: sqlite3.Connection,
    observer: sqlite3.Connection,
    markets: list[tuple[int, int]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    targets: list[Event] = []
    shadows: list[Event] = []
    matches: list[dict[str, Any]] = []
    for market_id, start_ms in markets:
        target = _parent_targets(wallet, market_id, "TAKER", start_ms)
        shadow = fair_value_candidate(
            observer,
            market_id,
            start_ms,
            minimum_edge=float(policy["minimumEdge"]),
            cooldown_ms=int(policy["cooldownMs"]),
        )
        matched = one_to_one_matches(target, shadow, max_lag_ms=3_000, max_price_delta=0.021)
        targets.extend(target)
        shadows.extend(shadow)
        matches.extend(matched)
    return score(targets, shadows, matches)


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay target-independent fair-value Taker candidates")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--observer-db", type=Path, default=DEFAULT_OBSERVER_DB)
    parser.add_argument("--train-markets", type=int, default=3)
    parser.add_argument("--validation-markets", type=int, default=3)
    parser.add_argument("--forward-markets", type=int, default=0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    policies = [
        {"name": "EDGE_M005_EVERY_5S", "minimumEdge": -0.05, "cooldownMs": 5_000},
        {"name": "EDGE_M010_EVERY_12S", "minimumEdge": -0.10, "cooldownMs": 12_000},
        {"name": "EDGE_000_EVERY_5S", "minimumEdge": 0.00, "cooldownMs": 5_000},
        {"name": "EDGE_005_EVERY_5S", "minimumEdge": 0.05, "cooldownMs": 5_000},
    ]
    wallet = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    observer = sqlite3.connect(f"file:{args.observer_db}?mode=ro", uri=True)
    wallet.row_factory = sqlite3.Row
    observer.row_factory = sqlite3.Row
    try:
        keys = eligible_markets(wallet, None)
        needed = args.train_markets + args.validation_markets + args.forward_markets
        if len(keys) < needed:
            raise SystemExit(f"need {needed} settled eligible markets, found {len(keys)}")
        train = keys[: args.train_markets]
        validation_end = args.train_markets + args.validation_markets
        validation = keys[args.train_markets : validation_end]
        forward = keys[validation_end:needed]
        results = [
            {
                "policy": policy,
                "train": evaluate(wallet, observer, train, policy),
                "validation": evaluate(wallet, observer, validation, policy),
                "forward": evaluate(wallet, observer, forward, policy) if forward else None,
            }
            for policy in policies
        ]
    finally:
        wallet.close()
        observer.close()
    selected = max(results, key=lambda row: row["train"]["f1"])
    validation_score = selected["validation"]
    report = {
        "boundary": {
            "decisionInputs": "BTC multi_prediction_trajectory rows only",
            "targetUse": "post-hoc scoring only",
            "trainMarketIds": [market_id for market_id, _ in train],
            "validationMarketIds": [market_id for market_id, _ in validation],
            "forwardMarketIds": [market_id for market_id, _ in forward],
            "matching": "one-to-one same-side <=3s and <=0.021 absolute price delta",
        },
        "policies": results,
        "selectedOnTrain": selected["policy"]["name"],
        "selectedValidation": validation_score,
        "selectedForward": selected["forward"],
        "decision": (
            "PROMOTE_TO_NEW_FORWARD_COHORT"
            if validation_score["f1"] >= 0.8
            and validation_score["precision"] >= 0.75
            and validation_score["recall"] >= 0.75
            else "REJECT_BELOW_THRESHOLD"
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("selectedOnTrain", "selectedValidation", "decision")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
