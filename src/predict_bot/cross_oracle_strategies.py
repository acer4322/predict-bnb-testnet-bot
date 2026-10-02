from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
SIM_DB_PATH = Path(os.environ.get("PREDICT_SIM_DB", ROOT / "data" / "simulation.db"))
BINANCE_REALTIME_URL = os.environ.get(
    "PREDICT_CROSS_ORACLE_BINANCE_REALTIME_URL",
    "http://127.0.0.1:8766/api/realtime",
)
POLL_INTERVAL_SECONDS = max(
    0.10,
    float(os.environ.get("PREDICT_CROSS_ORACLE_STRATEGY_POLL_SECONDS", "0.25")),
)
POLY_UP_THRESHOLD = min(
    0.99,
    max(0.51, float(os.environ.get("PREDICT_POLY_FLIP_UP_THRESHOLD", "0.55"))),
)
POLY_DOWN_THRESHOLD = max(
    0.01,
    min(0.49, float(os.environ.get("PREDICT_POLY_FLIP_DOWN_THRESHOLD", "0.45"))),
)
SCALP_MIN_EDGE = max(
    0.0,
    float(os.environ.get("PREDICT_POLY_GAP_SCALP_MIN_EDGE", "0.03")),
)
PAPER_STAKE_USDT = max(
    0.01,
    float(os.environ.get("PREDICT_CROSS_ORACLE_PAPER_STAKE_USDT", "1.0")),
)
MAX_ALIGNMENT_SKEW_SECONDS = max(
    1.0,
    float(os.environ.get("PREDICT_CROSS_ORACLE_MAX_ALIGNMENT_SKEW_SECONDS", "10.0")),
)

STRATEGY_POLY_LEAD_ENTRY = "R_POLY_LEAD_ENTRY"
STRATEGY_POLY_LEAD_EXIT = "R_POLY_LEAD_EXIT"
STRATEGY_POLY_GAP_SCALP = "R_POLY_GAP_SCALP"
STRATEGIES = (
    STRATEGY_POLY_LEAD_ENTRY,
    STRATEGY_POLY_LEAD_EXIT,
    STRATEGY_POLY_GAP_SCALP,
)
TERMINAL_STATUSES = {"EXITED", "SETTLED_WIN", "SETTLED_LOSS"}


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def probability_mid(bid: Any, ask: Any) -> float | None:
    bid_value = _float(bid)
    ask_value = _float(ask)
    if bid_value is None or ask_value is None:
        return None
    if not (0 <= bid_value <= ask_value <= 1):
        return None
    return (bid_value + ask_value) / 2.0


def probability_direction(
    up_mid: float | None,
    *,
    up_threshold: float = POLY_UP_THRESHOLD,
    down_threshold: float = POLY_DOWN_THRESHOLD,
) -> str | None:
    if up_mid is None:
        return None
    if up_mid >= up_threshold:
        return "UP"
    if up_mid <= down_threshold:
        return "DOWN"
    return None


def selected_probability(up_mid: float, side: str) -> float:
    return up_mid if side == "UP" else 1.0 - up_mid


def selected_quote(latest: dict[str, Any], side: str, kind: str) -> float | None:
    value = _float(latest.get(f"{side.lower()}_{kind}"))
    if value is None or not (0 < value <= 1):
        return None
    return value


