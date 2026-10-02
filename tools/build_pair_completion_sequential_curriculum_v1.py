from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hftbacktest_pair_completion_counterfactual_v3_sequence as cf
from tools.build_pair_completion_tradeoff_curriculum_v3 import pareto_label

D = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
COHORT = D / "pair_completion_canonical_cohort_v1.json"
OUT = D / "pair_completion_sequential_curriculum_v1.jsonl"
REPORT = D / "pair_completion_sequential_curriculum_v1_report.json"
DEFAULT_DELAYS = (0, 1000, 2000, 3000, 5000, 8000)
H = (5, 10, 20)
EPS = 1e-9


def make_row(mid: int, delay_ms: int) -> dict[str, Any]:
    keep = cf.run_recovery(mid, False, candidate_delay_ms=delay_ms)
    repl = cf.run_recovery(mid, True, candidate_delay_ms=delay_ms)
    inter = repl.get("intervention")
    if inter is None:
        return {
            "version": "PAIR_COMPLETION_SEQUENTIAL_CURRICULUM_V1",
            "marketId": int(mid),
            "delayMs": int(delay_ms),
            "hasCandidate": False,
            "paretoLabel": "NO_PERSISTENT_STATE",
            "candidateAtMs": repl.get("candidateAtMs"),
            "candidateAsymmetrySinceMs": repl.get("candidateAsymmetrySinceMs"),
            "candidateAsymmetryAgeMs": repl.get("candidateAsymmetryAgeMs"),
            "features": {},
        }
    dtrack: list[float] = []
    dcost: list[float] = []
    row: dict[str, Any] = {
        "version": "PAIR_COMPLETION_SEQUENTIAL_CURRICULUM_V1",
        "marketId": int(mid),
        "delayMs": int(delay_ms),
        "hasCandidate": True,
        "candidateAtMs": int(inter["atMs"]),
        "candidateAsymmetrySinceMs": repl.get("candidateAsymmetrySinceMs"),
        "candidateAsymmetryAgeMs": repl.get("candidateAsymmetryAgeMs"),
        "candidateSide": keep.get("candidateRecoverySide"),
        "candidateQty": float(keep.get("candidateQty") or 0.0),
        "candidateAsk": keep.get("candidateRecoveryAsk"),
        "actionMode": inter.get("actionMode"),
        "resolvedDuringCancel": bool(inter.get("resolvedDuringCancel")),
        "features": dict(inter.get("features") or {}),
        "childKeepLabels": keep.get("candidateChildKeepLabels"),
    }
    for h in H:
        kt = float(keep[f"targetErrorArea{h}s"])
        rt = float(repl[f"targetErrorArea{h}s"])
        kc = keep.get(f"completionCost{h}s")
        rc = repl.get(f"completionCost{h}s")
        dt = rt - kt
        dc = (float(rc) - float(kc)) if rc is not None and kc is not None else None
        row[f"keepTargetErrorArea{h}s"] = kt
        row[f"replaceTargetErrorArea{h}s"] = rt
        row[f"deltaTargetErrorArea{h}s"] = dt
        row[f"keepCompletionCost{h}s"] = kc
        row[f"replaceCompletionCost{h}s"] = rc
        row[f"deltaCompletionCost{h}s"] = dc
        dtrack.append(dt)
        if dc is not None:
            dcost.append(dc)
    row["paretoLabel"] = pareto_label(dtrack, dcost) if len(dcost) == 3 else "AMBIGUOUS_COST"
    return row


