from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import analyze_target_controller_fragment_ebm_v272 as v272
import analyze_target_controller_prediction_common_support_v273b as v273

base = v272.base
ROOT = base.ROOT
REPORT_VERSION = "TARGET_CONTROLLER_PREDICTION_JOINT_SUPPORT_V275B"
DEFAULT_V275 = ROOT / "data" / "research" / "target_controller_prediction_independent_windows_v275_report.json"
DEFAULT_STATES = base.DEFAULT_STATES
DEFAULT_MICRO_DB = base.DEFAULT_DB
DEFAULT_BOOK_DB = v272.DEFAULT_BOOK_DB
DEFAULT_REPORT = ROOT / "data" / "research" / "target_controller_prediction_joint_support_v275b_report.json"
DEFAULT_ROWS = ROOT / "data" / "research" / "target_controller_prediction_joint_support_v275b_rows.csv"
ALLOWED_REGIMES = {"STRESS_2026_08_16", "ORDINARY_2026_08_17"}
REQUIRED_PREDICTION = list(v273.EXPERIMENTS["CURRENT_PLUS_3S_COMPLETE_CASE"]["required"])


def _load_v275(path: Path) -> tuple[set[int], dict[str, Any]]:
    payload = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    decision = payload.get("decision") or {}
    if decision.get("status") != "READY_FOR_INDEPENDENT_REPAIR_VALIDATION":
        raise RuntimeError("V2.7.5 must be READY before joint-support audit")
    ids = {int(x["marketId"]) for x in payload.get("eligibleMarkets", [])}
    if not ids:
        raise RuntimeError("V2.7.5 has no eligible independent markets")
    return ids, payload


def _load_states(path: Path, market_ids: set[int]) -> list[dict[str, Any]]:
    required = {
        "market_id", "segment_id", "regime", "sample_ms",
        *base.STATE_CORE, *base.LIFECYCLE_DESCRIPTIVE,
        *[base.TASK_LABEL_PREFIX[t].format(h) for t in base.TASK_LABEL_PREFIX for h in base.SUPPORTED_FUTURE_HORIZONS],
    }
    rows: list[dict[str, Any]] = []
    with path.expanduser().resolve().open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise RuntimeError("V2.1 states CSV missing columns: " + ", ".join(missing))
        for raw in reader:
            market_id = base._int(raw.get("market_id"))
            regime = str(raw.get("regime") or "")
            if market_id not in market_ids or regime not in ALLOWED_REGIMES:
                continue
            row = dict(raw)
            row["market_id"] = market_id
            row["segment_id"] = base._int(row.get("segment_id"))
            row["sample_ms"] = base._int(row.get("sample_ms"))
            for field in base.STATE_CORE + base.LIFECYCLE_DESCRIPTIVE:
                row[field] = base._num(row.get(field))
            for task in base.TASK_LABEL_PREFIX:
                for horizon in base.SUPPORTED_FUTURE_HORIZONS:
                    label = base.TASK_LABEL_PREFIX[task].format(horizon)
                    row[label] = base._int(row.get(label))
            rows.append(row)
    rows.sort(key=lambda r: (r["sample_ms"], r["market_id"], r["segment_id"]))
    return rows


def _audit_regime(states: list[dict[str, Any]], micro_db: Path, book_db: Path) -> tuple[list[dict[str, Any]], Counter, dict[str, Any]]:
    if not states:
        return [], Counter(), {}
    start_ms = min(int(r["sample_ms"]) for r in states)
    end_ms = max(int(r["sample_ms"]) for r in states)
    snapshots, _ = base._load_micro(micro_db, start_ms, end_ms)
    pred_by_key, pred_audit = v272._load_8778_rows(states, book_db)
    rows, join_audit = v272.build_ab_rows(states, snapshots, pred_by_key)
    return rows, join_audit, pred_audit


def _market_report(states: list[dict[str, Any]], joined: list[dict[str, Any]], complete: list[dict[str, Any]]) -> list[dict[str, Any]]:
    state_n = Counter(int(r["market_id"]) for r in states)
    joined_n = Counter(int(r["market_id"]) for r in joined)
    complete_n = Counter(int(r["market_id"]) for r in complete)
    regime_by_market: dict[int, str] = {}
    for row in states:
        regime_by_market[int(row["market_id"])] = str(row["regime"])
    out = []
    for market_id in sorted(state_n):
        n = state_n[market_id]
        j = joined_n[market_id]
        c = complete_n[market_id]
        micro_rate = j / n if n else 0.0
        joint_rate = c / n if n else 0.0
        eligible = n >= 30 and j >= 30 and c >= 30 and micro_rate >= 0.80 and joint_rate >= 0.50
        out.append({
            "marketId": market_id,
            "regime": regime_by_market[market_id],
            "stateRows": n,
            "freshMicroJoinedRows": j,
            "freshMicroJoinRate": micro_rate,
            "jointCompleteRows": c,
            "jointCompleteRate": joint_rate,
            "jointEligible": eligible,
        })
    return out


