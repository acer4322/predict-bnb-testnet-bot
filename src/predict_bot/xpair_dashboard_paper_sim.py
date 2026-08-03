from __future__ import annotations

import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import xpair_canary_autopilot_server as base
from . import xpair_canary_autopilot_server_v2 as v2
from .core import BinancePredictionClient, select_binary_market
from .xpair_btc_eth_paper import (
    MarketRef,
    XPairStore,
    discover_active_pair,
    reconcile_settlements,
    utc_iso,
)

PAPER_MIN_SECONDS_AFTER_START = 5.0
PAPER_MIN_SECONDS_LEFT = 20.0
PAPER_DISCOVERY_INTERVAL_SECONDS = 5.0
PAPER_SETTLEMENT_INTERVAL_SECONDS = 15.0
PAPER_POLL_INTERVAL_SECONDS = 0.25
PAPER_RECENT_LIMIT = 40
PAPER_VARIANTS = ("BTC_DOWN_ETH_UP", "BTC_UP_ETH_DOWN")
PAPER_DB_PATH = Path(
    os.environ.get(
        "XPAIR_DASHBOARD_PAPER_DB",
        str(base.DB_PATH.with_name("xpair_dashboard_dual_paper.db")),
    )
)
_VARIANT_SIDES = {
    "BTC_DOWN_ETH_UP": ("DOWN", "UP"),
    "BTC_UP_ETH_DOWN": ("UP", "DOWN"),
}


class PaperRuntime:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.running = False
        self.status = "STARTING"
        self.last_error: str | None = None
        self.last_capture: dict[str, Any] | None = None
        self.updated_at = utc_iso()

    def update(
        self,
        status: str,
        *,
        error: str | None = None,
        capture: dict[str, Any] | None = None,
    ) -> None:
        with self.lock:
            self.status = status
            self.last_error = error
            if capture is not None:
                self.last_capture = dict(capture)
            self.updated_at = utc_iso()

    def set_running(self, value: bool) -> None:
        with self.lock:
            self.running = bool(value)
            self.updated_at = utc_iso()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "running": self.running,
                "status": self.status,
                "lastError": self.last_error,
                "lastCapture": dict(self.last_capture) if self.last_capture else None,
                "updatedAt": self.updated_at,
            }


PAPER_RUNTIME = PaperRuntime()


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _analysis_age_seconds(analysis: dict[str, Any]) -> float | None:
    raw = analysis.get("updatedAt")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def selected_preview_trial(
    analysis: dict[str, Any], selection: str
) -> dict[str, Any] | None:
    normalized = str(selection or "").upper()
    desired_variant = (
        str(analysis.get("selectedVariant") or "").upper()
        if normalized == "CHEAPEST_ELIGIBLE"
        else normalized
    )
    if desired_variant not in _VARIANT_SIDES:
        return None
    for item in analysis.get("trials") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("variant") or "").upper() != desired_variant:
            continue
        if item.get("eligible") is not True:
            return None
        return dict(item)
    return None


def trial_for_store(preview: dict[str, Any]) -> dict[str, Any] | None:
    variant = str(preview.get("variant") or "").upper()
    sides = _VARIANT_SIDES.get(variant)
    shares = _finite(preview.get("filledShares"))
    cost_per_share = _finite(preview.get("costPerShare"))
    if sides is None or shares is None or shares <= 0:
        return None
    if cost_per_share is None or cost_per_share <= 0:
        return None
    btc_vwap = _finite(preview.get("btcVwap"))
    eth_vwap = _finite(preview.get("ethVwap"))
    total_cost = shares * cost_per_share
    return {
        "variant": variant,
        "btc_side": sides[0],
        "eth_side": sides[1],
        "eligible": True,
        "entry_status": "ELIGIBLE",
        "rejection_reason": None,
        "requested_shares": shares,
        "filled_shares": shares,
        "fill_ratio": 1.0,
        "btc_vwap": btc_vwap,
        "eth_vwap": eth_vwap,
        "btc_fee": None,
        "eth_fee": None,
        "total_cost": total_cost,
        "cost_per_share": cost_per_share,
        "diagnostics": {
            "paper_only": True,
            "source": "dashboard_continuous_book_monitor",
            "experiment": "DUAL_VARIANT_FIRST_ELIGIBLE",
            "entry_rule": "FIRST_ELIGIBLE_ONCE_PER_VARIANT_PER_ALIGNED_MARKET",
            "minimum_seconds_after_start": PAPER_MIN_SECONDS_AFTER_START,
            "minimum_seconds_left": PAPER_MIN_SECONDS_LEFT,
            "signed_quote_used": False,
            "atomic_execution_assumed": True,
        },
    }


