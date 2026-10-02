from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("PREDICT_WALLET_SHADOW_DB", ROOT / "data" / "predict_wallet_shadow.db"))
HOST = os.environ.get("PREDICT_WALLET_SHADOW_HOST", "127.0.0.1")
PORT = int(os.environ.get("PREDICT_WALLET_SHADOW_PORT", "8776"))
API_BASE = os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun").rstrip("/")
API_KEY_ENV = "PREDICT_FUN_API_KEY"
TARGET_WALLET = os.environ.get(
    "PREDICT_WALLET_SHADOW_TARGET_ADDRESS",
    "0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03",
).strip().lower()
PREDICT_OBSERVER_URL = os.environ.get(
    "PREDICT_WALLET_SHADOW_PREDICT_OBSERVER_URL",
    "http://127.0.0.1:8771/state",
)
STRATEGY_URL = os.environ.get(
    "PREDICT_WALLET_SHADOW_STRATEGY_URL",
    "http://127.0.0.1:8768/state",
)
POLL_SECONDS = max(0.5, float(os.environ.get("PREDICT_WALLET_SHADOW_POLL_SECONDS", "1.0")))
PAGE_SIZE = max(20, min(100, int(os.environ.get("PREDICT_WALLET_SHADOW_PAGE_SIZE", "100"))))
INITIAL_BACKFILL_PAGES = max(1, min(10, int(os.environ.get("PREDICT_WALLET_SHADOW_BACKFILL_PAGES", "4"))))
MAKER_UNIT_SHARES = max(1.0, float(os.environ.get("PREDICT_WALLET_SHADOW_MAKER_UNIT", "18")))
MAKER_GRID = max(0.001, float(os.environ.get("PREDICT_WALLET_SHADOW_MAKER_GRID", "0.01")))
VERSION = "PREDICT_WALLET_SHADOW_V0"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _positive_int(value: Any) -> int | None:
    try:
        result = int(float(value))
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _iso_ms(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _side(value: Any) -> str:
    if isinstance(value, dict):
        return _side(value.get("name") or value.get("label") or value.get("outcome"))
    text = str(value or "").strip().upper()
    if text == "UP" or " UP" in text:
        return "UP"
    if text == "DOWN" or " DOWN" in text:
        return "DOWN"
    return "UNKNOWN"


def _quote_type(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BID", "BUY"}:
        return "BID"
    if text in {"ASK", "SELL"}:
        return "ASK"
    return "UNKNOWN"


def _unwrap_state(payload: Any) -> dict[str, Any]:
    current = _record(payload)
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


def _grid_floor(value: Any, grid: float = MAKER_GRID) -> float | None:
    price = _finite(value)
    if price is None or price <= 0 or price >= 1:
        return None
    steps = math.floor((price + 1e-12) / grid)
    quote = round(steps * grid, 6)
    return quote if 0 < quote < 1 else None


@dataclass
class ParentEvent:
    id: str
    role: str
    market_id: int
    side: str
    quote_type: str
    order_hash: str | None
    first_event_ms: int
    last_event_ms: int
    average_price: float | None
    shares: float
    fill_legs: int
    title: str | None = None

    def json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "marketId": self.market_id,
            "side": self.side,
            "quoteType": self.quote_type,
            "orderHash": self.order_hash,
            "firstEventMs": self.first_event_ms,
            "lastEventMs": self.last_event_ms,
            "eventAt": datetime.fromtimestamp(self.first_event_ms / 1000).astimezone().isoformat(),
            "averagePrice": self.average_price,
            "shares": self.shares,
            "costUsdtApprox": self.average_price * self.shares if self.average_price is not None else None,
            "fillLegs": self.fill_legs,
            "title": self.title,
        }


@dataclass
class ShadowEvent:
    id: str
    market_id: int
    at_ms: int
    event_type: str
    role: str
    side: str
    price: float | None
    shares: float
    inference: str
    reason: str
    core_side: str | None
    core_source: str | None
    maker_up_shares: float
    maker_down_shares: float
    taker_up_shares: float
    taker_down_shares: float

    def json(self) -> dict[str, Any]:
        data = asdict(self)
        return {
            "id": data["id"],
            "marketId": data["market_id"],
            "atMs": data["at_ms"],
            "eventType": data["event_type"],
            "role": data["role"],
            "side": data["side"],
            "price": data["price"],
            "shares": data["shares"],
            "inference": data["inference"],
            "reason": data["reason"],
            "coreSide": data["core_side"],
            "coreSource": data["core_source"],
            "makerUpShares": data["maker_up_shares"],
            "makerDownShares": data["maker_down_shares"],
            "takerUpShares": data["taker_up_shares"],
            "takerDownShares": data["taker_down_shares"],
            "makerDelta": data["maker_up_shares"] - data["maker_down_shares"],
            "takerDelta": data["taker_up_shares"] - data["taker_down_shares"],
        }


def normalize_match_leg(raw: Any, *, wallet: str, role: str, maker_index: int | None = None) -> dict[str, Any] | None:
    row = _record(raw)
    market = _record(row.get("market"))
    market_id = _positive_int(market.get("id"))
    event_ms = _iso_ms(row.get("executedAt"))
    if market_id is None or event_ms is None:
        return None

    if role == "TAKER":
        participant = _record(row.get("taker"))
    else:
        makers = _rows(row.get("makers"))
        if maker_index is None or maker_index < 0 or maker_index >= len(makers):
            return None
        participant = makers[maker_index]

    signer = str(participant.get("signer") or "").strip().lower()
    if signer != wallet.lower():
        return None
    order_hash = str(participant.get("hash") or "").strip() or None
    amount = _finite(participant.get("amount"))
    price = _finite(participant.get("price"))
    if amount is None:
        amount = _finite(row.get("amountFilled"))
    if price is None:
        price = _finite(row.get("priceExecuted"))
    if amount is None or amount < 0:
        amount = 0.0
    if price is not None and not 0 <= price <= 1:
        price = None
    side = _side(participant.get("outcome"))
    quote_type = _quote_type(participant.get("quoteType"))
    transaction_hash = str(row.get("transactionHash") or "").strip()
    settlement_id = str(row.get("settlementId") or "").strip()
    leg_id = ":".join(
        [
            role,
            order_hash or "NO_ORDER",
            transaction_hash or "NO_TX",
            settlement_id or "NO_SETTLEMENT",
            str(maker_index if maker_index is not None else "TAKER"),
            side,
            quote_type,
            f"{amount:.12f}",
            f"{price:.12f}" if price is not None else "NO_PRICE",
            str(event_ms),
        ]
    )
    parent_id = f"{role}:{order_hash or leg_id}"
    return {
        "legId": leg_id,
        "parentId": parent_id,
        "role": role,
        "marketId": market_id,
        "title": str(market.get("title") or market.get("question") or "") or None,
        "side": side,
        "quoteType": quote_type,
        "orderHash": order_hash,
        "eventMs": event_ms,
        "price": price,
        "shares": float(amount),
    }


def aggregate_parent(existing: ParentEvent | None, leg: dict[str, Any]) -> ParentEvent:
    shares = float(leg.get("shares") or 0.0)
    price = _finite(leg.get("price"))
    if existing is None:
        return ParentEvent(
            id=str(leg["parentId"]),
            role=str(leg["role"]),
            market_id=int(leg["marketId"]),
            side=str(leg["side"]),
            quote_type=str(leg["quoteType"]),
            order_hash=leg.get("orderHash"),
            first_event_ms=int(leg["eventMs"]),
            last_event_ms=int(leg["eventMs"]),
            average_price=price,
            shares=shares,
            fill_legs=1,
            title=leg.get("title"),
        )
    previous = existing.shares
    total = previous + shares
    if total > 0 and existing.average_price is not None and price is not None:
        average = (existing.average_price * previous + price * shares) / total
    else:
        average = existing.average_price if existing.average_price is not None else price
    existing.average_price = average
    existing.shares = total
    existing.fill_legs += 1
    existing.first_event_ms = min(existing.first_event_ms, int(leg["eventMs"]))
    existing.last_event_ms = max(existing.last_event_ms, int(leg["eventMs"]))
    existing.title = existing.title or leg.get("title")
    return existing


def inventory_from_shadow(events: list[ShadowEvent]) -> dict[str, float]:
    maker_up = maker_down = taker_up = taker_down = 0.0
    for event in events:
        if event.event_type == "MAKER_FILL_PROXY":
            if event.side == "UP":
                maker_up += event.shares
            elif event.side == "DOWN":
                maker_down += event.shares
        elif event.event_type == "TAKER_INTENT":
            if event.side == "UP":
                taker_up += event.shares
            elif event.side == "DOWN":
                taker_down += event.shares
    return {
        "makerUpShares": maker_up,
        "makerDownShares": maker_down,
        "takerUpShares": taker_up,
        "takerDownShares": taker_down,
        "makerDelta": maker_up - maker_down,
        "takerDelta": taker_up - taker_down,
    }


def inventory_from_target(events: list[ParentEvent]) -> dict[str, float]:
    maker_up = maker_down = taker_up = taker_down = 0.0
    for event in events:
        if event.quote_type != "BID":
            continue
        if event.role == "MAKER":
            if event.side == "UP":
                maker_up += event.shares
            elif event.side == "DOWN":
                maker_down += event.shares
        elif event.role == "TAKER":
            if event.side == "UP":
                taker_up += event.shares
            elif event.side == "DOWN":
                taker_down += event.shares
    return {
        "makerUpShares": maker_up,
        "makerDownShares": maker_down,
        "takerUpShares": taker_up,
        "takerDownShares": taker_down,
        "makerDelta": maker_up - maker_down,
        "takerDelta": taker_up - taker_down,
    }


def _ratio(matches: int, total: int) -> float | None:
    return matches / total if total > 0 else None


def similarity(target: list[ParentEvent], shadow: list[ShadowEvent]) -> dict[str, Any]:
    target_buy = [item for item in target if item.quote_type == "BID"]
    maker = [item for item in target_buy if item.role == "MAKER"]
    taker = [item for item in target_buy if item.role == "TAKER"]
    maker_18 = sum(1 for item in maker if abs(item.shares - MAKER_UNIT_SHARES) <= 0.05)

    def nearest(item: ParentEvent, kind: str, max_ms: int, same_side: bool = True) -> ShadowEvent | None:
        candidates = [
            event
            for event in shadow
            if event.event_type == kind
            and (not same_side or event.side == item.side)
            and abs(event.at_ms - item.first_event_ms) <= max_ms
        ]
        return min(candidates, key=lambda event: abs(event.at_ms - item.first_event_ms), default=None)

    def active_quote(item: ParentEvent) -> ShadowEvent | None:
        candidates = [
            event
            for event in shadow
            if event.event_type == "MAKER_QUOTE"
            and event.side == item.side
            and event.at_ms <= item.first_event_ms
        ]
        return max(candidates, key=lambda event: event.at_ms, default=None)

    maker_quote_comparable = maker_quote_match = 0
    maker_timing_match = 0
    taker_side_comparable = taker_side_match = 0
    taker_timing_match = 0
    price_comparable = price_match = 0

    for item in maker:
        quote = active_quote(item)
        if quote is not None and quote.price is not None and item.average_price is not None:
            maker_quote_comparable += 1
            if abs(quote.price - item.average_price) <= MAKER_GRID * 1.1:
                maker_quote_match += 1
        fill = nearest(item, "MAKER_FILL_PROXY", 3000)
        if fill is not None:
            maker_timing_match += 1
            if fill.price is not None and item.average_price is not None:
                price_comparable += 1
                if abs(fill.price - item.average_price) <= MAKER_GRID * 1.1:
                    price_match += 1

    for item in taker:
        any_side = nearest(item, "TAKER_INTENT", 5000, same_side=False)
        if any_side is not None:
            taker_side_comparable += 1
            if any_side.side == item.side:
                taker_side_match += 1
        same = nearest(item, "TAKER_INTENT", 3000)
        if same is not None:
            taker_timing_match += 1
            if same.price is not None and item.average_price is not None:
                price_comparable += 1
                if abs(same.price - item.average_price) <= MAKER_GRID * 1.1:
                    price_match += 1

    metrics = {
        "buyOnlyRate": _ratio(len(target_buy), len(target)),
        "makerUnitRate": _ratio(maker_18, len(maker)),
        "makerQuotePriceWithin1Tick": _ratio(maker_quote_match, maker_quote_comparable),
        "makerFillTimingWithin3s": _ratio(maker_timing_match, len(maker)),
        "takerSideMatchWithin5s": _ratio(taker_side_match, taker_side_comparable),
        "takerTimingWithin3s": _ratio(taker_timing_match, len(taker)),
        "matchedPriceWithin1Tick": _ratio(price_match, price_comparable),
    }
    valid = [value for value in metrics.values() if value is not None]
    return {**metrics, "overall": sum(valid) / len(valid) if valid else None}


class WalletShadowObserver:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.api_key = str(os.environ.get(API_KEY_ENV) or "").strip()
        self.wallet = TARGET_WALLET
        self.lock = threading.RLock()
        self.db_lock = threading.RLock()
        self.stop_event = threading.Event()
        self.started_at_ms = _now_ms()
        self.http = httpx.Client(
            timeout=httpx.Timeout(3.0, connect=0.8),
            verify=True,
            trust_env=False,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": "BTC-5M-Lab-Wallet-Shadow/0.1",
                **({"x-api-key": self.api_key} if self.api_key else {}),
            },
        )
        self.local_http = httpx.Client(timeout=httpx.Timeout(1.2, connect=0.4), trust_env=False)
        self.market_id: int | None = None
        self.market_title: str | None = None
        self.bucket_start_sec: int | None = None
        self.last_error: str | None = None
        self.last_poll_ms: int | None = None
        self.last_predict_book: dict[str, Any] = {}
        self.last_core: dict[str, Any] = {"side": None, "source": None, "raw": None}
        self.parents: dict[str, ParentEvent] = {}
        self.seen_legs: set[str] = set()
        self.shadow_events: list[ShadowEvent] = []
        self.active_quotes: dict[str, float | None] = {"UP": None, "DOWN": None}
        self.last_book: dict[str, Any] | None = None
        self.last_taker_signature: str | None = None
        self.initialized_roles: set[str] = set()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self._create_schema()

    def _create_schema(self) -> None:
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_shadow_target_events (
                    leg_id TEXT PRIMARY KEY,
                    wallet TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    side TEXT,
                    quote_type TEXT,
                    order_hash TEXT,
                    event_ms INTEGER NOT NULL,
                    price REAL,
                    shares REAL,
                    raw_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_shadow_target_market
                    ON wallet_shadow_target_events(wallet, market_id, event_ms);
                CREATE TABLE IF NOT EXISTS wallet_shadow_events (
                    id TEXT PRIMARY KEY,
                    wallet TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    at_ms INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    role TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL,
                    shares REAL NOT NULL,
                    core_side TEXT,
                    core_source TEXT,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_shadow_event_market
                    ON wallet_shadow_events(wallet, market_id, at_ms);
                """
            )
            self.db.commit()

    def start(self) -> None:
        if not self.api_key:
            self.last_error = f"{API_KEY_ENV} is not configured"
            return
        if not (self.wallet.startswith("0x") and len(self.wallet) == 42):
            self.last_error = "PREDICT_WALLET_SHADOW_TARGET_ADDRESS is invalid"
            return
        threading.Thread(target=self._loop, name="predict-wallet-shadow", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()
        self.http.close()
        self.local_http.close()
        with self.db_lock:
            self.db.commit()
            self.db.close()

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self._poll_once()
                self.last_error = None
            except Exception as exc:
                self.last_error = str(exc)[:500]
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.05, POLL_SECONDS - elapsed))

    def _get_local_state(self, url: str) -> dict[str, Any]:
        response = self.local_http.get(url)
        response.raise_for_status()
        return _unwrap_state(response.json())

    def _current_context(self) -> tuple[int | None, int | None, str | None, dict[str, Any]]:
        state = self._get_local_state(PREDICT_OBSERVER_URL)
        btc = _record(_record(state.get("assets")).get("BTC"))
        market = _record(btc.get("market"))
        market_id = _positive_int(market.get("id"))
        bucket = _positive_int(market.get("bucketStartSec") or btc.get("bucketStartSec"))
        title = str(market.get("title") or market.get("question") or "") or None
        book = {
            "upBid": _finite(_record(btc.get("up")).get("bid")),
            "upAsk": _finite(_record(btc.get("up")).get("ask")),
            "upMid": _finite(_record(btc.get("up")).get("mid")),
            "downBid": _finite(_record(btc.get("down")).get("bid")),
            "downAsk": _finite(_record(btc.get("down")).get("ask")),
            "downMid": _finite(_record(btc.get("down")).get("mid")),
            "receivedTimestampMs": _positive_int(btc.get("receivedTimestampMs")),
            "secondsLeft": _finite(btc.get("secondsLeft")),
        }
        return market_id, bucket, title, book

    def _core_signal(self, book: dict[str, Any]) -> dict[str, Any]:
        try:
            state = self._get_local_state(STRATEGY_URL)
            runtime = _record(state.get("runtime"))
            direction = _side(runtime.get("binanceDirection"))
            if direction in {"UP", "DOWN"}:
                return {
                    "side": direction,
                    "source": "8768.runtime.binanceDirection",
                    "raw": {
                        "binanceDirection": runtime.get("binanceDirection"),
                        "polyDirection": runtime.get("polyDirection"),
                        "probabilityGap": runtime.get("probabilityGap"),
                        "aligned": runtime.get("aligned"),
                    },
                }
        except Exception:
            pass
        up_mid = _finite(book.get("upMid"))
        if up_mid is not None and up_mid != 0.5:
            return {
                "side": "UP" if up_mid > 0.5 else "DOWN",
                "source": "8771.predictMidFallback",
                "raw": {"upMid": up_mid},
            }
        return {"side": None, "source": None, "raw": None}

    def _reset_market(self, market_id: int, bucket: int | None, title: str | None) -> None:
        self.market_id = market_id
        self.bucket_start_sec = bucket
        self.market_title = title
        self.parents = {}
        self.seen_legs = set()
        self.shadow_events = []
        self.active_quotes = {"UP": None, "DOWN": None}
        self.last_book = None
        self.last_taker_signature = None
        self.initialized_roles = set()

    def _fetch_matches_page(self, market_id: int, role: str, after: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "first": PAGE_SIZE,
            "marketId": market_id,
            "signerAddress": self.wallet,
            "isSignerMaker": "true" if role == "MAKER" else "false",
        }
        if after:
            params["after"] = after
        response = self.http.get(f"{API_BASE}/v1/orders/matches", params=params)
        response.raise_for_status()
        payload = _record(response.json())
        if payload.get("success") is False:
            raise RuntimeError(f"Predict matches rejected for role={role}")
        return payload

    def _ingest_role(self, market_id: int, role: str) -> None:
        pages = INITIAL_BACKFILL_PAGES if role not in self.initialized_roles else 1
        after: str | None = None
        for _ in range(pages):
            payload = self._fetch_matches_page(market_id, role, after)
            data = _rows(payload.get("data"))
            for raw in data:
                participant_count = 1 if role == "TAKER" else len(_rows(raw.get("makers")))
                for index in range(participant_count):
                    leg = normalize_match_leg(raw, wallet=self.wallet, role=role, maker_index=None if role == "TAKER" else index)
                    if leg is None or int(leg["marketId"]) != market_id:
                        continue
                    leg_id = str(leg["legId"])
                    if leg_id in self.seen_legs:
                        continue
                    self.seen_legs.add(leg_id)
                    parent_id = str(leg["parentId"])
                    self.parents[parent_id] = aggregate_parent(self.parents.get(parent_id), leg)
                    self._persist_target_leg(leg, raw)
            cursor = str(payload.get("cursor") or "").strip() or None
            if not cursor or not data:
                break
            after = cursor
        self.initialized_roles.add(role)

    def _persist_target_leg(self, leg: dict[str, Any], raw: dict[str, Any]) -> None:
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_shadow_target_events(
                       leg_id,wallet,market_id,role,side,quote_type,order_hash,event_ms,price,shares,raw_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    leg["legId"], self.wallet, leg["marketId"], leg["role"], leg["side"],
                    leg["quoteType"], leg.get("orderHash"), leg["eventMs"], leg.get("price"),
                    leg.get("shares"), json.dumps(raw, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()

    def _append_shadow(self, *, event_type: str, role: str, side: str, price: float | None, shares: float, inference: str, reason: str, core: dict[str, Any]) -> None:
        if self.market_id is None:
            return
        inventory = inventory_from_shadow(self.shadow_events)
        maker_up = inventory["makerUpShares"]
        maker_down = inventory["makerDownShares"]
        taker_up = inventory["takerUpShares"]
        taker_down = inventory["takerDownShares"]
        if event_type == "MAKER_FILL_PROXY":
            if side == "UP":
                maker_up += shares
            else:
                maker_down += shares
        elif event_type == "TAKER_INTENT":
            if side == "UP":
                taker_up += shares
            else:
                taker_down += shares
        at_ms = _now_ms()
        event = ShadowEvent(
            id=f"{self.market_id}:{at_ms}:{event_type}:{side}:{len(self.shadow_events)}",
            market_id=self.market_id,
            at_ms=at_ms,
            event_type=event_type,
            role=role,
            side=side,
            price=price,
            shares=shares,
            inference=inference,
            reason=reason,
            core_side=core.get("side"),
            core_source=core.get("source"),
            maker_up_shares=maker_up,
            maker_down_shares=maker_down,
            taker_up_shares=taker_up,
            taker_down_shares=taker_down,
        )
        self.shadow_events.append(event)
        if len(self.shadow_events) > 2000:
            self.shadow_events = self.shadow_events[-2000:]
        with self.db_lock:
            payload = event.json()
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_shadow_events(
                       id,wallet,market_id,at_ms,event_type,role,side,price,shares,
                       core_side,core_source,reason,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event.id, self.wallet, self.market_id, event.at_ms, event.event_type,
                    event.role, event.side, event.price, event.shares, event.core_side,
                    event.core_source, event.reason, json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()

    def _advance_shadow(self, book: dict[str, Any], core: dict[str, Any]) -> None:
        previous_book = self.last_book
        previous_quotes = dict(self.active_quotes)
        for side in ("UP", "DOWN"):
            bid = _finite(book.get("upBid" if side == "UP" else "downBid"))
            quote = _grid_floor(bid)
            if quote is not None and quote != self.active_quotes[side]:
                self.active_quotes[side] = quote
                self._append_shadow(
                    event_type="MAKER_QUOTE",
                    role="MAKER",
                    side=side,
                    price=quote,
                    shares=MAKER_UNIT_SHARES,
                    inference="KNOWN_PATTERN",
                    reason=f"passive {MAKER_UNIT_SHARES:g}-share cent-grid BID quote at current best bid",
                    core=core,
                )

        if previous_book is not None:
            for side in ("UP", "DOWN"):
                quote = previous_quotes.get(side)
                if quote is None:
                    continue
                bid = _finite(book.get("upBid" if side == "UP" else "downBid"))
                ask = _finite(book.get("upAsk" if side == "UP" else "downAsk"))
                bid_through = bid is not None and bid < quote - MAKER_GRID * 0.45
                ask_touch = ask is not None and ask <= quote + 1e-9
                if bid_through or ask_touch:
                    recent_same = any(
                        event.event_type == "MAKER_FILL_PROXY"
                        and event.side == side
                        and event.price == quote
                        and _now_ms() - event.at_ms < 1800
                        for event in self.shadow_events[-12:]
                    )
                    if not recent_same:
                        self._append_shadow(
                            event_type="MAKER_FILL_PROXY",
                            role="MAKER",
                            side=side,
                            price=quote,
                            shares=MAKER_UNIT_SHARES,
                            inference="INFERRED_FILL",
                            reason="best bid moved through inferred passive quote" if bid_through else "best ask touched inferred passive quote",
                            core=core,
                        )

        inventory = inventory_from_shadow(self.shadow_events)
        maker_delta = inventory["makerDelta"]
        maker_side = "UP" if maker_delta > 0 else "DOWN" if maker_delta < 0 else None
        core_side = core.get("side")
        if maker_side and core_side in {"UP", "DOWN"} and maker_side != core_side:
            tier = max(1, math.ceil(abs(maker_delta) / MAKER_UNIT_SHARES))
            signature = f"{core_side}:{maker_side}:{tier}"
            if signature != self.last_taker_signature:
                shares = min(180.0, math.ceil((abs(maker_delta) + MAKER_UNIT_SHARES) / MAKER_UNIT_SHARES) * MAKER_UNIT_SHARES)
                ask = _finite(book.get("upAsk" if core_side == "UP" else "downAsk"))
                self._append_shadow(
                    event_type="TAKER_INTENT",
                    role="TAKER",
                    side=str(core_side),
                    price=ask,
                    shares=shares,
                    inference="INFERRED_TAKER_TRIGGER",
                    reason="WALLET_MAKER_TAKER_DIVERGENCE: core direction opposes inferred passive residual",
                    core=core,
                )
                self.last_taker_signature = signature
        self.last_book = dict(book)

    def _poll_once(self) -> None:
        market_id, bucket, title, book = self._current_context()
        if market_id is None:
            raise RuntimeError("8771 has no current BTC Predict.fun 5m market")
        with self.lock:
            if market_id != self.market_id:
                self._reset_market(market_id, bucket, title)
            self.last_predict_book = dict(book)
            core = self._core_signal(book)
            self.last_core = core
            self._ingest_role(market_id, "MAKER")
            self._ingest_role(market_id, "TAKER")
            self._advance_shadow(book, core)
            self.last_poll_ms = _now_ms()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            target = sorted(self.parents.values(), key=lambda item: item.first_event_ms, reverse=True)
            shadow = sorted(self.shadow_events, key=lambda item: item.at_ms, reverse=True)
            target_inventory = inventory_from_target(target)
            shadow_inventory = inventory_from_shadow(self.shadow_events)
            maker_side = "UP" if target_inventory["makerDelta"] > 0 else "DOWN" if target_inventory["makerDelta"] < 0 else None
            taker_side = "UP" if target_inventory["takerDelta"] > 0 else "DOWN" if target_inventory["takerDelta"] < 0 else None
            status = "LIVE" if self.api_key and self.market_id is not None and not self.last_error else "DEGRADED" if self.market_id else "WAITING"
            return {
                "version": VERSION,
                "paperOnly": True,
                "readOnlyTarget": True,
                "liveOrdersAffected": False,
                "apiKeyConfigured": bool(self.api_key),
                "targetWallet": self.wallet,
                "status": status,
                "error": self.last_error,
                "startedAtMs": self.started_at_ms,
                "lastPollMs": self.last_poll_ms,
                "market": {
                    "marketId": self.market_id,
                    "title": self.market_title,
                    "bucketStartSec": self.bucket_start_sec,
                    "book": dict(self.last_predict_book),
                },
                "coreSignal": dict(self.last_core),
                "assumptions": {
                    "openOrdersVisible": False,
                    "makerUnitShares": MAKER_UNIT_SHARES,
                    "makerGrid": MAKER_GRID,
                    "makerQuoteRule": "both sides passive BID at floor(current best bid to 0.01 grid)",
                    "makerFillRule": "book-through / ask-touch proxy only; not an observed fill",
                    "takerRule": "core side opposite inferred maker residual => active corrective intent",
                    "targetEventsDriveShadow": False,
                },
                "target": {
                    "parentCount": len(target),
                    "makerParents": sum(1 for item in target if item.role == "MAKER"),
                    "takerParents": sum(1 for item in target if item.role == "TAKER"),
                    "bidParents": sum(1 for item in target if item.quote_type == "BID"),
                    "askParents": sum(1 for item in target if item.quote_type == "ASK"),
                    "inventory": target_inventory,
                    "makerResidualSide": maker_side,
                    "takerResidualSide": taker_side,
                    "makerTakerDivergence": bool(maker_side and taker_side and maker_side != taker_side),
                    "events": [item.json() for item in target[:120]],
                },
                "shadow": {
                    "eventCount": len(self.shadow_events),
                    "inventory": shadow_inventory,
                    "events": [item.json() for item in shadow[:120]],
                },
                "similarity": similarity(target, self.shadow_events),
            }


class _Handler(BaseHTTPRequestHandler):
    observer: WalletShadowObserver

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, allow_nan=False, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in {"/state", "/health", "/api/state"}:
            self._send(404, {"ok": False, "error": "not found"})
            return
        self._send(200, {"ok": True, "state": self.observer.snapshot()})

    def do_POST(self) -> None:  # noqa: N802
        self._send(405, {"ok": False, "error": "wallet shadow is paper-only and read-only"})

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowHandler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Predict wallet shadow listening on http://{HOST}:{PORT}/state; "
        f"target={observer.wallet}; apiKeyConfigured={bool(observer.api_key)}; db={DB_PATH}",
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
