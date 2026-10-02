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

from tools.evaluate_wallet_shadow_sync import (
    DEFAULT_DB,
    Event,
    _parent_targets,
    _shadow_events,
    eligible_markets,
    one_to_one_matches,
    score,
)


def quote_change_candidate(
    conn: sqlite3.Connection,
    market_id: int,
    start_ms: int,
    *,
    cooldown_ms: int,
    direction: str,
) -> list[Event]:
    """Generate causal Maker decisions using only previously observed local book quotes."""
    rows = conn.execute(
        """
        SELECT id,at_ms,side,price
          FROM wallet_shadow_events
         WHERE market_id=? AND at_ms>=? AND event_type='MAKER_QUOTE'
         ORDER BY at_ms,id
        """,
        (market_id, start_ms),
    ).fetchall()
    previous: dict[str, float] = {}
    last_emit: dict[str, int] = {}
    events: list[Event] = []
    for row in rows:
        side = str(row["side"])
        price = round(float(row["price"]) + 1e-9, 2)
        old_price = previous.get(side)
        previous[side] = price
        if old_price is None or price == old_price:
            continue
        if direction == "DOWN_ONLY" and price >= old_price:
            continue
        if int(row["at_ms"]) - last_emit.get(side, -10**15) < cooldown_ms:
            continue
        last_emit[side] = int(row["at_ms"])
        events.append(
            Event(
                at_ms=int(row["at_ms"]),
                side=side,
                price=price,
                event_id=f"QUOTE_CHANGE:{row['id']}",
            )
        )
    return events


def evaluate_policy(
    conn: sqlite3.Connection,
    markets: list[tuple[int, int]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    targets: list[Event] = []
    shadows: list[Event] = []
    matches: list[dict[str, Any]] = []
    per_market = []
    for market_id, start_ms in markets:
        target = _parent_targets(conn, market_id, "MAKER", start_ms)
        if policy["kind"] == "CURRENT_V1_PROXY":
            shadow = _shadow_events(conn, market_id, start_ms, "maker")
        else:
            shadow = quote_change_candidate(
                conn,
                market_id,
                start_ms,
                cooldown_ms=int(policy["cooldownMs"]),
                direction=str(policy["direction"]),
            )
        matched = one_to_one_matches(target, shadow, max_lag_ms=3_000, max_price_delta=0.011)
        per_market.append({"marketId": market_id, **score(target, shadow, matched)})
        targets.extend(target)
        shadows.extend(shadow)
        matches.extend(matched)
    return {"aggregate": score(targets, shadows, matches), "markets": per_market}


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay target-independent Wallet Shadow Maker candidates")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--train-markets", type=int, default=3)
    parser.add_argument("--validation-markets", type=int, default=3)
    parser.add_argument("--forward-markets", type=int, default=0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    policies = [
        {"name": "CURRENT_V1_PROXY", "kind": "CURRENT_V1_PROXY"},
        {"name": "QUOTE_CHANGE_NEW_BID_1S", "kind": "QUOTE_CHANGE", "cooldownMs": 1_000, "direction": "ANY"},
        {"name": "DOWN_CROSS_NEW_BID", "kind": "QUOTE_CHANGE", "cooldownMs": 0, "direction": "DOWN_ONLY"},
    ]
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        keys = eligible_markets(conn, None)
        needed = args.train_markets + args.validation_markets + args.forward_markets
        if len(keys) < needed:
            raise SystemExit(f"need {needed} settled eligible markets, found {len(keys)}")
        train = keys[: args.train_markets]
        validation_end = args.train_markets + args.validation_markets
        validation = keys[args.train_markets : validation_end]
        forward = keys[validation_end:needed]
        results = []
        for policy in policies:
            results.append(
                {
                    "policy": policy,
                    "train": evaluate_policy(conn, train, policy),
                    "validation": evaluate_policy(conn, validation, policy),
                    "forward": evaluate_policy(conn, forward, policy) if forward else None,
                }
            )
    finally:
        conn.close()
    selected = max(results, key=lambda row: row["train"]["aggregate"]["f1"])
    report = {
        "boundary": {
            "decisionInputs": "wallet_shadow_events MAKER_QUOTE rows only; no target event is read by candidate generation",
            "targetUse": "post-hoc scoring only",
            "trainMarketIds": [market_id for market_id, _ in train],
            "validationMarketIds": [market_id for market_id, _ in validation],
            "forwardMarketIds": [market_id for market_id, _ in forward],
            "matching": "one-to-one same-side <=3s and <=0.011 absolute price delta",
        },
        "policies": results,
        "selectedOnTrain": selected["policy"]["name"],
        "selectedValidation": selected["validation"]["aggregate"],
        "selectedForward": selected["forward"]["aggregate"] if selected["forward"] else None,
        "decision": (
            "PROMOTE_TO_NEW_FORWARD_COHORT"
            if selected["validation"]["aggregate"]["f1"] >= 0.8
            and selected["validation"]["aggregate"]["precision"] >= 0.75
            and selected["validation"]["aggregate"]["recall"] >= 0.75
            else "REJECT_BELOW_THRESHOLD"
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("selectedOnTrain", "selectedValidation", "decision")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
