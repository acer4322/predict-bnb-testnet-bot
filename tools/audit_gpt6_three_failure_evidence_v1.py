"""Audit supplied, already-executed evidence only; no strategy imports or replay.

Prints JSON to stdout. Does not extract the archive or modify any files.
"""
import argparse
import hashlib
import json
from collections import Counter
from zipfile import ZipFile

PREFIX = "gpt6_three_failure_system_challenge_v1_20260906/"
RESULTS = (
    "34_SOURCE_R244_STAGEA16_BATCH_A_RESULT.json",
    "35_SOURCE_R244_STAGEA16_BATCH_B_RESULT.json",
    "36_SOURCE_R244_REMAINDER8_RESULT.json",
)


def audit(path):
    with ZipFile(path) as archive:
        def read(name):
            return json.loads(archive.read(PREFIX + name))

        manifest = read("SHA256_MANIFEST.json")["files"]
        errors = []
        for name, expected in manifest.items():
            raw = archive.read(PREFIX + name)
            if (hashlib.sha256(raw).hexdigest() != expected["sha256"]
                    or len(raw) != expected["bytes"]):
                errors.append(name)
        if errors:
            raise ValueError(f"Archive manifest mismatch: {errors}")
        benchmark = read("25_SOURCE_R240_CANONICAL_FULL24_BENCHMARK.json")
        expected_ids = {r["marketId"] for r in benchmark["markets"]}
        rows = [r for name in RESULTS for r in read(name)["rows"]]
        counts = Counter((r["cell"], r["marketId"]) for r in rows)
        if any(n != 1 for n in counts.values()):
            raise ValueError("Duplicate cell/market in supplied R244 evidence")
        cells = sorted({r["cell"] for r in rows})
        if len(expected_ids) != 24 or len(cells) != 2:
            raise ValueError("Unexpected benchmark/cell cardinality")
        for cell in cells:
            if {r["marketId"] for r in rows if r["cell"] == cell} != expected_ids:
                raise ValueError(f"Incomplete or extra markets: {cell}")

        interventions = []
        for row in rows:
            intervention = row.get("r244CoreIntervention")
            if not intervention:
                continue
            t = intervention["activeSubmitT"]
            source = [e for e in row["failureEvidenceActiveDrainEvents"]
                      if e.get("event") == "FAILURE_EVIDENCE_ACTIVE_DRAIN_SUBMIT"
                      and e["t"] == t]
            source_match = (len(source) == 1
                            and source[0]["sourceKey"] == intervention["sourceKey"]
                            and source[0]["sourceRole"] == "ECONOMIC_CORE"
                            and source[0]["generation"] == intervention["generation"])
            submits = [e for e in row["ms4R2ExecutionDecisions"]
                       if e.get("event") == "MS4_R2_ACTIVE_REPAIR_SUBMIT"
                       and e["t"] == t]
            unique_submit = len(submits) == 1
            key = submits[0]["key"] if unique_submit else None
            fills = [e for e in row["splitEvents"]
                     if e.get("event") == "ROLE_FILL_SPLIT"
                     and key is not None and e.get("key") == key]
            qty = sum(e["fillInc"] for e in fills)
            stats = row["ms4R2ActiveRepairStats"]
            zero_cancel_corrob = (unique_submit and qty == 0
                                  and row["ms4R2ActiveRepairFillQty"] == 0
                                  and stats.get("SUBMIT") == 1
                                  and stats.get("TERMINAL_CANCELED") == 1)
            interventions.append({
                "marketId": row["marketId"], "sourceKey": intervention["sourceKey"],
                "sourceRoleGenerationMatch": source_match,
                "activeSubmitT": t, "activeKey": key,
                "uniqueActiveSubmit": unique_submit,
                "confirmedFillQtyInSuppliedTrace": qty,
                "confirmedRepairQtyInSuppliedTrace": sum(e["repairAllocated"] for e in fills),
                "singleActiveZeroFillCanceledCorroborated": zero_cancel_corrob,
            })
        return {
            "version": "GPT6_THREE_FAILURE_EXISTING_EVIDENCE_AUDIT_V1",
            "mode": "EXISTING_RESULT_POSTPROCESS_ONLY_NO_SIMULATION",
            "manifestFilesVerified": len(manifest),
            "manifestMismatchCount": len(errors),
            "marketsPerCell": {c: sum(r["cell"] == c for r in rows) for c in cells},
            "interventionSubmitCount": len(interventions),
            "sourceMatchCount": sum(r["sourceRoleGenerationMatch"] for r in interventions),
            "interventionsWithConfirmedFill": sum(r["confirmedFillQtyInSuppliedTrace"] > 0 for r in interventions),
            "zeroFillCanceledMarkets": [r["marketId"] for r in interventions if r["singleActiveZeroFillCanceledCorroborated"]],
            "rows": interventions,
            "limits": ["No candidate tested", "Missing trace alone does not prove no fill",
                       "Submit attribution is not a test of economic benefit",
                       "Archive hashes prove package consistency, not execution correctness"],
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.bundle), indent=2, allow_nan=False))
