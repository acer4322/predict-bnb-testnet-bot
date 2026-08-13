from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.evaluate_wallet_shadow_sync import DEFAULT_DB, eligible_markets  # noqa: E402


def parent_rows(conn: sqlite3.Connection, market_id: int, start_ms: int, role: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT side,
               CASE WHEN order_hash IS NULL OR order_hash='' THEN leg_id ELSE order_hash END AS parent_id,
               MIN(event_ms) AS at_ms,
               SUM(COALESCE(shares,0)) AS shares,
               CASE WHEN SUM(COALESCE(shares,0)) > 0
                    THEN SUM(COALESCE(price,0)*COALESCE(shares,0))/SUM(COALESCE(shares,0))
                    ELSE AVG(price) END AS price
          FROM wallet_shadow_target_events
         WHERE market_id=? AND event_ms>=? AND role=? AND quote_type='BID'
         GROUP BY side,CASE WHEN order_hash IS NULL OR order_hash='' THEN leg_id ELSE order_hash END
         ORDER BY at_ms
        """,
        (market_id, start_ms, role),
    ).fetchall()
    return [dict(row) for row in rows]


def shadow_rows(conn: sqlite3.Connection, market_id: int, start_ms: int, event_type: str) -> list[dict[str, Any]]:
    table = "wallet_shadow_taker_v1_events" if event_type == "TAKER_V1" else "wallet_shadow_events"
    where = "" if event_type == "TAKER_V1" else "AND event_type='MAKER_FILL_PROXY'"
    rows = conn.execute(
        f"SELECT side,at_ms,shares,price FROM {table} WHERE market_id=? AND at_ms>=? {where} ORDER BY at_ms",
        (market_id, start_ms),
    ).fetchall()
    return [dict(row) for row in rows]


def quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]


def stream_fingerprint(markets: list[list[dict[str, Any]]]) -> dict[str, Any]:
    rows = [row for market in markets for row in market]
    shares = [float(row["shares"]) for row in rows]
    prices = [float(row["price"]) for row in rows if row.get("price") is not None]
    both_sides = sum({str(row["side"]) for row in market} >= {"UP", "DOWN"} for market in markets)
    up = sum(float(row["shares"]) for row in rows if row["side"] == "UP")
    down = sum(float(row["shares"]) for row in rows if row["side"] == "DOWN")
    return {
        "events": len(rows),
        "eventsPerMarketMedian": statistics.median([len(market) for market in markets]) if markets else None,
        "exact18Share": sum(abs(value - 18.0) <= 1e-6 for value in shares) / len(shares) if shares else None,
        "centGrid": sum(abs(value * 100 - round(value * 100)) <= 1e-6 for value in prices) / len(prices) if prices else None,
        "bothSidesMarketRate": both_sides / len(markets) if markets else None,
        "upShareFraction": up / (up + down) if up + down else None,
        "shareMedian": statistics.median(shares) if shares else None,
        "shareP90": quantile(shares, 0.90),
    }


def maker_to_taker_rate(makers: list[dict[str, Any]], takers: list[dict[str, Any]], window_ms: int = 5_000) -> float | None:
    if not takers:
        return None
    matched = 0
    for taker in takers:
        if any(
            maker["side"] == taker["side"]
            and 0 <= int(taker["at_ms"]) - int(maker["at_ms"]) <= window_ms
            for maker in makers
        ):
            matched += 1
    return matched / len(takers)


def residual_side(rows: list[dict[str, Any]]) -> str:
    delta = sum(float(row["shares"]) * (1 if row["side"] == "UP" else -1) for row in rows)
    return "UP" if delta > 1e-9 else "DOWN" if delta < -1e-9 else "FLAT"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare causal Wallet Shadow strategy fingerprints")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        keys = eligible_markets(conn, args.limit)
        target_maker = [parent_rows(conn, mid, start, "MAKER") for mid, start in keys]
        target_taker = [parent_rows(conn, mid, start, "TAKER") for mid, start in keys]
        shadow_maker = [shadow_rows(conn, mid, start, "MAKER") for mid, start in keys]
        shadow_taker = [shadow_rows(conn, mid, start, "TAKER_V1") for mid, start in keys]
        target_follow = [maker_to_taker_rate(m, t) for m, t in zip(target_maker, target_taker)]
        shadow_follow = [maker_to_taker_rate(m, t) for m, t in zip(shadow_maker, shadow_taker)]
        residual_matches = sum(
            residual_side(target) == residual_side(shadow) and residual_side(target) != "FLAT"
            for target, shadow in zip(target_taker, shadow_taker)
        )
    finally:
        conn.close()
    report = {
        "markets": [mid for mid, _ in keys],
        "targetMaker": stream_fingerprint(target_maker),
        "shadowMaker": stream_fingerprint(shadow_maker),
        "targetTaker": stream_fingerprint(target_taker),
        "shadowTakerV1": stream_fingerprint(shadow_taker),
        "roleTransition": {
            "targetMeanSameSideMakerPrior5s": statistics.mean(x for x in target_follow if x is not None),
            "shadowMeanSameSideMakerPrior5s": statistics.mean(x for x in shadow_follow if x is not None),
        },
        "takerResidualSideMatch": {
            "markets": residual_matches,
            "rate": residual_matches / len(keys) if keys else None,
        },
        "interpretation": {
            "structuralMatch": "18-share and cent-grid rates can support execution-structure similarity",
            "notProof": "matching static fingerprints does not prove trigger, hidden-order state, queue position, or inventory objective",
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