def captured_variants(
    db: sqlite3.Connection,
    *,
    btc_market_id: int,
    eth_market_id: int,
) -> set[str]:
    rows = db.execute(
        """SELECT variant FROM xpair_trials
             WHERE btc_market_id=? AND eth_market_id=? AND eligible=1""",
        (int(btc_market_id), int(eth_market_id)),
    ).fetchall()
    return {str(row["variant"]) for row in rows}


def _summary_query(
    db: sqlite3.Connection,
    *,
    variant: str | None = None,
) -> dict[str, Any]:
    where = "eligible=1"
    parameters: tuple[Any, ...] = ()
    if variant is not None:
        where += " AND variant=?"
        parameters = (variant,)
    row = db.execute(
        f"""SELECT
                COUNT(*) AS captured,
                SUM(CASE WHEN settlement_status='PENDING' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN settlement_status='SETTLED' THEN 1 ELSE 0 END) AS settled,
                SUM(CASE WHEN settlement_status='SETTLED' AND winning_legs=1 THEN 1 ELSE 0 END) AS one_win,
                SUM(CASE WHEN settlement_status='SETTLED' AND winning_legs=2 THEN 1 ELSE 0 END) AS two_wins,
                SUM(CASE WHEN settlement_status='SETTLED' AND winning_legs=0 THEN 1 ELSE 0 END) AS double_losses,
                SUM(CASE WHEN settlement_status='SETTLED' THEN total_cost ELSE 0 END) AS total_cost,
                SUM(CASE WHEN settlement_status='SETTLED' THEN pnl ELSE 0 END) AS pnl
            FROM xpair_trials
           WHERE {where}""",
        parameters,
    ).fetchone()
    captured = int(row["captured"] or 0)
    pending = int(row["pending"] or 0)
    settled = int(row["settled"] or 0)
    one_win = int(row["one_win"] or 0)
    two_wins = int(row["two_wins"] or 0)
    double_losses = int(row["double_losses"] or 0)
    total_cost = float(row["total_cost"] or 0.0)
    pnl = float(row["pnl"] or 0.0)
    return {
        "captured": captured,
        "pending": pending,
        "settled": settled,
        "oneWin": one_win,
        "twoWins": two_wins,
        "doubleLosses": double_losses,
        "oneWinRate": one_win / settled if settled else None,
        "twoWinRate": two_wins / settled if settled else None,
        "doubleLossRate": double_losses / settled if settled else None,
        "totalCostUsdt": total_cost,
        "pnlUsdt": pnl,
        "roi": pnl / total_cost if total_cost > 0 else None,
    }


def _comparison(db: sqlite3.Connection, variants: dict[str, dict[str, Any]]) -> dict[str, Any]:
    paired_captured = int(
        db.execute(
            """SELECT COUNT(*) FROM (
                   SELECT btc_market_id, eth_market_id
                     FROM xpair_trials
                    WHERE eligible=1
                    GROUP BY btc_market_id, eth_market_id
                   HAVING COUNT(DISTINCT variant)=2
               )"""
        ).fetchone()[0]
    )
    paired_settled = int(
        db.execute(
            """SELECT COUNT(*) FROM (
                   SELECT btc_market_id, eth_market_id
                     FROM xpair_trials
                    WHERE eligible=1 AND settlement_status='SETTLED'
                    GROUP BY btc_market_id, eth_market_id
                   HAVING COUNT(DISTINCT variant)=2
               )"""
        ).fetchone()[0]
    )
    unique_markets = int(
        db.execute(
            """SELECT COUNT(*) FROM (
                   SELECT DISTINCT btc_market_id, eth_market_id
                     FROM xpair_trials WHERE eligible=1
               )"""
        ).fetchone()[0]
    )
    down = variants["BTC_DOWN_ETH_UP"]
    up = variants["BTC_UP_ETH_DOWN"]

    def difference(first: Any, second: Any) -> float | None:
        left = _finite(first)
        right = _finite(second)
        return left - right if left is not None and right is not None else None

    return {
        "uniqueMarkets": unique_markets,
        "bothVariantsCapturedMarkets": paired_captured,
        "bothVariantsSettledMarkets": paired_settled,
        "downMinusUpRoi": difference(down.get("roi"), up.get("roi")),
        "downMinusUpPnlUsdt": difference(down.get("pnlUsdt"), up.get("pnlUsdt")),
        "downMinusUpDoubleLossRate": difference(
            down.get("doubleLossRate"), up.get("doubleLossRate")
        ),
        "downMinusUpAverageCostPerTrade": difference(
            (
                down["totalCostUsdt"] / down["settled"]
                if down.get("settled")
                else None
            ),
            (
                up["totalCostUsdt"] / up["settled"]
                if up.get("settled")
                else None
            ),
        ),
    }


