from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v8 as v8

VERSION = "ECHTGELD_ENGINE_V2_4310_POLY_LIFECYCLE_REDEEM_PNL_STOP_LOSS_V6_SETTLEMENT_REPAIR"
HOST = v8.HOST
PORT = v8.PORT
POLY_ORDER_STATUSES = {"SUBMITTED", "AMBIGUOUS"}


class EchtgeldEngine(v8.EchtgeldEngine):
    """Repair legacy Poly migration and close rounds from Binance claim settlement.

    engine_redeems rows only exist after Binance exposed a tracked position as
    PENDING_CLAIM + canClaim=true.  Therefore claimable_value_usdt is settlement
    evidence even if the later redeem transaction itself becomes AMBIGUOUS.
    Redeem ambiguity must never reopen/retry the original BUY and must not keep a
    settled market permanently active.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.legacy_poly_rows_repaired = self._repair_legacy_poly_rows()
        self.poly_settlement_rounds_closed = self._sync_poly_claim_settlements()

    @staticmethod
    def _asset_from_intent(row: dict[str, Any], payload: dict[str, Any]) -> str:
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
        asset = str(payload.get("asset") or snapshot.get("asset") or "").strip().upper()
        if asset not in {"BTC", "ETH", "BNB"}:
            parts = str(row.get("intent_id") or "").split(":")
            asset = parts[1].upper() if len(parts) > 1 else ""
        return asset

    def _repair_legacy_poly_rows(self) -> int:
        repaired = 0
        with self.db_lock:
            # V8 migrated every historic poly-fast intent, including PAUSED and
            # DUPLICATE_FENCE rows.  Those were never positions and must never
            # participate in the one-active-round lifecycle.
            self.db.execute(
                """DELETE FROM engine_poly_rounds
                     WHERE round_id LIKE 'legacy:%'
                       AND entry_intent_id NOT IN (
                           SELECT intent_id FROM engine_orders
                            WHERE venue='binance' AND status IN ('SUBMITTED','AMBIGUOUS')
                       )"""
            )

            rows = self.db.execute(
                """SELECT i.*,o.status AS order_status,o.result_json
                     FROM engine_intents i
                     JOIN engine_orders o ON o.intent_id=i.intent_id
                    WHERE i.intent_id LIKE 'poly-fast:%'
                      AND o.venue='binance'
                      AND o.status IN ('SUBMITTED','AMBIGUOUS')
                    ORDER BY i.created_at_ms ASC"""
            ).fetchall()
            for raw in rows:
                row = dict(raw)
                try:
                    payload = json.loads(str(row.get("payload_json") or "{}"))
                except Exception:
                    payload = {}
                asset = self._asset_from_intent(row, payload)
                market_id = int(row.get("market_id") or 0)
                side = str(row.get("side") or "").upper()
                if asset not in {"BTC", "ETH", "BNB"} or market_id <= 0 or side not in {"UP", "DOWN"}:
                    continue
                redeem = self.db.execute(
                    """SELECT token_id,claimable_value_usdt,discovered_at_ms,completed_at_ms
                         FROM engine_redeems
                        WHERE venue_market_id=? AND side=?
                        ORDER BY discovered_at_ms DESC LIMIT 1""",
                    (market_id, side),
                ).fetchone()
                token_id = str(redeem["token_id"] or "") if redeem is not None else ""
                intent_id = str(row.get("intent_id") or "")
                round_id = f"legacy:{intent_id}"
                existing = self.db.execute(
                    "SELECT entry_intent_id FROM engine_poly_rounds WHERE entry_intent_id=?",
                    (intent_id,),
                ).fetchone()
                if existing is None:
                    self.db.execute(
                        """INSERT INTO engine_poly_rounds(
                               entry_intent_id,round_id,asset,market_id,side,token_id,fee_rate_bps,created_at_ms
                           ) VALUES(?,?,?,?,?,?,200,?)""",
                        (intent_id, round_id, asset, market_id, side, token_id, int(row.get("created_at_ms") or v1._now_ms())),
                    )
                else:
                    self.db.execute(
                        "UPDATE engine_poly_rounds SET asset=?,market_id=?,side=?,token_id=? WHERE entry_intent_id=?",
                        (asset, market_id, side, token_id, intent_id),
                    )
                repaired += 1
            self.db.commit()
        return repaired

    def _sync_poly_claim_settlements(self) -> int:
        closed = 0
        now = v1._now_ms()
        with self.db_lock:
            rounds = self.db.execute(
                """SELECT r.entry_intent_id,r.market_id,r.side,r.exit_status
                     FROM engine_poly_rounds r
                     JOIN engine_orders o ON o.intent_id=r.entry_intent_id
                    WHERE o.venue='binance' AND o.status IN ('SUBMITTED','AMBIGUOUS')
                      AND COALESCE(r.exit_status,'') NOT IN ('FLAT','CLOSED')"""
            ).fetchall()
            for raw in rounds:
                row = dict(raw)
                redeem = self.db.execute(
                    """SELECT token_id,claimable_value_usdt,discovered_at_ms,completed_at_ms
                         FROM engine_redeems
                        WHERE venue_market_id=? AND side=?
                          AND claimable_value_usdt IS NOT NULL
                        ORDER BY discovered_at_ms DESC LIMIT 1""",
                    (int(row["market_id"]), str(row["side"]).upper()),
                ).fetchone()
                if redeem is None:
                    continue
                completed = int(redeem["completed_at_ms"] or redeem["discovered_at_ms"] or now)
                self.db.execute(
                    """UPDATE engine_poly_rounds
                          SET token_id=CASE WHEN ?<>'' THEN ? ELSE token_id END,
                              exit_status='CLOSED',
                              exit_completed_at_ms=COALESCE(exit_completed_at_ms,?),
                              exit_error=NULL
                        WHERE entry_intent_id=?""",
                    (str(redeem["token_id"] or ""), str(redeem["token_id"] or ""), completed, str(row["entry_intent_id"])),
                )
                closed += 1
            self.db.commit()
        return closed

    def _poly_performance(self) -> dict[str, Any]:
        self._sync_poly_claim_settlements()
        with self.db_lock:
            rounds = [
                dict(row)
                for row in self.db.execute(
                    """SELECT r.*,o.status AS order_status,o.submitted_usdt,o.target_notional_usdt
                         FROM engine_poly_rounds r
                         LEFT JOIN engine_orders o ON o.intent_id=r.entry_intent_id
                        ORDER BY r.created_at_ms ASC"""
                ).fetchall()
            ]
            redeems = [dict(row) for row in self.db.execute("SELECT * FROM engine_redeems").fetchall()]
        by_market_side: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for row in redeems:
            key = (int(row.get("venue_market_id") or 0), str(row.get("side") or "").upper())
            by_market_side.setdefault(key, []).append(row)

        realized = wins = losses = unresolved = 0
        pnl = stake = payout_total = 0.0
        rows_out: list[dict[str, Any]] = []
        for row in rounds:
            order_status = str(row.get("order_status") or "").upper()
            if order_status not in POLY_ORDER_STATUSES:
                continue
            cost = float(row.get("submitted_usdt") or row.get("target_notional_usdt") or 0.0)
            payout: float | None = None
            basis: str | None = None
            if str(row.get("exit_status") or "").upper() == "FLAT" and row.get("exit_proceeds_usdt") is not None:
                payout = float(row.get("exit_proceeds_usdt") or 0.0)
                basis = "CONFIRMED_FLAT_EXIT"
            else:
                # A row reaches engine_redeems only after Binance itself exposed
                # PENDING_CLAIM + canClaim=true.  The payout value remains valid
                # settlement evidence even if redeem submission later has no txHash.
                candidates = by_market_side.get(
                    (int(row.get("market_id") or 0), str(row.get("side") or "").upper()),
                    [],
                )
                settled = next(
                    (item for item in candidates if item.get("claimable_value_usdt") is not None),
                    None,
                )
                if settled is not None:
                    payout = float(settled.get("claimable_value_usdt") or 0.0)
                    basis = "BINANCE_CAN_CLAIM_PAYOUT"
            if payout is None or cost <= 0:
                unresolved += 1
                continue
            round_pnl = payout - cost
            realized += 1
            stake += cost
            payout_total += payout
            pnl += round_pnl
            wins += int(round_pnl > 1e-9)
            losses += int(round_pnl < -1e-9)
            rows_out.append(
                {
                    "roundId": row.get("round_id"),
                    "asset": row.get("asset"),
                    "marketId": row.get("market_id"),
                    "side": row.get("side"),
                    "costUsdt": cost,
                    "payoutUsdt": payout,
                    "netPnlUsdt": round_pnl,
                    "basis": basis,
                }
            )
        return {
            "realizedRounds": realized,
            "unresolvedRounds": unresolved,
            "wins": wins,
            "losses": losses,
            "stakeUsdt": stake,
            "payoutUsdt": payout_total,
            "netPnlUsdt": pnl,
            "winRate": wins / realized if realized else None,
            "accountingBasis": (
                "confirmed flat exit proceeds OR Binance PENDING_CLAIM/canClaim payout; "
                "redeem tx ambiguity does not erase venue settlement evidence"
            ),
            "recentRealized": rows_out[-30:],
        }

    def state(self) -> dict[str, Any]:
        self._sync_poly_claim_settlements()
        payload = super().state()
        payload["version"] = VERSION
        payload["polyFastLifecycle"] = self.poly_lifecycle()
        payload["polyFastPerformance"] = self._poly_performance()
        gateway = payload.get("polyFastGateway")
        if isinstance(gateway, dict):
            gateway["legacyPolyRowsRepaired"] = int(self.legacy_poly_rows_repaired)
            gateway["claimSettlementClosesActiveRound"] = True
            gateway["claimPayoutIndependentOfRedeemTxStatus"] = True
            gateway["migrationOnlyVenueWrittenOrders"] = True
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["polyClaimSettlementRepair"] = True
        payload["claimSettlementClosesActiveRound"] = True
        return payload


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV9Handler", (v8.v7.v6._Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; legacy Poly migration repaired; claim settlement closes rounds",
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
