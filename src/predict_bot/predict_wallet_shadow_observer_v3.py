from __future__ import annotations

import os
from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v2 as v2

VERSION = "PREDICT_WALLET_SHADOW_V0_3"
RETENTION_DAYS = max(1, min(30, int(os.environ.get("PREDICT_WALLET_SHADOW_RETENTION_DAYS", "7"))))
RETENTION_MS = RETENTION_DAYS * 24 * 60 * 60 * 1000
CLEANUP_INTERVAL_MS = max(
    60_000,
    int(float(os.environ.get("PREDICT_WALLET_SHADOW_CLEANUP_MINUTES", "10")) * 60_000),
)
SETTLEMENT_POLL_MS = max(
    2_000,
    int(float(os.environ.get("PREDICT_WALLET_SHADOW_SETTLEMENT_POLL_SECONDS", "5")) * 1000),
)
PNL_EPSILON = 1e-9


def resolved_winner(market: dict[str, Any]) -> str | None:
    outcomes = market.get("outcomes") if isinstance(market.get("outcomes"), list) else []
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        status = str(outcome.get("status") or "").upper().strip()
        won = outcome.get("isWinner") is True or outcome.get("won") is True or status == "WON"
        if won:
            side = base._side(outcome.get("name") or outcome.get("outcome"))
            if side in {"UP", "DOWN"}:
                return side

    resolution = market.get("resolution") if isinstance(market.get("resolution"), dict) else {}
    resolution_status = str(resolution.get("status") or "").upper().strip()
    if resolution_status in {"WON", "RESOLVED", "SETTLED"}:
        side = base._side(resolution.get("name") or resolution.get("outcome"))
        if side in {"UP", "DOWN"}:
            return side

    variant = market.get("variantData") if isinstance(market.get("variantData"), dict) else {}
    start = base._finite(variant.get("startPrice"))
    end = base._finite(variant.get("endPrice"))
    if start is not None and end is not None and abs(end - start) > 1e-15:
        return "UP" if end > start else "DOWN"
    return None


def paper_market_result(
    events: list[dict[str, Any]],
    winner: str,
) -> dict[str, Any]:
    fills = []
    for event in events:
        event_type = str(event.get("event_type") or event.get("eventType") or "")
        if event_type not in {"MAKER_FILL_PROXY", "TAKER_INTENT"}:
            continue
        side = base._side(event.get("side"))
        price = base._finite(event.get("price"))
        shares = base._finite(event.get("shares"))
        role = str(event.get("role") or "").upper().strip()
        if side not in {"UP", "DOWN"} or price is None or shares is None or shares <= 0:
            continue
        if not 0 <= price <= 1:
            continue
        fills.append({"side": side, "price": price, "shares": shares, "role": role})

    def summarize(role: str | None = None) -> dict[str, float]:
        selected = fills if role is None else [fill for fill in fills if fill["role"] == role]
        cost = sum(fill["price"] * fill["shares"] for fill in selected)
        payout = sum(fill["shares"] for fill in selected if fill["side"] == winner)
        pnl = payout - cost
        return {
            "costUsdt": cost,
            "payoutUsdt": payout,
            "grossPnlUsdt": pnl,
            "grossRoi": pnl / cost if cost > 0 else 0.0,
            "fillCount": float(len(selected)),
        }

    total = summarize()
    maker = summarize("MAKER")
    taker = summarize("TAKER")
    traded = bool(fills)
    pnl = total["grossPnlUsdt"]
    status = (
        "NO_TRADE"
        if not traded
        else "WIN"
        if pnl > PNL_EPSILON
        else "LOSS"
        if pnl < -PNL_EPSILON
        else "FLAT"
    )
    return {
        **total,
        "fillCount": len(fills),
        "traded": traded,
        "status": status,
        "maker": maker,
        "taker": taker,
    }


