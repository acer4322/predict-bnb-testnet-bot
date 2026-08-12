from __future__ import annotations

import os
from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v3 as v3

VERSION = "PREDICT_WALLET_SHADOW_V0_4_TAKER_V1"
V1_BASE_SHARES = max(1.0, min(18.0, float(os.environ.get("PREDICT_WALLET_SHADOW_TAKER_V1_BASE_SHARES", "12"))))
V1_CORE_FLIP_SHARES = max(1.0, min(18.0, float(os.environ.get("PREDICT_WALLET_SHADOW_TAKER_V1_CORE_FLIP_SHARES", "10"))))
V1_MAX_EVENT_SHARES = max(1.0, min(25.0, float(os.environ.get("PREDICT_WALLET_SHADOW_TAKER_V1_MAX_EVENT_SHARES", "18"))))
V1_MAX_NET_SHARES = max(18.0, float(os.environ.get("PREDICT_WALLET_SHADOW_TAKER_V1_MAX_NET_SHARES", "90")))
V1_MAX_TOTAL_SHARES = max(36.0, float(os.environ.get("PREDICT_WALLET_SHADOW_TAKER_V1_MAX_TOTAL_SHARES", "600")))
V1_FOLLOW_MAX_PRICE_DELTA = max(0.0, min(0.10, float(os.environ.get("PREDICT_WALLET_SHADOW_TAKER_V1_FOLLOW_MAX_PRICE_DELTA", "0.025"))))
V1_MAX_ASK = max(0.50, min(0.99, float(os.environ.get("PREDICT_WALLET_SHADOW_TAKER_V1_MAX_ASK", "0.90"))))
V1_COOLDOWN_MS = max(500, int(float(os.environ.get("PREDICT_WALLET_SHADOW_TAKER_V1_COOLDOWN_SECONDS", "1.2")) * 1000))


def v1_size_after_limits(*, current_delta: float, current_total: float, side: str, requested: float) -> float:
    """Conservative event sizing: small ticket, total cap, and one-sided net cap."""
    size = min(max(0.0, requested), V1_MAX_EVENT_SHARES)
    size = min(size, max(0.0, V1_MAX_TOTAL_SHARES - current_total))
    if size <= 0:
        return 0.0
    same_direction = (side == "UP" and current_delta >= 0) or (side == "DOWN" and current_delta <= 0)
    if same_direction:
        size = min(size, max(0.0, V1_MAX_NET_SHARES - abs(current_delta)))
    return round(max(0.0, size), 6)


def taker_v1_similarity(target: list[base.ParentEvent], events: list[dict[str, Any]]) -> dict[str, Any]:
    target_taker = [item for item in target if item.role == "TAKER" and item.quote_type == "BID"]
    v1 = [item for item in events if str(item.get("eventType") or "") == "TAKER_V1_INTENT"]
    side_match = 0
    timing_match = 0
    for item in target_taker:
        any_near = [event for event in v1 if abs(int(event["atMs"]) - item.first_event_ms) <= 5000]
        if any_near:
            nearest_any = min(any_near, key=lambda event: abs(int(event["atMs"]) - item.first_event_ms))
            if str(nearest_any.get("side")) == item.side:
                side_match += 1
        same_near = [
            event for event in v1
            if str(event.get("side")) == item.side
            and abs(int(event["atMs"]) - item.first_event_ms) <= 3000
        ]
        if same_near:
            timing_match += 1

    up = sum(float(event.get("shares") or 0.0) for event in v1 if event.get("side") == "UP")
    down = sum(float(event.get("shares") or 0.0) for event in v1 if event.get("side") == "DOWN")
    target_up = sum(item.shares for item in target_taker if item.side == "UP")
    target_down = sum(item.shares for item in target_taker if item.side == "DOWN")
    v1_side = "UP" if up > down else "DOWN" if down > up else None
    target_side = "UP" if target_up > target_down else "DOWN" if target_down > target_up else None
    return {
        "targetParents": len(target_taker),
        "v1Intents": len(v1),
        "eventCountRatioV1ToTarget": len(v1) / len(target_taker) if target_taker else None,
        "sideMatchWithin5s": side_match / len(target_taker) if target_taker else None,
        "sameSideTimingWithin3s": timing_match / len(target_taker) if target_taker else None,
        "targetResidualSide": target_side,
        "v1ResidualSide": v1_side,
        "residualSideMatch": bool(target_side and v1_side and target_side == v1_side),
    }


