from __future__ import annotations

import time
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v4 import ImmediateRiskPolyGapLiveEngine


class OperationalMetricsPolyGapLiveEngine(ImmediateRiskPolyGapLiveEngine):
    """V5: expose live status, last error, and entry execution quality separately."""

    def _entry_execution_metrics(self) -> dict[str, Any]:
        with self.db_lock:
            totals = self.db.execute(
                """SELECT
                       COUNT(*) AS attempts,
                       COALESCE(SUM(CASE WHEN entry_order_id IS NOT NULL THEN 1 ELSE 0 END),0) AS order_ids,
                       COALESCE(SUM(CASE WHEN error_kind='ENTRY_QUOTE_REJECTED' OR close_reason='ENTRY_QUOTE_REJECTED' THEN 1 ELSE 0 END),0) AS quote_rejected,
                       COALESCE(SUM(CASE WHEN close_reason='SIGNED_QUOTE_EDGE_GONE' THEN 1 ELSE 0 END),0) AS edge_gone,
                       COALESCE(SUM(CASE WHEN error_kind='ENTRY_PLACE_REJECTED' OR close_reason='ENTRY_PLACE_REJECTED' THEN 1 ELSE 0 END),0) AS place_rejected,
                       COALESCE(SUM(CASE WHEN error_kind='ENTRY_PLACE_AMBIGUOUS' OR error_kind='ENTRY_POSITION_UNCONFIRMED' THEN 1 ELSE 0 END),0) AS ambiguous
                   FROM poly_gap_live_rounds"""
            ).fetchone()
            placed = self.db.execute(
                """SELECT COUNT(DISTINCT round_id) AS n
                     FROM poly_gap_live_events
                    WHERE event_type='ENTRY_PLACED' AND round_id IS NOT NULL"""
            ).fetchone()
            confirmed = self.db.execute(
                """SELECT COUNT(DISTINCT round_id) AS n
                     FROM poly_gap_live_events
                    WHERE event_type='ENTRY_CONFIRMED' AND round_id IS NOT NULL"""
            ).fetchone()
        attempts = int(totals["attempts"] if totals else 0)
        submitted = int(placed["n"] if placed else 0)
        succeeded = int(confirmed["n"] if confirmed else 0)
        return {
            "attempts": attempts,
            "submitted": submitted,
            "confirmed": succeeded,
            "successRate": succeeded / attempts if attempts else None,
            "quoteRejected": int(totals["quote_rejected"] if totals else 0),
            "edgeGoneAfterQuote": int(totals["edge_gone"] if totals else 0),
            "placeRejected": int(totals["place_rejected"] if totals else 0),
            "ambiguous": int(totals["ambiguous"] if totals else 0),
            "orderIdRows": int(totals["order_ids"] if totals else 0),
            "definition": "ENTRY_CONFIRMED / round entry attempts",
        }

    def _last_error_detail(self) -> dict[str, Any] | None:
        with self.db_lock:
            round_error = self.db.execute(
                """SELECT updated_at_ms, market_id, round_no, state,
                          error_kind, error_message, close_reason
                     FROM poly_gap_live_rounds
                    WHERE error_message IS NOT NULL AND error_message<>''
                    ORDER BY updated_at_ms DESC, id DESC LIMIT 1"""
            ).fetchone()
            event_error = self.db.execute(
                """SELECT at_ms, event_type, market_id, round_id, message
                     FROM poly_gap_live_events
                    WHERE UPPER(level)='ERROR'
                    ORDER BY at_ms DESC, id DESC LIMIT 1"""
            ).fetchone()

        candidates: list[dict[str, Any]] = []
        if round_error is not None:
            candidates.append(
                {
                    "atMs": int(round_error["updated_at_ms"] or 0),
                    "source": "ROUND",
                    "kind": str(round_error["error_kind"] or round_error["close_reason"] or "ROUND_ERROR"),
                    "message": str(round_error["error_message"] or ""),
                    "marketId": int(round_error["market_id"]),
                    "roundNo": int(round_error["round_no"]),
                    "state": str(round_error["state"] or ""),
                }
            )
        if event_error is not None:
            candidates.append(
                {
                    "atMs": int(event_error["at_ms"] or 0),
                    "source": "EVENT",
                    "kind": str(event_error["event_type"] or "ERROR"),
                    "message": str(event_error["message"] or ""),
                    "marketId": int(event_error["market_id"]) if event_error["market_id"] is not None else None,
                    "roundId": int(event_error["round_id"]) if event_error["round_id"] is not None else None,
                }
            )
        if candidates:
            return max(candidates, key=lambda item: int(item.get("atMs") or 0))
        if self.last_error:
            return {
                "atMs": None,
                "source": "RUNTIME",
                "kind": "RUNTIME_ERROR",
                "message": str(self.last_error),
                "marketId": None,
            }
        return None

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V5"
        payload["currentStatus"] = {
            "status": str(payload.get("status") or "UNKNOWN"),
            "runtimeEnabled": bool((payload.get("settings") or {}).get("runtimeEnabled")),
            "masterEnabled": bool(payload.get("masterEnabled")),
            "marketId": (payload.get("market") or {}).get("market_id"),
            "activeRoundId": (payload.get("activeRound") or {}).get("id"),
            "activeRoundNo": (payload.get("activeRound") or {}).get("round_no"),
            "activeRoundState": (payload.get("activeRound") or {}).get("state"),
            "generatedAtMs": int(time.time() * 1000),
        }
        payload["lastErrorDetail"] = self._last_error_detail()
        payload["entryExecution"] = self._entry_execution_metrics()
        return payload


base.PolyGapLiveEngine = OperationalMetricsPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
