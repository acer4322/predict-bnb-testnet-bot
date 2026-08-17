from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("PREDICT_TARGET_WALLET_OFFICIAL_DB", ROOT / "data" / "target_wallet_official_v1.db"))
LEGACY_DB_PATH = Path(os.environ.get("PREDICT_WALLET_SHADOW_LEGACY_DB", ROOT / "data" / "predict_wallet_shadow.db"))
HOST = os.environ.get("PREDICT_TARGET_WALLET_OFFICIAL_HOST", "127.0.0.1")
PORT = int(os.environ.get("PREDICT_TARGET_WALLET_OFFICIAL_PORT", "8776"))
API_BASE = os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun").rstrip("/")
API_KEY_ENV = "PREDICT_FUN_API_KEY"
TARGET_WALLET = os.environ.get(
    "PREDICT_TARGET_WALLET_ADDRESS",
    os.environ.get("PREDICT_WALLET_SHADOW_TARGET_ADDRESS", "0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03"),
).strip().lower()
PREDICT_OBSERVER_URL = os.environ.get("PREDICT_TARGET_WALLET_PREDICT_OBSERVER_URL", "http://127.0.0.1:8771/state")
POLL_SECONDS = max(0.25, float(os.environ.get("PREDICT_TARGET_WALLET_POLL_SECONDS", "0.5")))
INITIAL_BACKFILL_PAGES = max(1, min(10, int(os.environ.get("PREDICT_TARGET_WALLET_BACKFILL_PAGES", "4"))))
SETTLEMENT_POLL_SECONDS = max(2.0, float(os.environ.get("PREDICT_TARGET_WALLET_SETTLEMENT_POLL_SECONDS", "5")))
PERFORMANCE_WINDOW_DAYS = max(1, int(os.environ.get("PREDICT_TARGET_WALLET_PERFORMANCE_WINDOW_DAYS", "30")))
VERSION = "TARGET_WALLET_OFFICIAL_V1"
ASSETS = ("BTC", "ETH")
WEI = 10**18


def now_ms() -> int:
    return int(time.time() * 1000)


