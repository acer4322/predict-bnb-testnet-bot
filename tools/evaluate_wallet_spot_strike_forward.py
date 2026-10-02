from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHADOW_DB = ROOT / "data" / "predict_wallet_shadow.db"
DEFAULT_SIMULATION_DB = ROOT / "data" / "simulation.db"
DEFAULT_OUTPUT = ROOT / "artifacts" / "wallet_profit_strategy" / "spot_strike_forward_report.json"
DEFAULT_COHORT = "SPOT_STRIKE_10S_V2"
TARGET_WALLET = "0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03"


def effective_taker_cost(price: float, fee_rate_bps: int = 200) -> float:
    return price + min(price, 1.0 - price) * fee_rate_bps / 10_000.0


def stressed_performance(results: list[dict[str, Any]], adverse_ticks: int) -> dict[str, Any]:
    tick_size = 0.01
    stake = sum(float(row["stake_usdt"]) for row in results)
    pnl = 0.0
    for row in results:
        entry = min(0.99, float(row["observed_ask"]) + adverse_ticks * tick_size)
        shares = float(row["stake_usdt"]) / effective_taker_cost(entry)
        pnl += shares - float(row["stake_usdt"]) if row["status"] == "WIN" else -float(row["stake_usdt"])
    return {
        "adverseTicks": adverse_ticks,
        "assumedTickSize": tick_size,
        "netStakeUsdt": stake,
        "netPnlUsdt": pnl,
        "netRoi": pnl / stake if stake else None,
    }


def fixed_stake_counterfactual(rows: list[dict[str, Any]], stake_usdt: float = 1.0) -> dict[str, Any]:
    settled = [
        row for row in rows
        if row.get("side") in {"UP", "DOWN"}
        and row.get("officialWinner") in {"UP", "DOWN"}
        and row.get("ask") is not None
        and 0 < float(row["ask"]) < 1
    ]
    pnl = 0.0
    wins = 0
    for row in settled:
        won = row["side"] == row["officialWinner"]
        wins += int(won)
        shares = stake_usdt / effective_taker_cost(float(row["ask"]))
        pnl += shares - stake_usdt if won else -stake_usdt
    stake = stake_usdt * len(settled)
    return {
        "settled": len(settled),
        "wins": wins,
        "winRate": wins / len(settled) if settled else None,
        "netStakeUsdt": stake,
        "netPnlUsdt": pnl,
        "netRoi": pnl / stake if stake else None,
        "minimumAsk": min((float(row["ask"]) for row in settled), default=None),
        "maximumAsk": max((float(row["ask"]) for row in settled), default=None),
    }


def _connect_ro(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3.0)
    db.row_factory = sqlite3.Row
    return db


def wilson_interval(wins: int, total: int, z: float = 1.959963984540054) -> list[float] | None:
    if total <= 0:
        return None
    p = wins / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def _residual(rows: list[dict[str, Any]], cutoff_ms: int | None = None) -> dict[str, Any]:
    selected = [row for row in rows if cutoff_ms is None or int(row["event_ms"]) <= cutoff_ms]
    up = sum(float(row["shares"] or 0.0) for row in selected if row["side"] == "UP")
    down = sum(float(row["shares"] or 0.0) for row in selected if row["side"] == "DOWN")
    side = "UP" if up > down else "DOWN" if down > up else None
    up_cost = sum(
        float(row["shares"] or 0.0) * float(row["price"] or 0.0)
        for row in selected if row["side"] == "UP"
    )
    down_cost = sum(
        float(row["shares"] or 0.0) * float(row["price"] or 0.0)
        for row in selected if row["side"] == "DOWN"
    )
    capital_side = "UP" if up_cost > down_cost else "DOWN" if down_cost > up_cost else None
    return {
        "side": side,
        "upShares": up,
        "downShares": down,
        "capitalSide": capital_side,
        "upCostUsdt": up_cost,
        "downCostUsdt": down_cost,
        "legs": len(selected),
    }


