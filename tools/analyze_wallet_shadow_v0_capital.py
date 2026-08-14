from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "predict_wallet_shadow.db"
DEFAULT_OUTPUT = ROOT / "artifacts" / "wallet_profit_strategy" / "wallet_shadow_v0_capital.json"


@dataclass(frozen=True)
class Scenario:
    taker_cap_base_shares: float
    unit_scale: float

    @property
    def name(self) -> str:
        return f"TAKER_CAP_{self.taker_cap_base_shares:g}_SCALE_{self.unit_scale:g}"


@dataclass(frozen=True)
class ChurnScenario:
    maximum_taker_side_switches: int
    taker_cap_base_shares: float
    unit_scale: float

    @property
    def name(self) -> str:
        return (
            f"MAX_SWITCHES_{self.maximum_taker_side_switches}_"
            f"TAKER_CAP_{self.taker_cap_base_shares:g}_SCALE_{self.unit_scale:g}"
        )


def chronological_splits(market_ids: list[int]) -> dict[str, set[int]]:
    ordered = list(dict.fromkeys(market_ids))
    n = len(ordered)
    train_end = int(n * 0.60)
    validation_end = int(n * 0.80)
    return {
        "train": set(ordered[:train_end]),
        "validation": set(ordered[train_end:validation_end]),
        "holdout": set(ordered[validation_end:]),
    }


def replay_market(events: list[dict[str, Any]], winner: str, scenario: Scenario) -> dict[str, Any]:
    cost = payout = maker_cost = maker_payout = taker_cost = taker_payout = 0.0
    for event in events:
        role = str(event["role"]).upper()
        shares = float(event["shares"])
        if role == "TAKER":
            shares = min(shares, scenario.taker_cap_base_shares)
        shares *= scenario.unit_scale
        event_cost = float(event["price"]) * shares
        event_payout = shares if str(event["side"]).upper() == winner else 0.0
        cost += event_cost
        payout += event_payout
        if role == "MAKER":
            maker_cost += event_cost
            maker_payout += event_payout
        else:
            taker_cost += event_cost
            taker_payout += event_payout
    return {
        "cost": cost,
        "pnl": payout - cost,
        "makerCost": maker_cost,
        "makerPnl": maker_payout - maker_cost,
        "takerCost": taker_cost,
        "takerPnl": taker_payout - taker_cost,
        "eventCount": len(events),
    }


def replay_market_with_churn_guard(
    events: list[dict[str, Any]], winner: str, scenario: ChurnScenario
) -> dict[str, Any]:
    accepted: list[dict[str, Any]] = []
    last_taker_side: str | None = None
    switches = 0
    taker_blocked = False
    for event in events:
        role = str(event["role"]).upper()
        side = str(event["side"]).upper()
        if role == "TAKER":
            if last_taker_side is not None and side != last_taker_side:
                switches += 1
            last_taker_side = side
            if switches > scenario.maximum_taker_side_switches:
                taker_blocked = True
            if taker_blocked:
                continue
        shares = float(event["shares"])
        if role == "TAKER":
            shares = min(shares, scenario.taker_cap_base_shares)
        accepted.append({**event, "shares": shares * scenario.unit_scale})
    replayed = replay_market(accepted, winner, Scenario(float("inf"), 1.0))
    replayed["originalEventCount"] = len(events)
    replayed["takerSideSwitchesObserved"] = switches
    return replayed


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cost = sum(float(row["cost"]) for row in rows)
    pnl = sum(float(row["pnl"]) for row in rows)
    equity = peak = max_drawdown = 0.0
    loss_streak = longest_loss_streak = 0
    for row in rows:
        equity += float(row["pnl"])
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        if float(row["pnl"]) < 0:
            loss_streak += 1
            longest_loss_streak = max(longest_loss_streak, loss_streak)
        else:
            loss_streak = 0
    return {
        "markets": len(rows),
        "events": sum(int(row["eventCount"]) for row in rows),
        "profitableMarkets": sum(float(row["pnl"]) > 0 for row in rows),
        "marketWinRate": sum(float(row["pnl"]) > 0 for row in rows) / len(rows) if rows else None,
        "costUsdt": cost,
        "pnlUsdt": pnl,
        "roi": pnl / cost if cost else None,
        "makerCostUsdt": sum(float(row["makerCost"]) for row in rows),
        "makerPnlUsdt": sum(float(row["makerPnl"]) for row in rows),
        "takerCostUsdt": sum(float(row["takerCost"]) for row in rows),
        "takerPnlUsdt": sum(float(row["takerPnl"]) for row in rows),
        "averageCostPerMarketUsdt": cost / len(rows) if rows else None,
        "maximumCostPerMarketUsdt": max((float(row["cost"]) for row in rows), default=None),
        "maxDrawdownUsdt": max_drawdown,
        "longestLossStreak": longest_loss_streak,
    }


