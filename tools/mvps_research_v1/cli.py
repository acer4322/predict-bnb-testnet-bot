"""Small offline CLI: doctor, templates, synthetic demo, freeze, replay, evaluate."""
from __future__ import annotations

import argparse
import importlib.metadata
import sys
from pathlib import Path

from .core import EconomicLedger, digest, evaluate, file_digest, read_json, write_json_new
from .runner import (CONSUMED_MARKETS, PROFILES, ROOT, TraceSink, draft_plan,
                     replay, source_pins, validate_plan)


def render_report(result, assessment):
    lines = ["# MVPS Research System V1 — Policy Economics Screen", "",
             f"Evidence: `{result['evidenceKind']}`",
             f"Policy: `{result['policyId']}`",
             f"Verdict: `{assessment['verdict']}`", "",
             "Live eligible: **false**. This system never promotes to live.", "",
             "| Metric | Value |", "|---|---:|",
             f"| Markets | {assessment['marketCount']} |",
             f"| Gross aggregate | {assessment['grossAggregate']} |",
             f"| Full-net lower aggregate | {assessment['netLowerAggregate']} |",
             f"| Leave one best market out | {assessment.get('leaveOneBestOut')} |",
             f"| Terminal sequence drawdown | {assessment.get('terminalSequenceDrawdown')} |",
             f"| Trade coverage | {assessment['tradeCoverage']} |", "",
             "## Gates / missing evidence", ""]
    lines += [f"- `{issue}`" for issue in assessment["issues"]] or ["- Development screen only; not promotion."]
    lines += ["", "## Complete policy accounting", "",
              "| Market | Paired gross | Residual cost | UP gross | DOWN gross | Fills |", "|---|---:|---:|---:|---:|---:|"]
    for row in result["markets"]:
        lines.append(f"| {row['marketId']} | {row['pairedGrossProfit']} | {row['residualCost']} | "
                     f"{row['endpointGross']['UP']} | {row['endpointGross']['DOWN']} | {row['fillEvents']} |")
    lines += ["", "## Boundaries", "",
              "- Paired + residual values reconcile to the complete terminal ledger; no successful-cycle selection.",
              "- Matching is accounting, not proof of responsibility ownership or released capital.",
              "- The historical 8/10 BE pool and all locked cohorts remain untouched.",
              "- Synthetic fixtures are not HFT or profit evidence. Development evidence is not OOS evidence.",
              "- Unknown rebates are not assumed zero; only their unconfirmed positive contribution is omitted from the absolute lower bound.",
              "- Cost bounds must include all applicable costs not already in buyNotional; no double charging.",
              "- Lower favorable payoff is not itself a rejection gate.", ""]
    return "\n".join(lines)


def save_assessment(directory, result):
    assessment = evaluate(result)
    write_json_new(Path(directory) / "assessment.json", assessment)
    with (Path(directory) / "REPORT.md").open("x", encoding="utf-8") as stream:
        stream.write(render_report(result, assessment))
    return assessment


def load_completed(directory):
    directory = Path(directory)
    if (directory / "FAILED.json").exists():
        raise ValueError("failed run cannot be evaluated as completed evidence")
    completed = read_json(directory / "COMPLETED.json")
    if completed["resultSha256"] != file_digest(directory / "result.json"):
        raise ValueError("result artifact changed after completion")
    result = read_json(directory / "result.json")
    if result.get("schema") != "MVPS_RESEARCH_RUN_V1":
        raise ValueError("unsupported result schema")
    if result.get("evidenceKind") == "REALISTIC_HFT_REPLAY":
        plan = read_json(directory / "plan.json")
        if digest(plan) != result.get("planSha256"):
            raise ValueError("frozen plan hash mismatch")
        for key in ("policyId", "marketIds", "evaluationContract", "activityControl", "cohortKind"):
            if result.get(key) != plan.get(key):
                raise ValueError(f"post-result contract drift: {key}")
    for row in result["markets"]:
        # Market keys cannot turn file checks into arbitrary path traversals.
        mid = str(row["marketId"])
        if not mid.replace("_", "").isalnum():
            raise ValueError("invalid market artifact key")
        if result.get("evidenceKind") == "REALISTIC_HFT_REPLAY":
            state = read_json(directory / mid / "native_state.json")
            if digest(state) != row.get("behaviorHash"):
                raise ValueError("native-state provenance changed")
        for kind, meta in row["traceManifest"].items():
            if kind not in {"fills", "allocations", "orders", "decisions", "slot_releases"}:
                raise ValueError("unknown trace kind")
            path = directory / mid / f"{kind}.jsonl"
            if file_digest(path) != meta["sha256"]:
                raise ValueError("trace changed or incomplete")
            with path.open(encoding="utf-8") as stream:
                if sum(1 for _ in stream) != meta["rows"]:
                    raise ValueError("trace row-count mismatch")
        # Recompute economics from saved confirmed receipts, not a trusted PASS flag.
        ledger = EconomicLedger(row["startMs"])
        if "fills" in row["traceManifest"]:
            with (directory / mid / "fills.jsonl").open(encoding="utf-8") as stream:
                for line in stream:
                    ledger.fill(__import__("json").loads(line))
        recomputed = ledger.summary(row["endMs"])
        if any(row.get(key) != value for key, value in recomputed.items()):
            raise ValueError("saved summary does not reconcile to confirmed receipts")
    return result