def _http_json(url: str, timeout: float = 1.5) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "BTC-5M-Lab-Poly-Lead/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class CrossOraclePaperEngine:
    """Forward-only Paper strategies comparing Polymarket with Binance Prediction.

    This ledger is intentionally isolated from simulation.db and live_m0w.db.  It
    reads Binance realtime snapshots and official settlements but never calls a
    quote, order, redeem, or live-control endpoint.
    """

    def __init__(
        self,
        db_path: Path,
        poly_snapshot_provider: Callable[[], dict[str, Any]],
    ) -> None:
        self.db_path = Path(db_path)
        self.poly_snapshot_provider = poly_snapshot_provider
        self.lock = threading.RLock()
        self.db_lock = threading.RLock()
        self.stop_event = threading.Event()
        self.db = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()
        self.last_confident_poly_direction: dict[str, str] = {}
        self.last_flip: dict[str, Any] | None = None
        self.runtime: dict[str, Any] = {
            "status": "STARTING",
            "error": None,
            "updatedAtMs": None,
            "polyUpMid": None,
            "binanceUpMid": None,
            "probabilityGap": None,
            "polyDirection": None,
            "binanceDirection": None,
            "binanceMarketId": None,
            "polyMarketSlug": None,
            "secondsLeftSkew": None,
            "aligned": False,
        }

    def _create_schema(self) -> None:
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS cross_oracle_strategy_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL,
                    binance_market_id INTEGER NOT NULL,
                    poly_market_slug TEXT NOT NULL,
                    side TEXT NOT NULL,
                    status TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    stake_usdt REAL NOT NULL,
                    shares REAL NOT NULL,
                    gross_pnl_usdt REAL,
                    opened_at_ms INTEGER NOT NULL,
                    closed_at_ms INTEGER,
                    entry_poly_up_mid REAL,
                    entry_binance_up_mid REAL,
                    entry_probability_gap REAL,
                    entry_reason TEXT NOT NULL,
                    exit_reason TEXT,
                    official_winner TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_cross_oracle_strategy_status
                    ON cross_oracle_strategy_trades(strategy, status, id);
                CREATE INDEX IF NOT EXISTS idx_cross_oracle_strategy_market
                    ON cross_oracle_strategy_trades(strategy, binance_market_id, id);
                """
            )
            self.db.commit()

    def start(self) -> None:
        threading.Thread(
            target=self._run,
            name="cross-oracle-paper-strategies",
            daemon=True,
        ).start()

    def stop(self) -> None:
        self.stop_event.set()
        with self.db_lock:
            self.db.commit()
            self.db.close()

    def _run(self) -> None:
        last_settlement_check = 0.0
        while not self.stop_event.is_set():
            try:
                self._evaluate_once()
                now = time.monotonic()
                if now - last_settlement_check >= 2.0:
                    self._settle_official_positions()
                    last_settlement_check = now
                with self.lock:
                    self.runtime["status"] = "LIVE"
                    self.runtime["error"] = None
            except Exception as exc:  # pragma: no cover - runtime/network dependent.
                with self.lock:
                    self.runtime["status"] = "ERROR"
                    self.runtime["error"] = str(exc)[:300]
            self.stop_event.wait(POLL_INTERVAL_SECONDS)

    def _evaluate_once(self) -> None:
        payload = _http_json(BINANCE_REALTIME_URL)
        latest = payload.get("latest") if isinstance(payload, dict) else None
        if not isinstance(latest, dict):
            raise RuntimeError("Binance /api/realtime has no latest snapshot")
        poly_state = self.poly_snapshot_provider()
        market = poly_state.get("market") if isinstance(poly_state, dict) else None
        up = poly_state.get("up") if isinstance(poly_state, dict) else None
        down = poly_state.get("down") if isinstance(poly_state, dict) else None
        if not isinstance(market, dict) or not isinstance(up, dict) or not isinstance(down, dict):
            raise RuntimeError("Polymarket snapshot is not ready")

        binance_market_id = int(latest.get("market_id"))
        poly_slug = str(market.get("slug") or "")
        if not poly_slug:
            raise RuntimeError("Polymarket market slug is unavailable")

        poly_up_mid = probability_mid(up.get("bestBid"), up.get("bestAsk"))
        binance_up_mid = probability_mid(latest.get("up_bid"), latest.get("up_ask"))
        poly_direction = probability_direction(poly_up_mid)
        binance_direction = probability_direction(binance_up_mid)
        binance_seconds_left = _float(latest.get("seconds_left"))
        poly_seconds_left = _float(market.get("secondsLeft") or poly_state.get("secondsLeft"))
        seconds_left_skew = (
            abs(binance_seconds_left - poly_seconds_left)
            if binance_seconds_left is not None and poly_seconds_left is not None
            else None
        )
        aligned = seconds_left_skew is not None and seconds_left_skew <= MAX_ALIGNMENT_SKEW_SECONDS
        probability_gap = (
            poly_up_mid - binance_up_mid
            if poly_up_mid is not None and binance_up_mid is not None
            else None
        )

        now_ms = int(time.time() * 1000)
        with self.lock:
            self.runtime.update(
                status="LIVE",
                error=None,
                updatedAtMs=now_ms,
                polyUpMid=poly_up_mid,
                binanceUpMid=binance_up_mid,
                probabilityGap=probability_gap,
                polyDirection=poly_direction,
                binanceDirection=binance_direction,
                binanceMarketId=binance_market_id,
                polyMarketSlug=poly_slug,
                secondsLeftSkew=seconds_left_skew,
                aligned=aligned,
            )

        if not aligned or poly_up_mid is None or binance_up_mid is None:
            return

        previous_direction = self.last_confident_poly_direction.get(poly_slug)
        flipped = (
            poly_direction is not None
            and previous_direction is not None
            and poly_direction != previous_direction
        )
        if poly_direction is not None:
            self.last_confident_poly_direction[poly_slug] = poly_direction

        if flipped and poly_direction is not None:
            self.last_flip = {
                "atMs": now_ms,
                "from": previous_direction,
                "to": poly_direction,
                "polyUpMid": poly_up_mid,
                "binanceUpMid": binance_up_mid,
                "probabilityGap": probability_gap,
                "binanceMarketId": binance_market_id,
                "polyMarketSlug": poly_slug,
            }
            self._handle_flip(
                latest=latest,
                binance_market_id=binance_market_id,
                poly_slug=poly_slug,
                new_direction=poly_direction,
                poly_up_mid=poly_up_mid,
                binance_up_mid=binance_up_mid,
                binance_direction=binance_direction,
                now_ms=now_ms,
            )

        if poly_direction is not None:
            self._maybe_open_gap_scalp(
                latest=latest,
                binance_market_id=binance_market_id,
                poly_slug=poly_slug,
                poly_direction=poly_direction,
                poly_up_mid=poly_up_mid,
                binance_up_mid=binance_up_mid,
                now_ms=now_ms,
            )

    def _handle_flip(
        self,
        *,
        latest: dict[str, Any],
        binance_market_id: int,
        poly_slug: str,
        new_direction: str,
        poly_up_mid: float,
        binance_up_mid: float,
        binance_direction: str | None,
        now_ms: int,
    ) -> None:
        # Exit variant: close its existing position on a Polymarket reversal.
        exit_trade = self._open_trade_for_market(STRATEGY_POLY_LEAD_EXIT, binance_market_id)
        if exit_trade is not None and str(exit_trade["side"]) != new_direction:
            self._exit_trade_at_bid(exit_trade, latest, now_ms, "POLY_DIRECTION_FLIP")

        # High-frequency variant: every confident Polymarket reversal liquidates
        # the old direction. A fresh opposite position may be opened by the gap
        # evaluator on the same or next polling tick if it is still cheap enough.
        scalp_trade = self._open_trade_for_market(STRATEGY_POLY_GAP_SCALP, binance_market_id)
        if scalp_trade is not None and str(scalp_trade["side"]) != new_direction:
            self._exit_trade_at_bid(scalp_trade, latest, now_ms, "POLY_DIRECTION_FLIP")

        # A lead entry exists only if Polymarket has already crossed to a new
        # side and Binance Prediction has not yet crossed to that same side.
        if binance_direction == new_direction:
            return
        for strategy in (STRATEGY_POLY_LEAD_ENTRY, STRATEGY_POLY_LEAD_EXIT):
            if self._has_trade_for_market(strategy, binance_market_id):
                continue
            self._open_trade(
                strategy=strategy,
                latest=latest,
                binance_market_id=binance_market_id,
                poly_slug=poly_slug,
                side=new_direction,
                poly_up_mid=poly_up_mid,
                binance_up_mid=binance_up_mid,
                now_ms=now_ms,
                reason="POLY_FLIP_LEADS_BINANCE",
            )

    def _maybe_open_gap_scalp(
        self,
        *,
        latest: dict[str, Any],
        binance_market_id: int,
        poly_slug: str,
        poly_direction: str,
        poly_up_mid: float,
        binance_up_mid: float,
        now_ms: int,
    ) -> None:
        if self._open_trade_for_market(STRATEGY_POLY_GAP_SCALP, binance_market_id) is not None:
            return
        ask = selected_quote(latest, poly_direction, "ask")
        if ask is None:
            return
        poly_selected = selected_probability(poly_up_mid, poly_direction)
        executable_edge = poly_selected - ask
        if executable_edge < SCALP_MIN_EDGE:
            return
        self._open_trade(
            strategy=STRATEGY_POLY_GAP_SCALP,
            latest=latest,
            binance_market_id=binance_market_id,
            poly_slug=poly_slug,
            side=poly_direction,
            poly_up_mid=poly_up_mid,
            binance_up_mid=binance_up_mid,
            now_ms=now_ms,
            reason="POLY_BINANCE_EXECUTABLE_GAP",
            metadata={"polySelectedMid": poly_selected, "executableEdge": executable_edge},
        )

    def _open_trade(
        self,
        *,
        strategy: str,
        latest: dict[str, Any],
        binance_market_id: int,
        poly_slug: str,
        side: str,
        poly_up_mid: float,
        binance_up_mid: float,
        now_ms: int,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        entry = selected_quote(latest, side, "ask")
        if entry is None:
            return False
        shares = PAPER_STAKE_USDT / entry
        with self.db_lock:
            self.db.execute(
                """INSERT INTO cross_oracle_strategy_trades(
                       strategy, binance_market_id, poly_market_slug, side, status,
                       entry_price, stake_usdt, shares, opened_at_ms,
                       entry_poly_up_mid, entry_binance_up_mid,
                       entry_probability_gap, entry_reason, metadata_json
                   ) VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    strategy,
                    int(binance_market_id),
                    poly_slug,
                    side,
                    entry,
                    PAPER_STAKE_USDT,
                    shares,
                    now_ms,
                    poly_up_mid,
                    binance_up_mid,
                    poly_up_mid - binance_up_mid,
                    reason,
                    json.dumps(metadata or {}, separators=(",", ":"), sort_keys=True),
                ),
            )
            self.db.commit()
        return True

    def _exit_trade_at_bid(
        self,
        trade: sqlite3.Row,
        latest: dict[str, Any],
        now_ms: int,
        reason: str,
    ) -> bool:
        bid = selected_quote(latest, str(trade["side"]), "bid")
        if bid is None:
            return False
        pnl = float(trade["shares"]) * bid - float(trade["stake_usdt"])
        with self.db_lock:
            self.db.execute(
                """UPDATE cross_oracle_strategy_trades
                      SET status='EXITED', exit_price=?, gross_pnl_usdt=?,
                          closed_at_ms=?, exit_reason=?
                    WHERE id=? AND status='OPEN'""",
                (bid, pnl, now_ms, reason, int(trade["id"])),
            )
            self.db.commit()
        return True

    def _open_trade_for_market(self, strategy: str, market_id: int) -> sqlite3.Row | None:
        with self.db_lock:
            return self.db.execute(
                """SELECT * FROM cross_oracle_strategy_trades
                    WHERE strategy=? AND binance_market_id=? AND status='OPEN'
                    ORDER BY id DESC LIMIT 1""",
                (strategy, int(market_id)),
            ).fetchone()

    def _has_trade_for_market(self, strategy: str, market_id: int) -> bool:
        with self.db_lock:
            row = self.db.execute(
                """SELECT 1 FROM cross_oracle_strategy_trades
                    WHERE strategy=? AND binance_market_id=? LIMIT 1""",
                (strategy, int(market_id)),
            ).fetchone()
        return row is not None

    def _settle_official_positions(self) -> None:
        if not SIM_DB_PATH.exists():
            return
        with self.db_lock:
            open_rows = self.db.execute(
                """SELECT * FROM cross_oracle_strategy_trades
                    WHERE status='OPEN' ORDER BY id ASC"""
            ).fetchall()
        if not open_rows:
            return
        market_ids = sorted({int(row["binance_market_id"]) for row in open_rows})
        try:
            sim = sqlite3.connect(f"file:{SIM_DB_PATH}?mode=ro", uri=True, timeout=1.0)
            sim.row_factory = sqlite3.Row
            placeholders = ",".join("?" for _ in market_ids)
            settlements = sim.execute(
                f"""SELECT market_id, status, official_winner
                       FROM market_settlements
                      WHERE market_id IN ({placeholders})
                        AND status='OFFICIAL'
                        AND official_winner IN ('UP','DOWN')""",
                market_ids,
            ).fetchall()
            sim.close()
        except (sqlite3.Error, OSError):
            return
        winners = {int(row["market_id"]): str(row["official_winner"]) for row in settlements}
        if not winners:
            return
        now_ms = int(time.time() * 1000)
        with self.db_lock:
            for trade in open_rows:
                winner = winners.get(int(trade["binance_market_id"]))
                if winner is None:
                    continue
                won = str(trade["side"]) == winner
                payout = float(trade["shares"]) if won else 0.0
                pnl = payout - float(trade["stake_usdt"])
                self.db.execute(
                    """UPDATE cross_oracle_strategy_trades
                          SET status=?, gross_pnl_usdt=?, closed_at_ms=?,
                              exit_reason='OFFICIAL_SETTLEMENT', official_winner=?
                        WHERE id=? AND status='OPEN'""",
                    (
                        "SETTLED_WIN" if won else "SETTLED_LOSS",
                        pnl,
                        now_ms,
                        winner,
                        int(trade["id"]),
                    ),
                )
            self.db.commit()

    def _summary(self, strategy: str) -> dict[str, Any]:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT status, gross_pnl_usdt FROM cross_oracle_strategy_trades
                    WHERE strategy=?""",
                (strategy,),
            ).fetchall()
        terminal = [row for row in rows if str(row["status"]) in TERMINAL_STATUSES]
        wins = sum(
            (row["gross_pnl_usdt"] is not None and float(row["gross_pnl_usdt"]) > 0)
            for row in terminal
        )
        losses = sum(
            (row["gross_pnl_usdt"] is not None and float(row["gross_pnl_usdt"]) < 0)
            for row in terminal
        )
        pnl = sum(float(row["gross_pnl_usdt"] or 0.0) for row in terminal)
        return {
            "trades": len(rows),
            "open": sum(str(row["status"]) == "OPEN" for row in rows),
            "closed": len(terminal),
            "wins": wins,
            "losses": losses,
            "winRate": wins / (wins + losses) if wins + losses else None,
            "grossPnlUsdt": pnl,
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            runtime = dict(self.runtime)
            last_flip = dict(self.last_flip) if self.last_flip else None
        with self.db_lock:
            recent = [
                dict(row)
                for row in self.db.execute(
                    """SELECT * FROM cross_oracle_strategy_trades
                        ORDER BY id DESC LIMIT 20"""
                ).fetchall()
            ]
            open_positions = [
                dict(row)
                for row in self.db.execute(
                    """SELECT * FROM cross_oracle_strategy_trades
                        WHERE status='OPEN' ORDER BY id DESC LIMIT 20"""
                ).fetchall()
            ]
        return {
            "status": runtime.get("status"),
            "paperOnly": True,
            "liveOrdersAffected": False,
            "feesIncluded": False,
            "parameters": {
                "polyUpThreshold": POLY_UP_THRESHOLD,
                "polyDownThreshold": POLY_DOWN_THRESHOLD,
                "scalpMinExecutableEdge": SCALP_MIN_EDGE,
                "paperStakeUsdt": PAPER_STAKE_USDT,
                "pollIntervalMs": int(POLL_INTERVAL_SECONDS * 1000),
                "maxAlignmentSkewSeconds": MAX_ALIGNMENT_SKEW_SECONDS,
            },
            "runtime": runtime,
            "lastFlip": last_flip,
            "summaries": {strategy: self._summary(strategy) for strategy in STRATEGIES},
            "openPositions": open_positions,
            "recentTrades": recent,
            "strategyRules": {
                STRATEGY_POLY_LEAD_ENTRY: "Polymarket confident direction flips first while Binance Prediction has not crossed to the same direction; buy Binance ask and hold until official settlement.",
                STRATEGY_POLY_LEAD_EXIT: "Same lead entry as R_POLY_LEAD_ENTRY, but exit the held Binance side at its current bid when Polymarket confidently flips against it; otherwise hold to official settlement.",
                STRATEGY_POLY_GAP_SCALP: "Whenever the Polymarket-selected side mid exceeds the executable Binance ask by the configured edge, buy Binance ask; sell at Binance bid on the next confident Polymarket direction flip; if no flip occurs, hold to official settlement.",
            },
        }