class WalletShadowObserver(v2.WalletShadowObserver):
    def __init__(self, db_path=base.DB_PATH) -> None:
        self.retention_days = RETENTION_DAYS
        self.retention_ms = RETENTION_MS
        self.pending_settlement_ids: set[int] = set()
        self.last_cleanup_ms = 0
        self.last_cleanup_deleted = {"target": 0, "shadow": 0, "results": 0}
        self.last_settlement_attempt_ms = 0
        self.last_settlement_error: str | None = None
        super().__init__(db_path)
        self._seed_pending_settlements()
        self._cleanup_retention(force=True)

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_shadow_market_results (
                    wallet TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    winner TEXT NOT NULL,
                    resolved_at_ms INTEGER NOT NULL,
                    traded INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    fill_count INTEGER NOT NULL,
                    cost_usdt REAL NOT NULL,
                    payout_usdt REAL NOT NULL,
                    gross_pnl_usdt REAL NOT NULL,
                    gross_roi REAL NOT NULL,
                    maker_cost_usdt REAL NOT NULL,
                    maker_pnl_usdt REAL NOT NULL,
                    taker_cost_usdt REAL NOT NULL,
                    taker_pnl_usdt REAL NOT NULL,
                    PRIMARY KEY(wallet, market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_shadow_results_resolved
                    ON wallet_shadow_market_results(wallet, resolved_at_ms);
                """
            )
            self.db.commit()

    def _seed_pending_settlements(self) -> None:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            rows = self.db.execute(
                """
                SELECT DISTINCT s.market_id
                FROM wallet_shadow_events s
                LEFT JOIN wallet_shadow_market_results r
                  ON r.wallet=s.wallet AND r.market_id=s.market_id
                WHERE s.wallet=?
                  AND s.at_ms>=?
                  AND s.event_type IN ('MAKER_FILL_PROXY','TAKER_INTENT')
                  AND r.market_id IS NULL
                """,
                (self.wallet, cutoff),
            ).fetchall()
        self.pending_settlement_ids.update(int(row[0]) for row in rows)

    def _fetch_market(self, market_id: int) -> dict[str, Any]:
        response = self.http.get(f"{base.API_BASE}/v1/markets/{int(market_id)}")
        response.raise_for_status()
        payload = base._record(response.json())
        if payload.get("success") is False:
            raise RuntimeError(f"Predict market lookup rejected for market={market_id}")
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def _market_shadow_events(self, market_id: int) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = self.db.execute(
                """
                SELECT event_type, role, side, price, shares
                FROM wallet_shadow_events
                WHERE wallet=? AND market_id=?
                  AND event_type IN ('MAKER_FILL_PROXY','TAKER_INTENT')
                ORDER BY at_ms ASC
                """,
                (self.wallet, int(market_id)),
            ).fetchall()
        return [dict(row) for row in rows]

    def _store_market_result(self, market_id: int, market: dict[str, Any], winner: str) -> None:
        result = paper_market_result(self._market_shadow_events(market_id), winner)
        title = str(market.get("title") or market.get("question") or "") or None
        now_ms = base._now_ms()
        maker = result["maker"]
        taker = result["taker"]
        with self.db_lock:
            self.db.execute(
                """
                INSERT INTO wallet_shadow_market_results(
                    wallet,market_id,title,winner,resolved_at_ms,traded,status,fill_count,
                    cost_usdt,payout_usdt,gross_pnl_usdt,gross_roi,
                    maker_cost_usdt,maker_pnl_usdt,taker_cost_usdt,taker_pnl_usdt
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(wallet,market_id) DO UPDATE SET
                    title=excluded.title,
                    winner=excluded.winner,
                    resolved_at_ms=excluded.resolved_at_ms,
                    traded=excluded.traded,
                    status=excluded.status,
                    fill_count=excluded.fill_count,
                    cost_usdt=excluded.cost_usdt,
                    payout_usdt=excluded.payout_usdt,
                    gross_pnl_usdt=excluded.gross_pnl_usdt,
                    gross_roi=excluded.gross_roi,
                    maker_cost_usdt=excluded.maker_cost_usdt,
                    maker_pnl_usdt=excluded.maker_pnl_usdt,
                    taker_cost_usdt=excluded.taker_cost_usdt,
                    taker_pnl_usdt=excluded.taker_pnl_usdt
                """,
                (
                    self.wallet,
                    int(market_id),
                    title,
                    winner,
                    now_ms,
                    1 if result["traded"] else 0,
                    result["status"],
                    result["fillCount"],
                    result["costUsdt"],
                    result["payoutUsdt"],
                    result["grossPnlUsdt"],
                    result["grossRoi"],
                    maker["costUsdt"],
                    maker["grossPnlUsdt"],
                    taker["costUsdt"],
                    taker["grossPnlUsdt"],
                ),
            )
            self.db.commit()

    def _settle_pending(self) -> None:
        now_ms = base._now_ms()
        if now_ms - self.last_settlement_attempt_ms < SETTLEMENT_POLL_MS:
            return
        self.last_settlement_attempt_ms = now_ms
        self.last_settlement_error = None
        for market_id in list(sorted(self.pending_settlement_ids))[:8]:
            if market_id == self.market_id:
                continue
            try:
                market = self._fetch_market(market_id)
                winner = resolved_winner(market)
                if winner not in {"UP", "DOWN"}:
                    continue
                self._store_market_result(market_id, market, winner)
                self.pending_settlement_ids.discard(market_id)
            except Exception as exc:
                self.last_settlement_error = f"market {market_id}: {exc}"[:500]

    def _cleanup_retention(self, *, force: bool = False) -> None:
        now_ms = base._now_ms()
        if not force and now_ms - self.last_cleanup_ms < CLEANUP_INTERVAL_MS:
            return
        self.last_cleanup_ms = now_ms
        cutoff = now_ms - self.retention_ms
        deleted: dict[str, int] = {}
        with self.db_lock:
            cur = self.db.execute(
                "DELETE FROM wallet_shadow_target_events WHERE wallet=? AND event_ms<?",
                (self.wallet, cutoff),
            )
            deleted["target"] = max(0, cur.rowcount)
            cur = self.db.execute(
                "DELETE FROM wallet_shadow_events WHERE wallet=? AND at_ms<?",
                (self.wallet, cutoff),
            )
            deleted["shadow"] = max(0, cur.rowcount)
            cur = self.db.execute(
                "DELETE FROM wallet_shadow_market_results WHERE wallet=? AND resolved_at_ms<?",
                (self.wallet, cutoff),
            )
            deleted["results"] = max(0, cur.rowcount)
            self.db.commit()
            self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")
            live_ids = {
                int(row[0])
                for row in self.db.execute(
                    "SELECT DISTINCT market_id FROM wallet_shadow_events WHERE wallet=? AND at_ms>=?",
                    (self.wallet, cutoff),
                ).fetchall()
            }
        self.pending_settlement_ids.intersection_update(live_ids)
        self.last_cleanup_deleted = deleted

    def _poll_once(self) -> None:
        market_id, bucket, title, book = self._current_context()
        if market_id is None:
            raise RuntimeError("8771 has no current BTC Predict.fun 5m market")
        with self.lock:
            previous_market = self.market_id
            if market_id != previous_market:
                if previous_market is not None:
                    self.pending_settlement_ids.add(int(previous_market))
                self._reset_market(market_id, bucket, title)
            self.last_predict_book = dict(book)
            core = self._core_signal(book)
            self.last_core = core
            self._ingest_role(market_id, "MAKER")
            self._ingest_role(market_id, "TAKER")
            self._advance_shadow(book, core)
            self.last_poll_ms = base._now_ms()
            self._settle_pending()
            self._cleanup_retention()

    def _performance_snapshot(self) -> dict[str, Any]:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            summary = self.db.execute(
                """
                SELECT
                    COUNT(*) AS settled_markets,
                    SUM(CASE WHEN traded=1 THEN 1 ELSE 0 END) AS traded_markets,
                    SUM(CASE WHEN traded=1 AND status='WIN' THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN traded=1 AND status='LOSS' THEN 1 ELSE 0 END) AS losses,
                    SUM(CASE WHEN traded=1 AND status='FLAT' THEN 1 ELSE 0 END) AS flats,
                    COALESCE(SUM(CASE WHEN traded=1 THEN cost_usdt ELSE 0 END),0) AS cost_usdt,
                    COALESCE(SUM(CASE WHEN traded=1 THEN gross_pnl_usdt ELSE 0 END),0) AS pnl_usdt,
                    COALESCE(SUM(CASE WHEN traded=1 THEN maker_cost_usdt ELSE 0 END),0) AS maker_cost_usdt,
                    COALESCE(SUM(CASE WHEN traded=1 THEN maker_pnl_usdt ELSE 0 END),0) AS maker_pnl_usdt,
                    COALESCE(SUM(CASE WHEN traded=1 THEN taker_cost_usdt ELSE 0 END),0) AS taker_cost_usdt,
                    COALESCE(SUM(CASE WHEN traded=1 THEN taker_pnl_usdt ELSE 0 END),0) AS taker_pnl_usdt
                FROM wallet_shadow_market_results
                WHERE wallet=? AND resolved_at_ms>=?
                """,
                (self.wallet, cutoff),
            ).fetchone()
            recent = self.db.execute(
                """
                SELECT market_id,title,winner,resolved_at_ms,traded,status,fill_count,
                       cost_usdt,gross_pnl_usdt,gross_roi,
                       maker_pnl_usdt,taker_pnl_usdt
                FROM wallet_shadow_market_results
                WHERE wallet=? AND resolved_at_ms>=?
                ORDER BY resolved_at_ms DESC
                LIMIT 30
                """,
                (self.wallet, cutoff),
            ).fetchall()
            target_rows = self.db.execute(
                "SELECT COUNT(*) FROM wallet_shadow_target_events WHERE wallet=? AND event_ms>=?",
                (self.wallet, cutoff),
            ).fetchone()[0]
            shadow_rows = self.db.execute(
                "SELECT COUNT(*) FROM wallet_shadow_events WHERE wallet=? AND at_ms>=?",
                (self.wallet, cutoff),
            ).fetchone()[0]

        data = dict(summary) if summary is not None else {}
        traded = int(data.get("traded_markets") or 0)
        wins = int(data.get("wins") or 0)
        cost = float(data.get("cost_usdt") or 0.0)
        pnl = float(data.get("pnl_usdt") or 0.0)
        return {
            "windowDays": self.retention_days,
            "settledMarkets": int(data.get("settled_markets") or 0),
            "tradedMarkets": traded,
            "wins": wins,
            "losses": int(data.get("losses") or 0),
            "flats": int(data.get("flats") or 0),
            "winRate": wins / traded if traded > 0 else None,
            "grossCostUsdt": cost,
            "grossPnlUsdt": pnl,
            "grossRoi": pnl / cost if cost > 0 else None,
            "makerCostUsdt": float(data.get("maker_cost_usdt") or 0.0),
            "makerGrossPnlUsdt": float(data.get("maker_pnl_usdt") or 0.0),
            "takerCostUsdt": float(data.get("taker_cost_usdt") or 0.0),
            "takerGrossPnlUsdt": float(data.get("taker_pnl_usdt") or 0.0),
            "pendingSettlementMarkets": len(self.pending_settlement_ids),
            "recentMarkets": [dict(row) for row in recent],
            "paperFillModel": "MAKER_FILL_PROXY at inferred quote + TAKER_INTENT at observed ask; BUY and hold to binary settlement; gross before fees/rebates/slippage",
            "winDefinition": "market grossPnlUsdt > 0; winRate = winning traded markets / all traded settled markets",
            "storedRows": {
                "target": int(target_rows),
                "shadow": int(shadow_rows),
            },
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        payload["retention"] = {
            "days": self.retention_days,
            "bounded": True,
            "cutoffMs": base._now_ms() - self.retention_ms,
            "cleanupIntervalMs": CLEANUP_INTERVAL_MS,
            "lastCleanupMs": self.last_cleanup_ms,
            "lastCleanupDeleted": dict(self.last_cleanup_deleted),
            "policy": "target fills, shadow events, and settled market results older than the retention window are deleted",
        }
        payload["performance"] = self._performance_snapshot()
        payload["settlement"] = {
            "pollIntervalMs": SETTLEMENT_POLL_MS,
            "pendingMarketIds": sorted(self.pending_settlement_ids),
            "lastError": self.last_settlement_error,
        }
        return payload


class _Handler(base._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV3Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"target={observer.wallet}; retention={RETENTION_DAYS}d; "
        f"apiKeyConfigured={bool(observer.api_key)}; db={base.DB_PATH}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        observer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
