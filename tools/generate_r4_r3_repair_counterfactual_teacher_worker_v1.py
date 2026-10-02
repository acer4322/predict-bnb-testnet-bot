from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("BTC5M_WORKER_ROOT", r"C:\BTC5M-worker")).resolve()
if not (ROOT / "tools").is_dir():
    ROOT = Path.cwd().resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Optional job-local numeric-runtime overlay must be inserted before importing
# numpy/pandas/scipy/sklearn transitively through the replay modules.
if "--runtime-site" in sys.argv:
    i = sys.argv.index("--runtime-site")
    if i + 1 < len(sys.argv):
        runtime_site = Path(sys.argv[i + 1]).resolve()
        if runtime_site.is_dir():
            sys.path.insert(0, str(runtime_site))

from tools import hftbacktest_r2_execution_school_v0 as base
from tools.generate_r4_r3_repair_counterfactual_teacher_v1 import one


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--strategy-db")
    ap.add_argument("--tape-dir")
    ap.add_argument("--book-db")
    ap.add_argument("--runtime-site")
    ap.add_argument("--compute-boundary", default="authorized LAN worker")
    a = ap.parse_args()

    if a.strategy_db:
        base.STRATEGY_DB = Path(a.strategy_db).resolve()
    if a.tape_dir:
        base.tape_v1.ARCHIVE_DIR = Path(a.tape_dir).resolve()
    if a.book_db:
        base.ex.BOOK_DB = Path(a.book_db).resolve()

    ids = [int(x) for x in a.ids.split(",") if x.strip()]
    rows = []
    for mid in ids:
        try:
            r = one(mid)
        except Exception as e:
            r = {"marketId": mid, "error": f"{type(e).__name__}:{e}", "branchClass": "ERROR"}
        rows.append(r)
        print(json.dumps({
            "marketId": mid,
            "class": r.get("branchClass"),
            "delta": r.get("delta"),
            "exact": r.get("exactBranchApplied"),
            "error": r.get("error"),
        }, ensure_ascii=False), flush=True)

    keys = ["PARETO_BENEFICIAL", "PARETO_HARMFUL", "TRADEOFF", "NO_EFFECT", "NO_CANDIDATE", "ERROR"]
    counts = {k: sum(r.get("branchClass") == k for r in rows) for k in keys}
    rep = {
        "version": "R4_R3_REPAIR_COUNTERFACTUAL_TEACHER_V1_DATASET_CHUNK",
        "researchOnly": True,
        "actionAuthority": False,
        "promotionEvidence": False,
        "computeBoundary": a.compute_boundary,
        "strategyDbOverride": str(base.STRATEGY_DB),
        "tapeDirOverride": str(base.tape_v1.ARCHIVE_DIR),
        "bookDbOverride": str(base.ex.BOOK_DB),
        "runtimeSiteOverride": str(Path(a.runtime_site).resolve()) if a.runtime_site else None,
        "marketCount": len(ids),
        "counts": counts,
        "rows": rows,
        "contract": "r4_r3_repair_counterfactual_teacher_v1_contract.json",
        "guards": [
            "current R3 realistic-HFT",
            "exact post-fill/pre-decision branch",
            "strict-past candidate features",
            "no fresh promotion cohort teacher development",
            "authorized LAN worker",
            "job-local consumed-development data bundle",
            "job-local numeric runtime overlay if provided",
        ],
    }
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rep, indent=2), encoding="utf-8")
    print(json.dumps({"marketCount": len(ids), "counts": counts, "out": str(p)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
