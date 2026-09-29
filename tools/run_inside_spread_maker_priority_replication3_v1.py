"""External research-only replication3 for the one-shot inside-spread Maker seam.

Selection is frozen in INSIDE_SPREAD_MAKER_PRIORITY_REPLICATION3_PREREGISTERED_20260906.json.
P reproduces consumed Pair-only for parity. A/B/C reuse the already-built V2 / inside-spread
instrumentation. No Active route, no threshold sweep, no auto-scale.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path.cwd() if (Path.cwd() / "tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.run_eth_role_separated_minimal_pair_safety_smoke as base
from tools import run_gpt6_detail_intelligence_v1_external as v1


def load_probe_module():
    staged = Path.cwd() / ".lan_worker_v1/staging/run_inside_spread_maker_priority_one_shot_probe_v1.py"
    if staged.exists():
        spec = importlib.util.spec_from_file_location("inside_spread_probe_frozen", staged)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod
    import tools.run_inside_spread_maker_priority_one_shot_probe_v1 as mod
    return mod


probe = load_probe_module()
P_CELL = "P_PAIR_ONLY_CONSUMED_PARITY"


def eq(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(eq(a[k], b[k]) for k in a)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-9)
    return a == b


def pair_only_parity(row, ref):
    mapping = {
        "submits": "submits",
        "fillEvents": "fills",
        "filledQty": "filledQty",
        "pnlDiagnosticOnly": "pnl",
        "floor": "floor",
        "best": "best",
        "fillSideAlternations": "fillSideAlternations",
        "twoSidedMaterialized": "twoSidedMaterialized",
        "roleSubmits": "roleSubmits",
        "roleFills": "roleFills",
        "roleFillQty": "roleFillQty",
        "reanchors": "reanchors",
    }
    return {a: eq(row.get(a), ref[b]) for a, b in mapping.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--prereg", required=True)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    prereg = json.loads(Path(a.prereg).read_text(encoding="utf-8"))
    mids = [int(x) for x in prereg["selectedMarketIds"]]
    if mids != [1823598, 1823755, 1823897]:
        raise RuntimeError("Replication3 market set changed from preregistration")
    output = Path(a.output).resolve()
    if output.exists():
        ap.error("Do not overwrite result")
    trace_dir = output.parent / (output.stem + "_traces")
    trace_dir.mkdir(parents=True, exist_ok=False)
    rows = []
    comparisons = []
    with tempfile.TemporaryDirectory(prefix="inside_spread_repl3_") as folder:
        root = Path(folder)
        with zipfile.ZipFile(a.bundle) as z:
            cohort = {int(r["marketId"]): r for r in json.loads(z.read("cohort.json"))["rows"]}
            for mid in mids:
                z.extract(f"tapes/{mid}.json.xz", root)
        for mid in mids:
            winner = str(cohort[mid]["winner"]).upper()
            tape = root / "tapes" / f"{mid}.json.xz"
            # P: exact Pair-only source used by consumed100.
            p_sim = base.MinimalPairRoleSim(tape, 4, False)
            try:
                pr = p_sim.run_minimal(winner)
            finally:
                p_sim.close()
            p_row = {"marketId": mid, "cell": P_CELL, **pr}
            pref = prereg["baselineReference"][str(mid)]
            p_row["consumedParity"] = pair_only_parity(p_row, pref)
            if not all(p_row["consumedParity"].values()):
                raise RuntimeError(f"Consumed Pair-only parity failed for {mid}: {p_row['consumedParity']}")
            rows.append(p_row)
            print(json.dumps({"marketId": mid, "cell": P_CELL, "parity": True,
                              "fills": p_row["fillEvents"], "alternations": p_row["fillSideAlternations"]}), flush=True)

            by_cell = {}
            for cell in probe.CELLS:
                tp = trace_dir / f"{mid}_{cell}.jsonl"
                sim = probe.InsideSpreadProbeSim(tape, cell, tp)
                try:
                    r = sim.run_probe()
                finally:
                    sim.close()
                r["pnlDiagnosticOnly"] = r["upQty" if winner == "UP" else "downQty"] - r["buyNotional"]
                row = {"marketId": mid, "cell": cell, "winnerPostHocOnly": winner,
                       "tapeSha256": v1.sha256(tape), "decisionTracePath": str(tp), **r}
                rows.append(row)
                by_cell[cell] = row
                v1.write_json(trace_dir / f"{mid}_{cell}_result.json", row)
                pc = row.get("probeCandidate") or {}
                po = row.get("probeOutcome") or {}
                fills = po.get("fills") or []
                print(json.dumps({
                    "marketId": mid, "cell": cell,
                    "fills": row["fillEvents"], "alternations": row["fillSideAlternations"],
                    "economicRepairQty": row["economicRepairQty"],
                    "probeUsed": row["probeUsed"], "probePrice": pc.get("probePrice"),
                    "probePairSum": pc.get("probePairSum"),
                    "probeTerminal": (po.get("terminal") or {}).get("status"),
                    "probeFillQty": sum(float(x.get("confirmedQty") or 0.0) for x in fills),
                    "probeRepairQty": sum(float(x.get("matchedRepairQty") or 0.0) for x in fills),
                    "cancelSuppressed": row.get("probeCancelSuppressed"),
                }, allow_nan=False), flush=True)
            A, B, C = (by_cell[x] for x in probe.CELLS)
            for left, right in ((probe.CELLS[0], probe.CELLS[1]),
                                (probe.CELLS[0], probe.CELLS[2]),
                                (probe.CELLS[1], probe.CELLS[2])):
                l, r = by_cell[left], by_cell[right]
                comparisons.append({
                    "marketId": mid, "left": left, "right": right,
                    "fillDelta": r["fillEvents"] - l["fillEvents"],
                    "alternationDelta": r["fillSideAlternations"] - l["fillSideAlternations"],
                    "repairQtyDelta": r["economicRepairQty"] - l["economicRepairQty"],
                    "pnlDiagnosticDelta": r["pnlDiagnosticOnly"] - l["pnlDiagnosticOnly"],
                    "floorDelta": r["floor"] - l["floor"],
                })
    out = {
        "version": "INSIDE_SPREAD_MAKER_PRIORITY_REPLICATION3_V1",
        "date": "2026-09-06", "researchOnly": True, "runtimeAuthority": False,
        "selectedMarketIds": mids,
        "selectionRule": prereg["selectionRule"],
        "bundleSha256": v1.sha256(a.bundle),
        "preregSha256": v1.sha256(a.prereg),
        "sourceFiles": {
            "runner": {"path": str(Path(__file__).resolve()), "sha256": v1.sha256(__file__)},
            "probe": {"path": str(Path(probe.__file__).resolve()), "sha256": v1.sha256(probe.__file__)},
            "pairOnly": {"path": str(Path(base.__file__).resolve()), "sha256": v1.sha256(base.__file__)},
        },
        "rows": rows, "comparisons": comparisons,
        "boundary": prereg["interpretationBoundary"],
        "noAutomaticScale": True,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    v1.write_json(output, out)


if __name__ == "__main__":
    main()