def evaluate(
    shadow_db_path: Path,
    simulation_db_path: Path,
    cohort: str = DEFAULT_COHORT,
    target_wallet: str = TARGET_WALLET,
) -> dict[str, Any]:
    shadow = _connect_ro(shadow_db_path)
    simulation = _connect_ro(simulation_db_path)
    try:
        meta_row = shadow.execute(
            "SELECT * FROM wallet_spot_strike_forward_meta WHERE cohort=?", (cohort,)
        ).fetchone()
        decisions = [dict(row) for row in shadow.execute(
            "SELECT * FROM wallet_spot_strike_forward_decisions WHERE cohort=? ORDER BY market_bucket",
            (cohort,),
        )]
        events = {int(row["market_id"]): dict(row) for row in shadow.execute(
            "SELECT * FROM wallet_spot_strike_forward_events WHERE cohort=?", (cohort,)
        )}
        results = {int(row["market_id"]): dict(row) for row in shadow.execute(
            "SELECT * FROM wallet_spot_strike_forward_results WHERE cohort=?", (cohort,)
        )}
        target_rows: dict[int, list[dict[str, Any]]] = {}
        for row in shadow.execute(
            """SELECT market_id,event_ms,side,shares,price
                 FROM wallet_shadow_target_events
                WHERE wallet=? AND role='TAKER' AND quote_type='BID'
                  AND side IN ('UP','DOWN') AND shares IS NOT NULL
                ORDER BY market_id,event_ms""",
            (target_wallet,),
        ):
            target_rows.setdefault(int(row["market_id"]), []).append(dict(row))

        market_details: list[dict[str, Any]] = []
        direction_wins = direction_total = 0
        target_at_matches = target_at_total = 0
        target_final_matches = target_final_total = 0
        target_capital_at_matches = target_capital_at_total = 0
        target_capital_final_matches = target_capital_final_total = 0
        for decision in decisions:
            bucket = int(decision["market_bucket"])
            settlement = simulation.execute(
                """SELECT s.official_winner,s.status,s.start_price,s.official_end_price
                     FROM strategy_m_market_sequence sequence
                     LEFT JOIN market_settlements s ON s.market_id=sequence.market_id
                    WHERE sequence.start_ms=? ORDER BY sequence.sequence_no DESC LIMIT 1""",
                (bucket * 1000,),
            ).fetchone()
            official_winner = settlement["official_winner"] if settlement else None
            if official_winner in {"UP", "DOWN"} and decision["side"] in {"UP", "DOWN"}:
                direction_total += 1
                direction_wins += int(decision["side"] == official_winner)
            target = target_rows.get(int(decision["market_id"]), [])
            at_decision = _residual(target, int(decision["decision_at_ms"]))
            final = _residual(target)
            decision_side = decision["side"]
            has_decision_side = decision_side in {"UP", "DOWN"}
            if has_decision_side and at_decision["side"] in {"UP", "DOWN"}:
                target_at_total += 1
                target_at_matches += int(decision_side == at_decision["side"])
            if has_decision_side and final["side"] in {"UP", "DOWN"}:
                target_final_total += 1
                target_final_matches += int(decision_side == final["side"])
            if has_decision_side and at_decision["capitalSide"] in {"UP", "DOWN"}:
                target_capital_at_total += 1
                target_capital_at_matches += int(decision_side == at_decision["capitalSide"])
            if has_decision_side and final["capitalSide"] in {"UP", "DOWN"}:
                target_capital_final_total += 1
                target_capital_final_matches += int(decision_side == final["capitalSide"])
            post_decision = [
                row for row in target if int(row["event_ms"]) > int(decision["decision_at_ms"])
            ]
            market_details.append({
                "marketId": int(decision["market_id"]),
                "marketBucket": bucket,
                "decision": decision["decision"],
                "reason": decision["reason"],
                "secondsLeft": decision["seconds_left"],
                "side": decision["side"],
                "ask": decision["observed_ask"],
                "displacementBps": decision["displacement_bps"],
                "officialWinner": official_winner,
                "directionCorrect": (
                    decision_side == official_winner
                    if has_decision_side and official_winner in {"UP", "DOWN"}
                    else None
                ),
                "targetAtDecision": at_decision,
                "targetFinal": final,
                "targetPostDecisionLegs": sum(
                    int(row["event_ms"]) > int(decision["decision_at_ms"]) for row in target
                ),
                "targetPostDecisionCostUsdt": {
                    side: sum(
                        float(row["shares"] or 0.0) * float(row["price"] or 0.0)
                        for row in post_decision if row["side"] == side
                    )
                    for side in ("UP", "DOWN")
                },
                "result": results.get(int(decision["market_id"])),
            })

        ordered_results = [results[key] for key in sorted(results, key=lambda key: results[key]["resolved_at_ms"])]
        high_ask_counterfactual = fixed_stake_counterfactual([
            row for row in market_details if row["reason"] == "ASK_ABOVE_MAX"
        ])
        wins = sum(row["status"] == "WIN" for row in ordered_results)
        stake = sum(float(row["stake_usdt"]) for row in ordered_results)
        pnl = sum(float(row["net_pnl_usdt"]) for row in ordered_results)
        equity = peak = max_drawdown = 0.0
        streak = longest_streak = 0
        for row in ordered_results:
            equity += float(row["net_pnl_usdt"])
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
            if row["status"] == "LOSS":
                streak += 1
                longest_streak = max(longest_streak, streak)
            else:
                streak = 0
        trade_decisions = sum(row["decision"] == "TRADE" for row in decisions)
        integrity = {
            "tradeDecisionWithoutEvent": sum(
                row["decision"] == "TRADE" and int(row["market_id"]) not in events for row in decisions
            ),
            "eventWithoutDecision": sum(
                market_id not in {int(row["market_id"]) for row in decisions} for market_id in events
            ),
            "skipWithEvent": sum(
                row["decision"] == "SKIP" and int(row["market_id"]) in events for row in decisions
            ),
            "resultWithoutEvent": sum(market_id not in events for market_id in results),
            "tradeDecisionEventCountMatch": trade_decisions == len(events),
        }
        settled = len(ordered_results)
        return {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "cohort": cohort,
            "paperOnly": True,
            "forwardOnly": True,
            "liveOrdersAffected": False,
            "deploymentBoundaryMs": int(meta_row["deployed_at_ms"]) if meta_row else None,
            "policy": json.loads(meta_row["policy_json"]) if meta_row else None,
            "coverage": {
                "decisions": len(decisions),
                "trades": trade_decisions,
                "skips": len(decisions) - trade_decisions,
                "tradeRate": trade_decisions / len(decisions) if decisions else None,
                "reasons": {
                    reason: sum(row["reason"] == reason for row in decisions)
                    for reason in sorted({str(row["reason"]) for row in decisions})
                },
            },
            "directionSignal": {
                "settled": direction_total,
                "correct": direction_wins,
                "winRate": direction_wins / direction_total if direction_total else None,
                "wilson95": wilson_interval(direction_wins, direction_total),
            },
            "targetSimilarity": {
                "atDecisionComparable": target_at_total,
                "atDecisionMatches": target_at_matches,
                "atDecisionMatchRate": target_at_matches / target_at_total if target_at_total else None,
                "finalComparable": target_final_total,
                "finalMatches": target_final_matches,
                "finalMatchRate": target_final_matches / target_final_total if target_final_total else None,
                "capitalAtDecisionComparable": target_capital_at_total,
                "capitalAtDecisionMatches": target_capital_at_matches,
                "capitalAtDecisionMatchRate": (
                    target_capital_at_matches / target_capital_at_total if target_capital_at_total else None
                ),
                "capitalFinalComparable": target_capital_final_total,
                "capitalFinalMatches": target_capital_final_matches,
                "capitalFinalMatchRate": (
                    target_capital_final_matches / target_capital_final_total if target_capital_final_total else None
                ),
            },
            "performance": {
                "settledTrades": settled,
                "wins": wins,
                "losses": settled - wins,
                "winRate": wins / settled if settled else None,
                "winRateWilson95": wilson_interval(wins, settled),
                "netStakeUsdt": stake,
                "netPnlUsdt": pnl,
                "netRoi": pnl / stake if stake else None,
                "maxDrawdownUsdt": max_drawdown,
                "longestLossStreak": longest_streak,
                "executionStress": {
                    "observedAsk": stressed_performance(ordered_results, 0),
                    "oneTickAdverse": stressed_performance(ordered_results, 1),
                    "twoTicksAdverse": stressed_performance(ordered_results, 2),
                    "note": "fixed 1 USDT stake repriced at adverse ask with the same 200 bps taker fee; not a fill guarantee",
                },
            },
            "skipEconomics": {
                "highAskCounterfactual": high_ask_counterfactual,
                "actualPlusHighAskCounterfactual": {
                    "netStakeUsdt": stake + high_ask_counterfactual["netStakeUsdt"],
                    "netPnlUsdt": pnl + high_ask_counterfactual["netPnlUsdt"],
                    "netRoi": (
                        (pnl + high_ask_counterfactual["netPnlUsdt"])
                        / (stake + high_ask_counterfactual["netStakeUsdt"])
                        if stake + high_ask_counterfactual["netStakeUsdt"] > 0
                        else None
                    ),
                },
                "note": (
                    "outcome-informed diagnostic only: assumes every ASK_ABOVE_MAX decision filled "
                    "1 USDT at the observed ask with 200 bps fee; never counts as forward execution"
                ),
            },
            "completion": {
                "minimumSettledTrades": 30,
                "winRateMustExceed": 0.60,
                "netRoiMustExceed": 0.10,
                "eligible": bool(
                    settled >= 30 and wins / settled > 0.60 and stake > 0 and pnl / stake > 0.10
                ),
            },
            "integrity": integrity,
            "markets": market_details,
        }
    finally:
        shadow.close()
        simulation.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the forward-only Spot/Strike cohort")
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--simulation-db", type=Path, default=DEFAULT_SIMULATION_DB)
    parser.add_argument("--cohort", default=DEFAULT_COHORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = evaluate(args.shadow_db, args.simulation_db, args.cohort)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "coverage": report["coverage"],
        "directionSignal": report["directionSignal"],
        "targetSimilarity": report["targetSimilarity"],
        "performance": report["performance"],
        "completion": report["completion"],
        "integrity": report["integrity"],
    }, indent=2))


if __name__ == "__main__":
    main()
