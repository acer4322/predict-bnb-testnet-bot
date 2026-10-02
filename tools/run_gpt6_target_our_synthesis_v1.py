"""External Lab entry point. One market/cell per job; merge never runs HFT."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DOCS = ROOT / "data/research/r4_v0"
PACK = DOCS / "gpt6_target_our_system_synthesis_challenge_v1_20260906.zip"
PREREG = DOCS / "GPT6_TARGET_OUR_SYNTHESIS_V1_PREREGISTERED.json"
CELLS = ("R247_CONTROL", "R264_CONTROL", "GPT6_SYNTHESIS_CANDIDATE")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def context():
    pre = json.loads(PREREG.read_text(encoding="utf-8"))
    with zipfile.ZipFile(PACK) as z:
        for name, expected in json.loads(z.read("SHA256_MANIFEST.json")).items():
            if hashlib.sha256(z.read(name)).hexdigest() != expected:
                raise ValueError(f"Evidence integrity failure: {name}")
        benchmark = json.loads(z.read("50_OUR_FULL24_R247_R257_R264_COMPARISON.json"))
    pins = dict(pre["frozenSourceHashes"])
    for name, expected in pins.items():
        if sha(ROOT / name) != expected:
            raise ValueError(f"Frozen source drift: {name}")
        staged = Path.cwd() / ".lan_worker_v1/staging" / Path(name).name
        if staged.exists() and sha(staged) != expected:
            raise ValueError(f"Staged source override drift: {staged.name}")
    for name in ("tools/gpt6_target_our_synthesis_v1.py", "tools/run_gpt6_target_our_synthesis_v1.py"):
        pins[name] = sha(ROOT / name)
    pins["preregistration"] = sha(PREREG)
    pins["evidencePack"] = sha(PACK)
    return pre, benchmark, pins


def write_new(path, obj):
    raw = json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as out:
        out.write(raw)


def finite(v):
    return type(v) in (int, float) and math.isfinite(v)


def validate(row, pre):
    if type(row.get("marketId")) is not int or row["marketId"] not in pre["marketIds"] or row.get("cell") not in CELLS:
        raise ValueError("Unknown/noninteger market/cell")
    for key in ("pnlDiagnosticOnly", "floor", "best", "filledQty", "unauthorizedOverflowQty", "repairQuotaExcessMax"):
        if not finite(row.get(key)):
            raise ValueError(f"Missing/null/nonfinite {key}")
    for key in ("fillEvents", "submits", "maxSimultaneousDistinctPrices"):
        if type(row.get(key)) is not int or row[key] < 0:
            raise ValueError(f"Invalid count {key}")
    for key in ("roleSubmits", "roleFills"):
        if not isinstance(row.get(key), dict) or any(not finite(v) or v < 0 for v in row[key].values()):
            raise ValueError(f"Invalid role telemetry {key}")
    if row["cell"] == "GPT6_SYNTHESIS_CANDIDATE":
        inv = row.get("candidateInvariantViolations")
        if not isinstance(inv, dict) or set(inv) != set(pre["candidateInvariantFields"]):
            raise ValueError("Candidate invariants missing/unexpected")
        if any(not finite(v) or v < 0 for v in inv.values()):
            raise ValueError("Invalid candidate invariants")
        service = row.get("r247ServiceChecks")
        if not isinstance(service, dict) or set(service) != set(pre["inheritedServiceInvariantFields"]):
            raise ValueError("Inherited service invariants missing/unexpected")
        if any(not finite(v) or v < 0 for v in service.values()):
            raise ValueError("Invalid inherited service invariants")
        actual_pass = (all(v <= 1e-9 for v in inv.values()) and all(v <= 1e-9 for v in service.values())
                       and row["unauthorizedOverflowQty"] <= 1e-9 and row["repairQuotaExcessMax"] <= 1e-9)
        if row.get("candidateCorrectnessPass") is not actual_pass:
            raise ValueError("Correctness boolean disagrees with measured fields")
        for key in ("synthesisRiskLedger", "synthesisEvents", "slotHistory", "splitEvents"):
            if not isinstance(row.get(key), list):
                raise ValueError(f"Missing full trace: {key}")


def run_one(args):
    pre, benchmark, pins = context()
    if args.market_id not in pre["marketIds"] or args.cell not in CELLS or not args.bundle:
        raise ValueError("One consumed market/cell and exact bundle required")
    if Path(args.output).exists():
        raise FileExistsError(args.output)
    if sha(args.bundle) != pre["consumedBundleSha256"]:
        raise ValueError("Not the exact consumed24 bundle; fresh execution not allowed here")
    pins["tapeBundle"] = sha(args.bundle)
    with tempfile.TemporaryDirectory(prefix="gpt6_synthesis_cell_") as task_dir:
        with zipfile.ZipFile(args.bundle) as z:
            cohort = json.loads(z.read("cohort.json"))["rows"]
            selected = [r for r in cohort if int(r["marketId"]) == args.market_id]
            if len(selected) != 1:
                raise ValueError("Missing/duplicate cohort row")
            tape = Path(task_dir) / f"{args.market_id}.json.xz"
            with tape.open("xb") as out:
                out.write(z.read(f"tapes/{args.market_id}.json.xz"))
        # Lazy imports: merge and CLI parsing cannot initialize market execution.
        from tools import run_eth_ms4_r2_47_bounded_core_service_favorable_recycle as r247
        from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
        from tools.gpt6_target_our_synthesis_v1 import TargetOurSynthesisSim
        cls, method_name = {
            "R247_CONTROL": (r247.BoundedCoreServiceFavorableRecycleSim, "run_r247"),
            "R264_CONTROL": (r264.ExecutionRepresentedPreRepairReexpandSim, "run_r264"),
            "GPT6_SYNTHESIS_CANDIDATE": (TargetOurSynthesisSim, "run_synthesis"),
        }[args.cell]
        print(json.dumps({"phase": "START", "marketId": args.market_id, "cell": args.cell}), flush=True)
        sim = cls(tape, 1, 4)
        try:
            result = getattr(sim, method_name)(selected[0]["winner"])
        finally:
            sim.close()
    row = {**result, "marketId": args.market_id, "cell": args.cell}
    validate(row, pre)
    write_new(args.output, {"version": "GPT6_SYNTHESIS_V1_CELL", "researchOnly": True,
                           "provenance": pins, "rows": [row]})
    print(json.dumps({"phase": "COMPLETE", "output": args.output}), flush=True)


def merge(args):
    pre, benchmark, pins = context()
    expected = pre["marketIds"] if not args.expected_market_ids else [int(x) for x in args.expected_market_ids.split(",")]
    if len(expected) != len(set(expected)) or not expected or not set(expected) <= set(pre["marketIds"]):
        raise ValueError("Bad requested consumed subset")
    rows, seen, provenance = [], set(), None
    for path in sorted(Path(args.merge_dir).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != "GPT6_SYNTHESIS_V1_CELL" or not isinstance(data.get("rows"), list) or len(data["rows"]) != 1:
            raise ValueError(f"Not a single-cell checkpoint: {path}")
        if provenance is None:
            provenance = data["provenance"]
        if (not isinstance(provenance, dict) or data["provenance"] != provenance
                or any(provenance.get(k) != v for k, v in pins.items())
                or provenance.get("tapeBundle") != pre["consumedBundleSha256"]):
            raise ValueError("Mixed/stale source provenance")
        row = data["rows"][0]
        validate(row, pre)
        key = (row["marketId"], row["cell"])
        if key in seen:
            raise ValueError(f"Duplicate result: {key}")
        seen.add(key)
        rows.append(row)
    if seen != {(m, c) for m in expected for c in CELLS}:
        raise ValueError("Missing or unexpected market/cell rows")
    canonical = {r["marketId"]: r for r in benchmark["rows"]}
    parity = []
    for row in rows:
        if row["cell"] == "GPT6_SYNTHESIS_CANDIDATE":
            continue
        base = canonical[row["marketId"]]["R247" if row["cell"] == "R247_CONTROL" else "R264"]
        for key in ("pnlDiagnosticOnly", "floor", "best", "filledQty", "submits", "fillEvents"):
            if abs(row[key] - base[key]) > 1e-8:
                parity.append({"marketId": row["marketId"], "cell": row["cell"], "field": key})
    candidate = [r for r in rows if r["cell"] == "GPT6_SYNTHESIS_CANDIDATE"]
    write_new(args.output, {"version": "GPT6_SYNTHESIS_V1_MERGED", "researchOnly": True,
                           "rows": rows, "provenance": provenance,
                           "requestedMarketIds": expected, "requestedCellsComplete": True,
                           "full24ReadyForScoring": set(expected) == set(pre["marketIds"]),
                           "controlParityFailures": parity,
                           "preScorePass": not parity and all(r["candidateCorrectnessPass"] for r in candidate),
                           "externalScorerUnchanged": True, "promotionAuthorized": False})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle")
    p.add_argument("--market-id", type=int)
    p.add_argument("--cell", choices=CELLS)
    p.add_argument("--merge-dir")
    p.add_argument("--expected-market-ids")
    p.add_argument("--output", required=True)
    a = p.parse_args()
    if a.merge_dir:
        if a.bundle or a.market_id is not None or a.cell:
            p.error("Merge and market-execution arguments are mutually exclusive")
        merge(a)
    else:
        if a.expected_market_ids:
            p.error("--expected-market-ids is merge-only")
        run_one(a)


if __name__ == "__main__":
    main()
