from __future__ import annotations

import bisect
import json
import os
import sqlite3
import zlib
from collections import defaultdict
from pathlib import Path

from predict_bot.cap100_venue_execution_simulator_v1 import (
    Cap100VenueExecutionSimulatorV1,
    QueueMode,
    VenueBookEvent,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "research" / "execution_realism_audit_v1.json"
SIM_DB = ROOT / "data" / "simulation.db"
BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"


def ro(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("pragma query_only=on")
    return c


def cap100_blind() -> dict:
    submit = 1787223603006
    end = 1787223610000
    c = ro(BOOK_DB)
    rows = c.execute(
        """select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z
             from maker_book_inference_updates
            where market_id=1513668 and source_timestamp_ms<=?
            order by source_timestamp_ms,id""",
        (end,),
    ).fetchall()
    state = {"bids": {}, "asks": {}}
    events: list[VenueBookEvent] = []
    initial_depth = 0.0
    for r in rows:
        t = int(r["source_timestamp_ms"])
        if int(r["is_checkpoint"]):
            state = {
                "bids": {float(k): float(v) for k, v in (json.loads(zlib.decompress(r["native_bids_z"]).decode()) if r["native_bids_z"] else {}).items()},
                "asks": {float(k): float(v) for k, v in (json.loads(zlib.decompress(r["native_asks_z"]).decode()) if r["native_asks_z"] else {}).items()},
            }
        else:
            ch = json.loads(zlib.decompress(r["changes_z"]).decode()) if r["changes_z"] else {}
            for side in ("bids", "asks"):
                for z in ch.get(side, []) or []:
                    p = float(z["price"])
                    before = float(z.get("before", state[side].get(p, 0.0)))
                    after = float(z["after"])
                    if t > submit and side == "bids" and abs(p - 0.42) < 1e-9 and before > after:
                        events.append(VenueBookEvent(t, "bids", 0.42, before, after, None))
                    if after <= 1e-12:
                        state[side].pop(p, None)
                    else:
                        state[side][p] = after
        if t <= submit:
            initial_depth = float(state["bids"].get(0.42, 0.0))
    out = {}
    for mode in (QueueMode.STRICT_RISK_AVERSE, QueueMode.L2_DEPLETION_APPROX):
        sim = Cap100VenueExecutionSimulatorV1(queue_mode=mode)
        order = sim.submit_maker(
            order_id="m1", market_id=1513668, side="UP", price=0.42, shares=18,
            submit_ms=submit, initial_visible_depth=initial_depth,
            native_side="bids", native_price=0.42,
        )
        fills = sim.replay(events)
        out[mode.value] = {
            "fills": [
                {
                    "afterSubmitMs": int(f.event_ms - submit),
                    "deltaShares": f.shares,
                    "cumulativeShares": f.cumulative_shares,
                }
                for f in fills
            ],
            "finalState": order.state.value,
            "filledShares": order.filled_shares,
            "queueAhead": order.queue_ahead,
        }
    c.close()
    return {
        "marketId": 1513668,
        "order": {"side": "UP", "shares": 18.0, "price": 0.42, "submitMs": submit},
        "initialVisibleDepth": initial_depth,
        "l2NegativeDepletionEvents": len(events),
        "blindModels": out,
        "venueTruthHeldOutForModel": {
            "firstPartialAfterSubmitMs": 3417,
            "firstPartialCumulativeShares": 11.54,
            "secondObservedAfterSubmitMs": 5653,
            "secondObservedCumulativeShares": 17.82,
        },
    }


def standard_paper_24h() -> dict:
    c = ro(SIM_DB)
    trades = [dict(r) for r in c.execute(
        """select id,strategy,market_id,side,entry_price,stake,shares,opened_at
             from trades where opened_at>=datetime('now','-1 day') order by id"""
    )]
    mids = sorted({int(r["market_id"]) for r in trades})
    obs: dict[int, list[dict]] = {m: [] for m in mids}
    for i in range(0, len(mids), 200):
        ids = mids[i:i+200]
        if not ids:
            continue
        ph = ",".join("?" for _ in ids)
        for r in c.execute(
            f"""select market_id,timestamp,up_ask,up_ask_size,down_ask,down_ask_size
                  from observations where market_id in ({ph}) order by market_id,timestamp""",
            ids,
        ):
            obs[int(r["market_id"])].append(dict(r))
    keys = {m: [x["timestamp"] for x in xs] for m, xs in obs.items()}
    rows = []
    for r in trades:
        xs = obs.get(int(r["market_id"])) or []
        ts = keys.get(int(r["market_id"])) or []
        if not xs:
            continue
        j = bisect.bisect_right(ts, r["opened_at"]) - 1
        if j < 0:
            j = 0
        x = xs[j]
        side = str(r["side"]).upper()
        ask = x["up_ask"] if side == "UP" else x["down_ask"]
        size = x["up_ask_size"] if side == "UP" else x["down_ask_size"]
        if ask is None or size is None:
            continue
        rows.append((str(r["strategy"]), float(r["shares"] or 0), float(size), float(r["entry_price"]), float(ask)))
    by = defaultdict(lambda: [0, 0])
    diffs = []
    insufficient = 0
    for strategy, req, size, entry, ask in rows:
        bad = req > size + 1e-9
        insufficient += int(bad)
        by[strategy][0] += 1
        by[strategy][1] += int(bad)
        diffs.append(abs(entry - ask))
    diffs.sort()
    ranked = []
    for strategy, (n, bad) in by.items():
        ranked.append({"strategy": strategy, "trades": n, "topLevelInsufficient": bad, "rate": bad / n if n else 0.0})
    ranked.sort(key=lambda x: (x["rate"], x["trades"]), reverse=True)
    c.close()
    return {
        "period": "latest 24h",
        "trades": len(trades),
        "matchedToPriorBook": len(rows),
        "strategies": len(by),
        "paperOpenTradeSemantics": "shares=stake/entry then immediately OPEN; no ask-size/latency/partial/reject gate in server.open_trade",
        "topLevelAskCapacityInsufficient": insufficient,
        "topLevelAskCapacityInsufficientRate": insufficient / len(rows) if rows else None,
        "entryVsPriorAskExactRate": sum(d < 1e-9 for d in diffs) / len(diffs) if diffs else None,
        "entryVsPriorAskAbsDiffMedian": diffs[len(diffs)//2] if diffs else None,
        "entryVsPriorAskAbsDiffP95": diffs[int(0.95*(len(diffs)-1))] if diffs else None,
        "worstStrategiesN10": [x for x in ranked if x["trades"] >= 10 and x["topLevelInsufficient"] > 0][:30],
    }


def main() -> int:
    report = {
        "reportVersion": "EXECUTION_REALISM_AUDIT_V1",
        "researchOnly": True,
        "liveTradingChanges": False,
        "cap100BlindValidation": cap100_blind(),
        "standardPaperTrade24h": standard_paper_24h(),
        "staticExecutionClasses": {
            "UNIFIED_MAKER_CONTROLLERS": {
                "severity": "CRITICAL",
                "current": "QUEUECLEAR_PASS full-order fill proxy; portfolio state changes from paper fill lifecycle",
                "needed": "queue/time-to-fill/partial-fill closed-loop replay",
            },
            "STANDARD_SIMULATION_TRADES": {
                "severity": "HIGH",
                "current": "immediate full taker fill at entry price",
                "needed": "latency + top-of-book/depth walk + reject/FOK model",
            },
            "MX_FAMILY": {
                "severity": "MEDIUM",
                "current": "partial-size aware using min(requested remaining, ask/bid size), with explicit order/fill lifecycle",
                "needed": "latency/persistence/re-use-of-visible-liquidity realism",
            },
            "PAIR_ARB_FAMILY": {
                "severity": "MEDIUM",
                "current": "multi-level depth VWAP + partial-fill model already present",
                "needed": "cross-leg timing/atomicity/latency realism",
            },
            "POLY_GAP_PAPER": {
                "severity": "HIGH",
                "current": "selected ask then shares=PAPER_STAKE_USDT/entry; no depth/latency/partial gate",
                "needed": "Polymarket depth/slippage/latency execution replay",
            },
        },
        "retrainingDecisionRule": {
            "doNotRetrainYet": True,
            "reason": "first isolate execution-layer drift by rerunning frozen policies under realistic execution; retrain only if model features/labels depend materially on dream-fill own-state trajectories",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "out": str(OUT),
        "cap100StrictFills": len(report["cap100BlindValidation"]["blindModels"]["STRICT_RISK_AVERSE"]["fills"]),
        "cap100L2Fills": report["cap100BlindValidation"]["blindModels"]["L2_DEPLETION_APPROX"]["fills"],
        "paper24hTrades": report["standardPaperTrade24h"]["trades"],
        "paper24hStrategies": report["standardPaperTrade24h"]["strategies"],
        "capacityBadRate": report["standardPaperTrade24h"]["topLevelAskCapacityInsufficientRate"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
