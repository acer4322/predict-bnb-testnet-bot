"""Offline anonymous export of additional historical real-order timing records."""
import argparse
import collections
import gzip
import hashlib
import importlib.util
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("latency_export", Path(__file__).with_name("export_live_execution_latency.py"))
if spec is None or not Path(spec.origin).exists():
    spec = importlib.util.spec_from_file_location("latency_export", Path(__file__).with_name("export_live_execution_latency_20261003.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

STAMP_KEYS = ("created_at_ms", "book_observed_at_ms", "quote_started_at_ms", "quote_completed_at_ms",
              "order_response_at_ms", "place_request_start_at_ms", "place_response_at_ms", "entry_commitment_started_at_ms")
LATENCY_KEYS = ("book_rtt_ms", "quote_rtt_ms", "quote_response_to_order_response_ms", "place_rtt_ms",
                "quote_response_to_place_start_ms", "book_to_quote_start_ms", "signal_to_place_start_ms", "signal_to_place_response_ms")


def day(value):
    return datetime.fromtimestamp(value/1000, timezone.utc).date().isoformat() if value else None


def connect(p):
    c = sqlite3.connect(p.resolve().as_uri()+"?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    return c


def delta(a, b):
    return b-a if a and b else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-dir", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--source-root", type=Path)
    args = ap.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    all_attempts, all_rounds, all_shotgun = [], [], []
    summary, coverage, source_files = {}, {}, []
    for name, asset in [("poly_gap_live.db", "BTC"), ("poly_gap_live_eth.db", "ETH"), ("poly_gap_live_bnb.db", "BNB")]:
        snapshot = args.snapshot_dir/(name+".snapshot.db")
        c = connect(snapshot)
        rounds = c.execute("SELECT * FROM poly_gap_live_rounds ORDER BY created_at_ms,id").fetchall()
        aliases = {r["id"]: f"gap_{asset.lower()}_round_{i+1:06d}" for i,r in enumerate(rounds)}
        rows = c.execute("SELECT * FROM poly_gap_live_execution_attempts ORDER BY created_at_ms,id").fetchall()
        records = []
        for i,r in enumerate(rows):
            t = {k: r[k] for k in LATENCY_KEYS}
            t["book_receipt_to_place_call_start_ms"] = delta(r["book_observed_at_ms"], r["place_request_start_at_ms"])
            t["book_receipt_to_place_call_return_ms"] = delta(r["book_observed_at_ms"], r["place_response_at_ms"])
            returned = bool(r["order_id"]) and r["outcome"] in {"SUBMITTED", "FILLED", "NO_FILL", "RETURNED_OPEN"}
            record = {
                "attempt_ref": f"gap_{asset.lower()}_attempt_{i+1:06d}", "round_ref": aliases.get(r["round_id"]),
                "asset": asset, "venue_route": "BINANCE_PREDICTION_REST", "signal_source": "POLYMARKET_PUBLIC_FEED",
                "date_utc": day(r["created_at_ms"]), "side": r["side"], "action": r["action"],
                "attempt_no": r["attempt_no"], "outcome": r["outcome"], "order_status": r["order_status"],
                "recorded_order_id_present": bool(r["order_id"]),
                "recorded_order_id_and_return_state": returned,
                "fill_status_evidence": "RECONCILED_SELL_STATUS" if r["action"] == "SELL" and r["outcome"] == "FILLED" and r["order_status"] == "FILLED" else "NO_PER_ATTEMPT_TRADE_CLOCK",
                "timing_anchor": "LOCAL_DIRECT_BOOK_GET_RETURN_WALL_CLOCK_MS",
                "stored_stamp_presence": {k: bool(r[k]) for k in STAMP_KEYS},
                "timings_ms": t, "fast_retry": bool(r["fast_retry"]), "retry_ordinal": r["retry_ordinal"],
                "book_to_wire_send_ms": None, "book_to_first_trade_ms": None, "book_to_full_fill_ms": None,
            }
            records.append(record)
        all_attempts.extend(records)
        for r in rounds:
            all_rounds.append({"round_ref": aliases[r["id"]], "asset": asset, "side": r["side"],
                "created_date_utc": day(r["created_at_ms"]), "state": r["state"],
                "entry_order_id_present": bool(r["entry_order_id"]), "exit_order_id_present": bool(r["exit_order_id"]),
                "entry_signal_clock_present": bool(r["entry_signal_at_ms"]),
                "entry_place_return_clock_present": bool(r["entry_placed_at_ms"]),
                "exit_signal_clock_present": bool(r["exit_signal_at_ms"]),
                "exit_place_return_clock_present": bool(r["exit_placed_at_ms"]),
                "book_to_trade_ms": None, "fill_clock_status": "UNKNOWN_NO_DIRECT_TRADE_TIMESTAMP"})
        shotgun = c.execute("SELECT * FROM poly_gap_live_shotgun_orders ORDER BY created_at_ms,id").fetchall()
        for i,r in enumerate(shotgun):
            all_shotgun.append({"order_ref":f"shotgun_{asset.lower()}_{i+1:06d}","round_ref":aliases.get(r["round_id"]),
                "asset":asset,"side":r["side"],"created_date_utc":day(r["created_at_ms"]),
                "state":r["state"],"order_status":r["order_status"],"recorded_order_id_present":bool(r["order_id"]),
                "quote_rtt_ms":r["quote_rtt_ms"],"place_rtt_ms":r["place_rtt_ms"],
                "book_to_place_ms":None,"book_to_trade_ms":None,"clock_status":"UNKNOWN_NO_BOOK_RECEIPT_CLOCK"})
        selected = [r for r in records if r["action"] == "BUY" and r["outcome"] == "SUBMITTED" and r["recorded_order_id_present"] and r["timings_ms"]["book_receipt_to_place_call_return_ms"] is not None]
        summary[asset] = {"cohort":"ALL_BUY_SUBMITTED_WITH_RECORDED_ID_AND_BOOK_PLACE_CLOCKS", "n":len(selected),
            "metrics":{k:base.stats([r["timings_ms"].get(k) for r in selected]) for k in list(LATENCY_KEYS)+["book_receipt_to_place_call_start_ms","book_receipt_to_place_call_return_ms"]}}
        coverage[asset] = {"attempt_rows":len(records),"round_rows":len(rounds),"shotgun_order_rows":len(shotgun),
            "date_range_utc":[min(r["date_utc"] for r in records),max(r["date_utc"] for r in records)],
            "attempt_outcome_counts":dict(collections.Counter(r["outcome"] for r in records)),
            "attempt_action_counts":dict(collections.Counter(r["action"] for r in records)),
            "complete_book_to_place_clock_rows":sum(r["timings_ms"]["book_receipt_to_place_call_return_ms"] is not None for r in records),
            "selected_buy_submitted_clock_rows":len(selected)}
        source_files.append({"name":name,"snapshot_sha256":hashlib.sha256(snapshot.read_bytes()).hexdigest()})
        for r in selected:
            assert r["timings_ms"]["book_receipt_to_place_call_start_ms"] >= 0
            assert r["timings_ms"]["book_receipt_to_place_call_return_ms"] >= r["timings_ms"]["book_receipt_to_place_call_start_ms"]
    base.write_rows(out/"gap_attempts.ndjson.gz", all_attempts)
    base.write_rows(out/"gap_rounds.ndjson.gz", all_rounds)
    base.write_rows(out/"shotgun_orders.ndjson.gz", all_shotgun)
    snapshot = args.snapshot_dir/"echtgeld_engine_v1.db.snapshot.db"
    c = connect(snapshot)
    engine = []
    for i,r in enumerate(c.execute("SELECT * FROM engine_orders ORDER BY attempted_at_ms,id")):
        result = json.loads(r["result_json"] or "{}")
        positive = lambda k: float(result.get(k) or 0) > 0
        engine.append({"order_ref":f"engine_{i+1:06d}","venue":r["venue"],"strategy":r["strategy"],
            "status":r["status"],"side":r["side"],"attempt_date_utc":day(r["attempted_at_ms"]),
            "result_status":result.get("status"),"recorded_vendor_order_id_present":bool(r["vendor_order_id"]),
            "recorded_result_vendor_id_matches":bool(r["vendor_order_id"]) and str(r["vendor_order_id"]) == str(result.get("vendorOrderId")),
            "exchange_status":result.get("exchangeStatus"),"order_history_status":result.get("orderHistoryStatus"),
            "result_positive_shares":positive("shares"),"result_positive_filled_shares":positive("filledShareQty"),
            "order_history_reconciliation":bool(result.get("orderHistoryReconciliation")),
            "delayed_reconciliation":bool(result.get("delayedReconciliation") or result.get("delayedOrderHistoryReconciliation")),
            "attempt_to_last_result_ms":delta(r["attempted_at_ms"],r["completed_at_ms"]),
            "quote_rtt_ms":result.get("quoteRttMs"),"book_to_place_ms":None,"book_to_trade_ms":None,
            "clock_status":"UNKNOWN_NO_BOOK_RECEIPT_OR_EXACT_TRADE_CLOCK"})
    base.write_rows(out/"engine_orders.ndjson.gz",engine)
    coverage["engine"]={"order_rows":len(engine),"status_counts":dict(collections.Counter(r["status"] for r in engine)),
        "submitted_with_vendor_id":sum(r["status"] == "SUBMITTED" and r["recorded_vendor_order_id_present"] for r in engine),
        "cap100_drill_and_stress_tables_excluded":True,
        "limit":"SUBMITTED alone is not proof of a fill; result/reconciliation presence flags are retained separately."}
    source_files.append({"name":"echtgeld_engine_v1.db","snapshot_sha256":hashlib.sha256(snapshot.read_bytes()).hexdigest()})
    snapshot=args.snapshot_dir/"xpair_btc_eth_canary.db.snapshot.db"
    c=connect(snapshot)
    canary=[]
    for i,r in enumerate(c.execute("SELECT mode,status,created_at FROM canary_runs ORDER BY created_at,id")):
        canary.append({"run_ref":f"canary_{i+1:06d}","mode":r["mode"],"status":r["status"],"date_utc":r["created_at"][:10],
            "book_to_place_ms":None,"book_to_trade_ms":None,"clock_status":"UNKNOWN_NO_PER_ORDER_RECEIPT_PLACE_TRADE_CLOCK"})
    base.write_rows(out/"canary_runs.ndjson.gz",canary)
    coverage["canary"]={"run_rows":len(canary),"mode_status_counts":[{"mode":m,"status":s,"n":n} for (m,s),n in sorted(collections.Counter((r["mode"],r["status"]) for r in canary).items())],
        "placement_incomplete_rows":sum(r["status"] == "PLACEMENT_INCOMPLETE_MANUAL_RECONCILE" for r in canary),
        "paper_xpair_trials_excluded":True,"limit":"Historical incomplete placement remains unresolved; not treated as a completed order/fill."}
    source_files.append({"name":"xpair_btc_eth_canary.db","snapshot_sha256":hashlib.sha256(snapshot.read_bytes()).hexdigest()})
    provenance={"source_files":source_files,"source_database_opened_read_only":True,
        "gap_route":"Polymarket public feed signal; Binance Prediction REST direct orderbook then signed MARKET/FOK placement.",
        "gap_clock":"book_observed_at_ms is local time.time() ms AFTER book GET returns; placement wall clocks bracket client method. place_rtt_ms and quote_rtt_ms use monotonic duration.",
        "unknowns":["Wire-send timestamp","Matching-engine acceptance timestamp","First trade timestamp","Full-fill timestamp","Historical exact binary/version per attempt","Wall-clock adjustments during elapsed spans"],
        "privacy":"Exact timestamps, identifiers, prices, amounts, account/response payloads and freeform messages omitted; UTC day and elapsed durations retained.",
        "source_code":[]}
    if args.source_root:
        for rel in ["src/predict_bot/poly_gap_live.py","src/predict_bot/poly_gap_live_v9.py","src/predict_bot/poly_gap_live_v32.py","src/predict_bot/poly_gap_live_v34.py","src/predict_bot/poly_gap_live_v35.py","src/predict_bot/poly_gap_live_v36.py","src/predict_bot/poly_gap_live_v37.py","src/predict_bot/echtgeld_engine_v1.py","src/predict_bot/target_taker_live_execution_v4.py"]:
            provenance["source_code"].append({"path":rel,"sha256":hashlib.sha256((args.source_root/rel).read_bytes()).hexdigest()})
    base.write_json(out/"ADDITIONAL_SUMMARY.json",summary)
    base.write_json(out/"ADDITIONAL_COVERAGE.json",coverage)
    base.write_json(out/"ADDITIONAL_PROVENANCE.json",provenance)
    shutil.copyfile(__file__,out/"export_additional_live_latency.py") if Path(__file__).resolve() != (out/"export_additional_live_latency.py").resolve() else None
    print(json.dumps({"rows":{"gap_attempts":len(all_attempts),"gap_rounds":len(all_rounds),"shotgun_orders":len(all_shotgun),"engine_orders":len(engine),"canary_runs":len(canary)},"ETH":summary["ETH"]},ensure_ascii=True))


if __name__ == "__main__":
    main()
