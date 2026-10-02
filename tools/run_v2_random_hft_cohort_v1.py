from __future__ import annotations

import argparse
import json
import random
import sys
import traceback
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
SRC = OUT / "theory_execution_handoff_curriculum_v1.jsonl"
STATE = OUT / "r2_candidate_v2_random_hft50_state_v1.json"
REPORT = OUT / "r2_candidate_v2_random_hft50_report_v1.json"
SEED = 20260821
EPS = 1e-9
warnings.filterwarnings("ignore")

from tools import hftbacktest_r2_execution_candidate_v2_frozen as candidate
from tools import r2_execution_graduation_exam_steward_v2_1 as steward


def canonical_markets() -> list[int]:
    import sqlite3
    freeze = json.loads((OUT / "r2_execution_graduation_candidate_freeze_v2.json").read_text(encoding="utf-8"))
    freeze_ms = int(freeze["freezeEpochMs"])
    con = sqlite3.connect(ROOT / "data/strategy_input_snapshot_archive_v1.db")
    try:
        rows = con.execute("""select market_id,min(sampled_at_ms) first_ms from strategy_input_snapshots_v1
            group by market_id having min(sampled_at_ms) < ? order by first_ms,market_id""", (freeze_ms,)).fetchall()
    finally:
        con.close()
    mids = [int(r[0]) for r in rows]
    rng = random.Random(SEED)
    rng.shuffle(mids)
    return mids


def default_state() -> dict:
    return {
        "version": "R2_CANDIDATE_V2_RANDOM_HFT50_STATE_V1",
        "researchOnly": True,
        "countsForFormalGraduation": False,
        "dreamFillUsedForPnl": False,
        "seed": SEED,
        "sourcePool": "Exact Strategy archive pre-V2-freeze opened markets",
        "cursor": 0,
        "accepted": [],
        "rejected": [],
    }


def load_state(reset: bool) -> dict:
    if reset or not STATE.exists():
        return default_state()
    return json.loads(STATE.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")


def prefix_stats(rows: list[dict], n: int) -> dict:
    x = rows[:n]
    pnls = [float(r["pnl"]) for r in x]
    return {
        "markets": len(x),
        "marketIds": [int(r["marketId"]) for r in x],
        "totalRealizedPnl": sum(pnls),
        "wins": sum(p > EPS for p in pnls),
        "losses": sum(p < -EPS for p in pnls),
        "winRate": (sum(p > EPS for p in pnls) / len(pnls)) if pnls else None,
        "meanFinalAbsTrackingError": (sum(float(r["finalAbsTrackingError"]) for r in x) / len(x)) if x else None,
        "meanCombinedFinalAbsNet": (sum(float(r["combinedFinalAbsNet"]) for r in x) / len(x)) if x else None,
        "paperMakerOrders": sum(int(r["paperMakerOrders"]) for r in x),
        "onlineMakerIntents": sum(int(r["onlineMakerIntents"]) for r in x),
        "intentCountExactAll": all(bool(r["intentCountExact"]) for r in x),
        "replaceActions": sum(int(r["replaceActions"]) for r in x),
        "zeroFillTakerChildren": sum(int(r["zeroFillTakerChildren"]) for r in x),
        "cancelPendingAtDataEnd": sum(int(r["cancelPendingAtDataEnd"]) for r in x),
        "dreamFillUsedForPnl": False,
    }


def save_report(state: dict) -> None:
    rows = state["accepted"]
    report = {
        "version": "R2_CANDIDATE_V2_RANDOM_HFT50_REPORT_V1",
        "researchOnly": True,
        "countsForFormalGraduation": False,
        "dreamFillUsedForPnl": False,
        "candidateRunner": "tools/hftbacktest_r2_execution_candidate_v2_frozen.py",
        "seed": SEED,
        "sourcePool": "Exact Strategy archive pre-V2-freeze opened markets",
        "sampling": "single deterministic shuffled sequence; 15 is prefix of 30, 30 is prefix of 50",
        "acceptedMarkets": len(rows),
        "rejectedMarkets": len(state["rejected"]),
        "prefix15": prefix_stats(rows, min(15, len(rows))),
        "prefix30": prefix_stats(rows, min(30, len(rows))),
        "prefix50": prefix_stats(rows, min(50, len(rows))),
        "accepted": rows,
        "rejected": state["rejected"],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--attempts", type=int, default=5)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    pool = canonical_markets()
    state = load_state(args.reset)
    attempts = 0
    while attempts < args.attempts and len(state["accepted"]) < 50 and int(state["cursor"]) < len(pool):
        mid = int(pool[int(state["cursor"])])
        state["cursor"] = int(state["cursor"]) + 1
        attempts += 1
        try:
            qc = steward.quality_check_without_answer(mid)
            if not bool(qc.get("eligible")):
                raise RuntimeError(f"formal-grade data quality failed: {qc.get('stage')} / {(qc.get('input') or {}).get('reasons')}")
            r = candidate.run_market(mid)
            if r.get("dreamFillUsedForPnl") is not False:
                raise RuntimeError("dreamFillUsedForPnl not false")
            if int(r["paperReference"]["makerOrders"]) != int(r["strategyRollout"]["makerIntents"]):
                raise RuntimeError(f"strategy trajectory mismatch: paper={r['paperReference']['makerOrders']} online={r['strategyRollout']['makerIntents']}")
            pnl = r["actualExecution"]["realizedPnl"]
            if pnl is None:
                raise RuntimeError("settlement unavailable")
            row = {
                "marketId": mid,
                "pnl": float(pnl),
                "win": bool(float(pnl) > EPS),
                "paperMakerOrders": int(r["paperReference"]["makerOrders"]),
                "onlineMakerIntents": int(r["strategyRollout"]["makerIntents"]),
                "intentCountExact": int(r["paperReference"]["makerOrders"]) == int(r["strategyRollout"]["makerIntents"]),
                "makerRealizationRate": float(r["actualExecution"]["makerRealizationRate"]),
                "finalAbsTrackingError": float(r["actualExecution"]["finalAbsTrackingError"]),
                "combinedFinalAbsNet": float(r["actualExecution"]["combinedFinalAbsNet"]),
                "replaceActions": int(r["lifecycle"]["actionCounts"].get("REPLACE_ROUTE", 0)),
                "zeroFillTakerChildren": int(r["lifecycle"]["takerChildStateCounts"].get("TERMINAL_ZERO_FILL", 0)),
                "cancelPendingAtDataEnd": int(r["lifecycle"].get("cancelPendingAtDataEnd", 0)),
                "dreamFillUsedForPnl": False,
            }
            state["accepted"].append(row)
            print(json.dumps({"accepted": len(state["accepted"]), **row}, ensure_ascii=False), flush=True)
        except Exception as exc:
            rej = {"marketId": mid, "error": repr(exc)}
            state["rejected"].append(rej)
            print(json.dumps({"rejected": len(state["rejected"]), **rej}, ensure_ascii=False), flush=True)
        save_state(state)
        save_report(state)
    print(json.dumps({
        "ok": True,
        "cursor": state["cursor"],
        "accepted": len(state["accepted"]),
        "rejected": len(state["rejected"]),
        "prefix15": prefix_stats(state["accepted"], min(15, len(state["accepted"]))),
        "prefix30": prefix_stats(state["accepted"], min(30, len(state["accepted"]))),
        "prefix50": prefix_stats(state["accepted"], min(50, len(state["accepted"]))),
        "report": str(REPORT),
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
