from __future__ import annotations

import sqlite3
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import target_wallet_official_v1 as v1


VERSION = "TARGET_WALLET_OFFICIAL_V2_LEGACY_HISTORY"
LEGACY_HISTORY_TABLE = "wallet_shadow_target_market_results"
HISTORY_CACHE_MS = 5_000
HISTORY_RECENT_LIMIT = 100


def _status_from_pnl(value: Any) -> str:
    pnl = v1.finite(value)
    if pnl is None:
        return "UNKNOWN"
    if pnl > 1e-9:
        return "WIN"
    if pnl < -1e-9:
        return "LOSS"
    return "FLAT"


class TargetWalletOfficialCollector(v1.TargetWalletOfficialCollector):
    """Official collector plus read-only continuity for the pre-rebuild Target ledger.

    The legacy database is never migrated into, modified by, or used to drive any
    strategy. It is queried only to restore the Target wallet's already-settled
    historical win/loss/accounting rows that Dashboard V2 exposed before 8776 was
    rebuilt. New 8776 rows win on market-id collisions.
    """

    def __init__(self, db_path=v1.DB_PATH) -> None:
        self._history_lock = threading.RLock()
        self._history_cache: dict[str, Any] | None = None
        self._history_cache_at_ms = 0
        super().__init__(db_path)

    @staticmethod
    def _legacy_uri(path: Path) -> str:
        return f"file:{path.resolve().as_posix()}?mode=ro"

    def _legacy_rows(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        path = Path(v1.LEGACY_DB_PATH)
        source = {
            "path": str(path),
            "mode": "READ_ONLY",
            "table": LEGACY_HISTORY_TABLE,
            "available": False,
            "rows": 0,
            "error": None,
        }
        if not path.exists():
            source["error"] = "legacy database not found"
            return [], source
        db: sqlite3.Connection | None = None
        try:
            db = sqlite3.connect(self._legacy_uri(path), uri=True, timeout=2.0)
            db.row_factory = sqlite3.Row
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (LEGACY_HISTORY_TABLE,),
            ).fetchone()
            if exists is None:
                source["error"] = f"{LEGACY_HISTORY_TABLE} not present in legacy database"
                return [], source
            rows = [
                dict(row)
                for row in db.execute(
                    f"""SELECT market_id,title,winner,resolved_at_ms,historical_reconstruction,
                                accounting_mode,status,event_count,maker_event_count,taker_event_count,
                                buy_notional_usdt,sell_proceeds_usdt,collateral_fees_usdt,payout_usdt,
                                net_pnl_usdt,net_roi,maker_notional_usdt,maker_pnl_usdt,
                                taker_notional_usdt,taker_pnl_usdt,gross_up_shares,gross_down_shares,
                                share_fees_up,share_fees_down,net_up_shares,net_down_shares,
                                share_conviction_side,capital_conviction_side,
                                share_direction_correct,capital_direction_correct
                           FROM {LEGACY_HISTORY_TABLE}
                          WHERE wallet=? AND event_count>0
                          ORDER BY resolved_at_ms,market_id""",
                    (self.wallet,),
                )
            ]
            for row in rows:
                row["asset"] = "BTC"
                row["source"] = "LEGACY_WALLET_SHADOW_TARGET_ACCOUNTING"
                row["sourceDatabase"] = str(path)
                row["sourceReadOnly"] = True
                row["fill_count"] = int(row.get("event_count") or 0)
                row["parent_count"] = None
                row["maker_net_pnl_usdt"] = row.get("maker_pnl_usdt")
                row["taker_net_pnl_usdt"] = row.get("taker_pnl_usdt")
                row["up_position_shares"] = row.get("net_up_shares")
                row["down_position_shares"] = row.get("net_down_shares")
            source["available"] = True
            source["rows"] = len(rows)
            return rows, source
        except (sqlite3.Error, OSError) as exc:
            source["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
            return [], source
        finally:
            if db is not None:
                db.close()

    def _current_history_rows(self) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = [
                dict(row)
                for row in self.db.execute(
                    """SELECT market_id,asset,title,winner,resolved_at_ms,fill_count,parent_count,
                              buy_notional_usdt,sell_proceeds_usdt,payout_usdt,net_pnl_usdt,net_roi,
                              maker_net_pnl_usdt,taker_net_pnl_usdt,up_position_shares,down_position_shares,
                              accounting_version
                         FROM target_market_results
                        WHERE fill_count>0
                        ORDER BY resolved_at_ms,market_id"""
                )
            ]
        for row in rows:
            up = v1.finite(row.get("up_position_shares")) or 0.0
            down = v1.finite(row.get("down_position_shares")) or 0.0
            row.update(
                {
                    "status": _status_from_pnl(row.get("net_pnl_usdt")),
                    "event_count": int(row.get("fill_count") or 0),
                    "historical_reconstruction": 0,
                    "accounting_mode": row.get("accounting_version"),
                    "collateral_fees_usdt": None,
                    "maker_event_count": None,
                    "taker_event_count": None,
                    "maker_notional_usdt": None,
                    "maker_pnl_usdt": row.get("maker_net_pnl_usdt"),
                    "taker_notional_usdt": None,
                    "taker_pnl_usdt": row.get("taker_net_pnl_usdt"),
                    "net_up_shares": up,
                    "net_down_shares": down,
                    "share_conviction_side": "UP" if up > down else "DOWN" if down > up else None,
                    "capital_conviction_side": None,
                    "share_direction_correct": None,
                    "capital_direction_correct": None,
                    "source": "TARGET_WALLET_OFFICIAL_V2",
                    "sourceDatabase": str(self.db_path),
                    "sourceReadOnly": False,
                }
            )
        return rows

    def _historical_performance(self) -> dict[str, Any]:
        now = v1.now_ms()
        with self._history_lock:
            if self._history_cache is not None and now - self._history_cache_at_ms < HISTORY_CACHE_MS:
                return dict(self._history_cache)

        legacy_rows, legacy_source = self._legacy_rows()
        current_rows = self._current_history_rows()
        merged: dict[int, dict[str, Any]] = {}
        for row in legacy_rows:
            market_id = v1.positive_int(row.get("market_id"))
            if market_id is not None:
                merged[market_id] = row
        # New official accounting is authoritative for overlapping markets.
        for row in current_rows:
            market_id = v1.positive_int(row.get("market_id"))
            if market_id is not None:
                merged[market_id] = row

        ordered = sorted(
            merged.values(),
            key=lambda row: (int(row.get("resolved_at_ms") or 0), int(row.get("market_id") or 0)),
        )
        wins = sum(str(row.get("status") or "") == "WIN" for row in ordered)
        losses = sum(str(row.get("status") or "") == "LOSS" for row in ordered)
        flats = sum(str(row.get("status") or "") == "FLAT" for row in ordered)
        buy = sum(float(v1.finite(row.get("buy_notional_usdt")) or 0.0) for row in ordered)
        sell = sum(float(v1.finite(row.get("sell_proceeds_usdt")) or 0.0) for row in ordered)
        payout = sum(float(v1.finite(row.get("payout_usdt")) or 0.0) for row in ordered)
        pnl = sum(float(v1.finite(row.get("net_pnl_usdt")) or 0.0) for row in ordered)
        maker_pnl = sum(
            float(v1.finite(row.get("maker_net_pnl_usdt") if row.get("maker_net_pnl_usdt") is not None else row.get("maker_pnl_usdt")) or 0.0)
            for row in ordered
        )
        taker_pnl = sum(
            float(v1.finite(row.get("taker_net_pnl_usdt") if row.get("taker_net_pnl_usdt") is not None else row.get("taker_pnl_usdt")) or 0.0)
            for row in ordered
        )
        share_comparable = [row for row in ordered if row.get("share_direction_correct") is not None]
        capital_comparable = [row for row in ordered if row.get("capital_direction_correct") is not None]
        payload = {
            "scope": "ALL_AVAILABLE_RETAINED_TARGET_HISTORY",
            "settledMarkets": len(ordered),
            "wins": wins,
            "losses": losses,
            "flats": flats,
            "winRate": wins / len(ordered) if ordered else None,
            "buyNotionalUsdt": buy,
            "sellProceedsUsdt": sell,
            "payoutUsdt": payout,
            "netPnlUsdt": pnl,
            "netRoi": pnl / buy if buy > 1e-12 else None,
            "makerNetPnlUsdt": maker_pnl,
            "takerNetPnlUsdt": taker_pnl,
            "historicallyReconstructedMarkets": sum(bool(row.get("historical_reconstruction")) for row in ordered),
            "legacyMarkets": sum(row.get("source") == "LEGACY_WALLET_SHADOW_TARGET_ACCOUNTING" for row in ordered),
            "currentOfficialMarkets": sum(row.get("source") == "TARGET_WALLET_OFFICIAL_V2" for row in ordered),
            "targetOfficialAccuracy": {
                "shareComparable": len(share_comparable),
                "shareCorrect": sum(bool(row.get("share_direction_correct")) for row in share_comparable),
                "shareAccuracy": (
                    sum(bool(row.get("share_direction_correct")) for row in share_comparable) / len(share_comparable)
                    if share_comparable else None
                ),
                "capitalComparable": len(capital_comparable),
                "capitalCorrect": sum(bool(row.get("capital_direction_correct")) for row in capital_comparable),
                "capitalAccuracy": (
                    sum(bool(row.get("capital_direction_correct")) for row in capital_comparable) / len(capital_comparable)
                    if capital_comparable else None
                ),
            },
            "recentMarkets": list(reversed(ordered[-HISTORY_RECENT_LIMIT:])),
            "sources": {
                "legacy": legacy_source,
                "current": {
                    "path": str(self.db_path),
                    "mode": "READ_WRITE_OFFICIAL_COLLECTOR",
                    "table": "target_market_results",
                    "rows": len(current_rows),
                },
            },
            "dedupeRule": "market_id; current TARGET_WALLET_OFFICIAL result overrides legacy row",
            "accountingCaveat": (
                "Legacy rows are the exact pre-rebuild Target accounting ledger restored read-only. "
                "Current rows use the rebuilt official collector accounting. They are combined for historical display only; "
                "neither source is a strategy signal."
            ),
        }
        with self._history_lock:
            self._history_cache = payload
            self._history_cache_at_ms = now
        return dict(payload)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        history = self._historical_performance()
        payload["targetHistoricalPerformance"] = history
        payload["targetHistoricalRecentMarkets"] = history["recentMarkets"]
        payload["historicalTargetResultsRestored"] = True
        payload["historicalTargetResultsStrategyInput"] = False
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        history = self._historical_performance()
        legacy = history.get("sources", {}).get("legacy", {})
        payload["historicalTargetResults"] = {
            "restored": True,
            "strategyInput": False,
            "legacyReadOnly": True,
            "legacyAvailable": bool(legacy.get("available")),
            "legacyRows": int(legacy.get("rows") or 0),
            "legacyError": legacy.get("error"),
            "combinedSettledMarkets": int(history.get("settledMarkets") or 0),
        }
        return payload


class Handler(v1.Handler):
    collector: TargetWalletOfficialCollector


def main() -> int:
    collector = TargetWalletOfficialCollector()
    collector.start()
    handler = type("TargetWalletOfficialV2Handler", (Handler,), {"collector": collector})
    server = ThreadingHTTPServer((v1.HOST, v1.PORT), handler)
    print(
        f"{VERSION} listening on http://{v1.HOST}:{v1.PORT}/state; target={collector.wallet}; "
        f"db={collector.db_path}; legacyHistory={v1.LEGACY_DB_PATH}; legacyReadOnly=true; "
        "strategyLogic=false; liveOrdersAffected=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
        collector.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
