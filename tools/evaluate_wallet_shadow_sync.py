from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_DB = Path("data/predict_wallet_shadow.db")


@dataclass(frozen=True)
class Event:
    at_ms: int
    side: str
    price: float | None
    event_id: str


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def one_to_one_matches(
    targets: Iterable[Event],
    shadows: Iterable[Event],
    *,
    max_lag_ms: int,
    max_price_delta: float | None,
) -> list[dict[str, Any]]:
    """Greedily select the lowest-cost unique target/shadow event pairs."""
    target_rows = list(targets)
    shadow_rows = list(shadows)
    candidates: list[tuple[float, int, int, int, float | None]] = []
    for target_index, target in enumerate(target_rows):
        for shadow_index, shadow in enumerate(shadow_rows):
            if target.side != shadow.side:
                continue
            lag_ms = shadow.at_ms - target.at_ms
            if abs(lag_ms) > max_lag_ms:
                continue
            price_delta = None
            if target.price is not None and shadow.price is not None:
                price_delta = shadow.price - target.price
            if max_price_delta is not None and (
                price_delta is None or abs(price_delta) > max_price_delta + 1e-12
            ):
                continue
            price_cost = abs(price_delta or 0.0) * 100_000.0
            candidates.append((abs(lag_ms) + price_cost, target_index, shadow_index, lag_ms, price_delta))
    candidates.sort(key=lambda row: (row[0], abs(row[3]), row[1], row[2]))
    used_targets: set[int] = set()
    used_shadows: set[int] = set()
    matches: list[dict[str, Any]] = []
    for _, target_index, shadow_index, lag_ms, price_delta in candidates:
        if target_index in used_targets or shadow_index in used_shadows:
            continue
        used_targets.add(target_index)
        used_shadows.add(shadow_index)
        target = target_rows[target_index]
        shadow = shadow_rows[shadow_index]
        matches.append(
            {
                "targetId": target.event_id,
                "shadowId": shadow.event_id,
                "side": target.side,
                "lagMs": lag_ms,
                "priceDelta": price_delta,
            }
        )
    return matches