def summarize(all_rows: list[dict[str, Any]], delays: list[int]) -> dict[str, Any]:
    by_delay: dict[str, Any] = {}
    for d in delays:
        z = [r for r in all_rows if int(r.get("delayMs", -1)) == d]
        counts: dict[str, int] = {}
        for r in z:
            lab = str(r.get("paretoLabel"))
            counts[lab] = counts.get(lab, 0) + 1
        by_delay[str(d)] = {
            "rows": len(z),
            "candidateRows": sum(bool(r.get("hasCandidate")) for r in z),
            "labels": counts,
        }
    # Track label evolution market-by-market across the fixed observation grid.
    by_mid: dict[int, list[dict[str, Any]]] = {}
    for r in all_rows:
        by_mid.setdefault(int(r["marketId"]), []).append(r)
    transitions: dict[str, int] = {}
    stable_clarity: dict[str, int] = {"REPLACE_DOMINATES": 0, "KEEP_DOMINATES": 0, "NO_STABLE_CLARITY": 0}
    for mid, rows in by_mid.items():
        rows.sort(key=lambda x: int(x["delayMs"]))
        labs = [str(x.get("paretoLabel")) for x in rows]
        for a, b in zip(labs, labs[1:]):
            k = f"{a}->{b}"
            transitions[k] = transitions.get(k, 0) + 1
        found = None
        # Stable clarity = same dominance at two consecutive observation checkpoints.
        for i in range(len(rows) - 1):
            a = str(rows[i].get("paretoLabel")); b = str(rows[i + 1].get("paretoLabel"))
            if a == b and a in {"REPLACE_DOMINATES", "KEEP_DOMINATES"}:
                found = a
                break
        stable_clarity[found or "NO_STABLE_CLARITY"] += 1
    return {
        "version": "PAIR_COMPLETION_SEQUENTIAL_CURRICULUM_V1_REPORT",
        "researchOnly": True,
        "liveTradingChanges": False,
        "delaysMs": delays,
        "teacher": "At each persistent-asymmetry checkpoint compare KEEP vs route REPLACE under the same frozen R2 intent and canonical HFT physics. Winner/PnL not used for labels.",
        "stableClarityDefinition": "same KEEP_DOMINATES or REPLACE_DOMINATES label at two consecutive fixed observation checkpoints",
        "byDelay": by_delay,
        "transitions": transitions,
        "stableClarity": stable_clarity,
        "rows": len(all_rows),
        "markets": len(by_mid),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-markets", type=int, default=12)
    ap.add_argument("--delays-ms", default=",".join(map(str, DEFAULT_DELAYS)))
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    delays = [int(x) for x in args.delays_ms.split(",") if x.strip()]
    mids = [int(x) for x in json.loads(COHORT.read_text(encoding="utf-8"))["marketIds"]]
    if args.reset and OUT.exists():
        OUT.unlink()
    done: set[tuple[int, int]] = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((int(r["marketId"]), int(r["delayMs"])))
    todo_markets = [m for m in mids if any((m, d) not in done for d in delays)][: max(1, args.limit_markets)]
    new_rows: list[dict[str, Any]] = []
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        for i, mid in enumerate(todo_markets, 1):
            for d in delays:
                if (mid, d) in done:
                    continue
                r = make_row(mid, d)
                new_rows.append(r)
                fh.write(json.dumps(r, ensure_ascii=False, allow_nan=True) + "\n")
                fh.flush()
                print(json.dumps({
                    "marketProgress": i,
                    "marketId": mid,
                    "delayMs": d,
                    "candidate": r.get("hasCandidate"),
                    "label": r.get("paretoLabel"),
                    "candidateAtMs": r.get("candidateAtMs"),
                    "asymmetryAgeMs": r.get("candidateAsymmetryAgeMs"),
                }, ensure_ascii=False), flush=True)
    all_rows = [json.loads(x) for x in OUT.read_text(encoding="utf-8").splitlines() if x.strip()]
    report = summarize(all_rows, delays)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    completed_markets = len({int(r["marketId"]) for r in all_rows if all((int(r["marketId"]), d) in {(int(q["marketId"]), int(q["delayMs"])) for q in all_rows} for d in delays)})
    print(json.dumps({
        "ok": True,
        "processedMarketsThisRun": len(todo_markets),
        "newRows": len(new_rows),
        "rowsTotal": len(all_rows),
        "marketsWithAnyRows": len({int(r["marketId"]) for r in all_rows}),
        "completedMarketsApprox": completed_markets,
        "totalMarkets": len(mids),
        "output": str(OUT),
        "report": str(REPORT),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