def paper_dashboard_payload(path: Path = PAPER_DB_PATH) -> dict[str, Any]:
    store = XPairStore(Path(path))
    try:
        variants = {
            variant: _summary_query(store.db, variant=variant)
            for variant in PAPER_VARIANTS
        }
        summary = _summary_query(store.db)
        comparison = _comparison(store.db, variants)
        rows = store.db.execute(
            """SELECT id, variant, btc_market_id, eth_market_id, signal_at,
                      seconds_left, filled_shares, cost_per_share, total_cost,
                      btc_winner, eth_winner, winning_legs, payout, pnl,
                      settlement_status, settled_at
                 FROM xpair_trials
                WHERE eligible=1
                ORDER BY id DESC LIMIT ?""",
            (PAPER_RECENT_LIMIT,),
        ).fetchall()
        recent = [dict(row) for row in rows]
    finally:
        store.close()
    return {
        **PAPER_RUNTIME.snapshot(),
        "paperOnly": True,
        "experiment": "DUAL_VARIANT_FIRST_ELIGIBLE",
        "entryRule": "FIRST_ELIGIBLE_ONCE_PER_VARIANT_PER_ALIGNED_MARKET",
        "minimumSecondsAfterStart": PAPER_MIN_SECONDS_AFTER_START,
        "minimumSecondsLeft": PAPER_MIN_SECONDS_LEFT,
        "usesSignedQuote": False,
        "independentOfLiveArm": True,
        "ledgerPath": str(path),
        "summary": summary,
        "variants": variants,
        "comparison": comparison,
        "recent": recent,
    }