def score(targets: list[Event], shadows: list[Event], matches: list[dict[str, Any]]) -> dict[str, Any]:
    matched = len(matches)
    precision = matched / len(shadows) if shadows else (1.0 if not targets else 0.0)
    recall = matched / len(targets) if targets else (1.0 if not shadows else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    lags = sorted(abs(int(row["lagMs"])) for row in matches)
    return {
        "targets": len(targets),
        "shadows": len(shadows),
        "matches": matched,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "medianAbsoluteLagMs": lags[len(lags) // 2] if lags else None,
    }


def _parent_targets(conn: sqlite3.Connection, market_id: int, role: str, start_ms: int) -> list[Event]:
    rows = conn.execute(
        """
        SELECT role, side, quote_type,
               CASE WHEN order_hash IS NULL OR order_hash='' THEN leg_id ELSE order_hash END AS parent_id,
               MIN(event_ms) AS first_event_ms,
               CASE WHEN SUM(COALESCE(shares,0)) > 0
                    THEN SUM(COALESCE(price,0) * COALESCE(shares,0)) / SUM(COALESCE(shares,0))
                    ELSE AVG(price) END AS average_price
          FROM wallet_shadow_target_events
         WHERE market_id=? AND role=? AND event_ms>=?
         GROUP BY role, side, quote_type,
                  CASE WHEN order_hash IS NULL OR order_hash='' THEN leg_id ELSE order_hash END
        HAVING quote_type='BID'
         ORDER BY first_event_ms
        """,
        (market_id, role, start_ms),
    ).fetchall()
    return [
        Event(int(row["first_event_ms"]), str(row["side"]), _finite(row["average_price"]), str(row["parent_id"]))
        for row in rows
        if row["side"] in {"UP", "DOWN"}
    ]


def _shadow_events(conn: sqlite3.Connection, market_id: int, start_ms: int, kind: str) -> list[Event]:
    if kind == "maker":
        rows = conn.execute(
            """
            SELECT id,at_ms,side,price FROM wallet_shadow_events
             WHERE market_id=? AND at_ms>=? AND event_type='MAKER_FILL_PROXY'
             ORDER BY at_ms
            """,
            (market_id, start_ms),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id,at_ms,side,price FROM wallet_shadow_taker_v1_events
             WHERE market_id=? AND at_ms>=?
             ORDER BY at_ms
            """,
            (market_id, start_ms),
        ).fetchall()
    return [
        Event(int(row["at_ms"]), str(row["side"]), _finite(row["price"]), str(row["id"]))
        for row in rows
        if row["side"] in {"UP", "DOWN"}
    ]


def collapse_bursts(events: list[Event], gap_ms: int) -> list[Event]:
    """Collapse same-side execution fragments without mixing opposing decisions."""
    collapsed: list[Event] = []
    for side in ("UP", "DOWN"):
        current: list[Event] = []
        for event in sorted((row for row in events if row.side == side), key=lambda row: row.at_ms):
            if current and event.at_ms - current[-1].at_ms > gap_ms:
                middle = current[len(current) // 2]
                collapsed.append(
                    Event(middle.at_ms, side, middle.price, "+".join(row.event_id for row in current))
                )
                current = []
            current.append(event)
        if current:
            middle = current[len(current) // 2]
            collapsed.append(
                Event(middle.at_ms, side, middle.price, "+".join(row.event_id for row in current))
            )
    return sorted(collapsed, key=lambda row: row.at_ms)


def eligible_markets(conn: sqlite3.Connection, limit: int | None) -> list[tuple[int, int]]:
    suffix = "LIMIT ?" if limit is not None else ""
    params: tuple[Any, ...] = (limit,) if limit is not None else ()
    rows = conn.execute(
        f"""
        SELECT m.market_id,m.started_at_ms,r.resolved_at_ms AS activity_ms
          FROM wallet_shadow_taker_v1_markets m
          JOIN wallet_shadow_taker_v1_market_results r ON r.wallet=m.wallet AND r.market_id=m.market_id
          LEFT JOIN wallet_shadow_target_events e ON e.wallet=m.wallet AND e.market_id=m.market_id
          LEFT JOIN wallet_shadow_taker_v1_events s ON s.wallet=m.wallet AND s.market_id=m.market_id
         GROUP BY m.wallet,m.market_id,m.started_at_ms,r.resolved_at_ms
         ORDER BY activity_ms DESC
         {suffix}
        """,
        params,
    ).fetchall()
    return [(int(row["market_id"]), int(row["started_at_ms"])) for row in reversed(rows)]


def evaluate_market(conn: sqlite3.Connection, market_id: int, start_ms: int) -> dict[str, Any]:
    maker_targets = _parent_targets(conn, market_id, "MAKER", start_ms)
    taker_targets = _parent_targets(conn, market_id, "TAKER", start_ms)
    maker_shadows = _shadow_events(conn, market_id, start_ms, "maker")
    taker_shadows = _shadow_events(conn, market_id, start_ms, "taker")

    def evaluate(targets: list[Event], shadows: list[Event], price_delta: float) -> dict[str, Any]:
        structural_matches = one_to_one_matches(
            targets, shadows, max_lag_ms=3_000, max_price_delta=None
        )
        strict_matches = one_to_one_matches(
            targets, shadows, max_lag_ms=3_000, max_price_delta=price_delta
        )
        target_bursts = collapse_bursts(targets, 1_000)
        shadow_bursts = collapse_bursts(shadows, 1_000)
        burst_matches = one_to_one_matches(
            target_bursts, shadow_bursts, max_lag_ms=3_000, max_price_delta=None
        )
        return {
            "structural": score(targets, shadows, structural_matches),
            "strict": score(targets, shadows, strict_matches),
            "burstStructural": score(target_bursts, shadow_bursts, burst_matches),
        }

    return {
        "marketId": market_id,
        "cohortStartedAtMs": start_ms,
        "maker": evaluate(maker_targets, maker_shadows, 0.011),
        "takerV1": evaluate(taker_targets, taker_shadows, 0.021),
    }


def aggregate(markets: list[dict[str, Any]], group: str, level: str) -> dict[str, Any]:
    targets = sum(int(m[group][level]["targets"]) for m in markets)
    shadows = sum(int(m[group][level]["shadows"]) for m in markets)
    matches = sum(int(m[group][level]["matches"]) for m in markets)
    precision = matches / shadows if shadows else (1.0 if not targets else 0.0)
    recall = matches / targets if targets else (1.0 if not shadows else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "targets": targets,
        "shadows": shadows,
        "matches": matches,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def batches(markets: list[dict[str, Any]], batch_size: int) -> list[dict[str, Any]]:
    result = []
    for index in range(0, len(markets), batch_size):
        group = markets[index : index + batch_size]
        result.append(
            {
                "batch": index // batch_size + 1,
                "marketIds": [row["marketId"] for row in group],
                "makerStrict": aggregate(group, "maker", "strict"),
                "takerStrict": aggregate(group, "takerV1", "strict"),
                "makerBurstStructural": aggregate(group, "maker", "burstStructural"),
                "takerBurstStructural": aggregate(group, "takerV1", "burstStructural"),
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict one-to-one Wallet Shadow event synchronization evaluator")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        market_keys = eligible_markets(conn, max(1, args.limit) if args.limit else None)
        markets = [evaluate_market(conn, market_id, start_ms) for market_id, start_ms in market_keys]
    finally:
        conn.close()
    report = {
        "definition": {
            "matching": "one-to-one; same side; absolute timing <=3s",
            "makerStrictPriceDelta": 0.011,
            "takerStrictPriceDelta": 0.021,
            "eligibility": "target and shadow events at or after the market-specific V1 start boundary",
            "thresholdMetric": "strict F1; precision and recall both prevent over-emission from inflating synchronization",
            "diagnosticBurstMetric": "same-side events <=1s apart are collapsed only as a secondary fragmentation diagnostic",
        },
        "summary": {
            "markets": len(markets),
            "makerStructural": aggregate(markets, "maker", "structural"),
            "makerStrict": aggregate(markets, "maker", "strict"),
            "takerStructural": aggregate(markets, "takerV1", "structural"),
            "takerStrict": aggregate(markets, "takerV1", "strict"),
            "makerBurstStructural": aggregate(markets, "maker", "burstStructural"),
            "takerBurstStructural": aggregate(markets, "takerV1", "burstStructural"),
        },
        "batches": batches(markets, max(1, args.batch_size)),
        "markets": markets,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
