"""External Lab only: one market/cell per job; merge is result-only postprocessing."""
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
CELLS = ("CAP1_CONTROL", "R240_CONTROL", "GPT6_CANDIDATE")
MIDS = (1945866,1945869,1945898,1945986,1946036,1946298,1946317,1946448,
        1946468,1946475,1946488,1946640,1946653,1946656,1946668,1946683,
        1946748,1946756,1946760,1946784,1946792,1946872,1946876,1946899)
ROUND2 = ROOT / "data/research/r4_v0/gpt6_three_failure_system_challenge_round2_evidence_v1_20260906.zip"
BENCH = ROOT / "data/research/r4_v0/p0_provenance_v1/MS4_R240_CANONICAL_FULL24_BENCHMARK_20260906.json"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_pins():
    with zipfile.ZipFile(ROUND2) as z:
        for item in json.loads(z.read("SHA256_MANIFEST.json"))["files"]:
            raw = z.read(item["path"])
            if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ValueError("Round2 evidence checksum mismatch")
        sources = json.loads(z.read("11_REQUEST1_SOURCE_MANIFEST.json"))["files"]
        pins = {x["path"]: x["sha256"] for x in sources}
        for name, path in (
            ("80_R246_MECHANISM_RUNNER.py", "tools/run_eth_ms4_r2_46_r240_core_active_credit_mechanism_ablation.py"),
            ("90_EXTERNAL_SCORER_V2_STRICT.py", "tools/score_gpt6_three_failure_system_challenge_v2_strict.py"),
        ):
            pins[path] = hashlib.sha256(z.read(name)).hexdigest()
    for path, sha in pins.items():
        if digest(ROOT / path) != sha:
            raise ValueError(f"Frozen dependency drift: {path}")
    for path in ("tools/gpt6_three_failure_resource_service_v1.py",
                 "tools/run_gpt6_three_failure_resource_service_v1.py",
                 "data/research/r4_v0/GPT6_RESOURCE_SERVICE_V1_PREREGISTERED_20260906.json"):
        pins[path] = digest(ROOT / path)
    pins["round2Evidence"] = digest(ROUND2)
    pins["canonicalBenchmark"] = digest(BENCH)
    return pins


def write_new(path, data):
    raw = json.dumps(data, indent=2, allow_nan=False)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(raw)


def validate(row):
    from tools.score_gpt6_three_failure_system_challenge_v2_strict import validate_row
    errors = validate_row(row)
    if type(row.get("marketId")) is not int or row["marketId"] not in MIDS:
        errors.append("unexpected/noninteger marketId")
    if row.get("cell") not in CELLS:
        errors.append("unexpected cell")
    for field in ("submits", "fillEvents", "maxSimultaneousDistinctPrices"):
        val = row.get(field)
        if type(val) is not int or val < 0:
            errors.append(f"invalid count: {field}")
    if row.get("cell") == "GPT6_CANDIDATE":
        checks = row.get("candidateCorrectness")
        required = {"insuranceConservationErrorMax", "insuranceOverfillNotionalMax",
                    "combinedAuthorityExcessMax", "quarantineConservationErrorMax",
                    "quarantineShortfallTotal", "duplicateOwnershipCount",
                    "staleServiceSubmitCount", "nonzeroNativeSubmitRcCount",
                    "coreSubmitBudgetShortfallCount", "activeHandoffOverlapCount",
                    "serviceSourceMissingCount"}
        if not isinstance(checks, dict) or set(checks) != required:
            errors.append("missing/unexpected candidate correctness fields")
        elif any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in checks.values()):
            errors.append("invalid candidate correctness values")
        for field in ("serviceLedger", "serviceEvents", "slotHistory", "splitEvents"):
            if not isinstance(row.get(field), list):
                errors.append(f"missing trace: {field}")
    if errors:
        raise ValueError(errors)


