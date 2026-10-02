from __future__ import annotations

import os
import threading
import time
from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v5 as v5
from . import echtgeld_engine_v6 as v6
from . import echtgeld_engine_v12 as v12
from . import echtgeld_engine_v17 as v17
from . import poly_gap_live as poly_base
from .core import ApiTransportError

VERSION = "ECHTGELD_ENGINE_V2_4310_POLY_V15_RESTORED_ROUND_STATE_MACHINE"
HOST = v17.HOST
PORT = v17.PORT
EXIT_SYNC_INTERVAL_SECONDS = 0.50
EXIT_SYNC_TIMEOUT_MS = int(poly_base.POSITION_SYNC_TIMEOUT_MS)
EMPTY_POSITION_CONFIRMATIONS_REQUIRED = 3


class EchtgeldEngine(v17.EchtgeldEngine):
    """V17 with the proven 4310 Poly round transitions restored.

    The original dedicated Poly live engine treated only ENTRY_QUOTE, ENTRY_SYNC,
    OPEN, EXIT_QUOTE and EXIT_SYNC as active.  REJECTED/FAILED entries were
    terminal and therefore never blocked a later same-market round.  A successful
    SELL entered EXIT_SYNC; if a fresh position still existed after the bounded
    sync window, the round returned to OPEN so a later risk-reducing exit could
    sell the *fresh remaining position*.  Only transport ambiguity halted/retried
    nothing blindly.

    This adapter keeps today's 8792 -> 8781 ownership split and today's entry/
    stake/slippage rules, but restores those round-state semantics.  8792 remains
    signal-only and 8781 remains the only venue writer.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._poly_4310_exit_submitted_mono: dict[str, float] = {}
        self._poly_4310_empty_position_confirmations: dict[str, int] = {}
        self._poly_4310_exit_sync_stop = threading.Event()
        super().__init__(*args, **kwargs)
        self._poly_4310_exit_sync_thread = threading.Thread(
            target=self._poly_4310_exit_sync_loop,
            name="echtgeld-poly-4310-exit-sync",
            daemon=True,
        )
        self._poly_4310_exit_sync_thread.start()

    # ------------------------------------------------------------------
    # ENTRY: use the same active-round truth as /poly-lifecycle.
    # ------------------------------------------------------------------
    def submit_poly_fast_intent(self, raw: dict[str, Any]) -> dict[str, Any]:
        round_id = str(raw.get("roundId") or "").strip()
        token_id = str(raw.get("tokenId") or "").strip()
        asset = str(raw.get("asset") or "").strip().upper()
        side = str(raw.get("side") or "").strip().upper()
        market_id = int(v1._finite(raw.get("marketId")) or 0)
        created_at_ms = int(v1._finite(raw.get("createdAtMs")) or 0)
        fee_rate_bps = int(v1._finite(raw.get("feeRateBps")) or 200)

        if asset == "BNB":
            return {
                "ok": True,
                "accepted": False,
                "queued": False,
                "status": "ASSET_DISABLED",
                "asset": asset,
                "message": "BNB new Poly entries are temporarily disabled in 8781; existing BNB exits remain allowed",
            }
        if asset not in {"BTC", "ETH"}:
            raise v1.EchtgeldEngineError("Poly Fast asset must be BTC or ETH for new entry")
        if side not in {"UP", "DOWN"}:
            raise v1.EchtgeldEngineError("Poly Fast side must be UP or DOWN")
        if not round_id or not token_id or market_id <= 0 or created_at_ms <= 0:
            raise v1.EchtgeldEngineError("Poly Fast roundId, tokenId, marketId and createdAtMs are required")

        # Critical 4310 behavior: there is one active round per asset, but a
        # REJECTED/FAILED terminal round is not active and must not fence a later
        # same-market attempt.  Reuse the exact lifecycle projection so admission
        # and diagnostics cannot disagree about what is active.
        active = self._poly_active_round(asset)
        if active is not None and str(active.get("round_id") or "") != round_id:
            return {
                "ok": True,
                "accepted": False,
                "queued": False,
                "status": "ACTIVE_ROUND_EXISTS",
                "message": "Poly Fast has a genuinely active/potential round; wait for confirmed flat before re-entry",
                "activeRoundId": str(active.get("round_id") or ""),
                "activePhase": active.get("phase"),
            }

        intent_id = str(raw.get("intentId") or "").strip()
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO engine_poly_rounds(
                       entry_intent_id,round_id,asset,market_id,side,token_id,fee_rate_bps,created_at_ms
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (intent_id, round_id, asset, market_id, side, token_id, fee_rate_bps, created_at_ms),
            )
            self.db.commit()

        # Bypass V6's second, inconsistent same-market SQL fence.  V5/V4 retain
        # all mature 8781 pause, stop-loss, balance, durable intent/order fences,
        # signed MARKET/FOK execution and Binance reconciliation.
        result = v5.EchtgeldEngine.submit_poly_fast_intent(self, raw)
        result["roundId"] = round_id
        result["poly4310RoundStateMachine"] = True
        return result

    # ------------------------------------------------------------------
    # EXIT: restore OPEN -> SUBMITTED(sync) -> FLAT, or OPEN retry.
    # ------------------------------------------------------------------
    def _set_exit_open_for_fresh_retry(self, round_id: str, message: str) -> None:
        with self.db_lock:
            self.db.execute(
                """UPDATE engine_poly_rounds
                      SET exit_status=NULL,exit_completed_at_ms=NULL,exit_error=?
                    WHERE round_id=? AND exit_status='SUBMITTED'""",
                (str(message)[:500], round_id),
            )
            self.db.commit()
        self._poly_4310_exit_submitted_mono.pop(round_id, None)
        self._poly_4310_empty_position_confirmations.pop(round_id, None)

    def _exit_history_order(self, order_id: str) -> dict[str, Any] | None:
        if not order_id:
            return None
        try:
            client, wallet = self._poly_ensure_client()
            payload = client.order_history(wallet["walletAddress"], limit=100)
            orders = v12.EchtgeldEngine._history_order_map(payload if isinstance(payload, dict) else {})
            row = orders.get(order_id)
            return dict(row) if isinstance(row, dict) else None
        except Exception:
            return None

    def _mark_exit_flat_4310(
        self,
        row: dict[str, Any],
        *,
        order: dict[str, Any] | None = None,
        position_shape: str,
    ) -> bool:
        round_id = str(row.get("round_id") or "").strip()
        if not round_id:
            return False
        filled_usdt = v12._num((order or {}).get("filledUsdtAmount"))
        filled_shares = v12._num((order or {}).get("filledShareQty"))
        fill_pct = v12._num((order or {}).get("fillPercentage"))
        realized_pnl = v12._num((order or {}).get("realizedPnl"))
        exchange_status = str((order or {}).get("status") or "").upper()
        proceeds = (
            filled_usdt
            if filled_usdt is not None and filled_usdt >= 0
            else v12._num(row.get("exit_proceeds_usdt"))
        )
        now_ms = v1._now_ms()
        with self.db_lock:
            cur = self.db.execute(
                """UPDATE engine_poly_rounds
                      SET exit_status='FLAT',exit_proceeds_usdt=COALESCE(?,exit_proceeds_usdt),
                          exit_completed_at_ms=?,exit_error=NULL
                    WHERE round_id=? AND exit_status='SUBMITTED'""",
                (proceeds, now_ms, round_id),
            )
            self.db.commit()
        if cur.rowcount != 1:
            return False

        self.history_sync_exit_flats += 1
        self.history_sync_last_result = {
            "kind": "EXIT_CONFIRMED_FLAT_4310_STATE_MACHINE",
            "roundId": round_id,
            "asset": row.get("asset"),
            "marketId": row.get("market_id"),
            "orderId": row.get("exit_order_id"),
            "exchangeStatus": exchange_status or None,
            "filledUsdtAmount": filled_usdt,
            "filledShareQty": filled_shares,
            "fillPercentage": fill_pct,
            "realizedPnl": realized_pnl,
            "exitProceedsUsdt": proceeds,
            "positionResponseShape": position_shape,
            "readOnlyReconciliation": True,
            "duplicateSell": False,
        }
        self._poly_4310_exit_submitted_mono.pop(round_id, None)
        self._poly_4310_empty_position_confirmations.pop(round_id, None)
        return True

    def _sync_submitted_exit_4310(self, row: dict[str, Any]) -> None:
        round_id = str(row.get("round_id") or "").strip()
        token_id = str(row.get("token_id") or "").strip()
        if not round_id or not token_id:
            return

        started = self._poly_4310_exit_submitted_mono.setdefault(round_id, time.monotonic())
        try:
            client, wallet = self._poly_ensure_client()
            position = client.position_by_token(wallet["walletAddress"], token_id)
        except Exception as exc:
            self.history_sync_last_result = {
                "kind": "EXIT_4310_POSITION_READ_RETRY",
                "roundId": round_id,
                "asset": row.get("asset"),
                "marketId": row.get("market_id"),
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            }
            return

        shares = poly_base._first_number(
            position,
            ("availableShares", "availableShareQty", "shares", "shareQty", "quantity"),
        ) if isinstance(position, dict) else None
        order_id = str(row.get("exit_order_id") or "").strip()

        if shares is not None and shares <= 1e-9:
            order = self._exit_history_order(order_id)
            self._mark_exit_flat_4310(row, order=order, position_shape="SHARES_ZERO")
            return

        # Current Binance token-position may return HTTP-200 {} after a position
        # disappears.  Keep V17's conservative evidence rule: require the SELL
        # itself to be FILLED 100% and observe the empty position repeatedly.
        if isinstance(position, dict) and not position:
            order = self._exit_history_order(order_id)
            status = str((order or {}).get("status") or "").upper()
            fill_pct = v12._num((order or {}).get("fillPercentage"))
            filled_shares = v12._num((order or {}).get("filledShareQty"))
            fully_filled = bool(
                status == "FILLED"
                and fill_pct is not None
                and fill_pct >= 1.0 - 1e-9
                and filled_shares is not None
                and filled_shares > 0
            )
            if fully_filled:
                count = int(self._poly_4310_empty_position_confirmations.get(round_id, 0)) + 1
                self._poly_4310_empty_position_confirmations[round_id] = count
                self.history_sync_last_result = {
                    "kind": "EXIT_4310_EMPTY_POSITION_CONFIRMING",
                    "roundId": round_id,
                    "asset": row.get("asset"),
                    "marketId": row.get("market_id"),
                    "orderId": order_id,
                    "exchangeStatus": status,
                    "fillPercentage": fill_pct,
                    "filledShareQty": filled_shares,
                    "emptyPositionConfirmations": count,
                    "emptyPositionConfirmationsRequired": EMPTY_POSITION_CONFIRMATIONS_REQUIRED,
                }
                if count >= EMPTY_POSITION_CONFIRMATIONS_REQUIRED:
                    self._mark_exit_flat_4310(row, order=order, position_shape="EMPTY_OBJECT")
                return

            # Empty position without order-history proof is not enough evidence to
            # sell again.  Preserve fail-closed ambiguity rather than duplicate.
            if (time.monotonic() - started) * 1000.0 >= EXIT_SYNC_TIMEOUT_MS:
                with self.db_lock:
                    self.db.execute(
                        """UPDATE engine_poly_rounds
                              SET exit_status='AMBIGUOUS',exit_error=?
                            WHERE round_id=? AND exit_status='SUBMITTED'""",
                        ("SELL submitted but empty position response lacked FILLED order-history proof; no automatic duplicate sell", round_id),
                    )
                    self.db.commit()
                self._poly_4310_exit_submitted_mono.pop(round_id, None)
            return

        self._poly_4310_empty_position_confirmations.pop(round_id, None)
        elapsed_ms = (time.monotonic() - started) * 1000.0
        self.history_sync_last_result = {
            "kind": "EXIT_4310_SYNC_PENDING",
            "roundId": round_id,
            "asset": row.get("asset"),
            "marketId": row.get("market_id"),
            "orderId": order_id or None,
            "remainingShares": shares,
            "elapsedMs": elapsed_ms,
            "timeoutMs": EXIT_SYNC_TIMEOUT_MS,
        }

        if elapsed_ms < EXIT_SYNC_TIMEOUT_MS:
            return

        if shares is not None and shares > 1e-9:
            # Exact old-4310 behavior: a FOK cannot leave a resting order.  If a
            # fresh position still exists after the bounded sync period, return
            # the round to OPEN.  A later reversal/TP exit reads the *current*
            # position and quotes only that remaining amount.
            self._set_exit_open_for_fresh_retry(
                round_id,
                f"SELL FOK did not leave wallet flat after {EXIT_SYNC_TIMEOUT_MS}ms; retrying from a fresh position read",
            )
            self.history_sync_last_result = {
                "kind": "EXIT_4310_RETURNED_OPEN_FOR_FRESH_RETRY",
                "roundId": round_id,
                "asset": row.get("asset"),
                "marketId": row.get("market_id"),
                "remainingShares": shares,
                "timeoutMs": EXIT_SYNC_TIMEOUT_MS,
                "riskReducingOnly": True,
            }
            return

        # Non-empty but unparseable position is not safe evidence for a retry.
        with self.db_lock:
            self.db.execute(
                """UPDATE engine_poly_rounds
                      SET exit_status='AMBIGUOUS',exit_error=?
                    WHERE round_id=? AND exit_status='SUBMITTED'""",
                ("SELL submitted but token position could not be interpreted after bounded sync; no automatic duplicate sell", round_id),
            )
            self.db.commit()
        self._poly_4310_exit_submitted_mono.pop(round_id, None)

    def _poly_4310_exit_sync_once(self) -> None:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT r.*
                     FROM engine_poly_rounds r
                    WHERE r.exit_status='SUBMITTED'
                    ORDER BY r.created_at_ms ASC"""
            ).fetchall()
        for raw in rows:
            self._sync_submitted_exit_4310(dict(raw))

    def _poly_4310_exit_sync_loop(self) -> None:
        while not self._poly_4310_exit_sync_stop.wait(EXIT_SYNC_INTERVAL_SECONDS):
            try:
                self._poly_4310_exit_sync_once()
            except Exception as exc:
                self.history_sync_last_at_ms = v1._now_ms()
                self.history_sync_last_error = f"4310 exit sync: {type(exc).__name__}: {str(exc)[:400]}"

    def submit_poly_exit_intent(self, raw: dict[str, Any]) -> dict[str, Any]:
        if str(self.config.venue).strip().lower() != "binance":
            raise v1.EchtgeldEngineError("Poly Fast exit requires Echtgeld venue=binance")
        asset = str(raw.get("asset") or "").strip().upper()
        round_id = str(raw.get("roundId") or "").strip()
        exit_intent_id = str(raw.get("intentId") or "").strip()
        if asset not in {"BTC", "ETH", "BNB"} or not round_id or not exit_intent_id:
            raise v1.EchtgeldEngineError("Poly exit requires asset, roundId and intentId")

        active = self._poly_active_round(asset)
        if not active or str(active.get("round_id") or "") != round_id:
            return {"ok": True, "accepted": False, "status": "NO_MATCHING_ACTIVE_ROUND"}
        exit_status = str(active.get("exit_status") or "").upper()
        if exit_status in {"ATTEMPTING", "SUBMITTED", "AMBIGUOUS", "FLAT", "CLOSED"}:
            return {
                "ok": True,
                "accepted": False,
                "status": "EXIT_ALREADY_HANDLED",
                "roundId": round_id,
                "exitStatus": exit_status,
            }

        token_id = str(active.get("token_id") or "").strip()
        if not token_id:
            return {
                "ok": False,
                "accepted": False,
                "status": "TOKEN_REPAIR_REQUIRED",
                "roundId": round_id,
                "error": "persisted Poly round has no tokenId; no position/SELL request is allowed",
            }

        with self._poly_exit_lock:
            # Re-read under the serialized exit lock so two concurrent reversal/
            # TP requests cannot both reach the venue.
            active = self._poly_active_round(asset)
            if not active or str(active.get("round_id") or "") != round_id:
                return {"ok": True, "accepted": False, "status": "NO_MATCHING_ACTIVE_ROUND"}
            if str(active.get("exit_status") or "").upper() in {"ATTEMPTING", "SUBMITTED", "AMBIGUOUS", "FLAT", "CLOSED"}:
                return {"ok": True, "accepted": False, "status": "EXIT_ALREADY_HANDLED", "roundId": round_id}

            with self.db_lock:
                self.db.execute(
                    "UPDATE engine_poly_rounds SET exit_intent_id=?,exit_status='ATTEMPTING',exit_error=NULL WHERE round_id=?",
                    (exit_intent_id, round_id),
                )
                self.db.commit()

            try:
                client, wallet = self._poly_ensure_client()
                wallet_address = wallet["walletAddress"]
                wallet_id = wallet["walletId"]
                position = client.position_by_token(wallet_address, token_id)
                shares = poly_base._first_number(
                    position,
                    ("availableShares", "availableShareQty", "shares", "shareQty", "quantity"),
                )
                if shares is not None and shares <= 1e-9:
                    with self.db_lock:
                        self.db.execute(
                            "UPDATE engine_poly_rounds SET exit_status='FLAT',exit_completed_at_ms=?,exit_error=NULL WHERE round_id=?",
                            (v1._now_ms(), round_id),
                        )
                        self.db.commit()
                    return {"ok": True, "accepted": True, "status": "FLAT", "roundId": round_id}
                if shares is None or shares <= 0:
                    with self.db_lock:
                        self.db.execute(
                            "UPDATE engine_poly_rounds SET exit_status=NULL,exit_error=? WHERE round_id=?",
                            ("Poly exit could not read a positive available position; round returned OPEN without venue write", round_id),
                        )
                        self.db.commit()
                    return {"ok": False, "accepted": False, "status": "POSITION_UNAVAILABLE", "roundId": round_id}

                try:
                    quote = client.get_quote(
                        wallet_address=wallet_address,
                        token_id=token_id,
                        amount_in_wei=poly_base._to_wei(float(shares)),
                        price_limit=None,
                        slippage_bps=v6.EXIT_SLIPPAGE_BPS,
                        fee_rate_bps=int(active.get("fee_rate_bps") or 200),
                        funding_source="MPC",
                        side="SELL",
                        order_type="MARKET",
                    )
                except Exception as exc:
                    with self.db_lock:
                        self.db.execute(
                            "UPDATE engine_poly_rounds SET exit_status=NULL,exit_error=? WHERE round_id=?",
                            (f"EXIT_QUOTE_REJECTED: {type(exc).__name__}: {str(exc)[:350]}", round_id),
                        )
                        self.db.commit()
                    return {"ok": False, "accepted": False, "status": "EXIT_QUOTE_REJECTED", "roundId": round_id, "error": str(exc)[:350]}

                quote_id = str(quote.get("quoteId") or "").strip()
                proceeds = poly_base._from_wei(quote.get("amountOut"))
                if not quote_id or proceeds is None or proceeds < 0:
                    with self.db_lock:
                        self.db.execute(
                            "UPDATE engine_poly_rounds SET exit_status=NULL,exit_error=? WHERE round_id=?",
                            ("EXIT_QUOTE_INVALID: SELL quote lacked executable quoteId/proceeds", round_id),
                        )
                        self.db.commit()
                    return {"ok": False, "accepted": False, "status": "EXIT_QUOTE_INVALID", "roundId": round_id}

                try:
                    placed = client.place_market_order(
                        wallet_address=wallet_address,
                        wallet_id=wallet_id,
                        quote_id=quote_id,
                        slippage_bps=v6.EXIT_SLIPPAGE_BPS,
                        account_type=str(os.environ.get("PREDICT_TARGET_TAKER_BINANCE_ACCOUNT_TYPE") or "SPOT").strip().upper(),
                        funding_source="MPC",
                    )
                except ApiTransportError as exc:
                    with self.db_lock:
                        self.db.execute(
                            "UPDATE engine_poly_rounds SET exit_status='AMBIGUOUS',exit_error=? WHERE round_id=?",
                            (f"{type(exc).__name__}: {str(exc)[:400]}", round_id),
                        )
                        self.db.commit()
                    return {
                        "ok": False,
                        "accepted": True,
                        "status": "AMBIGUOUS",
                        "roundId": round_id,
                        "error": str(exc)[:400],
                        "automaticRetry": False,
                    }
                except Exception as exc:
                    # Old 4310 semantics: a known placement rejection with no
                    # ambiguous transport goes back OPEN; a later risk-reducing
                    # exit may quote the fresh remaining position again.
                    with self.db_lock:
                        self.db.execute(
                            "UPDATE engine_poly_rounds SET exit_status=NULL,exit_error=? WHERE round_id=?",
                            (f"EXIT_PLACE_REJECTED: {type(exc).__name__}: {str(exc)[:350]}", round_id),
                        )
                        self.db.commit()
                    return {"ok": False, "accepted": False, "status": "EXIT_PLACE_REJECTED", "roundId": round_id, "error": str(exc)[:350]}

                order_id = poly_base._first_text(placed, ("orderId", "order_id", "id"))
                with self.db_lock:
                    self.db.execute(
                        """UPDATE engine_poly_rounds
                              SET exit_status='SUBMITTED',exit_order_id=?,exit_proceeds_usdt=?,
                                  exit_completed_at_ms=NULL,exit_error=NULL
                            WHERE round_id=?""",
                        (order_id, proceeds, round_id),
                    )
                    self.db.commit()
                self._poly_4310_exit_submitted_mono[round_id] = time.monotonic()
                self._poly_4310_empty_position_confirmations.pop(round_id, None)
                return {
                    "ok": True,
                    "accepted": True,
                    "status": "SUBMITTED",
                    "roundId": round_id,
                    "vendorOrderId": order_id,
                    "exitProceedsUsdt": proceeds,
                    "positionSync": "OLD_4310_BACKGROUND_500MS",
                    "positionSyncTimeoutMs": EXIT_SYNC_TIMEOUT_MS,
                    "automaticDuplicateSell": False,
                }

            except Exception as exc:
                # No placement ambiguity escaped the explicit branch above.
                # Return OPEN so the next risk-reducing signal can use a fresh
                # position read; no venue write is performed by this handler.
                with self.db_lock:
                    self.db.execute(
                        "UPDATE engine_poly_rounds SET exit_status=NULL,exit_error=? WHERE round_id=? AND exit_status='ATTEMPTING'",
                        (f"{type(exc).__name__}: {str(exc)[:400]}", round_id),
                    )
                    self.db.commit()
                raise

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        gateway = payload.get("polyFastGateway")
        if isinstance(gateway, dict):
            gateway.update(
                roundStateAuthority="POLY_LIFECYCLE_SINGLE_SOURCE",
                sameMarketMultipleRounds=True,
                rejectedEntryIsTerminal=True,
                failedEntryIsTerminal=True,
                activeRoundAdmissionUsesLifecycleProjection=True,
                exitStateMachine="OPEN_TO_SUBMITTED_SYNC_TO_FLAT_OR_OPEN_RETRY",
                exitSyncIntervalMs=int(EXIT_SYNC_INTERVAL_SECONDS * 1000),
                exitSyncTimeoutMs=EXIT_SYNC_TIMEOUT_MS,
                exitRetryUsesFreshRemainingPosition=True,
                ambiguousOnlyOnUncertainVenueWrite=True,
            )
        payload["poly4310RoundStateMachineV18"] = {
            "enabled": True,
            "source": "feature/echtgeld-4310-balance-pnl-20260816:poly_gap_live.py",
            "entryRejectedTerminal": True,
            "entryFailedTerminal": True,
            "sameMarketReentryAfterTerminalReject": True,
            "oneActiveRoundPerAsset": True,
            "exitSubmittedBackgroundSync": True,
            "exitReturnsOpenIfSharesRemainAfterTimeout": True,
            "freshRemainingPositionBeforeRetry": True,
            "transportAmbiguityNeverBlindlyRetried": True,
            "venueOwner": "8781_ONLY",
        }
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["poly4310RoundStateMachineRestored"] = True
        payload["polyRejectedEntryTerminal"] = True
        payload["polyExitFreshPositionRetry"] = True
        payload["polyExitSyncTimeoutMs"] = EXIT_SYNC_TIMEOUT_MS
        return payload

    def close(self) -> None:
        self._poly_4310_exit_sync_stop.set()
        if getattr(self, "_poly_4310_exit_sync_thread", None) is not None:
            self._poly_4310_exit_sync_thread.join(timeout=2.0)
        super().close()


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV18Handler", (v6._Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; "
        "4310 Poly round state machine restored; rejected entry terminal; "
        "SELL submitted -> 500ms position sync -> FLAT or OPEN fresh-position retry",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
