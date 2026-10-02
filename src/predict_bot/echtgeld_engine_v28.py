from __future__ import annotations

import os
import time
from http.server import ThreadingHTTPServer
from typing import Any

import httpx

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v23 as v23
from . import echtgeld_engine_v27 as v27

VERSION = "ECHTGELD_ENGINE_V2_4310_CAP100_PNL_LEDGER_BRIDGE_V1_R2_R21_SEMANTIC_V2"
HOST = v27.HOST
PORT = v27.PORT
CAP100_SETTLEMENT_SOURCE = "predict.fun.v1.markets/CAP100_REAL_EXECUTION"
CAP100_SETTLEMENT_POLL_MS = 2_000
CAP100_SETTLEMENT_END_GRACE_MS = 2_000
CAP100_API_BASE = str(os.environ.get("PREDICT_FUN_API_BASE") or "https://api.predict.fun").rstrip("/")


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _side(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if text in {"UP", "YES", "HIGHER", "ABOVE"}:
        return "UP"
    if text in {"DOWN", "NO", "LOWER", "BELOW"}:
        return "DOWN"
    return None


def _strict_official_winner(market: dict[str, Any]) -> str | None:
    """Accept only explicit venue resolution evidence; never infer from prices."""
    for outcome in _rows(market.get("outcomes")):
        status = str(outcome.get("status") or "").strip().upper()
        if outcome.get("isWinner") is True or outcome.get("won") is True or status == "WON":
            winner = _side(outcome.get("name") or outcome.get("outcome") or outcome.get("side"))
            if winner in {"UP", "DOWN"}:
                return winner

    resolution = _record(market.get("resolution"))
    resolution_status = str(resolution.get("status") or "").strip().upper()
    if resolution_status in {"WON", "RESOLVED", "SETTLED"}:
        winner = _side(
            resolution.get("name")
            or resolution.get("outcome")
            or resolution.get("winner")
            or resolution.get("side")
        )
        if winner in {"UP", "DOWN"}:
            return winner

    market_status = str(market.get("status") or "").strip().upper()
    if market_status in {"RESOLVED", "SETTLED", "CLOSED"}:
        winner = _side(
            market.get("winner")
            or market.get("winningOutcome")
            or market.get("winning_outcome")
            or market.get("result")
        )
        if winner in {"UP", "DOWN"}:
            return winner
    return None


class EchtgeldEngine(v27.EchtgeldEngine):
    """V27 plus the missing CAP100 durable ledger / settlement / PnL / stop-loss bridge.

    CAP100 venue writes remain owned by V23/V27.  This layer changes no frozen
    Maker/Taker decision logic and performs no extra venue order writes.

    The bridge has four responsibilities:
      1. project engine_cap100_orders into the generic /orders + recentOrders view;
      2. project the same rows into permanentTradeMessages/recentEvents;
      3. settle filled CAP100 source markets directly from Predict.fun official
         market metadata, requiring explicit winner evidence only;
      4. force CAP100 entry admission to refresh settlement/risk first and fail
         closed when an ended filled CAP100 market still lacks official settlement.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        api_key = str(os.environ.get("PREDICT_FUN_API_KEY") or "").strip()
        self.cap100_settlement_http = httpx.Client(
            timeout=httpx.Timeout(8.0, connect=2.0),
            trust_env=False,
            headers={
                "Accept": "application/json",
                **({"x-api-key": api_key} if api_key else {}),
            },
        )
        self.cap100_settlement_sync_last_ms = 0
        self.cap100_settlement_sync_state: dict[str, Any] = {
            "status": "NOT_SYNCED",
            "authority": CAP100_SETTLEMENT_SOURCE,
            "apiBase": CAP100_API_BASE,
            "asOfMs": None,
            "filledMarkets": 0,
            "settledMarkets": 0,
            "pendingEndedMarkets": 0,
            "importedRows": 0,
            "error": None,
            "winnerPolicy": "explicit official resolved winner only; no price inference",
        }
        super().__init__(*args, **kwargs)

    def _cap100_filled_markets(self) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT source_market_id,
                          MAX(window_end_ms) AS window_end_ms,
                          MIN(bucket_start_sec) AS bucket_start_sec,
                          SUM(filled_share_qty) AS filled_shares,
                          SUM(filled_usdt_amount) AS filled_usdt
                     FROM engine_cap100_orders
                    WHERE filled_share_qty>0
                    GROUP BY source_market_id
                    ORDER BY source_market_id"""
            ).fetchall()
        return [dict(row) for row in rows]

    def _cap100_settled_market_ids(self) -> set[int]:
        with self.db_lock:
            rows = self.db.execute(
                "SELECT market_id FROM engine_settlements WHERE cohort=?",
                (v23.CAP100_COHORT,),
            ).fetchall()
        return {int(row["market_id"]) for row in rows}

    def _cap100_overdue_unsettled(self) -> list[dict[str, Any]]:
        now = v1._now_ms()
        settled = self._cap100_settled_market_ids()
        return [
            item
            for item in self._cap100_filled_markets()
            if int(item.get("source_market_id") or 0) not in settled
            and int(item.get("window_end_ms") or 0) > 0
            and now >= int(item.get("window_end_ms") or 0) + CAP100_SETTLEMENT_END_GRACE_MS
        ]

    def _fetch_cap100_market(self, market_id: int) -> dict[str, Any]:
        response = self.cap100_settlement_http.get(f"{CAP100_API_BASE}/v1/markets/{int(market_id)}")
        response.raise_for_status()
        payload = _record(response.json())
        if payload.get("success") is False:
            raise RuntimeError(f"Predict market lookup rejected market={market_id}")
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def _sync_cap100_settlements(self, *, force: bool = False) -> dict[str, Any]:
        now = v1._now_ms()
        if (
            not force
            and self.cap100_settlement_sync_last_ms > 0
            and now - self.cap100_settlement_sync_last_ms < CAP100_SETTLEMENT_POLL_MS
        ):
            return dict(self.cap100_settlement_sync_state)
        self.cap100_settlement_sync_last_ms = now

        filled = self._cap100_filled_markets()
        settled_before = self._cap100_settled_market_ids()
        due = [
            item
            for item in filled
            if int(item.get("source_market_id") or 0) not in settled_before
            and int(item.get("window_end_ms") or 0) > 0
            and now >= int(item.get("window_end_ms") or 0) + CAP100_SETTLEMENT_END_GRACE_MS
        ]
        imported = 0
        errors: list[str] = []

        for item in due[:24]:
            market_id = int(item.get("source_market_id") or 0)
            try:
                market = self._fetch_cap100_market(market_id)
                winner = _strict_official_winner(market)
                if winner not in {"UP", "DOWN"}:
                    continue
                resolved_at_ms = v1._now_ms()
                with self.db_lock:
                    self.db.execute(
                        """INSERT INTO engine_settlements(
                               cohort,market_id,winner,resolved_at_ms,source,synced_at_ms
                           ) VALUES(?,?,?,?,?,?)
                           ON CONFLICT(cohort,market_id) DO UPDATE SET
                               winner=excluded.winner,
                               resolved_at_ms=excluded.resolved_at_ms,
                               source=excluded.source,
                               synced_at_ms=excluded.synced_at_ms""",
                        (
                            v23.CAP100_COHORT,
                            market_id,
                            winner,
                            resolved_at_ms,
                            CAP100_SETTLEMENT_SOURCE,
                            resolved_at_ms,
                        ),
                    )
                    self.db.commit()
                imported += 1
            except Exception as exc:
                errors.append(f"#{market_id} {type(exc).__name__}: {str(exc)[:220]}")

        settled_after = self._cap100_settled_market_ids()
        pending_after = [
            item
            for item in filled
            if int(item.get("source_market_id") or 0) not in settled_after
            and int(item.get("window_end_ms") or 0) > 0
            and now >= int(item.get("window_end_ms") or 0) + CAP100_SETTLEMENT_END_GRACE_MS
        ]
        status = "OK" if not pending_after else "WAITING_OFFICIAL_WINNER" if not errors else "DEGRADED"
        self.cap100_settlement_sync_state = {
            "status": status,
            "authority": CAP100_SETTLEMENT_SOURCE,
            "apiBase": CAP100_API_BASE,
            "asOfMs": now,
            "filledMarkets": len(filled),
            "settledMarkets": len([item for item in filled if int(item.get("source_market_id") or 0) in settled_after]),
            "pendingEndedMarkets": len(pending_after),
            "pendingMarketIds": [int(item.get("source_market_id") or 0) for item in pending_after[:24]],
            "importedRows": imported,
            "error": " | ".join(errors[:4]) if errors else None,
            "winnerPolicy": "explicit official resolved winner only; no price inference",
            "failClosedBeforeNewCap100Entry": True,
        }
        return dict(self.cap100_settlement_sync_state)

    def _sync_settlements(self, *, force: bool = False) -> dict[str, Any]:
        # Preserve all historical Target Taker / Poly settlement behavior, then add
        # an independent source-market authority for CAP100.
        state = super()._sync_settlements(force=force)
        self._sync_cap100_settlements(force=force)
        return state

    def _cap100_gate(self, payload: dict[str, Any], *, require_armed: bool = True) -> None:
        # V23 calls this twice for a new write: once before durable planning and once
        # immediately before venue placement.  Refresh settlement/stop-loss only on
        # the first call (no durable row exists yet), while the second call still
        # retains V23's armed/source/unknown-write pre-venue fence.
        if require_armed:
            cid = str(payload.get("clientOrderId") or "").strip()
            existing = None
            if cid:
                with self.db_lock:
                    existing = self.db.execute(
                        "SELECT state FROM engine_cap100_orders WHERE client_order_id=?",
                        (cid,),
                    ).fetchone()
            if existing is None:
                self._sync_settlements(force=True)
                if float(self.stop_loss_usdt) > 0:
                    # Settlement was just refreshed; avoid a second external fetch.
                    self._enforce_stop_loss(force_sync=False)
                overdue = self._cap100_overdue_unsettled()
                if overdue:
                    ids = ",".join(str(int(item.get("source_market_id") or 0)) for item in overdue[:8])
                    raise v1.EchtgeldEngineError(
                        "CAP100 entry blocked fail-closed: ended real-fill market(s) still lack explicit official settlement: "
                        + ids
                    )
        return super()._cap100_gate(payload, require_armed=require_armed)

    def resume(self) -> dict[str, Any]:
        self._sync_settlements(force=True)
        overdue = self._cap100_overdue_unsettled()
        if overdue and self._selected_entry_source() in v23.CAP100_ALLOWED_SOURCES:
            ids = ",".join(str(int(item.get("source_market_id") or 0)) for item in overdue[:8])
            raise v1.EchtgeldEngineError(
                "Cannot resume CAP100 Echtgeld: ended real-fill market(s) still lack explicit official settlement: " + ids
            )
        return super().resume()

    def _cap100_order_projection(self, limit: int = 200) -> list[dict[str, Any]]:
        self._sync_cap100_settlements(force=False)
        settlements = self._settlement_map()
        with self.db_lock:
            rows = [
                dict(row)
                for row in self.db.execute(
                    "SELECT * FROM engine_cap100_orders ORDER BY created_at_ms DESC LIMIT ?",
                    (max(1, min(1000, int(limit))),),
                ).fetchall()
            ]
        output: list[dict[str, Any]] = []
        for row in rows:
            market_id = int(row.get("source_market_id") or 0)
            shares = float(row.get("filled_share_qty") or 0.0)
            cost = float(row.get("filled_usdt_amount") or 0.0)
            if shares > 1e-9 and cost <= 1e-9:
                cost = float(row.get("avg_fill_price") or row.get("requested_price") or 0.0) * shares
            settlement = settlements.get((v23.CAP100_COHORT, market_id))
            winner = str((settlement or {}).get("winner") or "").upper()
            pnl = roi = None
            result_status = "PENDING"
            if shares > 1e-9 and winner in {"UP", "DOWN"}:
                payout = shares if str(row.get("side") or "").upper() == winner else 0.0
                pnl = payout - cost
                roi = pnl / cost if cost > 1e-9 else None
                result_status = "WIN" if pnl > 1e-9 else "LOSS" if pnl < -1e-9 else "FLAT"
            elif shares <= 1e-9 and str(row.get("state") or "").upper() in {"REJECTED", "CANCELED", "FAILED", "EXPIRED"}:
                result_status = "NO_FILL"

            output.append(
                {
                    "id": f"cap100:{row.get('client_order_id')}",
                    "dedupe_key": row.get("client_order_id"),
                    "intent_id": row.get("client_order_id"),
                    "strategy": row.get("strategy") or v23.CAP100_STRATEGY,
                    "entry_source": row.get("source_id"),
                    "cohort": v23.CAP100_COHORT,
                    "market_id": market_id,
                    "venue_market_id": row.get("venue_market_id"),
                    "venue": "binance",
                    "role": row.get("role"),
                    "side": row.get("side"),
                    "signal_ask": row.get("requested_price"),
                    "target_notional_usdt": row.get("requested_cost_usdt"),
                    "status": row.get("state"),
                    "attempted_at_ms": row.get("created_at_ms"),
                    "completed_at_ms": row.get("completed_at_ms"),
                    "execution_price": row.get("avg_fill_price") if shares > 1e-9 else None,
                    "shares": shares,
                    "submitted_usdt": cost if shares > 1e-9 else 0.0,
                    "vendor_order_id": row.get("order_id") or row.get("vendor_order_id"),
                    "vendor_order_hash": row.get("vendor_order_id"),
                    "error_message": row.get("error_message"),
                    "result": {
                        "cap100": row.get("source_id") == v23.CAP100_SOURCE,
                        "sharedExecutionAdapter": True,
                        "role": row.get("role"),
                        "exchangeStatus": row.get("exchange_status"),
                        "fillPercentage": row.get("fill_percentage"),
                        "sourceMarketId": market_id,
                        "venueMarketId": row.get("venue_market_id"),
                        "ledgerSource": "engine_cap100_orders",
                    },
                    "context": {
                        "entrySource": row.get("source_id"),
                        "bucketStartSec": row.get("bucket_start_sec"),
                        "windowEndMs": row.get("window_end_ms"),
                        "clientOrderId": row.get("client_order_id"),
                    },
                    "winner": winner or None,
                    "resolvedAtMs": (settlement or {}).get("resolved_at_ms"),
                    "resultStatus": result_status,
                    "netPnlUsdt": pnl,
                    "netRoi": roi,
                    "durableLedgerSource": "engine_cap100_orders",
                    "cap100": row.get("source_id") == v23.CAP100_SOURCE,
                    "sharedExecutionAdapter": True,
                }
            )
        return output

    def orders(self, limit: int = 100) -> list[dict[str, Any]]:
        generic = super().orders(limit)
        cap = self._cap100_order_projection(limit)
        merged = [*generic, *cap]
        merged.sort(key=lambda item: int(item.get("attempted_at_ms") or 0), reverse=True)
        return merged[: max(1, int(limit))]

    @staticmethod
    def _money(value: Any) -> str:
        try:
            return f"${float(value):.4f}"
        except (TypeError, ValueError):
            return "—"

    @staticmethod
    def _signed_money(value: Any) -> str:
        try:
            return f"{float(value):+.4f} USDT"
        except (TypeError, ValueError):
            return "—"

    def _cap100_permanent_trade_messages(self) -> list[dict[str, Any]]:
        rows = self._cap100_order_projection(120)
        messages: list[dict[str, Any]] = []
        for row in rows:
            status = str(row.get("status") or "UNKNOWN").upper()
            role = str(row.get("role") or "ORDER").upper()
            side = str(row.get("side") or "").upper()
            market_id = int(row.get("market_id") or 0)
            shares = float(row.get("shares") or 0.0)
            cost = float(row.get("submitted_usdt") or 0.0)
            price = row.get("execution_price")
            result_status = str(row.get("resultStatus") or "PENDING").upper()
            pnl = row.get("netPnlUsdt")
            winner = row.get("winner")
            occurred = int(row.get("completed_at_ms") or row.get("attempted_at_ms") or 0)
            entry_source = str(row.get("entry_source") or row.get("context", {}).get("entrySource") or "UNKNOWN_SOURCE")
            source_label = "R2+R2.1" if entry_source == v23.R2_R21_SOURCE else "CAP100" if entry_source == v23.CAP100_SOURCE else entry_source
            event_prefix = "R2_R21" if entry_source == v23.R2_R21_SOURCE else "CAP100" if entry_source == v23.CAP100_SOURCE else "STRATEGY"

            level = "INFO"
            if status in {"UNKNOWN_SUBMISSION", "CANCEL_UNKNOWN", "FAILED"}:
                level = "ERROR"
            elif status in {"REJECTED", "CANCELED", "CANCEL_PENDING"}:
                level = "WARN"

            if shares > 1e-9:
                fill_text = f"{shares:.4f} sh @ {float(price):.4f}" if price is not None else f"{shares:.4f} sh"
                if result_status in {"WIN", "LOSS", "FLAT"}:
                    message = (
                        f"{source_label} [{entry_source}] BTC #{market_id} {role} {side} {status} · {fill_text} · "
                        f"成本 {self._money(cost)} · winner {winner} · PnL {self._signed_money(pnl)}"
                    )
                    event_type = f"{event_prefix}_SETTLED_ORDER"
                else:
                    message = (
                        f"{source_label} [{entry_source}] BTC #{market_id} {role} {side} {status} · {fill_text} · "
                        f"成本 {self._money(cost)} · 等待官方結算"
                    )
                    event_type = f"{event_prefix}_FILLED_ORDER"
            else:
                detail = str(row.get("error_message") or "").strip()
                message = f"{source_label} [{entry_source}] BTC #{market_id} {role} {side} {status} · no fill"
                if detail:
                    message += f" · {detail}"
                event_type = f"{event_prefix}_ORDER_{status}"

            messages.append(
                {
                    "id": f"durable-execution:{row.get('intent_id')}:{status}:{result_status}",
                    "occurred_at_ms": occurred,
                    "level": level,
                    "event_type": event_type,
                    "phase": status,
                    "market_id": market_id,
                    "side": side,
                    "asset": "BTC",
                    "message": message,
                    "error_class": row.get("error_message") if level in {"WARN", "ERROR"} else None,
                    "durableProjection": True,
                    "source": "engine_cap100_orders",
                    "entry_source": entry_source,
                    "strategy": row.get("strategy"),
                    "cap100": entry_source == v23.CAP100_SOURCE,
                    "sharedExecutionAdapter": True,
                }
            )
        return messages

    def _permanent_trade_messages(self) -> list[dict[str, Any]]:
        projected = [*super()._permanent_trade_messages(), *self._cap100_permanent_trade_messages()]
        projected.sort(key=lambda item: int(item.get("occurred_at_ms") or 0), reverse=True)
        return projected[:200]

    def cap100_state(self) -> dict[str, Any]:
        payload = super().cap100_state()
        payload["settlementSync"] = dict(self.cap100_settlement_sync_state)
        payload["overdueUnsettledMarkets"] = [
            int(item.get("source_market_id") or 0) for item in self._cap100_overdue_unsettled()
        ]
        payload["pnlStopLossBridge"] = True
        payload["durableOrderProjection"] = True
        payload["permanentTradeMessageProjection"] = True
        return payload

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        payload["cap100SettlementSync"] = dict(self.cap100_settlement_sync_state)
        payload["cap100PnlStopLossBridge"] = {
            "enabled": True,
            "settlementAuthority": CAP100_SETTLEMENT_SOURCE,
            "winnerPolicy": "explicit official resolved winner only; no price inference",
            "failClosedBeforeNewEntryWhenEndedFillUnsettled": True,
            "durableOrderSource": "engine_cap100_orders",
            "stopLossBasis": "combined settled Echtgeld PnL including CAP100",
        }
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["cap100SettlementAuthority"] = True
        payload["cap100SettlementSource"] = CAP100_SETTLEMENT_SOURCE
        payload["cap100SettlementExplicitWinnerOnly"] = True
        payload["cap100SettlementFailClosed"] = True
        payload["cap100PnlStopLossBridge"] = True
        payload["cap100DurableOrderProjection"] = True
        payload["cap100PermanentTradeMessageProjection"] = True
        payload["cap100SettlementStatus"] = self.cap100_settlement_sync_state.get("status")
        return payload

    def close(self) -> None:
        try:
            super().close()
        finally:
            self.cap100_settlement_http.close()


class _Handler(v27._Handler):
    engine: EchtgeldEngine


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV28Handler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; "
        "CAP100 durable ledger projection + direct official settlement + PnL/stop-loss bridge enabled",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown(); server.server_close(); engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
