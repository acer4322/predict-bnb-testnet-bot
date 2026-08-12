from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

DEFAULT_DB = Path("data/predict_wallet_shadow.db")


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def side_from_delta(value: float, eps: float = 1e-9) -> str:
    if value > eps:
        return "UP"
    if value < -eps:
        return "DOWN"
    return "FLAT"


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def parent_target_events(conn: sqlite3.Connection, market_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT role, side, quote_type, order_hash,
               MIN(event_ms) AS first_event_ms,
               MAX(event_ms) AS last_event_ms,
               SUM(COALESCE(shares,0)) AS shares,
               CASE WHEN SUM(COALESCE(shares,0)) > 0
                    THEN SUM(COALESCE(price,0) * COALESCE(shares,0)) / SUM(COALESCE(shares,0))
                    ELSE AVG(price) END AS average_price,
               COUNT(*) AS fill_legs
          FROM wallet_shadow_target_events
         WHERE market_id=?
         GROUP BY role, side, quote_type,
                  CASE WHEN order_hash IS NULL OR order_hash='' THEN leg_id ELSE order_hash END
         ORDER BY first_event_ms ASC
        """,
        (market_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def shadow_events(conn: sqlite3.Connection, market_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, at_ms, event_type, role, side, price, shares,
               core_side, core_source, reason
          FROM wallet_shadow_events
         WHERE market_id=?
         ORDER BY at_ms ASC
        """,
        (market_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def market_result(conn: sqlite3.Connection, market_id: int) -> dict[str, Any] | None:
    try:
        row = conn.execute(
            "SELECT * FROM wallet_shadow_market_results WHERE market_id=? ORDER BY resolved_at_ms DESC LIMIT 1",
            (market_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row else None


def recent_market_ids(conn: sqlite3.Connection, limit: int) -> list[int]:
    rows = conn.execute(
        """
        WITH activity AS (
            SELECT market_id, MAX(event_ms) AS t FROM wallet_shadow_target_events GROUP BY market_id
            UNION ALL
            SELECT market_id, MAX(at_ms) AS t FROM wallet_shadow_events GROUP BY market_id
        )
        SELECT market_id, MAX(t) AS latest
          FROM activity
         GROUP BY market_id
         ORDER BY latest DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [int(row[0]) for row in rows]


def inventory(events: list[dict[str, Any]], role: str) -> dict[str, Any]:
    up = down = 0.0
    for event in events:
        if str(event.get("role") or "").upper() != role:
            continue
        shares = finite(event.get("shares")) or 0.0
        side = str(event.get("side") or "").upper()
        if side == "UP":
            up += shares
        elif side == "DOWN":
            down += shares
    delta = up - down
    return {"upShares": up, "downShares": down, "delta": delta, "residualSide": side_from_delta(delta)}


def nearest(events: list[dict[str, Any]], at_ms: int, *, same_side: str | None = None) -> tuple[dict[str, Any] | None, int | None]:
    candidates = [event for event in events if same_side is None or str(event.get("side") or "").upper() == same_side]
    if not candidates:
        return None, None
    chosen = min(candidates, key=lambda event: abs(int(event["at_ms"]) - at_ms))
    return chosen, int(chosen["at_ms"]) - at_ms


def target_taker_after_same_side_maker(target: list[dict[str, Any]], window_ms: int = 5000) -> dict[str, Any]:
    makers = [event for event in target if str(event.get("role") or "").upper() == "MAKER"]
    takers = [event for event in target if str(event.get("role") or "").upper() == "TAKER"]
    lags: list[float] = []
    price_diffs: list[float] = []
    matched = 0
    for taker in takers:
        t_ms = int(taker["first_event_ms"])
        side = str(taker.get("side") or "").upper()
        prior = [
            maker for maker in makers
            if str(maker.get("side") or "").upper() == side
            and 0 <= t_ms - int(maker["first_event_ms"]) <= window_ms
        ]
        if not prior:
            continue
        maker = max(prior, key=lambda event: int(event["first_event_ms"]))
        matched += 1
        lags.append((t_ms - int(maker["first_event_ms"])) / 1000.0)
        tp = finite(taker.get("average_price"))
        mp = finite(maker.get("average_price"))
        if tp is not None and mp is not None:
            price_diffs.append(tp - mp)
    return {
        "targetTakerParents": len(takers),
        "withSameSideMakerPrior5s": matched,
        "rate": matched / len(takers) if takers else None,
        "medianLagSeconds": median(lags),
        "medianPriceDelta": median(price_diffs),
    }


def taker_comparison(target: list[dict[str, Any]], shadow: list[dict[str, Any]]) -> dict[str, Any]:
    target_taker = [event for event in target if str(event.get("role") or "").upper() == "TAKER" and str(event.get("quote_type") or "").upper() == "BID"]
    shadow_taker = [event for event in shadow if str(event.get("event_type") or "") == "TAKER_INTENT"]
    nearest_any_abs: list[float] = []
    nearest_same_abs: list[float] = []
    side_match_5s = 0
    same_side_3s = 0
    price_diffs: list[float] = []
    qty_ratios: list[float] = []
    for target_event in target_taker:
        t_ms = int(target_event["first_event_ms"])
        t_side = str(target_event.get("side") or "").upper()
        any_event, any_dt = nearest(shadow_taker, t_ms)
        if any_event is not None and any_dt is not None:
            nearest_any_abs.append(abs(any_dt) / 1000.0)
            if abs(any_dt) <= 5000 and str(any_event.get("side") or "").upper() == t_side:
                side_match_5s += 1
        same_event, same_dt = nearest(shadow_taker, t_ms, same_side=t_side)
        if same_event is not None and same_dt is not None:
            nearest_same_abs.append(abs(same_dt) / 1000.0)
            if abs(same_dt) <= 3000:
                same_side_3s += 1
            tp = finite(target_event.get("average_price"))
            sp = finite(same_event.get("price"))
            if tp is not None and sp is not None:
                price_diffs.append(sp - tp)
            tq = finite(target_event.get("shares"))
            sq = finite(same_event.get("shares"))
            if tq and sq is not None:
                qty_ratios.append(sq / tq)
    target_inv = inventory(target_taker, "TAKER")
    shadow_for_inventory = [dict(event, role="TAKER") for event in shadow_taker]
    shadow_inv = inventory(shadow_for_inventory, "TAKER")
    return {
        "targetParents": len(target_taker),
        "shadowIntents": len(shadow_taker),
        "eventCountRatioShadowToTarget": len(shadow_taker) / len(target_taker) if target_taker else None,
        "sideMatchWithin5s": side_match_5s / len(target_taker) if target_taker else None,
        "sameSideTimingWithin3s": same_side_3s / len(target_taker) if target_taker else None,
        "medianNearestAnySeconds": median(nearest_any_abs),
        "medianNearestSameSideSeconds": median(nearest_same_abs),
        "medianShadowMinusTargetPrice": median(price_diffs),
        "medianShadowToTargetQtyRatio": median(qty_ratios),
        "targetInventory": target_inv,
        "shadowInventory": shadow_inv,
        "residualSideMatch": target_inv["residualSide"] == shadow_inv["residualSide"] and target_inv["residualSide"] != "FLAT",
    }


def market_report(conn: sqlite3.Connection, market_id: int) -> dict[str, Any]:
    target = parent_target_events(conn, market_id)
    shadow = shadow_events(conn, market_id)
    target_maker = [event for event in target if str(event.get("role") or "").upper() == "MAKER"]
    target_taker = [event for event in target if str(event.get("role") or "").upper() == "TAKER"]
    shadow_taker = [event for event in shadow if str(event.get("event_type") or "") == "TAKER_INTENT"]
    return {
        "marketId": market_id,
        "result": market_result(conn, market_id),
        "counts": {
            "targetMakerParents": len(target_maker),
            "targetTakerParents": len(target_taker),
            "shadowMakerQuotes": sum(str(event.get("event_type") or "") == "MAKER_QUOTE" for event in shadow),
            "shadowMakerFillProxies": sum(str(event.get("event_type") or "") == "MAKER_FILL_PROXY" for event in shadow),
            "shadowTakerIntents": len(shadow_taker),
        },
        "targetMakerInventory": inventory(target_maker, "MAKER"),
        "targetTakerInventory": inventory(target_taker, "TAKER"),
        "makerToTaker": target_taker_after_same_side_maker(target),
        "takerComparison": taker_comparison(target, shadow),
        "targetTakerEvents": target_taker,
        "shadowTakerEvents": shadow_taker,
    }


def aggregate_report(markets: list[dict[str, Any]]) -> dict[str, Any]:
    comparisons = [market["takerComparison"] for market in markets]
    target_count = sum(int(item["targetParents"]) for item in comparisons)
    shadow_count = sum(int(item["shadowIntents"]) for item in comparisons)
    residual_matches = sum(bool(item["residualSideMatch"]) for item in comparisons)
    market_with_target = sum(int(item["targetParents"]) > 0 for item in comparisons)
    event_ratios = [float(item["eventCountRatioShadowToTarget"]) for item in comparisons if item["eventCountRatioShadowToTarget"] is not None]
    side_rates = [float(item["sideMatchWithin5s"]) for item in comparisons if item["sideMatchWithin5s"] is not None]
    timing_rates = [float(item["sameSideTimingWithin3s"]) for item in comparisons if item["sameSideTimingWithin3s"] is not None]
    maker_follow = [market["makerToTaker"] for market in markets]
    prior5_target = sum(int(item["targetTakerParents"]) for item in maker_follow)
    prior5_match = sum(int(item["withSameSideMakerPrior5s"]) for item in maker_follow)
    return {
        "markets": len(markets),
        "marketsWithTargetTaker": market_with_target,
        "targetTakerParents": target_count,
        "shadowTakerIntents": shadow_count,
        "shadowToTargetEventCountRatio": shadow_count / target_count if target_count else None,
        "medianPerMarketEventCountRatio": median(event_ratios),
        "meanPerMarketSideMatchWithin5s": sum(side_rates) / len(side_rates) if side_rates else None,
        "meanPerMarketSameSideTimingWithin3s": sum(timing_rates) / len(timing_rates) if timing_rates else None,
        "residualSideMatchMarkets": residual_matches,
        "residualSideMatchRate": residual_matches / market_with_target if market_with_target else None,
        "targetTakerAfterSameSideMakerPrior5s": prior5_match,
        "targetTakerParentsForMakerFollow": prior5_target,
        "targetTakerAfterSameSideMakerPrior5sRate": prior5_match / prior5_target if prior5_target else None,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    summary = report["summary"]
    lines = [
        "# Wallet Shadow Recent Taker Analysis",
        "",
        f"Markets: **{summary['markets']}**",
        f"Target Taker parents: **{summary['targetTakerParents']}**",
        f"Shadow Taker intents: **{summary['shadowTakerIntents']}**",
        f"Shadow / Target event-count ratio: **{summary['shadowToTargetEventCountRatio'] if summary['shadowToTargetEventCountRatio'] is not None else 'n/a'}**",
        f"Residual-side match: **{summary['residualSideMatchMarkets']}/{summary['marketsWithTargetTaker']}**",
        f"Target Taker after same-side Maker <=5s: **{summary['targetTakerAfterSameSideMakerPrior5s']}/{summary['targetTakerParentsForMakerFollow']}**",
        "",
        "| Market | Target Taker | Shadow Taker | Count ratio | Residual target | Residual shadow | Side <=5s | Timing <=3s |",
        "|---:|---:|---:|---:|---|---|---:|---:|",
    ]
    for market in report["markets"]:
        comp = market["takerComparison"]
        target_side = comp["targetInventory"]["residualSide"]
        shadow_side = comp["shadowInventory"]["residualSide"]
        ratio = comp["eventCountRatioShadowToTarget"]
        side_rate = comp["sideMatchWithin5s"]
        timing_rate = comp["sameSideTimingWithin3s"]
        lines.append(
            f"| {market['marketId']} | {comp['targetParents']} | {comp['shadowIntents']} | "
            f"{ratio:.2f} | {target_side} | {shadow_side} | "
            f"{side_rate:.1%} | {timing_rate:.1%} |"
            if ratio is not None and side_rate is not None and timing_rate is not None
            else f"| {market['marketId']} | {comp['targetParents']} | {comp['shadowIntents']} | n/a | {target_side} | {shadow_side} | n/a | n/a |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export recent Wallet Shadow markets with Taker event diagnostics")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--out", type=Path, default=Path("wallet_shadow_recent10.json"))
    parser.add_argument("--markdown", type=Path, default=Path("wallet_shadow_recent10.md"))
    args = parser.parse_args()
    if not args.db.exists():
        raise SystemExit(f"database not found: {args.db}")
    limit = max(1, min(50, args.limit))
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        ids = recent_market_ids(conn, limit)
        markets = [market_report(conn, market_id) for market_id in ids]
        report = {
            "sourceDb": str(args.db),
            "limit": limit,
            "summary": aggregate_report(markets),
            "markets": markets,
        }
    finally:
        conn.close()
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report, args.markdown)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"wrote {args.out}")
    print(f"wrote {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