def analyze(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        results = [dict(row) for row in conn.execute(
            """SELECT market_id,winner,resolved_at_ms FROM wallet_shadow_market_results
                 WHERE traded=1 ORDER BY resolved_at_ms,market_id"""
        )]
        market_ids = [int(row["market_id"]) for row in results]
        marks = ",".join("?" for _ in market_ids)
        events = [dict(row) for row in conn.execute(
            f"""SELECT market_id,at_ms,event_type,role,side,price,shares
                   FROM wallet_shadow_events
                  WHERE market_id IN ({marks})
                    AND event_type IN ('MAKER_FILL_PROXY','TAKER_INTENT')
                    AND price IS NOT NULL AND shares>0
                  ORDER BY market_id,at_ms,id""",
            market_ids,
        )]
    finally:
        conn.close()
    events_by_market: dict[int, list[dict[str, Any]]] = {}
    for event in events:
        events_by_market.setdefault(int(event["market_id"]), []).append(event)
    split_ids = chronological_splits(market_ids)
    scenarios = [
        Scenario(taker_cap, scale)
        for taker_cap in (18.0, 36.0, 54.0, 72.0, 90.0, 108.0, 144.0, 180.0)
        for scale in (1.0, 1.0 / 3.0, 1.0 / 6.0, 1.0 / 9.0, 1.0 / 18.0)
    ]
    rows = []
    for scenario in scenarios:
        replayed = {
            int(result["market_id"]): replay_market(
                events_by_market.get(int(result["market_id"]), []),
                str(result["winner"]),
                scenario,
            )
            for result in results
        }
        split_summary = {
            name: summarize([replayed[mid] for mid in market_ids if mid in ids])
            for name, ids in split_ids.items()
        }
        full = summarize([replayed[mid] for mid in market_ids])
        stable_positive = all(float(split_summary[name]["roi"] or 0) > 0 for name in split_summary)
        rows.append({
            "scenario": {
                "name": scenario.name,
                "takerCapBaseShares": scenario.taker_cap_base_shares,
                "unitScale": scenario.unit_scale,
                "effectiveMakerUnitShares": 18.0 * scenario.unit_scale,
                "effectiveTakerCapShares": scenario.taker_cap_base_shares * scenario.unit_scale,
            },
            "full": full,
            "splits": split_summary,
            "stablePositive": stable_positive,
        })
    stable = [row for row in rows if row["stablePositive"]]
    stable.sort(key=lambda row: (float(row["full"]["maximumCostPerMarketUsdt"]), -float(row["full"]["roi"])))
    churn_rows = []
    for scenario in (
        ChurnScenario(max_switches, taker_cap, scale)
        for max_switches in (0, 1, 2)
        for taker_cap in (18.0, 36.0, 54.0, 72.0, 90.0, 108.0, 144.0, 180.0)
        for scale in (1.0, 1.0 / 3.0, 1.0 / 6.0, 1.0 / 9.0, 1.0 / 18.0)
    ):
        replayed = {
            int(result["market_id"]): replay_market_with_churn_guard(
                events_by_market.get(int(result["market_id"]), []),
                str(result["winner"]),
                scenario,
            )
            for result in results
        }
        split_summary = {
            name: summarize([replayed[mid] for mid in market_ids if mid in ids])
            for name, ids in split_ids.items()
        }
        full = summarize([replayed[mid] for mid in market_ids])
        retained_by_split = {
            name: (
                sum(int(replayed[mid]["eventCount"]) for mid in market_ids if mid in ids)
                / sum(len(events_by_market.get(mid, [])) for mid in market_ids if mid in ids)
            )
            for name, ids in split_ids.items()
        }
        retained = sum(int(replayed[mid]["eventCount"]) for mid in market_ids) / len(events) if events else None
        churn_rows.append({
            "scenario": {
                "name": scenario.name,
                "maximumTakerSideSwitches": scenario.maximum_taker_side_switches,
                "takerCapBaseShares": scenario.taker_cap_base_shares,
                "unitScale": scenario.unit_scale,
                "effectiveMakerUnitShares": 18.0 * scenario.unit_scale,
                "effectiveTakerCapShares": scenario.taker_cap_base_shares * scenario.unit_scale,
            },
            "eventRetention": retained,
            "eventRetentionBySplit": retained_by_split,
            "full": full,
            "splits": split_summary,
            "stablePositive": all(float(split_summary[name]["roi"] or 0) > 0 for name in split_summary),
        })
    churn_eligible = [
        row for row in churn_rows
        if all(float(row["splits"][name]["roi"] or 0) > 0 for name in ("train", "validation"))
        and all(float(row["eventRetentionBySplit"][name] or 0) >= 0.75 for name in ("train", "validation"))
        and float(row["scenario"]["effectiveMakerUnitShares"]) >= 1.0
    ]
    churn_eligible.sort(key=lambda row: (
        max(float(row["splits"][name]["maximumCostPerMarketUsdt"]) for name in ("train", "validation")),
        -min(float(row["eventRetentionBySplit"][name]) for name in ("train", "validation")),
        -min(float(row["splits"][name]["roi"]) for name in ("train", "validation")),
    ))
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "version": "WALLET_SHADOW_V0_CAPITAL_REPLAY_V1",
        "paperOnly": True,
        "liveOrdersAffected": False,
        "method": {
            "events": "replay the original immutable V0 event sequence and official winners",
            "takerCap": "cap each recorded TAKER_INTENT before settlement accounting",
            "unitScale": "scale maker and capped taker shares uniformly; event count and timing remain unchanged",
            "fees": "matches V0 stored gross accounting; no fee, rebate, queue, or slippage assumptions added",
            "selection": "minimum maximum per-market cost among scenarios with positive ROI in chronological train, validation, and holdout",
        },
        "coverage": {"markets": len(market_ids), "events": len(events), "splits": {name: len(ids) for name, ids in split_ids.items()}},
        "recommendedResearchScenario": stable[0] if stable else None,
        "recommendedChurnGuardScenario": churn_eligible[0] if churn_eligible else None,
        "eligibleChurnGuardScenarios": churn_eligible,
        "allChurnGuardScenarios": churn_rows,
        "stablePositiveScenarios": stable,
        "allScenarios": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay V0 with lower taker caps and uniform unit scaling")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = analyze(args.db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "coverage": report["coverage"],
        "recommendedResearchScenario": report["recommendedResearchScenario"],
        "recommendedChurnGuardScenario": report["recommendedChurnGuardScenario"],
    }, ensure_ascii=False, indent=2))
    print(f"wrote {args.output.resolve()}")


if __name__ == "__main__":
    main()