def record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def rows(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def positive_int(value: Any) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def iso_ms(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def dec_wei(value: Any) -> float | None:
    try:
        result = float(value) / WEI
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def side(value: Any) -> str:
    if isinstance(value, dict):
        return side(value.get("name") or value.get("label") or value.get("outcome"))
    text = str(value or "").strip().upper()
    if text == "UP" or " UP" in text:
        return "UP"
    if text == "DOWN" or " DOWN" in text:
        return "DOWN"
    return "UNKNOWN"


def quote_type(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BID", "BUY"}:
        return "BID"
    if text in {"ASK", "SELL"}:
        return "ASK"
    return "UNKNOWN"


def unwrap_state(payload: Any) -> dict[str, Any]:
    current = record(payload)
    for _ in range(3):
        nested = current.get("state")
        if isinstance(nested, dict) and ("ok" in current or len(current) <= 3):
            current = nested
            continue
        nested = current.get("data")
        if isinstance(nested, dict) and ("ok" in current or "success" in current):
            current = nested
            continue
        break
    return current


def normalize_match_leg(raw: Any, *, wallet: str, role: str, maker_index: int | None = None) -> dict[str, Any] | None:
    row = record(raw)
    market = record(row.get("market"))
    market_id = positive_int(market.get("id"))
    event_ms = iso_ms(row.get("executedAt"))
    if market_id is None or event_ms is None:
        return None
    if role == "TAKER":
        participant = record(row.get("taker"))
    else:
        makers = rows(row.get("makers"))
        if maker_index is None or maker_index < 0 or maker_index >= len(makers):
            return None
        participant = makers[maker_index]
    signer = str(participant.get("signer") or "").strip().lower()
    if signer != wallet.lower():
        return None
    amount = dec_wei(participant.get("amount"))
    price = dec_wei(participant.get("price"))
    if amount is None:
        amount = dec_wei(row.get("amountFilled"))
    if price is None:
        price = dec_wei(row.get("priceExecuted"))
    if amount is None or amount <= 0 or price is None or not 0 <= price <= 1:
        return None
    outcome = side(participant.get("outcome"))
    qtype = quote_type(participant.get("quoteType"))
    if outcome not in {"UP", "DOWN"} or qtype not in {"BID", "ASK"}:
        return None
    order_hash = str(participant.get("hash") or "").strip() or None
    transaction_hash = str(row.get("transactionHash") or "").strip() or None
    settlement_id = str(row.get("settlementId") or "").strip() or None
    leg_id = ":".join([
        role, order_hash or "NO_ORDER", transaction_hash or "NO_TX", settlement_id or "NO_SETTLEMENT",
        str(maker_index if maker_index is not None else "TAKER"), outcome, qtype,
        f"{amount:.12f}", f"{price:.12f}", str(event_ms),
    ])
    return {
        "legId": leg_id, "role": role, "marketId": market_id,
        "title": str(market.get("title") or market.get("question") or "") or None,
        "side": outcome, "quoteType": qtype, "orderHash": order_hash,
        "transactionHash": transaction_hash, "settlementId": settlement_id,
        "eventMs": event_ms, "price": float(price), "shares": float(amount),
    }


def resolved_winner(market: dict[str, Any]) -> str | None:
    for outcome in rows(market.get("outcomes")):
        status = str(outcome.get("status") or "").upper().strip()
        if outcome.get("isWinner") is True or outcome.get("won") is True or status == "WON":
            candidate = side(outcome.get("name") or outcome.get("outcome"))
            if candidate in {"UP", "DOWN"}:
                return candidate
    resolution = record(market.get("resolution"))
    if str(resolution.get("status") or "").upper().strip() in {"WON", "RESOLVED", "SETTLED"}:
        candidate = side(resolution.get("name") or resolution.get("outcome"))
        if candidate in {"UP", "DOWN"}:
            return candidate
    variant = record(market.get("variantData"))
    start = finite(variant.get("startPrice"))
    end = finite(variant.get("endPrice"))
    if start is not None and end is not None and abs(end - start) > 1e-15:
        return "UP" if end > start else "DOWN"
    return None


class TargetWalletOfficialCollector:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.api_key = str(os.environ.get(API_KEY_ENV) or "").strip()
        self.wallet = TARGET_WALLET
        self.db_path = Path(db_path)
        self.started_at_ms = now_ms()
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.db_lock = threading.RLock()
        self.http = httpx.Client(
            timeout=httpx.Timeout(8.0, connect=2.0), trust_env=False,
            headers={"Accept": "application/json", **({"x-api-key": self.api_key} if self.api_key else {})},
        )
        self.local_http = httpx.Client(timeout=httpx.Timeout(1.5, connect=0.4), trust_env=False)
        self.current_markets: dict[str, dict[str, Any]] = {asset: {} for asset in ASSETS}
        self.initialized_roles: set[tuple[str, int, str]] = set()
        self.last_poll_ms: int | None = None
        self.last_fill_ms: int | None = None
        self.last_settlement_ms: int | None = None
        self.last_error: str | None = None
        self.poll_requests = 0
        self.fills_inserted_run = 0
        self.parents_updated_run = 0
        self.markets_settled_run = 0
        self.snapshot_cache_at = 0
        self.snapshot_cache: dict[str, Any] | None = None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_size_limit=67108864")
        self._create_schema()
        self._seed_compatibility_rowid()
        self._write_meta()

    def _create_schema(self) -> None:
        with self.db_lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS target_service_meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_shadow_target_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    leg_id TEXT NOT NULL UNIQUE, wallet TEXT NOT NULL, asset TEXT NOT NULL,
                    market_id INTEGER NOT NULL, role TEXT NOT NULL, side TEXT NOT NULL, quote_type TEXT NOT NULL,
                    order_hash TEXT, transaction_hash TEXT, settlement_id TEXT,
                    event_ms INTEGER NOT NULL, observed_at_ms INTEGER NOT NULL,
                    price REAL NOT NULL, shares REAL NOT NULL, raw_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_target_events_asset_market_time
                    ON wallet_shadow_target_events(asset,market_id,event_ms);
                CREATE INDEX IF NOT EXISTS idx_target_events_role_time
                    ON wallet_shadow_target_events(role,event_ms);
                CREATE TABLE IF NOT EXISTS wallet_shadow_target_event_context (
                    leg_id TEXT PRIMARY KEY, observed_at_ms INTEGER NOT NULL, asset TEXT NOT NULL, market_id INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS target_parent_orders (
                    parent_id TEXT PRIMARY KEY, wallet TEXT NOT NULL, asset TEXT NOT NULL, market_id INTEGER NOT NULL,
                    role TEXT NOT NULL, side TEXT NOT NULL, quote_type TEXT NOT NULL, order_hash TEXT,
                    first_event_ms INTEGER NOT NULL, last_event_ms INTEGER NOT NULL,
                    average_price REAL NOT NULL, shares REAL NOT NULL, fill_legs INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_target_parents_asset_market_time
                    ON target_parent_orders(asset,market_id,last_event_ms);
                CREATE TABLE IF NOT EXISTS target_markets (
                    market_id INTEGER PRIMARY KEY, asset TEXT NOT NULL, title TEXT, window_end_ms INTEGER,
                    first_seen_ms INTEGER NOT NULL, last_seen_ms INTEGER NOT NULL, status TEXT NOT NULL,
                    winner TEXT, resolved_at_ms INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_target_markets_status ON target_markets(status,window_end_ms);
                CREATE TABLE IF NOT EXISTS target_market_results (
                    market_id INTEGER PRIMARY KEY, asset TEXT NOT NULL, title TEXT, winner TEXT NOT NULL,
                    resolved_at_ms INTEGER NOT NULL, fill_count INTEGER NOT NULL, parent_count INTEGER NOT NULL,
                    buy_notional_usdt REAL NOT NULL, sell_proceeds_usdt REAL NOT NULL, payout_usdt REAL NOT NULL,
                    net_pnl_usdt REAL NOT NULL, net_roi REAL, maker_net_pnl_usdt REAL NOT NULL, taker_net_pnl_usdt REAL NOT NULL,
                    up_position_shares REAL NOT NULL, down_position_shares REAL NOT NULL, accounting_version TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_target_results_asset_resolved ON target_market_results(asset,resolved_at_ms);
            """)
            self.db.commit()

    def _seed_compatibility_rowid(self) -> None:
        with self.db_lock:
            existing = int(self.db.execute("SELECT COUNT(*) FROM wallet_shadow_target_events").fetchone()[0])
            seeded = self.db.execute("SELECT value FROM target_service_meta WHERE key='legacy_rowid_seed'").fetchone()
        if existing or seeded is not None:
            return
        legacy_max = 0
        if LEGACY_DB_PATH.exists() and LEGACY_DB_PATH.resolve() != self.db_path.resolve():
            try:
                con = sqlite3.connect(f"file:{LEGACY_DB_PATH.resolve()}?mode=ro", uri=True, timeout=2.0)
                try:
                    legacy_max = int(con.execute("SELECT COALESCE(MAX(rowid),0) FROM wallet_shadow_target_events").fetchone()[0] or 0)
                finally:
                    con.close()
            except sqlite3.Error:
                legacy_max = 0
        with self.db_lock:
            if legacy_max > 0:
                self.db.execute("INSERT OR REPLACE INTO sqlite_sequence(name,seq) VALUES('wallet_shadow_target_events',?)", (legacy_max,))
            self.db.execute(
                "INSERT OR REPLACE INTO target_service_meta(key,value,updated_at_ms) VALUES('legacy_rowid_seed',?,?)",
                (str(legacy_max), now_ms()),
            )
            self.db.commit()

    def _write_meta(self) -> None:
        meta = {
            "version": VERSION, "targetWallet": self.wallet, "dbPath": str(self.db_path),
            "legacyDbReadOnly": str(LEGACY_DB_PATH), "assets": list(ASSETS),
            "retention": "PERMANENT_UNLESS_MANUALLY_ARCHIVED", "liveOrdersAffected": False, "strategyLogic": False,
        }
        with self.db_lock:
            self.db.execute(
                "INSERT OR REPLACE INTO target_service_meta(key,value,updated_at_ms) VALUES('service',?,?)",
                (json.dumps(meta, separators=(",", ":")), now_ms()),
            )
            self.db.commit()

    def start(self) -> None:
        if not self.api_key:
            self.last_error = f"{API_KEY_ENV} is not configured"
            return
        if not (self.wallet.startswith("0x") and len(self.wallet) == 42):
            self.last_error = "target wallet address is invalid"
            return
        threading.Thread(target=self._poll_loop, name="target-official-poll", daemon=True).start()
        threading.Thread(target=self._settlement_loop, name="target-official-settlement", daemon=True).start()
        threading.Thread(target=self._checkpoint_loop, name="target-official-checkpoint", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()
        self.http.close()
        self.local_http.close()
        with self.db_lock:
            self.db.commit()
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.db.close()

    def _observer_markets(self) -> dict[str, dict[str, Any]]:
        response = self.local_http.get(PREDICT_OBSERVER_URL)
        response.raise_for_status()
        state = unwrap_state(response.json())
        asset_map = record(state.get("assets"))
        out: dict[str, dict[str, Any]] = {}
        for asset in ASSETS:
            asset_state = record(asset_map.get(asset))
            market = record(asset_state.get("market"))
            market_id = positive_int(market.get("id"))
            if market_id is None:
                continue
            out[asset] = {
                "marketId": market_id,
                "title": str(market.get("title") or market.get("question") or "") or None,
                "windowEndMs": positive_int(market.get("windowEndMs") or asset_state.get("windowEndMs")),
                "secondsLeft": finite(asset_state.get("secondsLeft")),
            }
        return out

    def _poll_loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                markets = self._observer_markets()
                for asset, market in markets.items():
                    self._activate_market(asset, market)
                    self._poll_market(asset, int(market["marketId"]))
                self.last_poll_ms = now_ms()
                self.last_error = None
            except Exception as exc:
                self.last_error = f"poll: {type(exc).__name__}: {str(exc)[:400]}"
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.05, POLL_SECONDS - elapsed))

    def _activate_market(self, asset: str, market: dict[str, Any]) -> None:
        market_id = int(market["marketId"])
        at = now_ms()
        with self.lock:
            previous = positive_int(self.current_markets.get(asset, {}).get("marketId"))
            self.current_markets[asset] = dict(market)
        with self.db_lock:
            if previous is not None and previous != market_id:
                self.db.execute(
                    "UPDATE target_markets SET status='PENDING_SETTLEMENT',last_seen_ms=? WHERE market_id=? AND winner IS NULL",
                    (at, previous),
                )
            self.db.execute(
                """INSERT INTO target_markets(market_id,asset,title,window_end_ms,first_seen_ms,last_seen_ms,status)
                   VALUES(?,?,?,?,?,?,'ACTIVE')
                   ON CONFLICT(market_id) DO UPDATE SET
                     asset=excluded.asset,title=COALESCE(excluded.title,target_markets.title),
                     window_end_ms=COALESCE(excluded.window_end_ms,target_markets.window_end_ms),
                     last_seen_ms=excluded.last_seen_ms,
                     status=CASE WHEN target_markets.winner IS NULL THEN 'ACTIVE' ELSE target_markets.status END""",
                (market_id, asset, market.get("title"), market.get("windowEndMs"), at, at),
            )
            self.db.commit()

    def _fetch_matches_page(self, market_id: int, role: str, after: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "first": 100, "marketId": int(market_id), "signerAddress": self.wallet,
            "isSignerMaker": "true" if role == "MAKER" else "false",
        }
        if after:
            params["after"] = after
        response = self.http.get(f"{API_BASE}/v1/orders/matches", params=params)
        response.raise_for_status()
        payload = record(response.json())
        if payload.get("success") is False:
            raise RuntimeError(f"Predict matches rejected role={role} market={market_id}")
        self.poll_requests += 1
        return payload

    def _poll_market(self, asset: str, market_id: int) -> None:
        for role in ("MAKER", "TAKER"):
            key = (asset, int(market_id), role)
            pages = INITIAL_BACKFILL_PAGES if key not in self.initialized_roles else 1
            after: str | None = None
            for _ in range(pages):
                payload = self._fetch_matches_page(market_id, role, after)
                data = rows(payload.get("data"))
                observed = now_ms()
                for raw in data:
                    participant_count = 1 if role == "TAKER" else len(rows(raw.get("makers")))
                    for index in range(participant_count):
                        leg = normalize_match_leg(
                            raw, wallet=self.wallet, role=role,
                            maker_index=None if role == "TAKER" else index,
                        )
                        if leg is None or int(leg["marketId"]) != int(market_id):
                            continue
                        self._persist_leg(asset, leg, raw, observed_at_ms=observed)
                cursor = str(payload.get("cursor") or "").strip() or None
                if not cursor or not data:
                    break
                after = cursor
            self.initialized_roles.add(key)

    def _persist_leg(self, asset: str, leg: dict[str, Any], raw: dict[str, Any], *, observed_at_ms: int) -> bool:
        at = int(observed_at_ms)
        with self.db_lock:
            cursor = self.db.execute(
                """INSERT OR IGNORE INTO wallet_shadow_target_events(
                       leg_id,wallet,asset,market_id,role,side,quote_type,order_hash,transaction_hash,settlement_id,
                       event_ms,observed_at_ms,price,shares,raw_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    leg["legId"], self.wallet, asset, leg["marketId"], leg["role"], leg["side"], leg["quoteType"],
                    leg.get("orderHash"), leg.get("transactionHash"), leg.get("settlementId"), leg["eventMs"], at,
                    leg["price"], leg["shares"], json.dumps(raw, separators=(",", ":"), default=str),
                ),
            )
            if cursor.rowcount != 1:
                return False
            self.db.execute(
                "INSERT OR IGNORE INTO wallet_shadow_target_event_context(leg_id,observed_at_ms,asset,market_id) VALUES(?,?,?,?)",
                (leg["legId"], at, asset, leg["marketId"]),
            )
            self._upsert_parent_locked(asset, leg, at)
            self.db.commit()
        self.fills_inserted_run += 1
        self.last_fill_ms = max(int(leg["eventMs"]), self.last_fill_ms or 0)
        self.snapshot_cache = None
        return True

    def _upsert_parent_locked(self, asset: str, leg: dict[str, Any], at: int) -> None:
        parent_identity = str(leg.get("orderHash") or leg["legId"])
        parent_id = f"{asset}:{leg['role']}:{parent_identity}:{leg['side']}:{leg['quoteType']}"
        existing = self.db.execute(
            "SELECT average_price,shares,fill_legs,first_event_ms,last_event_ms FROM target_parent_orders WHERE parent_id=?",
            (parent_id,),
        ).fetchone()
        shares = float(leg["shares"])
        price = float(leg["price"])
        event_ms = int(leg["eventMs"])
        if existing is None:
            self.db.execute(
                """INSERT INTO target_parent_orders(
                       parent_id,wallet,asset,market_id,role,side,quote_type,order_hash,first_event_ms,last_event_ms,
                       average_price,shares,fill_legs,updated_at_ms
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (parent_id, self.wallet, asset, leg["marketId"], leg["role"], leg["side"], leg["quoteType"], leg.get("orderHash"), event_ms, event_ms, price, shares, 1, at),
            )
        else:
            previous_shares = float(existing["shares"])
            total = previous_shares + shares
            average = ((float(existing["average_price"]) * previous_shares) + (price * shares)) / total if total > 0 else price
            self.db.execute(
                """UPDATE target_parent_orders SET first_event_ms=?,last_event_ms=?,average_price=?,shares=?,fill_legs=?,updated_at_ms=?
                   WHERE parent_id=?""",
                (min(int(existing["first_event_ms"]), event_ms), max(int(existing["last_event_ms"]), event_ms), average, total, int(existing["fill_legs"]) + 1, at, parent_id),
            )
        self.parents_updated_run += 1

    def _settlement_loop(self) -> None:
        while not self.stop_event.wait(SETTLEMENT_POLL_SECONDS):
            try:
                self._settle_pending()
            except Exception as exc:
                self.last_error = f"settlement: {type(exc).__name__}: {str(exc)[:400]}"

    def _settle_pending(self) -> None:
        current_ids = {
            int(value["marketId"]) for value in self.current_markets.values()
            if positive_int(value.get("marketId")) is not None
        }
        at = now_ms()
        with self.db_lock:
            candidates = [dict(row) for row in self.db.execute(
                """SELECT market_id,asset,title,window_end_ms FROM target_markets
                   WHERE winner IS NULL AND status!='SETTLED'
                   ORDER BY COALESCE(window_end_ms,first_seen_ms) ASC LIMIT 12"""
            )]
        for item in candidates:
            market_id = int(item["market_id"])
            if market_id in current_ids:
                continue
            window_end = positive_int(item.get("window_end_ms"))
            if window_end is not None and at < window_end + 2_000:
                continue
            try:
                market = self._fetch_market(market_id)
                winner = resolved_winner(market)
                if winner in {"UP", "DOWN"}:
                    self._store_market_result(item, market, winner)
            except Exception as exc:
                self.last_error = f"settlement market {market_id}: {type(exc).__name__}: {str(exc)[:300]}"

    def _fetch_market(self, market_id: int) -> dict[str, Any]:
        response = self.http.get(f"{API_BASE}/v1/markets/{int(market_id)}")
        response.raise_for_status()
        payload = record(response.json())
        if payload.get("success") is False:
            raise RuntimeError(f"Predict market lookup rejected market={market_id}")
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    @staticmethod
    def _account_rows(fill_rows: list[dict[str, Any]], winner: str, role: str | None = None) -> dict[str, float | int | None]:
        selected = fill_rows if role is None else [item for item in fill_rows if str(item["role"]) == role]
        buy = sell = 0.0
        positions = {"UP": 0.0, "DOWN": 0.0}
        for item in selected:
            price = float(item["price"])
            shares = float(item["shares"])
            outcome = str(item["side"])
            if str(item["quote_type"]) == "BID":
                buy += price * shares
                positions[outcome] += shares
            elif str(item["quote_type"]) == "ASK":
                sell += price * shares
                positions[outcome] -= shares
        payout = positions[winner]
        pnl = sell + payout - buy
        return {
            "buyNotionalUsdt": buy, "sellProceedsUsdt": sell, "payoutUsdt": payout,
            "netPnlUsdt": pnl, "netRoi": pnl / buy if buy > 1e-12 else None,
            "upPositionShares": positions["UP"], "downPositionShares": positions["DOWN"], "fillCount": len(selected),
        }

    def _store_market_result(self, item: dict[str, Any], market: dict[str, Any], winner: str) -> None:
        market_id = int(item["market_id"])
        with self.db_lock:
            fill_rows = [dict(row) for row in self.db.execute(
                "SELECT role,side,quote_type,price,shares FROM wallet_shadow_target_events WHERE market_id=? ORDER BY event_ms,id",
                (market_id,),
            )]
            parent_count = int(self.db.execute("SELECT COUNT(*) FROM target_parent_orders WHERE market_id=?", (market_id,)).fetchone()[0])
        total = self._account_rows(fill_rows, winner)
        maker = self._account_rows(fill_rows, winner, "MAKER")
        taker = self._account_rows(fill_rows, winner, "TAKER")
        resolved_at = now_ms()
        title = str(market.get("title") or market.get("question") or item.get("title") or "") or None
        with self.db_lock:
            self.db.execute(
                """INSERT INTO target_market_results(
                     market_id,asset,title,winner,resolved_at_ms,fill_count,parent_count,buy_notional_usdt,sell_proceeds_usdt,
                     payout_usdt,net_pnl_usdt,net_roi,maker_net_pnl_usdt,taker_net_pnl_usdt,up_position_shares,down_position_shares,accounting_version
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(market_id) DO UPDATE SET
                     asset=excluded.asset,title=excluded.title,winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,
                     fill_count=excluded.fill_count,parent_count=excluded.parent_count,buy_notional_usdt=excluded.buy_notional_usdt,
                     sell_proceeds_usdt=excluded.sell_proceeds_usdt,payout_usdt=excluded.payout_usdt,net_pnl_usdt=excluded.net_pnl_usdt,
                     net_roi=excluded.net_roi,maker_net_pnl_usdt=excluded.maker_net_pnl_usdt,taker_net_pnl_usdt=excluded.taker_net_pnl_usdt,
                     up_position_shares=excluded.up_position_shares,down_position_shares=excluded.down_position_shares,
                     accounting_version=excluded.accounting_version""",
                (
                    market_id, item["asset"], title, winner, resolved_at, int(total["fillCount"] or 0), parent_count,
                    float(total["buyNotionalUsdt"] or 0), float(total["sellProceedsUsdt"] or 0), float(total["payoutUsdt"] or 0),
                    float(total["netPnlUsdt"] or 0), total["netRoi"], float(maker["netPnlUsdt"] or 0), float(taker["netPnlUsdt"] or 0),
                    float(total["upPositionShares"] or 0), float(total["downPositionShares"] or 0),
                    "FILLED_CASHFLOW_PLUS_WINNER_V1_NO_EXPLICIT_FEE",
                ),
            )
            self.db.execute(
                "UPDATE target_markets SET title=?,winner=?,resolved_at_ms=?,status='SETTLED',last_seen_ms=? WHERE market_id=?",
                (title, winner, resolved_at, resolved_at, market_id),
            )
            self.db.commit()
        self.last_settlement_ms = resolved_at
        self.markets_settled_run += 1
        self.snapshot_cache = None

    def _checkpoint_loop(self) -> None:
        while not self.stop_event.wait(30.0):
            try:
                with self.db_lock:
                    self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.Error:
                pass

    def _inventory(self, asset: str, market_id: int | None) -> dict[str, Any]:
        if market_id is None:
            return {"makerUpShares": 0.0, "makerDownShares": 0.0, "takerUpShares": 0.0, "takerDownShares": 0.0, "makerDelta": 0.0, "takerDelta": 0.0}
        with self.db_lock:
            data = [dict(row) for row in self.db.execute(
                "SELECT role,side,quote_type,shares FROM wallet_shadow_target_events WHERE asset=? AND market_id=? ORDER BY event_ms,id",
                (asset, int(market_id)),
            )]
        out = {"makerUpShares": 0.0, "makerDownShares": 0.0, "takerUpShares": 0.0, "takerDownShares": 0.0}
        for item in data:
            direction = 1.0 if item["quote_type"] == "BID" else -1.0 if item["quote_type"] == "ASK" else 0.0
            key = (
                "makerUpShares" if item["role"] == "MAKER" and item["side"] == "UP" else
                "makerDownShares" if item["role"] == "MAKER" and item["side"] == "DOWN" else
                "takerUpShares" if item["role"] == "TAKER" and item["side"] == "UP" else "takerDownShares"
            )
            out[key] += direction * float(item["shares"])
        out["makerDelta"] = out["makerUpShares"] - out["makerDownShares"]
        out["takerDelta"] = out["takerUpShares"] - out["takerDownShares"]
        return out

    def _recent_parents(self, asset: str | None = None, market_id: int | None = None, limit: int = 60) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        if asset:
            clauses.append("asset=?")
            params.append(asset)
        if market_id is not None:
            clauses.append("market_id=?")
            params.append(int(market_id))
        params.append(int(limit))
        with self.db_lock:
            return [dict(row) for row in self.db.execute(
                f"""SELECT parent_id id,asset,market_id,role,side,quote_type,order_hash,first_event_ms,last_event_ms,
                           average_price,shares,fill_legs
                    FROM target_parent_orders WHERE {' AND '.join(clauses)} ORDER BY last_event_ms DESC LIMIT ?""",
                params,
            )]

    def _performance(self) -> dict[str, Any]:
        cutoff = now_ms() - PERFORMANCE_WINDOW_DAYS * 86_400_000
        with self.db_lock:
            summary = dict(self.db.execute(
                """SELECT COUNT(*) settled_markets,
                          SUM(CASE WHEN fill_count>0 THEN 1 ELSE 0 END) traded_markets,
                          SUM(CASE WHEN fill_count>0 AND net_pnl_usdt>1e-9 THEN 1 ELSE 0 END) wins,
                          SUM(CASE WHEN fill_count>0 AND net_pnl_usdt<-1e-9 THEN 1 ELSE 0 END) losses,
                          SUM(CASE WHEN fill_count>0 AND ABS(net_pnl_usdt)<=1e-9 THEN 1 ELSE 0 END) flats,
                          COALESCE(SUM(buy_notional_usdt),0) buy_notional_usdt,
                          COALESCE(SUM(sell_proceeds_usdt),0) sell_proceeds_usdt,
                          COALESCE(SUM(payout_usdt),0) payout_usdt,
                          COALESCE(SUM(net_pnl_usdt),0) net_pnl_usdt,
                          COALESCE(SUM(maker_net_pnl_usdt),0) maker_net_pnl_usdt,
                          COALESCE(SUM(taker_net_pnl_usdt),0) taker_net_pnl_usdt
                   FROM target_market_results WHERE resolved_at_ms>=?""",
                (cutoff,),
            ).fetchone())
            recent = [dict(row) for row in self.db.execute(
                """SELECT market_id,asset,title,winner,resolved_at_ms,fill_count,parent_count,buy_notional_usdt,
                          sell_proceeds_usdt,payout_usdt,net_pnl_usdt,net_roi,maker_net_pnl_usdt,taker_net_pnl_usdt
                   FROM target_market_results WHERE resolved_at_ms>=? ORDER BY resolved_at_ms DESC LIMIT 40""",
                (cutoff,),
            )]
        traded = int(summary.get("traded_markets") or 0)
        wins = int(summary.get("wins") or 0)
        buy = float(summary.get("buy_notional_usdt") or 0.0)
        pnl = float(summary.get("net_pnl_usdt") or 0.0)
        return {
            "windowDays": PERFORMANCE_WINDOW_DAYS, "settledMarkets": int(summary.get("settled_markets") or 0),
            "tradedMarkets": traded, "wins": wins, "losses": int(summary.get("losses") or 0),
            "flats": int(summary.get("flats") or 0), "winRate": wins / traded if traded else None,
            "buyNotionalUsdt": buy, "sellProceedsUsdt": float(summary.get("sell_proceeds_usdt") or 0.0),
            "payoutUsdt": float(summary.get("payout_usdt") or 0.0), "netPnlUsdt": pnl,
            "netRoi": pnl / buy if buy > 1e-12 else None,
            "makerNetPnlUsdt": float(summary.get("maker_net_pnl_usdt") or 0.0),
            "takerNetPnlUsdt": float(summary.get("taker_net_pnl_usdt") or 0.0),
            "feeAccounting": "NO_EXPLICIT_FEE_FIELD_YET",
            "accounting": "filled BID/ASK cashflow + official winner payout; raw fills retained permanently for later fee refinement",
            "recentMarkets": recent,
        }

    def health_snapshot(self) -> dict[str, Any]:
        at = now_ms()
        with self.lock:
            markets = {asset: dict(value) for asset, value in self.current_markets.items()}
        return {
            "ok": bool(self.api_key) and self.last_error is None,
            "status": "ONLINE" if self.api_key and self.last_error is None else "DEGRADED" if self.api_key else "CONFIG_REQUIRED",
            "version": VERSION, "processAlive": True, "targetWallet": self.wallet, "dbPath": str(self.db_path),
            "apiKeyConfigured": bool(self.api_key),
            "lastPollAgeMs": at - self.last_poll_ms if self.last_poll_ms else None,
            "lastFillAgeMs": at - self.last_fill_ms if self.last_fill_ms else None,
            "lastSettlementAgeMs": at - self.last_settlement_ms if self.last_settlement_ms else None,
            "markets": markets, "lastError": self.last_error, "paperOnly": True, "liveOrdersAffected": False,
            "strategyLogic": False, "permanentFillRetention": True,
        }

    def snapshot(self) -> dict[str, Any]:
        at = now_ms()
        if self.snapshot_cache is not None and at - self.snapshot_cache_at < 750:
            return self.snapshot_cache
        with self.lock:
            current = {asset: dict(value) for asset, value in self.current_markets.items()}
        assets: dict[str, Any] = {}
        for asset in ASSETS:
            market_id = positive_int(current.get(asset, {}).get("marketId"))
            with self.db_lock:
                fill_count = int(self.db.execute("SELECT COUNT(*) FROM wallet_shadow_target_events WHERE asset=?", (asset,)).fetchone()[0])
                parent_count = int(self.db.execute("SELECT COUNT(*) FROM target_parent_orders WHERE asset=?", (asset,)).fetchone()[0])
            assets[asset] = {
                "market": current.get(asset, {}), "inventory": self._inventory(asset, market_id),
                "recentParentOrders": self._recent_parents(asset=asset, limit=40),
                "storedFillLegs": fill_count, "storedParentOrders": parent_count,
            }
        with self.db_lock:
            stored_fills = int(self.db.execute("SELECT COUNT(*) FROM wallet_shadow_target_events").fetchone()[0])
            stored_parents = int(self.db.execute("SELECT COUNT(*) FROM target_parent_orders").fetchone()[0])
            db_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
        performance = self._performance()
        payload = {
            "ok": True, "status": "ONLINE" if self.api_key and self.last_error is None else "DEGRADED",
            "version": VERSION, "targetWallet": self.wallet, "dbPath": str(self.db_path), "assets": assets,
            "targetPerformance": performance, "targetRecentMarkets": performance["recentMarkets"],
            "targetEvents": self._recent_parents(limit=80),
            "storage": {
                "storedFillLegs": stored_fills, "storedParentOrders": stored_parents, "databaseBytes": db_bytes,
                "retention": "PERMANENT_UNLESS_MANUALLY_ARCHIVED", "legacyDbPreservedReadOnly": str(LEGACY_DB_PATH),
                "compatibilityTablesForMakerInference": True,
            },
            "run": {"pollRequests": self.poll_requests, "fillsInserted": self.fills_inserted_run,
                    "parentsUpdated": self.parents_updated_run, "marketsSettled": self.markets_settled_run},
            "health": self.health_snapshot(), "paperOnly": True, "liveOrdersAffected": False,
            "strategyLogic": False, "error": self.last_error,
        }
        self.snapshot_cache = payload
        self.snapshot_cache_at = at
        return payload


class Handler(BaseHTTPRequestHandler):
    collector: TargetWalletOfficialCollector

    def log_message(self, *_args: Any) -> None:
        return

    @staticmethod
    def _client_disconnected(exc: BaseException) -> bool:
        if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return True
        return isinstance(exc, OSError) and getattr(exc, "winerror", None) in {10053, 10054}

    def _write_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception as exc:
            if not self._client_disconnected(exc):
                raise

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._write_json(self.collector.health_snapshot())
            return
        if path in {"/state", "/"}:
            self._write_json(self.collector.snapshot())
            return
        self._write_json({"ok": False, "error": "not found"}, 404)


def main() -> int:
    collector = TargetWalletOfficialCollector()
    collector.start()
    handler = type("TargetWalletOfficialV1Handler", (Handler,), {"collector": collector})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}/state; target={collector.wallet}; "
        f"db={collector.db_path}; assets=BTC,ETH; strategyLogic=false; liveOrdersAffected=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        collector.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
