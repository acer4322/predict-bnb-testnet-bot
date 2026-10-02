"""Export anonymous historical timing evidence from an offline SQLite snapshot.

No exchange client imports, network calls, or live database writes.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import math
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

TIMING_KEYS = (
    "signalBookAgeMs", "latestLocalBookAgeMs", "eventToLocalCheckMs",
    "marketEventToDecisionStartMs", "decisionAndStoreMs", "storeMs",
    "candidateToLiveQueueMs", "marketEventToLiveQueueMs", "queueMs",
    "preQuoteMs", "preLedgerMs", "acceptedLedgerMs",
    "marketEventToQuoteStartMs", "quotePhaseMs", "quoteNetworkMs",
    "firstQuoteNetworkMs", "secondQuoteNetworkMs", "quoteToPlaceMs",
    "marketEventToPlaceStartMs", "placeNetworkMs", "eventToPlaceResponseMs",
    "totalMs",
)


def decode(value):
    if not value:
        return {}
    obj = json.loads(value)
    assert isinstance(obj, dict)
    return obj


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def iso_ms(value):
    return datetime.fromisoformat(value).timestamp() * 1000 if value else None


def stats(values):
    v = sorted(float(x) for x in values if numeric(x))
    if not v:
        return {"n": 0, "min": None, "median": None, "p90": None,
                "p95": None, "p99": None, "max": None, "mean": None}
    def q(p):
        i = (len(v) - 1) * p
        j = int(i)
        return v[j] + (v[min(j + 1, len(v) - 1)] - v[j]) * (i - j)
    return {"n": len(v), "min": v[0], "median": q(.5), "p90": q(.9),
            "p95": q(.95), "p99": q(.99), "max": v[-1], "mean": sum(v)/len(v)}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_rows(path, values):
    raw = "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":"), allow_nan=False)+"\n" for r in values)
    path.write_bytes(gzip.compress(raw.encode("utf-8"), mtime=0))


def response_evidence(row):
    api = decode(row["response_json"])
    local_id = row["order_id"]
    match = bool(local_id) and str(api.get("orderId", "")) == str(local_id)
    try:
        api_positive = float(api.get("filledShareQty") or 0) > 0
    except (ValueError, TypeError):
        api_positive = False
    filled = match and str(api.get("status", "")).upper() == "FILLED" and api_positive
    return {
        "recorded_api_order_id_matches_ledger": match,
        "api_status": api.get("status"),
        "api_positive_filled_quantity": api_positive,
        "ledger_positive_filled_quantity": bool((row["filled_share_qty"] or 0) > 0),
        "recorded_api_filled_confirmed": filled,
        "api_create_time_present": bool(api.get("createTime")),
        "api_modify_time_present": bool(api.get("modifyTime")),
        "api_terminal_time_present": bool(api.get("terminalTime")),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--source-root", type=Path)
    args = ap.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(args.snapshot.resolve().as_uri()+"?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    orders = con.execute("SELECT * FROM live_orders ORDER BY signal_at,id").fetchall()
    exits = con.execute("SELECT * FROM live_manual_exits ORDER BY requested_at,id").fetchall()
    telemetry = {r["order_local_id"]: r for r in con.execute("SELECT * FROM live_attempt_telemetry")}
    events = con.execute("SELECT timestamp,level,event_type FROM live_events ORDER BY timestamp,id").fetchall()
    assert len(telemetry) == con.execute("SELECT count(*) FROM live_attempt_telemetry").fetchone()[0]
    aliases = {r["id"]: f"entry_{i+1:06d}" for i,r in enumerate(orders)}
    exported = []
    attempts = []
    for r in orders:
        trow = telemetry.get(r["id"])
        t = decode(trow["telemetry_json"]) if trow else {}
        add = ":CONFIRM_ADD_" in r["strategy"]
        proof = response_evidence(r)
        ack = bool(trow and trow["final_outcome"] == "SUBMITTED" and proof["recorded_api_order_id_matches_ledger"])
        clocks = {key: t.get(key) if numeric(t.get(key)) else None for key in TIMING_KEYS}
        record = {
            "order_ref": aliases[r["id"]], "venue_route": "BINANCE_PREDICTION_REST",
            "asset": "UNKNOWN", "strategy": r["strategy"], "side": r["side"],
            "order_type": r["order_type"], "time_in_force": r["time_in_force"],
            "account_type": r["account_type"], "ledger_status": r["status"],
            "signal_date_utc": r["signal_at"][:10],
            "has_latency_telemetry": bool(trow),
            "telemetry_capture_date_utc": trow["captured_at"][:10] if trow else None,
            "attempt_outcome": trow["final_outcome"] if trow else None,
            "receipt_anchor": "CONFIRMATION_ADD_SIGNAL_CONSTRUCTION" if add else "RECORDED_SIGNAL_EVENT_RECEIPT",
            "ordinary_acknowledged_receipt_cohort": ack and not add,
            "placement_client_return_confirmed": ack,
            "placement_call_started": clocks["marketEventToPlaceStartMs"] is not None,
            "timings_ms": clocks,
            "quote_attempts": t.get("quoteAttempts"), "requote_triggered": t.get("requoteTriggered"),
            "book_to_wire_send_ms": None, "book_to_matching_engine_accept_ms": None,
            "book_to_first_trade_ms": None, "book_to_full_fill_ms": None,
            "fill_clock_status": "UNKNOWN_NO_COMMON_RECEIPT_TO_TRADE_CLOCK",
            **proof,
        }
        exported.append(record)
        if trow:
            assert t.get("finalOutcome") == trow["final_outcome"]
            attempts.append(record)
    manual = []
    for i,r in enumerate(exits):
        manual.append({
            "exit_ref": f"exit_{i+1:06d}", "entry_ref": aliases.get(r["order_local_id"]),
            "venue_route": "BINANCE_PREDICTION_REST", "strategy": r["strategy"],
            "side": r["side"], "order_type": r["order_type"], "time_in_force": r["time_in_force"],
            "ledger_status": r["status"], "request_date_utc": r["requested_at"][:10],
            "book_to_wire_send_ms": None, "book_to_first_trade_ms": None,
            "latency_clock_status": "UNKNOWN_NO_BOOK_RECEIPT_TELEMETRY",
            **response_evidence(r),
        })
    cohorts = {
        "ordinary_acknowledged_receipt": [x for x in attempts if x["ordinary_acknowledged_receipt_cohort"]],
        "ordinary_acknowledged_eventually_filled": [x for x in attempts if x["ordinary_acknowledged_receipt_cohort"] and x["recorded_api_filled_confirmed"]],
        "all_acknowledged_including_confirmation_add": [x for x in attempts if x["placement_client_return_confirmed"]],
        "confirmation_add_signal_to_return": [x for x in attempts if x["placement_client_return_confirmed"] and x["receipt_anchor"] == "CONFIRMATION_ADD_SIGNAL_CONSTRUCTION"],
        "non_submitted_attempt_final_failure": [x for x in attempts if not x["placement_client_return_confirmed"]],
    }
    summary = {"unit": "milliseconds", "quantiles": "linear interpolation at (n-1)*p; no rounding before computation", "cohorts": {}}
    for name,rows in cohorts.items():
        summary["cohorts"][name] = {
            "n": len(rows), "final_ledger_status_counts": dict(collections.Counter(x["ledger_status"] for x in rows)),
            "metrics": {k: stats([x["timings_ms"][k] for x in rows]) for k in TIMING_KEYS},
        }
    by_strategy = {}
    for strategy in sorted({x["strategy"] for x in cohorts["ordinary_acknowledged_receipt"]}):
        rows = [x for x in cohorts["ordinary_acknowledged_receipt"] if x["strategy"] == strategy]
        by_strategy[strategy] = {"n": len(rows), "metrics": {k: stats([x["timings_ms"][k] for x in rows]) for k in TIMING_KEYS}}
    summary["ordinary_acknowledged_by_strategy"] = by_strategy
    daily = collections.Counter((x["signal_date_utc"], x["attempt_outcome"] or "NO_TELEMETRY", x["ledger_status"]) for x in exported)
    coverage = {
        "selection": "All preserved rows from one frozen historical live ledger; no selection by speed, strategy, success or outcome.",
        "entry_rows": len(exported), "manual_exit_rows": len(manual), "latency_attempt_rows": len(attempts),
        "entry_signal_date_utc": [min(x["signal_date_utc"] for x in exported), max(x["signal_date_utc"] for x in exported)],
        "telemetry_capture_date_utc": [min(x["telemetry_capture_date_utc"] for x in attempts), max(x["telemetry_capture_date_utc"] for x in attempts)],
        "event_type_counts": dict(collections.Counter(x["event_type"] for x in events)),
        "entry_recorded_api_order_id_matches": sum(x["recorded_api_order_id_matches_ledger"] for x in exported),
        "entry_recorded_api_filled_confirmed": sum(x["recorded_api_filled_confirmed"] for x in exported),
        "entry_status_counts": dict(collections.Counter(x["ledger_status"] for x in exported)),
        "manual_exit_status_counts": dict(collections.Counter(x["ledger_status"] for x in manual)),
        "attempt_outcome_counts": dict(collections.Counter(x["attempt_outcome"] for x in attempts)),
        "daily_counts": [{"date_utc": d, "attempt_outcome": o, "ledger_status": s, "n": n} for (d,o,s),n in sorted(daily.items())],
    }
    residuals = []
    for x in cohorts["all_acknowledged_including_confirmation_add"]:
        t = x["timings_ms"]
        assert all(numeric(t[k]) and t[k] >= 0 for k in ["marketEventToPlaceStartMs", "placeNetworkMs", "eventToPlaceResponseMs"])
        residuals.append(abs(t["eventToPlaceResponseMs"] - t["marketEventToPlaceStartMs"] - t["placeNetworkMs"]))
        assert x["ledger_status"] == x["api_status"]
    assert max(residuals, default=0) < 1e-6
    validation = {
        "status": "PASS", "source_row_counts_reconciled": True,
        "submitted_order_id_matches_checked": len(residuals),
        "submitted_latency_identity_max_absolute_residual_ms": max(residuals,default=0),
        "identity": "eventToPlaceResponseMs = marketEventToPlaceStartMs + placeNetworkMs",
        "no_quote_rejection_or_local_block_in_ack_cohort": True,
        "confirmation_add_excluded_from_ordinary_receipt_headline": True,
        "exact_wire_send_or_trade_execution_latency_claimed": False,
        "source_database_opened_read_only": True,
        "source_snapshot_sha256": hashlib.sha256(args.snapshot.read_bytes()).hexdigest(),
        "public_export_policy": "Allowlist only; sequential aliases; dates coarsened to UTC day. No wallet, real order/token/market IDs, exact wall timestamps, amounts, prices, balances, credentials, responses or event/error messages.",
    }
    provenance = {
        "schema_version": 1, "dataset_kind": "HISTORICAL_REAL_ORDER_TIMING_EVIDENCE_ANONYMIZED",
        "source_name": "live_m0w.db", "snapshot_sha256": validation["source_snapshot_sha256"],
        "retrieval": "Read-only SQLite backup; offline export. No new real orders or authenticated API requests.",
        "api_documentation_url": "https://developers.binance.com/en/docs/catalog/web3-wallet-prediction-trading/api/rest-api/trade",
        "api_clock_limit": "createTime, modifyTime and terminalTime exist in the private frozen response evidence. Only presence flags are exported. They do not provide a same-clock local book-to-first/full-trade measurement.",
        "route_limit": "Historical Binance Prediction REST route. Does not establish direct Predict.fun ETH5M latency, passive queue latency or current runtime latency.",
        "source_version_limit": "Producing functions checked in current source; exact historical executing binary and trigger stream per attempt were not persisted in this telemetry table.",
        "source_code": [],
    }
    code_ranges = {
        "src/predict_bot/live_trading.py": [(821,844),(1391,1433),(1708,1724),(4000,4019),(7297,7324),(7582,7708),(7750,7865)],
        "src/predict_bot/core.py": [(299,325),(452,471),(495,527)],
        "src/predict_bot/m_realtime.py": [(819,844),(1017,1053)],
        "src/predict_bot/microstructure.py": [(1640,1659)],
    }
    if args.source_root:
        for rel,ranges in code_ranges.items():
            p = args.source_root/rel
            provenance["source_code"].append({"path":rel,"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"line_ranges":ranges})
    write_rows(out/"entries.ndjson.gz", exported)
    write_rows(out/"attempts.ndjson.gz", attempts)
    write_rows(out/"manual_exits.ndjson.gz", manual)
    write_json(out/"SUMMARY.json", summary)
    write_json(out/"COVERAGE.json", coverage)
    write_json(out/"VALIDATION.json", validation)
    write_json(out/"PROVENANCE.json", provenance)
    if Path(__file__).resolve() != (out/"export_live_execution_latency.py").resolve():
        shutil.copyfile(__file__, out/"export_live_execution_latency.py")
    print(json.dumps({"coverage":{k:v for k,v in coverage.items() if k not in ["daily_counts","entry_status_counts"]},"headline":summary["cohorts"]["ordinary_acknowledged_receipt"]["metrics"]["eventToPlaceResponseMs"]},ensure_ascii=True))


if __name__ == "__main__":
    main()