def paper_simulation_loop() -> None:
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        PAPER_RUNTIME.update("DISABLED_MISSING_CREDENTIALS", error="missing Binance credentials")
        return

    client = BinancePredictionClient(api_key, api_secret)
    store = XPairStore(PAPER_DB_PATH)
    pair: tuple[MarketRef, MarketRef] | None = None
    next_discovery = 0.0
    next_settlement = 0.0
    last_status = ""
    PAPER_RUNTIME.set_running(True)
    try:
        while True:
            loop_started = time.monotonic()
            try:
                now_ms = client.server_timestamp_ms()
                if loop_started >= next_settlement:
                    next_settlement = loop_started + PAPER_SETTLEMENT_INTERVAL_SECONDS
                    settled = reconcile_settlements(store, client, now_ms)
                    if settled:
                        base.STATE.log(f"PAPER_XPAIR_SETTLED count={settled}", "INFO")

                if pair is not None and now_ms >= max(pair[0].end_ms, pair[1].end_ms):
                    pair = None
                if pair is None and loop_started >= next_discovery:
                    next_discovery = loop_started + PAPER_DISCOVERY_INTERVAL_SECONDS
                    pair = discover_active_pair(
                        client,
                        select_binary_market,
                        now_ms=now_ms,
                        tolerance_ms=base.STATE.config.market_time_tolerance_ms,
                    )

                if pair is None:
                    status = "WAITING_FOR_ALIGNED_MARKET"
                else:
                    btc, eth = pair
                    key = base.market_key(btc, eth)
                    seconds_left = max(
                        0.0, (min(btc.end_ms, eth.end_ms) - now_ms) / 1000.0
                    )
                    seconds_after_start = max(
                        0.0, (now_ms - max(btc.start_ms, eth.start_ms)) / 1000.0
                    )
                    already_captured = captured_variants(
                        store.db,
                        btc_market_id=btc.market_id,
                        eth_market_id=eth.market_id,
                    )
                    missing_variants = [
                        item for item in PAPER_VARIANTS if item not in already_captured
                    ]
                    if not missing_variants:
                        status = f"BOTH_VARIANTS_CAPTURED_WAIT_SETTLEMENT market={key}"
                    elif seconds_after_start < PAPER_MIN_SECONDS_AFTER_START:
                        status = f"WAITING_START_GUARD elapsed={seconds_after_start:.1f}s"
                    elif seconds_left <= PAPER_MIN_SECONDS_LEFT:
                        status = (
                            f"NO_ENTRY_LATE_MARKET left={seconds_left:.1f}s "
                            f"missing={','.join(missing_variants)}"
                        )
                    else:
                        analysis = v2.latest_book_analysis()
                        analysis_age = _analysis_age_seconds(analysis or {})
                        if not analysis or analysis.get("marketKey") != key:
                            status = "WAITING_BOOK_ANALYSIS"
                        elif analysis_age is None or analysis_age > 3.0:
                            status = f"WAITING_FRESH_ANALYSIS age={analysis_age}"
                        else:
                            captured_now: list[dict[str, Any]] = []
                            for variant in missing_variants:
                                preview = selected_preview_trial(analysis, variant)
                                trial = trial_for_store(preview or {}) if preview else None
                                if trial is None:
                                    continue
                                store.record_capture(
                                    btc=btc,
                                    eth=eth,
                                    signal_at=utc_iso(),
                                    seconds_left=seconds_left,
                                    requested_stake=float(base.STATE.config.pair_budget_usdt),
                                    btc_book_age_ms=_finite(preview.get("btcBookAgeMs")),
                                    eth_book_age_ms=_finite(preview.get("ethBookAgeMs")),
                                    cross_book_skew_ms=_finite(preview.get("crossBookSkewMs")),
                                    trials=[trial],
                                )
                                captured_now.append(
                                    {
                                        "variant": trial["variant"],
                                        "secondsLeft": seconds_left,
                                        "filledShares": trial["filled_shares"],
                                        "costPerShare": trial["cost_per_share"],
                                        "totalCostUsdt": trial["total_cost"],
                                    }
                                )
                                base.STATE.log(
                                    f"PAPER_XPAIR_CAPTURED market={key} variant={trial['variant']} "
                                    f"left={seconds_left:.1f}s "
                                    f"cost/share={trial['cost_per_share']:.6f}",
                                    "INFO",
                                )

                            if captured_now:
                                capture = {
                                    "marketKey": key,
                                    "variant": "+".join(
                                        str(item["variant"]) for item in captured_now
                                    ),
                                    "variants": captured_now,
                                    "capturedAt": utc_iso(),
                                }
                                PAPER_RUNTIME.update(
                                    "PAPER_DUAL_ENTRY_CAPTURED",
                                    capture=capture,
                                )
                                after_capture = already_captured | {
                                    str(item["variant"]) for item in captured_now
                                }
                                remaining = [
                                    item for item in PAPER_VARIANTS
                                    if item not in after_capture
                                ]
                                status = (
                                    f"BOTH_VARIANTS_CAPTURED_WAIT_SETTLEMENT market={key}"
                                    if not remaining
                                    else f"ONE_VARIANT_CAPTURED_WAIT_OTHER "
                                    f"market={key} missing={','.join(remaining)}"
                                )
                            else:
                                status = (
                                    "WAITING_FIRST_ELIGIBLE_BOTH_VARIANTS "
                                    f"missing={','.join(missing_variants)}"
                                )

                if status != last_status:
                    PAPER_RUNTIME.update(status)
                    last_status = status
            except Exception as exc:
                message = f"{type(exc).__name__}: {str(exc)[:500]}"
                PAPER_RUNTIME.update("PAPER_SIM_ERROR", error=message)
                base.STATE.log(f"PAPER_XPAIR_ERROR {message}", "ERROR")
                time.sleep(2.0)

            elapsed = time.monotonic() - loop_started
            time.sleep(max(0.0, PAPER_POLL_INTERVAL_SECONDS - elapsed))
    finally:
        PAPER_RUNTIME.set_running(False)
        client.close()
        store.close()
