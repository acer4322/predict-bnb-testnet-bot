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

from tools import analyze_wallet_shadow_recent as base_analysis  # noqa: E402

DEFAULT_DB = Path("data/predict_wallet_shadow.db")


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def v1_events(conn: sqlite3.Connection, market_id: int) -> list[dict[str, Any]]:
    if not table_exists(conn, "wallet_shadow_taker_v1_events"):
        return []
    rows = conn.execute(
        """
        SELECT id,at_ms,'TAKER_INTENT' AS event_type,'TAKER' AS role,
               side,price,shares,trigger,core_side,reason
        FROM wallet_shadow_taker_v1_events
        WHERE market_id=?
        ORDER BY at_ms ASC
        """,
        (market_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def v1_result(conn: sqlite3.Connection, market_id: int) -> dict[str, Any] | None:
    if not table_exists(conn, "wallet_shadow_taker_v1_market_results"):
        return None
    row = conn.execute(
        "SELECT * FROM wallet_shadow_taker_v1_market_results WHERE market_id=? ORDER BY resolved_at_ms DESC LIMIT 1",
        (market_id,),
    ).fetchone()
    return dict(row) if row else None


def v1_start(conn: sqlite3.Connection, market_id: int) -> int | None:
    if not table_exists(conn, "wallet_shadow_taker_v1_markets"):
        return None
    row = conn.execute(
        "SELECT started_at_ms FROM wallet_shadow_taker_v1_markets WHERE market_id=? ORDER BY started_at_ms ASC LIMIT 1",
        (market_id,),
    ).fetchone()
    return int(row[0]) if row else None


def market_report(conn: sqlite3.Connection, market_id: int) -> dict[str, Any]:
    target = base_analysis.parent_target_events(conn, market_id)
    v0_shadow = base_analysis.shadow_events(conn, market_id)
    v1_shadow = v1_events(conn, market_id)
    target_taker = [event for event in target if str(event.get("role") or "").upper() == "TAKER"]
    v0_taker = [event for event in v0_shadow if str(event.get("event_type") or "") == "TAKER_INTENT"]
    return {
        "marketId": market_id,
        "v1StartedAtMs": v1_start(conn, market_id),
        "v0Result": base_analysis.market_result(conn, market_id),
        "v1Result": v1_result(conn, market_id),
        "makerToTaker": base_analysis.target_taker_after_same_side_maker(target),
        "v0": base_analysis.taker_comparison(target, v0_shadow),
        "v1": base_analysis.taker_comparison(target, v1_shadow),
        "counts": {
            "targetTakerParents": len(target_taker),
            "v0TakerIntents": len(v0_taker),
            "v1TakerIntents": len(v1_shadow),
        },
        "v1Events": v1_shadow,
    }


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize(markets: list[dict[str, Any]]) -> dict[str, Any]:
    started = [market for market in markets if market.get("v1StartedAtMs") is not None]
    target = sum(int(market["counts"]["targetTakerParents"]) for market in started)
    v0 = sum(int(market["counts"]["v0TakerIntents"]) for market in started)
    v1 = sum(int(market["counts"]["v1TakerIntents"]) for market in started)

    def metric(group: str, key: str) -> float | None:
        values = [
            float(market[group][key])
            for market in started
            if market[group].get(key) is not None
        ]
        return average(values)

    def residual(group: str) -> int:
        return sum(bool(market[group].get("residualSideMatch")) for market in started)

    return {
        "markets": len(markets),
        "v1EligibleMarkets": len(started),
        "targetTakerParents": target,
        "v0TakerIntents": v0,
        "v1TakerIntents": v1,
        "v0ToTargetCountRatio": v0 / target if target else None,
        "v1ToTargetCountRatio": v1 / target if target else None,
        "v0MeanSideMatchWithin5s": metric("v0", "sideMatchWithin5s"),
        "v1MeanSideMatchWithin5s": metric("v1", "sideMatchWithin5s"),
        "v0MeanTimingWithin3s": metric("v0", "sameSideTimingWithin3s"),
        "v1MeanTimingWithin3s": metric("v1", "sameSideTimingWithin3s"),
        "v0ResidualSideMatches": residual("v0"),
        "v1ResidualSideMatches": residual("v1"),
    }


def fmt_ratio(value: Any) -> str:
    parsed = base_analysis.finite(value)
    return f"{parsed:.2f}" if parsed is not None else "n/a"


def fmt_pct(value: Any) -> str:
    parsed = base_analysis.finite(value)
    return f"{parsed:.1%}" if parsed is not None else "n/a"


def write_markdown(report: dict[str, Any], path: Path) -> None:
    summary = report["summary"]
    lines = [
        "# Wallet Shadow Taker A/B Recent Analysis",
        "",
        f"Markets exported: **{summary['markets']}**",
        f"V1 eligible markets: **{summary['v1EligibleMarkets']}**",
        f"Target Taker parents: **{summary['targetTakerParents']}**",
        f"V0 / Target count ratio: **{fmt_ratio(summary['v0ToTargetCountRatio'])}x**",
        f"V1 / Target count ratio: **{fmt_ratio(summary['v1ToTargetCountRatio'])}x**",
        f"V0 side <=5s: **{fmt_pct(summary['v0MeanSideMatchWithin5s'])}**",
        f"V1 side <=5s: **{fmt_pct(summary['v1MeanSideMatchWithin5s'])}**",
        f"V0 timing <=3s: **{fmt_pct(summary['v0MeanTimingWithin3s'])}**",
        f"V1 timing <=3s: **{fmt_pct(summary['v1MeanTimingWithin3s'])}**",
        "",
        "| Market | Target | V0 | V1 | V0 ratio | V1 ratio | V0 side | V1 side | V0 timing | V1 timing | Target residual | V0 residual | V1 residual |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for market in report["markets"]:
        v0 = market["v0"]
        v1 = market["v1"]
        lines.append(
            f"| {market['marketId']} | {market['counts']['targetTakerParents']} | "
            f"{market['counts']['v0TakerIntents']} | {market['counts']['v1TakerIntents']} | "
            f"{fmt_ratio(v0.get('eventCountRatioShadowToTarget'))} | {fmt_ratio(v1.get('eventCountRatioShadowToTarget'))} | "
            f"{fmt_pct(v0.get('sideMatchWithin5s'))} | {fmt_pct(v1.get('sideMatchWithin5s'))} | "
            f"{fmt_pct(v0.get('sameSideTimingWithin3s'))} | {fmt_pct(v1.get('sameSideTimingWithin3s'))} | "
            f"{v0['targetInventory']['residualSide']} | {v0['shadowInventory']['residualSide']} | {v1['shadowInventory']['residualSide']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Target vs Taker V0 vs Taker V1 recent diagnostics")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--out", type=Path, default=Path("wallet_shadow_taker_ab_recent10.json"))
    parser.add_argument("--markdown", type=Path, default=Path("wallet_shadow_taker_ab_recent10.md"))
    args = parser.parse_args()
    if not args.db.exists():
        raise SystemExit(f"database not found: {args.db}")
    limit = max(1, min(50, args.limit))
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        ids = base_analysis.recent_market_ids(conn, limit)
        markets = [market_report(conn, market_id) for market_id in ids]
    finally:
        conn.close()
    report = {
        "sourceDb": str(args.db),
        "limit": limit,
        "summary": summarize(markets),
        "markets": markets,
    }
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(report, args.markdown)
    print(f"wrote {args.out} and {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
