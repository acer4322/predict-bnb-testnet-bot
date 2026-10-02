from __future__ import annotations

import json
import sqlite3
from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v6 as v6
from .echtgeld_redeem_v1 import EchtgeldRedeemManager, _finite, _now_ms

VERSION = "ECHTGELD_ENGINE_V2_4310_POLY_LIFECYCLE_REDEEM_PNL_STOP_LOSS_V4"
HOST = v6.HOST
PORT = v6.PORT
POLY_STRATEGY = v6.POLY_STRATEGY
VENUE_CONFIRMED_AMBIGUOUS = {"SUBMITTED", "FILLED", "PARTIALLY_FILLED", "PROCESSING"}


class PolyAwareRedeemManager(EchtgeldRedeemManager):
    """4310 claim lifecycle extended to venue-confirmed Poly ambiguous entries.

    An AMBIGUOUS buy is never retried.  It is only admitted to claim discovery
    when Binance already returned a vendor order id and an exchange status that
    proves the venue knows the order.  This lets a later PENDING_CLAIM position
    safely resolve the uncertainty without writing another BUY.
    """

    def _tracked_binance_markets(self) -> dict[int, set[str]]:
        tracked = super()._tracked_binance_markets()
        try:
            with self._connect() as db:
                rows = db.execute(
                    """SELECT side,vendor_order_id,result_json FROM engine_orders
                         WHERE venue='binance' AND status='AMBIGUOUS' AND vendor_order_id IS NOT NULL"""
                ).fetchall()
        except sqlite3.OperationalError:
            return tracked
        for row in rows:
            try:
                result = json.loads(str(row["result_json"] or "{}"))
            except Exception:
                continue
            exchange_status = str(result.get("exchangeStatus") or "").strip().upper()
            if exchange_status not in VENUE_CONFIRMED_AMBIGUOUS:
                continue
            try:
                market_id = int(result.get("venueMarketId") or 0)
            except (TypeError, ValueError):
                market_id = 0
            side = str(row["side"] or result.get("side") or "").strip().upper()
            if market_id > 0 and side in {"UP", "DOWN"}:
                tracked.setdefault(market_id, set()).add(side)
        return tracked

    @staticmethod
    def _claimable_position(position: dict[str, Any]) -> bool:
        token_id = str(position.get("tokenId") or "").strip()
        status = str(position.get("positionStatus") or "").strip().upper()
        shares = _finite(position.get("shares"))
        value = _finite(position.get("value"))
        try:
            end_date_ms = int(position.get("endDate") or 0)
        except (TypeError, ValueError):
            end_date_ms = 0
        return bool(
            token_id
            and position.get("canClaim") is True
            and status == "PENDING_CLAIM"
            and shares is not None
            and shares > 0
            and value is not None
            and value >= 0
            and end_date_ms > 0
        )

    def _submit_row(self, client: Any, wallet: dict[str, str], row: dict[str, Any]) -> None:
        # A zero payout is a fully resolved losing token.  Record the settlement
        # for PnL/stop-loss, but do not waste a redeem transaction on zero value.
        if float(row.get("claimable_value_usdt") or 0.0) <= 0.0:
            with self._connect() as db:
                db.execute(
                    """UPDATE engine_redeems SET status='SETTLED_ZERO',completed_at_ms=?,
                           last_checked_at_ms=?,last_error=NULL WHERE token_id=? AND status='CLAIMABLE'""",
                    (_now_ms(), _now_ms(), str(row.get("token_id") or "")),
                )
            self.last_success_at_ms = _now_ms()
            return
        super()._submit_row(client, wallet, row)


class EchtgeldEngine(v6.EchtgeldEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("redeem_manager_factory", PolyAwareRedeemManager)
        super().__init__(*args, **kwargs)

    def _poly_performance(self) -> dict[str, Any]:
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

        realized = wins = losses = 0
        pnl = stake = payout_total = 0.0
        unresolved = 0
        rows_out: list[dict[str, Any]] = []
        for row in rounds:
            order_status = str(row.get("order_status") or "").upper()
            if order_status not in {"SUBMITTED", "AMBIGUOUS"}:
                continue
            cost = float(row.get("submitted_usdt") or row.get("target_notional_usdt") or 0.0)
            payout: float | None = None
            basis: str | None = None
            if str(row.get("exit_status") or "").upper() == "FLAT" and row.get("exit_proceeds_usdt") is not None:
                payout = float(row.get("exit_proceeds_usdt") or 0.0)
                basis = "CONFIRMED_FLAT_EXIT"
            else:
                candidates = by_market_side.get((int(row.get("market_id") or 0), str(row.get("side") or "").upper()), [])
                settled = next(
                    (
                        item for item in candidates
                        if str(item.get("status") or "").upper()
                        in {"CLAIMABLE", "SUBMITTED", "PENDING", "PROCESSING", "REDEEMED", "SETTLED_ZERO"}
                    ),
                    None,
                )
                if settled is not None and settled.get("claimable_value_usdt") is not None:
                    payout = float(settled.get("claimable_value_usdt") or 0.0)
                    basis = "BINANCE_PENDING_CLAIM_PAYOUT"
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
            "accountingBasis": "confirmed flat exit proceeds OR Binance PENDING_CLAIM claimable payout; zero-value settled tokens count as losses",
            "recentRealized": rows_out[-30:],
        }

    def _performance_snapshot(self, *, force_sync: bool = False) -> dict[str, Any]:
        base = super()._performance_snapshot(force_sync=force_sync)
        poly = self._poly_performance()
        target_pnl = float(base.get("netPnlUsdt") or 0.0)
        poly_pnl = float(poly.get("netPnlUsdt") or 0.0)
        base["targetTakerNetPnlUsdt"] = target_pnl
        base["polyFastNetPnlUsdt"] = poly_pnl
        base["netPnlUsdt"] = target_pnl + poly_pnl
        base["polyFast"] = poly
        base["accountingBasis"] = (
            "Combined realized Echtgeld PnL: legacy Target Taker official settlements + "
            "Poly Fast confirmed-flat exits/Binance claimable payouts. Unresolved Poly rounds are not guessed."
        )
        return base

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        payload["polyFastPerformance"] = self._poly_performance()
        manager = self.redeem_manager
        if manager is not None:
            payload["autoRedeem"] = manager.summary()
            payload["recentRedeems"] = manager.recent(50)
        gateway = payload.get("polyFastGateway")
        if isinstance(gateway, dict):
            gateway.update(
                redeemTracksVenueConfirmedAmbiguous=True,
                zeroPayoutSettlementRecorded=True,
                polyRealizedPnlIncludedInStopLoss=True,
                unresolvedRoundsNeverGuessed=True,
            )
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["polyAwareRedeem"] = True
        payload["polyPnlInStopLoss"] = True
        return payload


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV7Handler", (v6._Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; Poly lifecycle + 4310 claim payout + combined stop loss",
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
