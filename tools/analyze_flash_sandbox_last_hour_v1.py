from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FLASH_DB = ROOT / "data" / "strategy_target_flash_v1.db"
BASE_DB = ROOT / "data" / "strategy_target_compare_v1.db"
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
OUT_JSON = ROOT / "data" / "research" / "flash_sandbox_last_hour_v1_report.json"
OUT_CSV = ROOT / "data" / "research" / "flash_sandbox_last_hour_v1_markets.csv"
VERSION = "FLASH_SANDBOX_LAST_HOUR_V1"


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    return con


def market_pnl(rows: list[dict[str, Any]], winner: str) -> dict[str, Any]:
    cost = 0.0
    up = 0.0
    down = 0.0
    for row in rows:
        side = str(row["side"])
        shares = float(row["shares"])
        price = float(row["price"])
        cost += price * shares
        if side == "UP":
            up += shares
        elif side == "DOWN":
            down += shares
    payout = up if winner == "UP" else down
    pnl = payout - cost
    return {
        "fills": len(rows), "upShares": up, "downShares": down,
        "costUsdt": cost, "payoutUsdt": payout, "pnlUsdt": pnl,
        "roi": pnl / cost if cost > 1e-12 else None,
    }


def load_fills(con: sqlite3.Connection, start_ms: int) -> dict[tuple[str, int], list[dict[str, Any]]]:
    data: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in con.execute(
        "SELECT strategy_version,market_id,channel,purpose,side,price,shares,filled_at_ms FROM our_fills WHERE filled_at_ms>=? ORDER BY filled_at_ms",
        (start_ms,),
    ):
        data[(str(row["strategy_version"]), int(row["market_id"]))].append(dict(row))
    return data


def load_decision_counts(con: sqlite3.Connection, start_ms: int) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in con.execute(
        "SELECT strategy_version,execution_choice,COUNT(*) n FROM our_decisions WHERE decision_ms>=? GROUP BY strategy_version,execution_choice",
        (start_ms,),
    ):
        result[str(row["strategy_version"])][str(row["execution_choice"])] += int(row["n"])
    return {k: dict(v) for k, v in result.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=1.0)
    parser.add_argument("--end-ms", type=int, default=0)
    args = parser.parse_args()
    end_ms = int(args.end_ms or time.time() * 1000)
    start_ms = int(end_ms - max(0.1, args.hours) * 3_600_000)

    if not FLASH_DB.exists() or not BASE_DB.exists() or not TARGET_DB.exists():
        missing = [str(p) for p in (FLASH_DB, BASE_DB, TARGET_DB) if not p.exists()]
        raise SystemExit(f"missing DB(s): {missing}")

    flash = ro(FLASH_DB)
    base = ro(BASE_DB)
    target = ro(TARGET_DB)
    try:
        flash_fills = load_fills(flash, start_ms)
        base_fills = load_fills(base, start_ms)
        flash_decisions = load_decision_counts(flash, start_ms)

        market_ids = sorted({mid for _, mid in flash_fills} | {
            int(row[0]) for row in flash.execute("SELECT DISTINCT market_id FROM our_decisions WHERE decision_ms>=?", (start_ms,))
        })
        winners: dict[int, str] = {}
        target_pnl: dict[int, float] = {}
        if market_ids:
            marks = ",".join("?" for _ in market_ids)
            for row in target.execute(
                f"SELECT market_id,winner,net_pnl_usdt FROM target_market_results WHERE market_id IN ({marks})",
                market_ids,
            ):
                winners[int(row["market_id"])] = str(row["winner"])
                target_pnl[int(row["market_id"])] = float(row["net_pnl_usdt"])

        versions = sorted({version for version, _ in flash_fills} | set(flash_decisions))
        rows_out: list[dict[str, Any]] = []
        summaries: dict[str, Any] = {}
        for version in versions:
            settled = 0
            traded = 0
            pnl_sum = 0.0
            base_pnl_sum = 0.0
            common = 0
            positive = 0
            worst = None
            best = None
            for market_id in market_ids:
                winner = winners.get(market_id)
                if winner not in {"UP", "DOWN"}:
                    continue
                settled += 1
                ours = market_pnl(flash_fills.get((version, market_id), []), winner)
                base_versions = [k for k in base_fills if k[1] == market_id and k[0].startswith("UNIFIED_CONTROLLER_PAPER_V1")]
                base_rows: list[dict[str, Any]] = []
                for key in base_versions:
                    base_rows.extend(base_fills[key])
                base_result = market_pnl(base_rows, winner)
                if ours["fills"]:
                    traded += 1
                pnl = float(ours["pnlUsdt"])
                pnl_sum += pnl
                if pnl > 1e-9:
                    positive += 1
                worst = pnl if worst is None else min(worst, pnl)
                best = pnl if best is None else max(best, pnl)
                if base_rows:
                    common += 1
                    base_pnl_sum += float(base_result["pnlUsdt"])
                rows_out.append({
                    "strategy_version": version,
                    "market_id": market_id,
                    "winner": winner,
                    "flash_fills": ours["fills"],
                    "flash_cost_usdt": ours["costUsdt"],
                    "flash_pnl_usdt": pnl,
                    "base_fills": base_result["fills"],
                    "base_pnl_usdt": base_result["pnlUsdt"],
                    "delta_vs_base_usdt": pnl - float(base_result["pnlUsdt"]),
                    "target_pnl_usdt": target_pnl.get(market_id),
                })
            delta = pnl_sum - base_pnl_sum if common else None
            summaries[version] = {
                "decisionCounts": flash_decisions.get(version, {}),
                "settledMarketsInWindow": settled,
                "tradedMarkets": traded,
                "positiveMarkets": positive,
                "positiveRate": positive / settled if settled else None,
                "flashPnlUsdt": pnl_sum,
                "commonMarketsWithBaseFills": common,
                "basePnlOnCommonUsdt": base_pnl_sum if common else None,
                "deltaVsBaseUsdt": delta,
                "worstMarketPnlUsdt": worst,
                "bestMarketPnlUsdt": best,
                "classification": "INSUFFICIENT_SAMPLE" if settled < 4 else "REVIEW_REQUIRED",
            }

        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "reportVersion": VERSION,
            "window": {"startMs": start_ms, "endMs": end_ms, "hours": args.hours},
            "researchOnly": True,
            "automaticPromotion": False,
            "warning": "One-hour flash results are triage evidence only. Promotion/removal still requires clean conceptual attribution and fresh blind/full validation.",
            "variants": summaries,
            "settledMarketRows": len(rows_out),
            "outputs": {"json": str(OUT_JSON), "csv": str(OUT_CSV)},
        }
        OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        with OUT_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
            fields = [
                "strategy_version", "market_id", "winner", "flash_fills", "flash_cost_usdt", "flash_pnl_usdt",
                "base_fills", "base_pnl_usdt", "delta_vs_base_usdt", "target_pnl_usdt",
            ]
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows_out)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        flash.close(); base.close(); target.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