def run_one(args):
    if args.market_id not in MIDS or args.cell not in CELLS or not args.bundle:
        raise ValueError("One consumed market, cell and bundle required")
    if Path(args.output).exists():
        raise FileExistsError(args.output)
    pins = source_pins()  # before importing anything that can initialize HFT
    pins["tapeBundle"] = digest(args.bundle)
    with tempfile.TemporaryDirectory(prefix="gpt6_service_one_") as tmp:
        # Exact two members only: no extractall/path traversal or strategy code from ZIP.
        with zipfile.ZipFile(args.bundle) as z:
            cohort = json.loads(z.read("cohort.json"))["rows"]
            selected = [r for r in cohort if r["marketId"] == args.market_id]
            if len(selected) != 1:
                raise ValueError("Missing/duplicate cohort member")
            winner = selected[0]["winner"]
            tape = Path(tmp) / f"{args.market_id}.json.xz"
            with tape.open("xb") as stream:
                stream.write(z.read(f"tapes/{args.market_id}.json.xz"))
        from tools.gpt6_three_failure_resource_service_v1 import ResourceServiceSim, r246
        if args.cell == "CAP1_CONTROL":
            sim = r246.r28.FanoutRoleCapacitySim(tape, 1, 4)
            method = sim.run_cap
        elif args.cell == "R240_CONTROL":
            sim = r246.r240.HandoffRepairCreditQuarantineSim(tape, 1, 4)
            method = sim.run_r240
        else:
            sim = ResourceServiceSim(tape, 1, 4)
            method = sim.run_candidate
        print(json.dumps({"phase": "START", "marketId": args.market_id, "cell": args.cell}), flush=True)
        try:
            result = method(winner)
        finally:
            sim.close()
        row = {**result, "marketId": args.market_id, "cell": args.cell}
        validate(row)
        write_new(args.output, {"version": "GPT6_RESOURCE_SERVICE_V1_CELL", "researchOnly": True,
                               "provenance": pins, "rows": [row]})
        print(json.dumps({"phase": "COMPLETE", "marketId": args.market_id, "cell": args.cell,
                          "output": args.output}), flush=True)


def merge(args):
    pins = source_pins()
    rows, seen, provenance = [], set(), None
    for path in sorted(Path(args.merge_dir).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != "GPT6_RESOURCE_SERVICE_V1_CELL":
            raise ValueError(f"Non-cell JSON in merge directory: {path}")
        if provenance is None:
            provenance = data["provenance"]
        if data["provenance"] != provenance or any(provenance.get(k) != v for k, v in pins.items()):
            raise ValueError("Mixed/stale source or tape provenance")
        if len(data["rows"]) != 1:
            raise ValueError("Expected one checkpoint row per file")
        row = data["rows"][0]
        validate(row)
        key = (row["marketId"], row["cell"])
        if key in seen:
            raise ValueError(f"Duplicate checkpoint: {key}")
        seen.add(key)
        rows.append(row)
    if seen != {(m, c) for m in MIDS for c in CELLS}:
        raise ValueError("Require exactly 72 unique market/cell results")
    benchmark = json.loads(BENCH.read_text(encoding="utf-8"))
    baseline = {r["marketId"]: r for r in benchmark["markets"]}
    parity = []
    for row in rows:
        if row["cell"] == "GPT6_CANDIDATE":
            continue
        expected = baseline[row["marketId"]][row["cell"]]
        for actual_key, base_key in (("pnlDiagnosticOnly", "pnl"), ("floor", "floor"),
                                     ("best", "best"), ("submits", "submits"), ("fillEvents", "fillEvents")):
            if abs(row[actual_key] - expected[base_key]) > 1e-9:
                parity.append({"marketId": row["marketId"], "cell": row["cell"], "field": actual_key})
    candidates = [r for r in rows if r["cell"] == "GPT6_CANDIDATE"]
    correct = all(all(v <= 1e-9 for v in r["candidateCorrectness"].values()) for r in candidates)
    from tools.score_gpt6_three_failure_system_challenge_v2_strict import compact, agg
    aggregates = {c: agg([compact(r) for r in rows if r["cell"] == c]) for c in CELLS}
    retention = {}
    for c in CELLS[:2]:
        retention[c] = {k: (aggregates["GPT6_CANDIDATE"][k] / aggregates[c][k]
                           if aggregates[c][k] else None)
                        for k in ("totalFillEvents", "totalFilledQty", "totalSubmits")}
    write_new(args.output, {"version": "GPT6_RESOURCE_SERVICE_V1_FULL24", "researchOnly": True,
                           "provenance": provenance, "rows": rows, "aggregate": aggregates,
                           "activityRetention": retention, "controlParityFailures": parity,
                           "candidateSemanticCorrectnessPass": correct,
                           "zeroAddedInterventionMarkets": [r["marketId"] for r in candidates
                               if not r["candidateInterventions"]["B_activeCreditQuarantineClocks"]
                               and not r["candidateInterventions"]["C_coreServiceSubmits"]],
                           "preScorePass": correct and not parity,
                           "promotionAuthorized": False})
    print(json.dumps({"rows": len(rows), "preScorePass": correct and not parity}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle")
    parser.add_argument("--market-id", type=int)
    parser.add_argument("--cell", choices=CELLS)
    parser.add_argument("--merge-dir")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.merge_dir:
        if args.market_id is not None or args.cell or args.bundle:
            parser.error("merge and simulation arguments are mutually exclusive")
        merge(args)
    else:
        run_one(args)


if __name__ == "__main__":
    main()
