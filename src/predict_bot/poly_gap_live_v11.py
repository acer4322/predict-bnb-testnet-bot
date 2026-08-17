from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v10 import (
    POSITION_EPSILON,
    ExitOrderReconciledPolyGapLiveEngine,
    _actual_exit_proceeds,
)


class LegacyAwareExitPolyGapLiveEngine(ExitOrderReconciledPolyGapLiveEngine):
    """V11: add legacy correction and full-position safeguards on top of V10.

    V10 fixes the live lifecycle by reconciling SELL order status before any
    duplicate SELL or official settlement. V11 also repairs recent historical
    rows created by the old lifecycle when Binance order history can establish
    that a SELL actually filled, and refuses to finalize a filled SELL as flat
    unless the submitted SELL amount covered essentially the full recorded
    position.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._legacy_exit_reconcile_done = False
        self.legacy_exit_reconcile_summary: dict[str, Any] = {
            "scanned": 0,
            "correctedFilled": 0,
            "confirmedNoFill": 0,
            "unresolved": 0,
            "lastCorrectedRoundIds": [],
        }
        self._reserved_share_warning_rounds: set[int] = set()

    @staticmethod
    def _submitted_exit_shares(row: dict[str, Any]) -> float | None:
        value = row.get("exit_quote_amount_in_wei")
        if value in (None, ""):
            return None
        return base._from_wei(value)

    @classmethod
    def _exit_covers_recorded_position(cls, row: dict[str, Any]) -> bool:
        submitted = cls._submitted_exit_shares(row)
        recorded = base._finite(row.get("shares"))
        if submitted is None or recorded is None or recorded <= POSITION_EPSILON:
            return True
        tolerance = max(1e-6, recorded * 0.01)
        return submitted + tolerance >= recorded

    def _finalize_reconciled_exit(
        self,
        row: dict[str, Any],
        *,
        reason: str,
        order: dict[str, Any] | None = None,
    ) -> None:
        if "ORDER_FILLED" in reason and not self._exit_covers_recorded_position(row):
            submitted = self._submitted_exit_shares(row)
            recorded = base._finite(row.get("shares"))
            message = (
                "SELL order reports filled but submitted quantity did not cover the "
                f"recorded position; submittedShares={submitted}; recordedShares={recorded}"
            )
            self._halt_market(int(row["market_id"]), message, int(row["id"]))
            self._update_round(
                int(row["id"]),
                state="AMBIGUOUS",
                error_kind="EXIT_FILLED_PARTIAL_POSITION_MISMATCH",
                error_message=message,
            )
            return
        super()._finalize_reconciled_exit(row, reason=reason, order=order)

    def _exit_round(self, row: dict[str, Any], signal_ms: int) -> None:
        if not str(row.get("exit_order_id") or "").strip():
            try:
                position = self._position_state(str(row["token_id"]))
            except Exception as exc:
                self.last_error = f"exit position read: {str(exc)[:300]}"
                return
            total = base._finite(position.get("totalShares"))
            available = base._finite(position.get("availableShares"))
            explicit_total = bool(position.get("totalSharesExplicit"))
            if (
                explicit_total
                and total is not None
                and total > POSITION_EPSILON
                and available is not None
                and available + POSITION_EPSILON < total
            ):
                self.status = "EXIT_BLOCKED_RESERVED_SHARES"
                self.last_error = (
                    f"cannot submit a fresh SELL while only {available:.8f} of "
                    f"{total:.8f} shares are available; another order may reserve shares"
                )
                round_id = int(row["id"])
                if round_id not in self._reserved_share_warning_rounds:
                    self._reserved_share_warning_rounds.add(round_id)
                    self._event(
                        "WARN",
                        "EXIT_RESERVED_SHARES_BLOCKED",
                        int(row["market_id"]),
                        round_id,
                        self.last_error,
                    )
                return
        super()._exit_round(row, signal_ms)

    def _ensure_clients(self) -> bool:
        ok = super()._ensure_clients()
        if ok and not self._legacy_exit_reconcile_done:
            self._legacy_exit_reconcile_done = True
            try:
                self._reconcile_legacy_settled_exits()
            except Exception as exc:
                self.last_error = f"legacy exit reconciliation: {str(exc)[:300]}"
        return ok

    def _reconcile_legacy_settled_exits(self) -> None:
        with self.db_lock:
            rows = [
                dict(row)
                for row in self.db.execute(
                    """SELECT * FROM poly_gap_live_rounds
                        WHERE state='SETTLED'
                          AND exit_order_id IS NOT NULL
                          AND TRIM(exit_order_id)<>''
                        ORDER BY id DESC LIMIT 50"""
                ).fetchall()
            ]

        corrected: list[int] = []
        confirmed_no_fill = 0
        unresolved = 0

        for row in rows:
            reconciliation = self._reconcile_exit_order(row)
            classification = str(reconciliation.get("classification") or "UNKNOWN")
            if classification == "FILLED" and self._exit_covers_recorded_position(row):
                proceeds = _actual_exit_proceeds(reconciliation.get("order"))
                if proceeds is None:
                    proceeds = base._finite(row.get("exit_proceeds_usdt"))
                if proceeds is None:
                    unresolved += 1
                    continue
                cost = base._finite(row.get("entry_cost_usdt")) or float(row["stake_usdt"])
                pnl = proceeds - cost
                self._update_round(
                    int(row["id"]),
                    state="CLOSED",
                    exit_proceeds_usdt=proceeds,
                    pnl_usdt=pnl,
                    close_reason="LEGACY_EXIT_ORDER_FILLED_RECONCILED",
                    error_kind=None,
                    error_message=None,
                )
                corrected.append(int(row["id"]))
                self._event(
                    "WARN",
                    "LEGACY_SETTLEMENT_CORRECTED_FROM_EXIT_HISTORY",
                    int(row["market_id"]),
                    int(row["id"]),
                    (
                        f"historical SETTLED row corrected because SELL order "
                        f"{row['exit_order_id']} is FILLED; corrected PnL={pnl:+.6f} USDT"
                    ),
                )
            elif classification == "NO_FILL":
                confirmed_no_fill += 1
            else:
                unresolved += 1

        if corrected:
            self._check_max_loss()

        self.legacy_exit_reconcile_summary = {
            "scanned": len(rows),
            "correctedFilled": len(corrected),
            "confirmedNoFill": confirmed_no_fill,
            "unresolved": unresolved,
            "lastCorrectedRoundIds": corrected[:20],
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V11"
        payload["legacyExitReconciliation"] = {
            **self.legacy_exit_reconcile_summary,
            "runsOnceAfterLiveClientReady": True,
            "onlyExplicitFilledMutatesHistoricalPnl": True,
            "requiresFullPositionSizedExit": True,
        }
        return payload


base.PolyGapLiveEngine = LegacyAwareExitPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
