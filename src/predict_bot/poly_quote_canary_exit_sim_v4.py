from __future__ import annotations

import json
from typing import Any

from .poly_quote_canary import _finite
from .poly_quote_canary_exit_sim_v3 import DurableExitSimulatedPolyQuoteCanary


class SafeDurableExitSimulatedPolyQuoteCanary(DurableExitSimulatedPolyQuoteCanary):
    """V4: migrate only structurally invalid Paper SELL quote rejects.

    Historical EXIT rows that successfully obtained a signed SELL quote are kept
    untouched for audit. Only the known Paper-position failure (-9000 / available
    shares) is converted into the new best/worst exit scenario model.
    """

    @staticmethod
    def _is_invalid_paper_sell_reject(row: dict[str, Any]) -> bool:
        if str(row.get("status") or "") != "QUOTE_REJECTED":
            return False
        reason = str(row.get("reason") or "").lower()
        return (
            "-9000" in reason
            or "exceeded your available shares" in reason
            or "available shares" in reason
        )

    def _migrate_legacy_exit_rows(self) -> None:
        with self.db_lock:
            rows = [
                dict(row)
                for row in self.db.execute(
                    """SELECT * FROM poly_quote_canary_attempts
                        WHERE phase='EXIT'
                          AND (execution_mode IS NULL OR execution_mode='')
                        ORDER BY id ASC"""
                ).fetchall()
            ]
        for row in rows:
            if not self._is_invalid_paper_sell_reject(row):
                continue
            source = self._source_trade(int(row["trade_id"]))
            if not source:
                continue
            exit_price = _finite(source.get("exit_price"))
            closed_at_ms = source.get("closed_at_ms")
            if exit_price is None or not (0 < exit_price <= 1) or closed_at_ms is None:
                continue
            event = {
                "trade_id": int(row["trade_id"]),
                "strategy": str(row["strategy"]),
                "phase": "EXIT",
                "market_id": int(row["binance_market_id"]),
                "side": str(row["side"]),
                "signal_at_ms": int(closed_at_ms),
                "queued_at_ms": int(closed_at_ms),
                "paper_price": float(exit_price),
                "stake_usdt": float(source.get("stake_usdt") or row.get("paper_stake_usdt") or 0.0),
                "shares": float(source.get("shares") or row.get("paper_shares") or 0.0),
                "poly_up_mid": _finite(source.get("entry_poly_up_mid")),
                "signal_binance_up_mid": _finite(source.get("entry_binance_up_mid")),
                "poly_selected_mid": None,
            }
            try:
                metadata = json.loads(str(source.get("metadata_json") or "{}"))
                if isinstance(metadata, dict):
                    event["poly_selected_mid"] = _finite(metadata.get("polySelectedMid"))
            except json.JSONDecodeError:
                pass
            self._process_exit_simulation(event, migrated=True)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_ENTRY_REAL_QUOTE_EXIT_SCENARIOS_V4"
        rules = payload.get("exitSimulationRules")
        if isinstance(rules, dict):
            rules["legacyMigrationScope"] = (
                "only EXIT QUOTE_REJECTED rows containing Binance -9000 / available-shares errors"
            )
        return payload
