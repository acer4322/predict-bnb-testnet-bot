from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "predict_own_wallet_lifecycle_v1.db"
CONTRACT = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0" / "predict_own_wallet_lifecycle_collection_v1_contract.json"
REPORT = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0" / "predict_own_wallet_lifecycle_v1_collection_report.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=REPORT)
    args = parser.parse_args()
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    path = args.db.resolve()
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        event_types = {
            str(row["event_type"]): int(row["n"])
            for row in connection.execute(
                "SELECT event_type,COUNT(*) n FROM own_wallet_lifecycle_events_v1 GROUP BY event_type"
            )
        }
        totals = dict(
            connection.execute(
                """SELECT COUNT(*) events,
                          COUNT(DISTINCT COALESCE(order_hash,order_id)) orders,
                          COUNT(DISTINCT market_id) markets,
                          COUNT(DISTINCT CASE WHEN event_type='orderAccepted' THEN COALESCE(order_hash,order_id) END) accepted_orders,
                          COUNT(DISTINCT CASE WHEN event_type='orderTransactionSuccess' THEN COALESCE(order_hash,order_id) END) filled_orders,
                          COUNT(DISTINCT CASE WHEN event_type IN ('orderCancelled','orderExpired','orderNotAccepted') THEN COALESCE(order_hash,order_id) END) no_fill_terminal_orders,
                          COUNT(DISTINCT CASE WHEN event_type='orderTransactionSuccess' AND quantity_filled>0 AND quantity_filled+1e-9<quantity THEN COALESCE(order_hash,order_id) END) partial_fill_orders,
                          MIN(source_timestamp_ms) first_source_ms,
                          MAX(source_timestamp_ms) latest_source_ms,
                          AVG(CASE WHEN source_receive_latency_ms BETWEEN -60000 AND 60000 THEN source_receive_latency_ms END) mean_source_receive_latency_ms
                     FROM own_wallet_lifecycle_events_v1"""
            ).fetchone()
        )
        session = dict(
            connection.execute(
                """SELECT COUNT(*) sessions,
                          COALESCE(SUM(subscribed_at_ms IS NOT NULL),0) subscribed_sessions,
                          MAX(subscribed_at_ms) latest_subscribed_ms,
                          MAX(disconnected_at_ms) latest_disconnected_ms
                     FROM own_wallet_lifecycle_sessions_v1"""
            ).fetchone()
        )
        rest = dict(
            connection.execute(
                """SELECT COUNT(*) snapshot_runs,
                          COALESCE(SUM(rest_status='OK'),0) successful_snapshot_runs,
                          COALESCE(SUM(open_order_count),0) open_order_observations,
                          MAX(observed_at_ms) latest_snapshot_ms
                     FROM own_wallet_open_snapshot_runs_v1"""
            ).fetchone()
        )
    finally:
        connection.close()
    gate = contract["calibrationReadiness"]
    support_checks = {
        "markets": int(totals["markets"] or 0) >= int(gate["minimumIndependentMarkets"]),
        "acceptedOrders": int(totals["accepted_orders"] or 0) >= int(gate["minimumAcceptedOrders"]),
        "filledOrders": int(totals["filled_orders"] or 0) >= int(gate["minimumFilledOrders"]),
        "cancelledExpiredOrRejectedOrders": int(totals["no_fill_terminal_orders"] or 0)
        >= int(gate["minimumCancelledExpiredOrRejectedOrders"]),
        "partialFillOrders": int(totals["partial_fill_orders"] or 0) >= int(gate["minimumPartialFillOrders"]),
    }
    report = {
        "reportVersion": "PREDICT_OWN_WALLET_LIFECYCLE_V1_COLLECTION_REPORT",
        "researchOnly": True,
        "readOnly": True,
        "database": str(path),
        "contract": str(CONTRACT.resolve()),
        "sessions": session,
        "restOpenBootstrap": rest,
        "events": totals,
        "eventTypes": event_types,
        "supportChecks": support_checks,
        "calibrationReady": all(support_checks.values()),
        "remainingMandatoryJoins": [
            "local submit-request timestamp by orderHash/orderId",
            "local cancel-request timestamp by orderHash/orderId",
            "Predict Execution Tape V1 COMPLETE_FORWARD interval and raw maker-hash matches",
            "terminal lifecycle coverage audit including reconnect gaps",
        ],
        "decision": "READY_FREEZE_CALIBRATION_GATE" if all(support_checks.values()) else "KEEP_COLLECTING",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
