from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .core import ApiHttpError, ApiTransportError, BinancePredictionTradingClient
from .target_taker_live_execution_v5 import select_single_binance_prediction_wallet


VERSION = "ECHTGELD_4310_REDEEM_V1"
AUTO_REDEEM_ENV = "PREDICT_ECHTGELD_AUTO_REDEEM"
SCAN_INTERVAL_SECONDS = 15.0
REDEEM_DELAY_SECONDS = 60.0
MAX_POSITION_PAGES = 5
MAX_REDEEMS_PER_SCAN = 20
PENDING_STATUSES = {"ATTEMPTING", "AMBIGUOUS", "SUBMITTED", "PENDING", "PROCESSING"}
TERMINAL_SUCCESS_STATUSES = {"CLAIMED", "REDEEMED", "SUCCESS", "SUCCEEDED", "COMPLETED"}
TERMINAL_FAILURE_STATUSES = {"FAILED", "REJECTED", "CANCELLED", "CANCELED", "EXPIRED"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _truthy_env(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _nested_rows(payload: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get(key)
    if not isinstance(rows, list):
        data = payload.get("data")
        rows = data.get(key) if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _first_text(payload: Any, *keys: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    candidates = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.insert(0, data)
    for candidate in candidates:
        for key in keys:
            value = candidate.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return None


def _status_text(payload: Any) -> str:
    return str(_first_text(payload, "status", "redeemStatus", "state") or "").strip().upper()


class EchtgeldRedeemManager:
    """Durable Binance Prediction redemption lifecycle ported from the 4310 runtime.

    Important invariants:
    - It runs independently of Echtgeld PAUSE/ARM, because already-filled money must
      still be settled and claimed while new trading is paused.
    - It only accepts venue-explicit PENDING_CLAIM + canClaim=true positions.
    - It only mutates positions whose Binance marketId is present in this 8781
      engine's durable SUBMITTED order ledger.
    - A redeem is persisted as ATTEMPTING before the venue write. Transport/5xx,
      missing txHash, or process uncertainty never causes an automatic retry.
    - In-flight rows with a txHash are reconciled through redeem/status.
    """

    def __init__(
        self,
        db_path: Path | str,
        *,
        venue_getter: Callable[[], str],
        start_worker: bool = True,
        client_factory: Callable[[str, str], BinancePredictionTradingClient] = BinancePredictionTradingClient,
        wallet_selector: Callable[[Any], dict[str, str]] = select_single_binance_prediction_wallet,
        scan_interval_seconds: float = SCAN_INTERVAL_SECONDS,
        redeem_delay_seconds: float = REDEEM_DELAY_SECONDS,
        enabled: bool | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.venue_getter = venue_getter
        self.client_factory = client_factory
        self.wallet_selector = wallet_selector
        self.scan_interval_seconds = max(1.0, float(scan_interval_seconds))
        self.redeem_delay_seconds = max(0.0, float(redeem_delay_seconds))
        self.enabled = _truthy_env(AUTO_REDEEM_ENV, True) if enabled is None else bool(enabled)
        self.lock = threading.RLock()
        self.cycle_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.client: BinancePredictionTradingClient | None = None
        self.wallet: dict[str, str] | None = None
        self.status = "STARTING" if self.enabled else "DISABLED"
        self.last_scan_at_ms: int | None = None
        self.last_success_at_ms: int | None = None
        self.last_error: str | None = None
        self.claimable_count = 0
        self.claimable_amount_usdt = 0.0
        self._setup_schema()
        self._quarantine_restart_attempts()
        if start_worker:
            self.thread = threading.Thread(
                target=self._loop,
                name="echtgeld-4310-redeem",
                daemon=True,
            )
            self.thread.start()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _setup_schema(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS engine_redeems (
                    token_id TEXT PRIMARY KEY,
                    venue TEXT NOT NULL,
                    venue_market_id INTEGER NOT NULL,
                    venue_topic_id INTEGER,
                    chain_id TEXT NOT NULL DEFAULT '56',
                    side TEXT,
                    end_date_ms INTEGER NOT NULL,
                    shares REAL,
                    claimable_value_usdt REAL,
                    eligible_at_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    tx_hash TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    discovered_at_ms INTEGER NOT NULL,
                    submitted_at_ms INTEGER,
                    completed_at_ms INTEGER,
                    last_checked_at_ms INTEGER,
                    last_error TEXT,
                    response_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_engine_redeems_status
                    ON engine_redeems(status, eligible_at_ms);
                CREATE INDEX IF NOT EXISTS idx_engine_redeems_market
                    ON engine_redeems(venue_market_id);
                """
            )

    def _quarantine_restart_attempts(self) -> None:
        # If the process died after durable ATTEMPTING but before a tx hash was
        # stored, we cannot know whether Binance received the request. Never
        # replay it automatically; this is intentionally fail-safe.
        with self._connect() as db:
            db.execute(
                """UPDATE engine_redeems
                      SET status='AMBIGUOUS',
                          last_error=COALESCE(last_error,
                              '8781 restarted with a pre-venue ATTEMPTING redeem; automatic replay is forbidden'),
                          last_checked_at_ms=?
                    WHERE status='ATTEMPTING'""",
                (_now_ms(),),
            )

    def _ensure_client(self) -> tuple[BinancePredictionTradingClient, dict[str, str]]:
        api_key = str(os.environ.get("BINANCE_API_KEY") or "").strip()
        api_secret = str(os.environ.get("BINANCE_API_SECRET") or "").strip()
        if not api_key or not api_secret:
            raise RuntimeError("Binance redeem requires BINANCE_API_KEY and BINANCE_API_SECRET")
        if self.client is None:
            self.client = self.client_factory(api_key, api_secret)
        if self.wallet is None:
            self.wallet = self.wallet_selector(self.client.wallets())
        return self.client, dict(self.wallet)

    def _tracked_binance_markets(self) -> dict[int, set[str]]:
        tracked: dict[int, set[str]] = {}
        try:
            with self._connect() as db:
                rows = db.execute(
                    """SELECT side,result_json FROM engine_orders
                         WHERE venue='binance' AND status='SUBMITTED'"""
                ).fetchall()
        except sqlite3.OperationalError:
            return tracked
        for row in rows:
            try:
                result = json.loads(str(row["result_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
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
    def _position_market_id(position: dict[str, Any]) -> int:
        for key in ("marketId", "market_id", "predictionMarketId"):
            try:
                value = int(position.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return 0

    @staticmethod
    def _position_side(position: dict[str, Any]) -> str | None:
        for key in ("outcome", "outcomeName", "side", "name"):
            value = str(position.get(key) or "").strip().upper()
            if value in {"UP", "DOWN"}:
                return value
        return None

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
            and value > 0
            and end_date_ms > 0
        )

    def _claimable_positions(
        self,
        client: BinancePredictionTradingClient,
        wallet_address: str,
    ) -> list[dict[str, Any]]:
        offset = 0
        seen: set[str] = set()
        output: list[dict[str, Any]] = []
        for _ in range(MAX_POSITION_PAGES):
            payload = client.positions(
                wallet_address,
                tab="PENDING_CLAIM",
                offset=offset,
                limit=100,
            )
            rows = _nested_rows(payload, "positions")
            for row in rows:
                token_id = str(row.get("tokenId") or "").strip()
                if token_id and token_id not in seen and self._claimable_position(row):
                    seen.add(token_id)
                    output.append(dict(row))
            has_more = bool(payload.get("hasMore")) if isinstance(payload, dict) else False
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict) and "hasMore" in data:
                has_more = bool(data.get("hasMore"))
            if not has_more or not rows:
                break
            offset += 100
        return output

    def _upsert_claimable(self, position: dict[str, Any], tracked: dict[int, set[str]]) -> bool:
        market_id = self._position_market_id(position)
        side = self._position_side(position)
        if market_id <= 0 or market_id not in tracked:
            return False
        if side is not None and side not in tracked[market_id]:
            return False
        token_id = str(position.get("tokenId") or "").strip()
        try:
            topic_id = int(position.get("marketTopicId") or position.get("topicId") or 0) or None
        except (TypeError, ValueError):
            topic_id = None
        end_date_ms = int(position.get("endDate") or 0)
        shares = _finite(position.get("shares"))
        value = _finite(position.get("value"))
        now_ms = _now_ms()
        eligible_at_ms = end_date_ms + int(self.redeem_delay_seconds * 1000)
        with self._connect() as db:
            db.execute(
                """INSERT INTO engine_redeems(
                       token_id,venue,venue_market_id,venue_topic_id,chain_id,side,
                       end_date_ms,shares,claimable_value_usdt,eligible_at_ms,status,
                       discovered_at_ms,last_checked_at_ms,response_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?, ?,?,?)
                   ON CONFLICT(token_id) DO UPDATE SET
                       venue_market_id=excluded.venue_market_id,
                       venue_topic_id=COALESCE(excluded.venue_topic_id,engine_redeems.venue_topic_id),
                       side=COALESCE(excluded.side,engine_redeems.side),
                       end_date_ms=excluded.end_date_ms,
                       shares=excluded.shares,
                       claimable_value_usdt=excluded.claimable_value_usdt,
                       eligible_at_ms=excluded.eligible_at_ms,
                       last_checked_at_ms=excluded.last_checked_at_ms
                """,
                (
                    token_id,
                    "binance",
                    market_id,
                    topic_id,
                    str(position.get("chainId") or "56"),
                    side,
                    end_date_ms,
                    shares,
                    value,
                    eligible_at_ms,
                    "CLAIMABLE",
                    now_ms,
                    now_ms,
                    "{}",
                ),
            )
        return True

    def _reconcile_row(
        self,
        client: BinancePredictionTradingClient,
        wallet_address: str,
        row: dict[str, Any],
    ) -> None:
        tx_hash = str(row.get("tx_hash") or "").strip()
        if not tx_hash:
            return
        now_ms = _now_ms()
        try:
            payload = client.redeem_status(wallet_address, tx_hash)
            venue_status = _status_text(payload)
        except Exception as exc:
            with self._connect() as db:
                db.execute(
                    "UPDATE engine_redeems SET last_checked_at_ms=?,last_error=? WHERE token_id=?",
                    (now_ms, f"{type(exc).__name__}: {str(exc)[:300]}", str(row["token_id"])),
                )
            return
        if venue_status in TERMINAL_SUCCESS_STATUSES:
            status = "REDEEMED"
            completed = now_ms
            error = None
            self.last_success_at_ms = now_ms
        elif venue_status in TERMINAL_FAILURE_STATUSES:
            status = "FAILED_REVIEW"
            completed = now_ms
            error = f"Binance redeem/status reached terminal {venue_status}; automatic resubmit is forbidden"
        else:
            status = venue_status if venue_status in {"SUBMITTED", "PENDING", "PROCESSING"} else "PENDING"
            completed = None
            error = None
        with self._connect() as db:
            db.execute(
                """UPDATE engine_redeems SET status=?,completed_at_ms=COALESCE(?,completed_at_ms),
                       last_checked_at_ms=?,last_error=?,response_json=? WHERE token_id=?""",
                (
                    status,
                    completed,
                    now_ms,
                    error,
                    json.dumps(payload, separators=(",", ":"), default=str)[:12000],
                    str(row["token_id"]),
                ),
            )

    def _submit_row(
        self,
        client: BinancePredictionTradingClient,
        wallet: dict[str, str],
        row: dict[str, Any],
    ) -> None:
        token_id = str(row["token_id"])
        now_ms = _now_ms()
        with self._connect() as db:
            updated = db.execute(
                """UPDATE engine_redeems SET status='ATTEMPTING',attempt_count=attempt_count+1,
                       submitted_at_ms=?,last_checked_at_ms=?,last_error=NULL
                     WHERE token_id=? AND status='CLAIMABLE'""",
                (now_ms, now_ms, token_id),
            ).rowcount
        if updated != 1:
            return
        try:
            payload = client.batch_redeem(
                wallet_address=wallet["walletAddress"],
                wallet_id=wallet["walletId"],
                token_ids=[token_id],
                chain_id=str(row.get("chain_id") or "56"),
            )
        except Exception as exc:
            ambiguous = isinstance(exc, ApiTransportError) or (
                isinstance(exc, ApiHttpError) and exc.status_code >= 500
            )
            status = "AMBIGUOUS" if ambiguous else "FAILED_REVIEW"
            message = f"{type(exc).__name__}: {str(exc)[:300]}"
            with self._connect() as db:
                db.execute(
                    "UPDATE engine_redeems SET status=?,last_checked_at_ms=?,last_error=? WHERE token_id=?",
                    (status, _now_ms(), message, token_id),
                )
            return

        tx_hash = _first_text(payload, "txHash", "transactionHash", "hash")
        venue_status = _status_text(payload)
        if not tx_hash:
            with self._connect() as db:
                db.execute(
                    """UPDATE engine_redeems SET status='AMBIGUOUS',last_checked_at_ms=?,
                           last_error=?,response_json=? WHERE token_id=?""",
                    (
                        _now_ms(),
                        "Binance batch-redeem response had no txHash; automatic replay is forbidden",
                        json.dumps(payload, separators=(",", ":"), default=str)[:12000],
                        token_id,
                    ),
                )
            return
        stored_status = "REDEEMED" if venue_status in TERMINAL_SUCCESS_STATUSES else "SUBMITTED"
        completed = _now_ms() if stored_status == "REDEEMED" else None
        if completed is not None:
            self.last_success_at_ms = completed
        with self._connect() as db:
            db.execute(
                """UPDATE engine_redeems SET status=?,tx_hash=?,completed_at_ms=?,
                       last_checked_at_ms=?,last_error=NULL,response_json=? WHERE token_id=?""",
                (
                    stored_status,
                    tx_hash,
                    completed,
                    _now_ms(),
                    json.dumps(payload, separators=(",", ":"), default=str)[:12000],
                    token_id,
                ),
            )

    def run_cycle(self) -> bool:
        if not self.enabled:
            self.status = "DISABLED"
            return False
        if not self.cycle_lock.acquire(blocking=False):
            return False
        try:
            if str(self.venue_getter() or "").strip().lower() != "binance":
                self.status = "UNSUPPORTED_VENUE"
                self.last_error = None
                return False
            self.status = "SCANNING"
            client, wallet = self._ensure_client()
            tracked = self._tracked_binance_markets()
            positions = self._claimable_positions(client, wallet["walletAddress"])
            admitted: list[dict[str, Any]] = []
            for position in positions:
                if self._upsert_claimable(position, tracked):
                    admitted.append(position)
            self.last_scan_at_ms = _now_ms()
            self.claimable_count = len(admitted)
            self.claimable_amount_usdt = sum(float(_finite(row.get("value")) or 0.0) for row in admitted)

            with self._connect() as db:
                pending = [dict(row) for row in db.execute(
                    """SELECT * FROM engine_redeems
                         WHERE status IN ('SUBMITTED','PENDING','PROCESSING') AND tx_hash IS NOT NULL
                         ORDER BY discovered_at_ms ASC"""
                )]
            for row in pending:
                self._reconcile_row(client, wallet["walletAddress"], row)

            now_ms = int(client.server_timestamp_ms())
            with self._connect() as db:
                candidates = [dict(row) for row in db.execute(
                    """SELECT * FROM engine_redeems
                         WHERE status='CLAIMABLE' AND eligible_at_ms<=?
                         ORDER BY eligible_at_ms ASC LIMIT ?""",
                    (now_ms, MAX_REDEEMS_PER_SCAN),
                )]
            current_tokens = {str(row.get("tokenId") or "") for row in admitted}
            for row in candidates:
                # Reconfirm the token is still venue-explicit claimable in this exact scan.
                if str(row["token_id"]) in current_tokens:
                    self._submit_row(client, wallet, row)
            self.status = "READY"
            self.last_error = None
            return True
        except Exception as exc:
            self.status = "ERROR"
            self.last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            return False
        finally:
            self.cycle_lock.release()

    def _loop(self) -> None:
        # Match the old 4310 cadence: scan every 15 seconds; the per-position
        # eligibility fence still waits 60 seconds after market end.
        self.run_cycle()
        while not self.stop_event.wait(self.scan_interval_seconds):
            self.run_cycle()

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM engine_redeems ORDER BY discovered_at_ms DESC LIMIT ?",
                (max(1, min(200, int(limit))),),
            )]
        for row in rows:
            row.pop("response_json", None)
        return rows

    def summary(self) -> dict[str, Any]:
        try:
            with self._connect() as db:
                row = db.execute(
                    """SELECT COUNT(*) AS discovered,
                              SUM(CASE WHEN status='CLAIMABLE' THEN 1 ELSE 0 END) AS claimable,
                              SUM(CASE WHEN status IN ('ATTEMPTING','AMBIGUOUS','SUBMITTED','PENDING','PROCESSING') THEN 1 ELSE 0 END) AS pending,
                              SUM(CASE WHEN status='REDEEMED' THEN 1 ELSE 0 END) AS redeemed,
                              COALESCE(SUM(CASE WHEN status='REDEEMED' THEN claimable_value_usdt ELSE 0 END),0) AS redeemed_usdt
                         FROM engine_redeems"""
                ).fetchone()
                aggregate = dict(row) if row is not None else {}
        except sqlite3.OperationalError:
            aggregate = {}
        return {
            "version": VERSION,
            "enabled": self.enabled,
            "status": self.status,
            "pauseIndependent": True,
            "scope": "8781_SUBMITTED_BINANCE_MARKETS_ONLY",
            "delaySeconds": self.redeem_delay_seconds,
            "scanIntervalSeconds": self.scan_interval_seconds,
            "claimableCount": self.claimable_count,
            "claimableAmountUsdt": self.claimable_amount_usdt,
            "lastScanAtMs": self.last_scan_at_ms,
            "lastSuccessAtMs": self.last_success_at_ms,
            "lastError": self.last_error,
            "discoveredTotal": int(aggregate.get("discovered") or 0),
            "ledgerClaimable": int(aggregate.get("claimable") or 0),
            "pendingTotal": int(aggregate.get("pending") or 0),
            "redeemedTotal": int(aggregate.get("redeemed") or 0),
            "redeemedUsdt": float(aggregate.get("redeemed_usdt") or 0.0),
            "retryAmbiguousRedeem": False,
            "requiresVenueCanClaim": True,
            "requiresTrackedSubmittedMarket": True,
        }

    def close(self) -> None:
        self.stop_event.set()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=min(2.5, self.scan_interval_seconds + 0.5))
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None


__all__ = [
    "AUTO_REDEEM_ENV",
    "EchtgeldRedeemManager",
    "MAX_REDEEMS_PER_SCAN",
    "REDEEM_DELAY_SECONDS",
    "SCAN_INTERVAL_SECONDS",
    "VERSION",
]