def _decision(markets: list[dict[str, Any]]) -> dict[str, Any]:
    good = [m for m in markets if m["jointEligible"]]
    by_regime: dict[str, dict[str, int]] = {}
    for regime in sorted(ALLOWED_REGIMES):
        xs = [m for m in good if m["regime"] == regime]
        by_regime[regime] = {
            "markets": len(xs),
            "jointCompleteRows": sum(int(m["jointCompleteRows"]) for m in xs),
        }
    total_rows = sum(int(m["jointCompleteRows"]) for m in good)
    each_regime_ready = all(v["markets"] >= 4 and v["jointCompleteRows"] >= 300 for v in by_regime.values())
    ready = len(good) >= 8 and total_rows >= 800 and each_regime_ready
    return {
        "status": "READY_FOR_V276_INDEPENDENT_REPAIR_AB" if ready else "INSUFFICIENT_JOINT_SUPPORT_DO_NOT_FIT_V276",
        "eligibleMarkets": len(good),
        "jointCompleteRows": total_rows,
        "byRegime": by_regime,
        "rule": "Start from V2.7.5 eligible markets. Per market require >=30 state rows, >=80% strict-past <=750ms micro join, >=30 joint complete rows, and >=50% joint complete rate for frozen current+3s Prediction fields. READY additionally requires >=4 markets and >=300 joint rows in each STRESS and ORDINARY regime, >=8 markets and >=800 rows total.",
    }


def main() -> None:
    p = argparse.ArgumentParser(description="V2.7.5b joint microstructure + Prediction support gate before independent REPAIR A/B")
    p.add_argument("--v275-report", type=Path, default=DEFAULT_V275)
    p.add_argument("--states", type=Path, default=DEFAULT_STATES)
    p.add_argument("--micro-db", type=Path, default=DEFAULT_MICRO_DB)
    p.add_argument("--book-db", type=Path, default=DEFAULT_BOOK_DB)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--rows", type=Path, default=DEFAULT_ROWS)
    args = p.parse_args()

    eligible_ids, v275_payload = _load_v275(args.v275_report)
    states = _load_states(args.states, eligible_ids)
    if not states:
        raise RuntimeError("no V2.7.5 eligible states found")

    all_joined: list[dict[str, Any]] = []
    regime_audit: dict[str, Any] = {}
    for regime in sorted(ALLOWED_REGIMES):
        regime_states = [r for r in states if r["regime"] == regime]
        joined, join_audit, pred_audit = _audit_regime(regime_states, args.micro_db, args.book_db)
        all_joined.extend(joined)
        regime_audit[regime] = {
            "stateRows": len(regime_states),
            "microJoinAudit": dict(join_audit),
            "predictionCoverageDecision": pred_audit.get("decision"),
        }

    complete = v273.complete(all_joined, REQUIRED_PREDICTION)
    markets = _market_report(states, all_joined, complete)
    decision = _decision(markets)
    good_ids = {int(m["marketId"]) for m in markets if m["jointEligible"]}
    export = [r for r in complete if int(r["market_id"]) in good_ids]

    report = {
        "version": REPORT_VERSION,
        "policy": {
            "purpose": "Joint-support gate before independent validation of the frozen RAW Prediction REPAIR feature family",
            "v275DevelopmentMarketsRemainExcluded": list(v275_payload.get("policy", {}).get("developmentMarketsExcluded", [])),
            "predictionFieldsFrozenFromV273": REQUIRED_PREDICTION,
            "microStrictPastFreshnessMs": base.FRESHNESS_MS,
            "noModelFitting": True,
            "noHyperparameterTuning": True,
            "noLiveTradingChanges": True,
        },
        "source": {
            "v275EligibleMarkets": len(eligible_ids),
            "selectedStateRows": len(states),
            "freshMicroJoinedRows": len(all_joined),
            "jointCompleteRowsBeforeMarketGate": len(complete),
            "regimeAudit": regime_audit,
        },
        "decision": decision,
        "jointEligibleMarkets": [m for m in markets if m["jointEligible"]],
        "allCandidateMarkets": markets,
    }
    out = args.report.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    base._write_csv(args.rows, export)
    print(json.dumps({
        "version": REPORT_VERSION,
        "decision": decision,
        "report": str(out),
        "rows": str(args.rows.expanduser().resolve()),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
