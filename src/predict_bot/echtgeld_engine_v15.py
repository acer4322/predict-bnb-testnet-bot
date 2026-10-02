from __future__ import annotations

from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v6 as v6
from . import echtgeld_engine_v14 as v14

VERSION = "ECHTGELD_ENGINE_V2_4310_POLY_V12_DURABLE_TRADE_MESSAGES"
HOST = v14.HOST
PORT = v14.PORT


class EchtgeldEngine(v14.EchtgeldEngine):
    """V14 plus read-only durable Poly exit/redeem message projection.

    This layer does not change BUY, SELL, reconciliation, redeem, lifecycle, PnL,
    arming or retry behavior.  It only derives human-readable permanent messages
    from the existing durable engine_poly_rounds / engine_orders / engine_redeems
    ledgers so dashboard messages survive process restarts without coupling logging
    failures to venue writes.
    """

    @staticmethod
    def _exit_time_ms(row: dict[str, Any]) -> int:
        completed = int(row.get("exit_completed_at_ms") or 0)
        if completed > 0:
            return completed
        parts = str(row.get("exit_intent_id") or "").split(":")
        if parts:
            try:
                value = int(parts[-1])
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return value
        return int(row.get("created_at_ms") or 0)

    @staticmethod
    def _money(value: Any) -> str:
        try:
            return f"${float(value):.4f}"
        except (TypeError, ValueError):
            return "—"

    @staticmethod
    def _signed_money(value: Any) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "—"
        return f"{number:+.4f} USDT"

    def _permanent_trade_messages(self) -> list[dict[str, Any]]:
        with self.db_lock:
            rounds = [
                dict(row)
                for row in self.db.execute(
                    """SELECT r.*,o.status AS order_status,o.submitted_usdt,o.target_notional_usdt
                         FROM engine_poly_rounds r
                         LEFT JOIN engine_orders o ON o.intent_id=r.entry_intent_id
                        ORDER BY r.created_at_ms DESC LIMIT 120"""
                ).fetchall()
            ]
            try:
                redeems = [
                    dict(row)
                    for row in self.db.execute(
                        "SELECT * FROM engine_redeems ORDER BY discovered_at_ms DESC LIMIT 120"
                    ).fetchall()
                ]
            except Exception:
                redeems = []

        messages: list[dict[str, Any]] = []
        by_market_side: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for item in rounds:
            key = (int(item.get("market_id") or 0), str(item.get("side") or "").upper())
            by_market_side.setdefault(key, []).append(item)

            exit_intent_id = str(item.get("exit_intent_id") or "").strip()
            exit_status = str(item.get("exit_status") or "").strip().upper()
            if not exit_intent_id or not exit_status:
                continue
            asset = str(item.get("asset") or "UNKNOWN").upper()
            market_id = int(item.get("market_id") or 0)
            side = str(item.get("side") or "").upper()
            cost = float(item.get("submitted_usdt") or item.get("target_notional_usdt") or 0.0)
            proceeds_raw = item.get("exit_proceeds_usdt")
            proceeds = float(proceeds_raw) if proceeds_raw is not None else None
            pnl = (proceeds - cost) if proceeds is not None and cost > 0 else None
            occurred = self._exit_time_ms(item)

            if exit_status == "FLAT":
                event_type = "POLY_EXIT_CONFIRMED_FLAT"
                level = "INFO"
                message = (
                    f"{asset} #{market_id} {side} 反轉賣出已確認 FLAT · "
                    f"賣出回收 {self._money(proceeds)} · 成本 {self._money(cost)} · "
                    f"PnL {self._signed_money(pnl)}"
                )
            elif exit_status == "AMBIGUOUS":
                event_type = "POLY_EXIT_SUBMITTED_AMBIGUOUS"
                level = "WARN"
                message = (
                    f"{asset} #{market_id} {side} 反轉 SELL 已送出但尚未確認 FLAT · "
                    f"quote/proceeds {self._money(proceeds)} · 成本 {self._money(cost)} · "
                    f"暫估 PnL {self._signed_money(pnl)} · 不會自動重送 SELL"
                )
            elif exit_status in {"ATTEMPTING", "SUBMITTED"}:
                event_type = "POLY_EXIT_IN_FLIGHT"
                level = "INFO"
                message = (
                    f"{asset} #{market_id} {side} 反轉 SELL {exit_status} · "
                    f"金額 {self._money(proceeds)} · 成本 {self._money(cost)} · "
                    f"暫估 PnL {self._signed_money(pnl)}"
                )
            else:
                continue

            messages.append(
                {
                    "id": f"durable-exit:{item.get('round_id')}:{exit_status}",
                    "occurred_at_ms": occurred,
                    "level": level,
                    "event_type": event_type,
                    "phase": f"EXIT_{exit_status}" if exit_status != "FLAT" else "FLAT",
                    "market_id": market_id,
                    "side": side,
                    "asset": asset,
                    "message": message,
                    "error_class": item.get("exit_error") if exit_status == "AMBIGUOUS" else None,
                    "durableProjection": True,
                    "source": "engine_poly_rounds",
                }
            )

        for redeem in redeems:
            market_id = int(redeem.get("venue_market_id") or 0)
            side = str(redeem.get("side") or "").upper()
            matches = by_market_side.get((market_id, side), [])
            asset = str((matches[0].get("asset") if matches else None) or "UNKNOWN").upper()
            costs = [float(row.get("submitted_usdt") or row.get("target_notional_usdt") or 0.0) for row in matches]
            cost = sum(value for value in costs if value > 0)
            payout_raw = redeem.get("claimable_value_usdt")
            payout = float(payout_raw) if payout_raw is not None else None
            pnl = (payout - cost) if payout is not None and cost > 0 else None
            status = str(redeem.get("status") or "UNKNOWN").upper()
            occurred = int(
                redeem.get("completed_at_ms")
                or redeem.get("submitted_at_ms")
                or redeem.get("discovered_at_ms")
                or 0
            )

            if status == "REDEEMED":
                event_type = "POLY_REDEEMED"
                level = "INFO"
                message = (
                    f"{asset} #{market_id} {side} 已領取 {self._money(payout)} · "
                    f"成本 {self._money(cost)} · PnL {self._signed_money(pnl)}"
                )
            elif status == "SETTLED_ZERO":
                event_type = "POLY_SETTLED_ZERO"
                level = "INFO"
                message = (
                    f"{asset} #{market_id} {side} 已結算 $0 · "
                    f"成本 {self._money(cost)} · PnL {self._signed_money(pnl)}"
                )
            elif status == "CLAIMABLE":
                event_type = "POLY_CLAIMABLE"
                level = "INFO"
                message = (
                    f"{asset} #{market_id} {side} 可領取 {self._money(payout)} · "
                    f"成本 {self._money(cost)} · 結算 PnL {self._signed_money(pnl)}"
                )
            elif status in {"SUBMITTED", "PENDING", "PROCESSING"}:
                event_type = "POLY_REDEEM_IN_FLIGHT"
                level = "INFO"
                message = (
                    f"{asset} #{market_id} {side} 領取 {status} · "
                    f"金額 {self._money(payout)} · PnL {self._signed_money(pnl)}"
                )
            elif status == "AMBIGUOUS":
                event_type = "POLY_REDEEM_AMBIGUOUS"
                level = "WARN"
                message = (
                    f"{asset} #{market_id} {side} 領取狀態 AMBIGUOUS · "
                    f"claimable {self._money(payout)} · PnL {self._signed_money(pnl)} · 不會自動重送"
                )
            else:
                continue

            messages.append(
                {
                    "id": f"durable-redeem:{redeem.get('token_id')}:{status}",
                    "occurred_at_ms": occurred,
                    "level": level,
                    "event_type": event_type,
                    "phase": status,
                    "market_id": market_id,
                    "side": side,
                    "asset": asset,
                    "message": message,
                    "error_class": redeem.get("last_error") if status == "AMBIGUOUS" else None,
                    "durableProjection": True,
                    "source": "engine_redeems",
                }
            )

        messages.sort(key=lambda item: int(item.get("occurred_at_ms") or 0), reverse=True)
        return messages[:120]

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        projected = self._permanent_trade_messages()
        historical = [item for item in payload.get("recentEvents", []) if isinstance(item, dict)]
        merged = historical + projected
        merged.sort(key=lambda item: int(item.get("occurred_at_ms") or 0), reverse=True)
        payload["permanentTradeMessages"] = projected
        payload["recentEvents"] = merged[:200]
        payload["durableTradeMessageProjectionV15"] = {
            "enabled": True,
            "readOnly": True,
            "venueWritesChanged": False,
            "sources": ["engine_poly_rounds", "engine_orders", "engine_redeems"],
            "includesAssetMarketAmountPnl": True,
        }
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["durableTradeMessageProjection"] = True
        return payload


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV15Handler", (v6._Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; "
        "durable Poly exit/redeem messages projected read-only from SQLite ledgers",
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