def synthetic_demo(directory):
    """Scripted confirmed events, not an exchange simulator or backtest."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    scripts = {
        "FIXTURE_A": [(10, "UP", "1", ".40"), (20, "UP", "1", ".40"),
                      (30, "DOWN", "2", ".50")],
        "FIXTURE_B": [(10, "DOWN", "2", ".30"), (20, "UP", "2", ".60")],
        "FIXTURE_C": [(10, "UP", "1", ".70")],
    }
    rows = []
    for mid, fills in scripts.items():
        sink = TraceSink(directory / mid)
        ledger = EconomicLedger(0, sink.emit)
        try:
            for i, (t, side, qty, price) in enumerate(fills):
                ledger.fill({"fillId": f"f{i}", "orderId": f"o{i}", "receiptMs": t,
                             "placedMs": 0, "side": side, "qty": qty, "price": price,
                             "orderQty": qty, "cumQty": qty, "priceAuthority": "EXCHANGE_SNAPSHOT"})
            row = ledger.summary(100)
        finally:
            sink.close()
        row.update(marketId=mid, winnerPostHocOnly="DOWN", traceManifest=sink.manifest())
        rows.append(row)
    contract = draft_plan()["evaluationContract"]
    result = {"schema": "MVPS_RESEARCH_RUN_V1", "researchOnly": True, "liveAuthority": False,
              "evidenceKind": "SYNTHETIC_FIXTURE_NOT_HFT", "policyId": "scripted_accounting_fixture",
              "cohortKind": "consumed_development", "marketIds": list(scripts), "markets": rows,
              "behaviorParityVerified": False, "evaluationContract": contract,
              "activityControl": None, "consumedBE": 0, "oldITTBudgetUnchanged": "8/10"}
    write_json_new(directory / "result.json", result)
    write_json_new(directory / "COMPLETED.json", {"resultSha256": file_digest(directory / "result.json")})
    return save_assessment(directory, result)


def doctor():
    versions = {}
    for package in ("hftbacktest", "numpy", "numba", "scikit-learn", "httpx", "joblib"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "NOT_INSTALLED_AS_DISTRIBUTION"
    return {"researchOnly": True, "liveAuthority": False, "python": sys.version.split()[0],
            "profiles": PROFILES, "consumedAllowlist": list(CONSUMED_MARKETS),
            "sourcePins": source_pins(), "installedDistributions": versions,
            "backendImported": False, "HFTReadiness": "NOT_EXECUTION_TESTED",
            "note": "Bundled HFT packages may not have installed metadata. Freeze actual package/native file hashes before replay."}


def main(argv=None):
    parser = argparse.ArgumentParser(description="MVPS V1: offline policy economics, no live path")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="read-only dependency/source check; no HFT import")
    template = commands.add_parser("template", help="write a non-authorized draft; thresholds stay unspecified")
    template.add_argument("--output", required=True)
    demo = commands.add_parser("demo", help="synthetic confirmed-fill fixture; 0 BE, NOT profit evidence")
    demo.add_argument("--output", required=True)
    freeze = commands.add_parser("freeze", help="pin a completed draft; does NOT authorize replay")
    freeze.add_argument("--draft", required=True)
    freeze.add_argument("--output", required=True)
    replay_parser = commands.add_parser("replay", help="requires separately authorized, plan-bound new BE pool")
    replay_parser.add_argument("--plan", required=True)
    replay_parser.add_argument("--authorization", required=True)
    replay_parser.add_argument("--output", required=True)
    assess = commands.add_parser("evaluate", help="evaluate a completed, hash-verified run")
    assess.add_argument("--run", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor()
        elif args.command == "template":
            write_json_new(args.output, draft_plan())
            result = {"draft": args.output, "authorized": False}
        elif args.command == "demo":
            result = synthetic_demo(args.output)
        elif args.command == "freeze":
            plan = read_json(args.draft)
            validate_plan(plan)
            plan["sourcePins"] = source_pins()
            validate_plan(plan, verify_files=True)
            write_json_new(args.output, plan)
            result = {"planSha256": digest(plan), "authorized": False}
        elif args.command == "replay":
            result = replay(read_json(args.plan), read_json(args.authorization), args.output)
            result = save_assessment(args.output, result)
        else:
            result = save_assessment(args.run, load_completed(args.run))
        print(__import__("json").dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"STOP: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