class WalletShadowObserver(v3.WalletShadowObserver):
    def __init__(self, db_path=base.DB_PATH) -> None:
        self.taker_v1_events: list[dict[str, Any]] = []
        self.taker_v1_processed_maker_fills: set[str] = set()
        self.taker_v1_last_core_side: str | None = None
        self.taker_v1_last_emit_ms: dict[str, int] = {}
        super().__init__(db_path)

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_shadow_taker_v1_markets (
                    wallet TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    started_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(wallet, market_id)
                );
                CREATE TABLE IF NOT EXISTS wallet_shadow_taker_v1_events (
                    id TEXT PRIMARY KEY,
                    wallet TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    at_ms INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    shares REAL NOT NULL,
                    trigger TEXT NOT NULL,
                    core_side TEXT,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_shadow_taker_v1_market
                    ON wallet_shadow_taker_v1_events(wallet, market_id, at_ms);
                CREATE TABLE IF NOT EXISTS wallet_shadow_taker_v1_market_results (
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
                CREATE INDEX IF NOT EXISTS idx_wallet_shadow_taker_v1_results
                    ON wallet_shadow_taker_v1_market_results(wallet, resolved_at_ms);
                """
            )
            self.db.commit()

    def _seed_pending_settlements(self) -> None:
        super()._seed_pending_settlements()
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            rows = self.db.execute(
                """
                SELECT m.market_id
                FROM wallet_shadow_taker_v1_markets m
                LEFT JOIN wallet_shadow_taker_v1_market_results r
                  ON r.wallet=m.wallet AND r.market_id=m.market_id
                WHERE m.wallet=? AND m.started_at_ms>=? AND r.market_id IS NULL
                """,
                (self.wallet, cutoff),
            ).fetchall()
        self.pending_settlement_ids.update(int(row[0]) for row in rows)

    def _register_v1_market(self, market_id: int) -> None:
        now_ms = base._now_ms()
        with self.db_lock:
            self.db.execute(
                "INSERT OR IGNORE INTO wallet_shadow_taker_v1_markets(wallet,market_id,started_at_ms) VALUES (?,?,?)",
                (self.wallet, int(market_id), now_ms),
            )
            self.db.commit()

    def _reset_market(self, market_id: int, bucket: int | None, title: str | None) -> None:
        super()._reset_market(market_id, bucket, title)
        self.taker_v1_events = []
        self.taker_v1_processed_maker_fills = set()
        self.taker_v1_last_core_side = None
        self.taker_v1_last_emit_ms = {}
        self._register_v1_market(market_id)

    def _v1_inventory(self) -> dict[str, float]:
        up = sum(float(event.get("shares") or 0.0) for event in self.taker_v1_events if event.get("side") == "UP")
        down = sum(float(event.get("shares") or 0.0) for event in self.taker_v1_events if event.get("side") == "DOWN")
        return {
            "upShares": up,
            "downShares": down,
            "delta": up - down,
            "totalShares": up + down,
        }

    def _emit_taker_v1(
        self,
        *,
        side: str,
        price: float | None,
        requested_shares: float,
        trigger: str,
        reason: str,
        core: dict[str, Any],
        source_event_id: str | None = None,
    ) -> None:
        if self.market_id is None or side not in {"UP", "DOWN"} or price is None:
            return
        if not 0 < price <= V1_MAX_ASK:
            return
        now_ms = base._now_ms()
        cooldown_key = f"{trigger}:{side}"
        if now_ms - self.taker_v1_last_emit_ms.get(cooldown_key, 0) < V1_COOLDOWN_MS:
            return
        inv = self._v1_inventory()
        shares = v1_size_after_limits(
            current_delta=inv["delta"],
            current_total=inv["totalShares"],
            side=side,
            requested=requested_shares,
        )
        if shares <= 0:
            return
        event = {
            "id": f"{self.market_id}:{now_ms}:TAKER_V1:{trigger}:{side}:{len(self.taker_v1_events)}",
            "marketId": self.market_id,
            "atMs": now_ms,
            "eventType": "TAKER_V1_INTENT",
            "role": "TAKER",
            "side": side,
            "price": price,
            "shares": shares,
            "trigger": trigger,
            "coreSide": core.get("side"),
            "coreSource": core.get("source"),
            "sourceEventId": source_event_id,
            "reason": reason,
        }
        self.taker_v1_events.append(event)
        if len(self.taker_v1_events) > 500:
            self.taker_v1_events = self.taker_v1_events[-500:]
        self.taker_v1_last_emit_ms[cooldown_key] = now_ms
        with self.db_lock:
            self.db.execute(
                """
                INSERT OR IGNORE INTO wallet_shadow_taker_v1_events(
                    id,wallet,market_id,at_ms,side,price,shares,trigger,core_side,reason,payload_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event["id"], self.wallet, self.market_id, now_ms, side, price, shares,
                    trigger, core.get("side"), reason,
                    base.json.dumps(event, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()

    def _advance_taker_v1(self, new_common_events: list[base.ShadowEvent], book: dict[str, Any], core: dict[str, Any]) -> None:
        for event in new_common_events:
            if event.event_type != "MAKER_FILL_PROXY" or event.id in self.taker_v1_processed_maker_fills:
                continue
            self.taker_v1_processed_maker_fills.add(event.id)
            ask = base._finite(book.get("upAsk" if event.side == "UP" else "downAsk"))
            if ask is None or event.price is None:
                continue
            price_delta = ask - event.price
            if -1e-9 <= price_delta <= V1_FOLLOW_MAX_PRICE_DELTA + 1e-9:
                self._emit_taker_v1(
                    side=event.side,
                    price=ask,
                    requested_shares=V1_BASE_SHARES,
                    trigger="MAKER_FOLLOW",
                    reason=f"same-side maker fill proxy followed at ask; ask-maker delta={price_delta:.4f}",
                    core=core,
                    source_event_id=event.id,
                )

        core_side = core.get("side")
        previous = self.taker_v1_last_core_side
        if core_side in {"UP", "DOWN"}:
            if previous in {"UP", "DOWN"} and previous != core_side:
                ask = base._finite(book.get("upAsk" if core_side == "UP" else "downAsk"))
                self._emit_taker_v1(
                    side=str(core_side),
                    price=ask,
                    requested_shares=V1_CORE_FLIP_SHARES,
                    trigger="CORE_FLIP",
                    reason=f"core direction flipped {previous}->{core_side}; small independent taker probe",
                    core=core,
                )
            self.taker_v1_last_core_side = str(core_side)

    def _advance_shadow(self, book: dict[str, Any], core: dict[str, Any]) -> None:
        before = len(self.shadow_events)
        super()._advance_shadow(book, core)
        self._advance_taker_v1(self.shadow_events[before:], book, core)

    def _market_v1_events(self, market_id: int) -> list[dict[str, Any]]:
        with self.db_lock:
            start_row = self.db.execute(
                "SELECT started_at_ms FROM wallet_shadow_taker_v1_markets WHERE wallet=? AND market_id=?",
                (self.wallet, int(market_id)),
            ).fetchone()
            if start_row is None:
                return []
            started_at_ms = int(start_row[0])
            maker_rows = self.db.execute(
                """
                SELECT at_ms,event_type,role,side,price,shares
                FROM wallet_shadow_events
                WHERE wallet=? AND market_id=? AND at_ms>=? AND event_type='MAKER_FILL_PROXY'
                ORDER BY at_ms ASC
                """,
                (self.wallet, int(market_id), started_at_ms),
            ).fetchall()
            taker_rows = self.db.execute(
                """
                SELECT at_ms,side,price,shares
                FROM wallet_shadow_taker_v1_events
                WHERE wallet=? AND market_id=?
                ORDER BY at_ms ASC
                """,
                (self.wallet, int(market_id)),
            ).fetchall()
        events = [dict(row) for row in maker_rows]
        events.extend(
            {
                "at_ms": int(row["at_ms"]),
                "event_type": "TAKER_INTENT",
                "role": "TAKER",
                "side": row["side"],
                "price": row["price"],
                "shares": row["shares"],
            }
            for row in taker_rows
        )
        return sorted(events, key=lambda item: int(item.get("at_ms") or 0))

    def _store_v1_market_result(self, market_id: int, market: dict[str, Any], winner: str) -> None:
        with self.db_lock:
            registered = self.db.execute(
                "SELECT 1 FROM wallet_shadow_taker_v1_markets WHERE wallet=? AND market_id=?",
                (self.wallet, int(market_id)),
            ).fetchone()
        if registered is None:
            return
        result = v3.paper_market_result(self._market_v1_events(market_id), winner)
        title = str(market.get("title") or market.get("question") or "") or None
        maker = result["maker"]
        taker = result["taker"]
        now_ms = base._now_ms()
        with self.db_lock:
            self.db.execute(
                """
                INSERT INTO wallet_shadow_taker_v1_market_results(
                    wallet,market_id,title,winner,resolved_at_ms,traded,status,fill_count,
                    cost_usdt,payout_usdt,gross_pnl_usdt,gross_roi,
                    maker_cost_usdt,maker_pnl_usdt,taker_cost_usdt,taker_pnl_usdt
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(wallet,market_id) DO UPDATE SET
                    title=excluded.title,winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,
                    traded=excluded.traded,status=excluded.status,fill_count=excluded.fill_count,
                    cost_usdt=excluded.cost_usdt,payout_usdt=excluded.payout_usdt,
                    gross_pnl_usdt=excluded.gross_pnl_usdt,gross_roi=excluded.gross_roi,
                    maker_cost_usdt=excluded.maker_cost_usdt,maker_pnl_usdt=excluded.maker_pnl_usdt,
                    taker_cost_usdt=excluded.taker_cost_usdt,taker_pnl_usdt=excluded.taker_pnl_usdt
                """,
                (
                    self.wallet, int(market_id), title, winner, now_ms,
                    1 if result["traded"] else 0, result["status"], result["fillCount"],
                    result["costUsdt"], result["payoutUsdt"], result["grossPnlUsdt"], result["grossRoi"],
                    maker["costUsdt"], maker["grossPnlUsdt"], taker["costUsdt"], taker["grossPnlUsdt"],
                ),
            )
            self.db.commit()

    def _store_market_result(self, market_id: int, market: dict[str, Any], winner: str) -> None:
        super()._store_market_result(market_id, market, winner)
        self._store_v1_market_result(market_id, market, winner)

    def _cleanup_retention(self, *, force: bool = False) -> None:
        super()._cleanup_retention(force=force)
        now_ms = base._now_ms()
        cutoff = now_ms - self.retention_ms
        with self.db_lock:
            self.db.execute(
                "DELETE FROM wallet_shadow_taker_v1_events WHERE wallet=? AND at_ms<?",
                (self.wallet, cutoff),
            )
            self.db.execute(
                "DELETE FROM wallet_shadow_taker_v1_market_results WHERE wallet=? AND resolved_at_ms<?",
                (self.wallet, cutoff),
            )
            self.db.execute(
                "DELETE FROM wallet_shadow_taker_v1_markets WHERE wallet=? AND started_at_ms<?",
                (self.wallet, cutoff),
            )
            unresolved = self.db.execute(
                """
                SELECT m.market_id
                FROM wallet_shadow_taker_v1_markets m
                LEFT JOIN wallet_shadow_taker_v1_market_results r
                  ON r.wallet=m.wallet AND r.market_id=m.market_id
                WHERE m.wallet=? AND r.market_id IS NULL
                """,
                (self.wallet,),
            ).fetchall()
            self.db.commit()
        self.pending_settlement_ids.update(int(row[0]) for row in unresolved)

    def _v1_performance_snapshot(self) -> dict[str, Any]:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            summary = self.db.execute(
                """
                SELECT COUNT(*) AS settled_markets,
                       SUM(CASE WHEN traded=1 THEN 1 ELSE 0 END) AS traded_markets,
                       SUM(CASE WHEN traded=1 AND status='WIN' THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN traded=1 AND status='LOSS' THEN 1 ELSE 0 END) AS losses,
                       SUM(CASE WHEN traded=1 AND status='FLAT' THEN 1 ELSE 0 END) AS flats,
                       COALESCE(SUM(CASE WHEN traded=1 THEN cost_usdt ELSE 0 END),0) AS cost_usdt,
                       COALESCE(SUM(CASE WHEN traded=1 THEN gross_pnl_usdt ELSE 0 END),0) AS pnl_usdt,
                       COALESCE(SUM(CASE WHEN traded=1 THEN maker_pnl_usdt ELSE 0 END),0) AS maker_pnl_usdt,
                       COALESCE(SUM(CASE WHEN traded=1 THEN taker_cost_usdt ELSE 0 END),0) AS taker_cost_usdt,
                       COALESCE(SUM(CASE WHEN traded=1 THEN taker_pnl_usdt ELSE 0 END),0) AS taker_pnl_usdt
                FROM wallet_shadow_taker_v1_market_results
                WHERE wallet=? AND resolved_at_ms>=?
                """,
                (self.wallet, cutoff),
            ).fetchone()
            recent = self.db.execute(
                """
                SELECT market_id,title,winner,resolved_at_ms,traded,status,fill_count,
                       cost_usdt,gross_pnl_usdt,gross_roi,maker_pnl_usdt,taker_pnl_usdt
                FROM wallet_shadow_taker_v1_market_results
                WHERE wallet=? AND resolved_at_ms>=?
                ORDER BY resolved_at_ms DESC LIMIT 30
                """,
                (self.wallet, cutoff),
            ).fetchall()
            event_rows = self.db.execute(
                "SELECT COUNT(*) FROM wallet_shadow_taker_v1_events WHERE wallet=? AND at_ms>=?",
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
            "winRate": wins / traded if traded else None,
            "grossCostUsdt": cost,
            "grossPnlUsdt": pnl,
            "grossRoi": pnl / cost if cost > 0 else None,
            "makerGrossPnlUsdt": float(data.get("maker_pnl_usdt") or 0.0),
            "takerCostUsdt": float(data.get("taker_cost_usdt") or 0.0),
            "takerGrossPnlUsdt": float(data.get("taker_pnl_usdt") or 0.0),
            "storedTakerEvents": int(event_rows),
            "recentMarkets": [dict(row) for row in recent],
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        target = sorted(self.parents.values(), key=lambda item: item.first_event_ms, reverse=True)
        inv = self._v1_inventory()
        payload["takerV1"] = {
            "paperOnly": True,
            "causalStart": True,
            "config": {
                "baseShares": V1_BASE_SHARES,
                "coreFlipShares": V1_CORE_FLIP_SHARES,
                "maxEventShares": V1_MAX_EVENT_SHARES,
                "maxNetShares": V1_MAX_NET_SHARES,
                "maxTotalShares": V1_MAX_TOTAL_SHARES,
                "followMaxPriceDelta": V1_FOLLOW_MAX_PRICE_DELTA,
                "maxAsk": V1_MAX_ASK,
                "cooldownMs": V1_COOLDOWN_MS,
                "triggers": ["MAKER_FOLLOW", "CORE_FLIP"],
                "directionRule": "inventory limits risk only; it does not choose taker side",
            },
            "current": {
                "eventCount": len(self.taker_v1_events),
                "inventory": {
                    **inv,
                    "residualSide": "UP" if inv["delta"] > 0 else "DOWN" if inv["delta"] < 0 else None,
                },
                "events": list(reversed(self.taker_v1_events[-120:])),
            },
            "similarity": taker_v1_similarity(target, self.taker_v1_events),
            "performance": self._v1_performance_snapshot(),
        }
        return payload


class _Handler(base._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"target={observer.wallet}; retention={v3.RETENTION_DAYS}d; takerV1=paper; "
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
