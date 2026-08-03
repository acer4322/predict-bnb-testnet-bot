from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

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
PAPER_RECENT_LIMIT = 20
PAPER_HISTORY_DEFAULT_LIMIT = 50
PAPER_HISTORY_MAX_LIMIT = 200
PAPER_VARIANTS = ("BTC_DOWN_ETH_UP", "BTC_UP_ETH_DOWN")
PAPER_EXPERIMENT = "DUAL_VARIANT_FIRST_ELIGIBLE"
# Keep this filename stable across server versions. Pulling code, restarting the
# process, or changing the vN wrapper must never select a new paper ledger.
PAPER_DB_PATH = Path(
    os.environ.get(
        "XPAIR_DASHBOARD_PAPER_DB",
        str(base.DB_PATH.with_name("xpair_dashboard_dual_paper.db")),
    )
).expanduser()
LEGACY_PAPER_DB_PATHS = (
    base.DB_PATH,
    base.DB_PATH.with_name("xpair_btc_eth_paper.db"),
)
_VARIANT_SIDES = {
    "BTC_DOWN_ETH_UP": ("DOWN", "UP"),
    "BTC_UP_ETH_DOWN": ("UP", "DOWN"),
}
_TRIAL_COLUMNS = (
    "strategy",
    "variant",
    "btc_topic_id",
    "btc_market_id",
    "eth_topic_id",
    "eth_market_id",
    "start_ms",
    "end_ms",
    "btc_start_price",
    "eth_start_price",
    "signal_at",
    "seconds_left",
    "btc_side",
    "eth_side",
    "entry_status",
    "rejection_reason",
    "eligible",
    "requested_stake",
    "requested_shares",
    "filled_shares",
    "fill_ratio",
    "btc_vwap",
    "eth_vwap",
    "btc_fee",
    "eth_fee",
    "total_cost",
    "cost_per_share",
    "btc_book_age_ms",
    "eth_book_age_ms",
    "cross_book_skew_ms",
    "btc_winner",
    "eth_winner",
    "winning_legs",
    "double_loss",
    "payout",
    "pnl",
    "settlement_status",
    "settled_at",
    "diagnostics_json",
)
_MIGRATION_LOCK = threading.RLock()
_MIGRATION_DONE = False


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


def _same_path(first: Path, second: Path) -> bool:
    try:
        return first.resolve() == second.resolve()
    except OSError:
        return str(first.absolute()) == str(second.absolute())


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def _init_persistence_metadata(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS xpair_paper_imports(
               source_path TEXT PRIMARY KEY,
               imported_rows INTEGER NOT NULL DEFAULT 0,
               last_checked_at TEXT NOT NULL
           )"""
    )
    db.commit()


def migrate_legacy_paper_ledgers(
    store: XPairStore,
    source_paths: Iterable[Path] = LEGACY_PAPER_DB_PATHS,
) -> dict[str, Any]:
    """Merge old paper databases without changing current A/B statistics.

    Imported rows remain visible in permanent history. They are excluded from the
    current dual-direction comparison unless their diagnostics already identify
    them as the same experiment.
    """
    _init_persistence_metadata(store.db)
    imported_now = 0
    checked_sources: list[str] = []
    columns = ", ".join(_TRIAL_COLUMNS)
    placeholders = ", ".join("?" for _ in _TRIAL_COLUMNS)
    insert_sql = (
        f"INSERT OR IGNORE INTO xpair_trials ({columns}) "
        f"VALUES ({placeholders})"
    )

    for raw_path in source_paths:
        source_path = Path(raw_path).expanduser()
        if _same_path(source_path, PAPER_DB_PATH) or not source_path.exists():
            continue
        source_name = str(source_path.resolve())
        checked_sources.append(source_name)
        source = sqlite3.connect(source_path, timeout=5.0)
        source.row_factory = sqlite3.Row
        source.execute("PRAGMA busy_timeout=5000")
        try:
            if not _table_exists(source, "xpair_trials"):
                rows: list[sqlite3.Row] = []
            else:
                source_columns = {
                    str(row[1]) for row in source.execute("PRAGMA table_info(xpair_trials)")
                }
                if not set(_TRIAL_COLUMNS).issubset(source_columns):
                    rows = []
                else:
                    rows = source.execute(
                        f"SELECT {columns} FROM xpair_trials WHERE eligible=1"
                    ).fetchall()
        finally:
            source.close()

        before = store.db.total_changes
        if rows:
            store.db.executemany(
                insert_sql,
                [tuple(row[column] for column in _TRIAL_COLUMNS) for row in rows],
            )
        imported = store.db.total_changes - before
        imported_now += imported
        store.db.execute(
            """INSERT INTO xpair_paper_imports(
                   source_path, imported_rows, last_checked_at
               ) VALUES (?, ?, ?)
               ON CONFLICT(source_path) DO UPDATE SET
                   imported_rows=xpair_paper_imports.imported_rows + excluded.imported_rows,
                   last_checked_at=excluded.last_checked_at""",
            (source_name, int(imported), utc_iso()),
        )
        store.db.commit()

    row = store.db.execute(
        "SELECT COALESCE(SUM(imported_rows), 0) FROM xpair_paper_imports"
    ).fetchone()
    return {
        "importedNow": int(imported_now),
        "importedTotal": int(row[0] or 0),
        "checkedSources": checked_sources,
    }


def ensure_legacy_paper_migration() -> dict[str, Any]:
    global _MIGRATION_DONE
    with _MIGRATION_LOCK:
        if _MIGRATION_DONE:
            store = XPairStore(PAPER_DB_PATH)
            try:
                _init_persistence_metadata(store.db)
                row = store.db.execute(
                    "SELECT COALESCE(SUM(imported_rows), 0) FROM xpair_paper_imports"
                ).fetchone()
                return {
                    "importedNow": 0,
                    "importedTotal": int(row[0] or 0),
                    "checkedSources": [],
                }
            finally:
                store.close()
        store = XPairStore(PAPER_DB_PATH)
        try:
            result = migrate_legacy_paper_ledgers(store)
            _MIGRATION_DONE = True
            return result
        finally:
            store.close()


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
            "experiment": PAPER_EXPERIMENT,
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
             WHERE btc_market_id=? AND eth_market_id=? AND eligible=1
               AND diagnostics_json LIKE ?""",
        (
            int(btc_market_id),
            int(eth_market_id),
            f"%{PAPER_EXPERIMENT}%",
        ),
    ).fetchall()
    return {str(row["variant"]) for row in rows}


def _summary_query(
    db: sqlite3.Connection,
    *,
    variant: str | None = None,
) -> dict[str, Any]:
    where = "eligible=1 AND diagnostics_json LIKE ?"
    parameters: list[Any] = [f"%{PAPER_EXPERIMENT}%"]
    if variant is not None:
        where += " AND variant=?"
        parameters.append(variant)
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
        tuple(parameters),
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
    experiment_pattern = f"%{PAPER_EXPERIMENT}%"
    paired_captured = int(
        db.execute(
            """SELECT COUNT(*) FROM (
                   SELECT btc_market_id, eth_market_id
                     FROM xpair_trials
                    WHERE eligible=1 AND diagnostics_json LIKE ?
                    GROUP BY btc_market_id, eth_market_id
                   HAVING COUNT(DISTINCT variant)=2
               )""",
            (experiment_pattern,),
        ).fetchone()[0]
    )
    paired_settled = int(
        db.execute(
            """SELECT COUNT(*) FROM (
                   SELECT btc_market_id, eth_market_id
                     FROM xpair_trials
                    WHERE eligible=1 AND settlement_status='SETTLED'
                      AND diagnostics_json LIKE ?
                    GROUP BY btc_market_id, eth_market_id
                   HAVING COUNT(DISTINCT variant)=2
               )""",
            (experiment_pattern,),
        ).fetchone()[0]
    )
    unique_markets = int(
        db.execute(
            """SELECT COUNT(*) FROM (
                   SELECT DISTINCT btc_market_id, eth_market_id
                     FROM xpair_trials
                    WHERE eligible=1 AND diagnostics_json LIKE ?
               )""",
            (experiment_pattern,),
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


def _history_item(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    raw_diagnostics = item.pop("diagnostics_json", None)
    try:
        diagnostics = json.loads(str(raw_diagnostics or "{}"))
    except json.JSONDecodeError:
        diagnostics = {}
    experiment = str(diagnostics.get("experiment") or "LEGACY_ARCHIVE")
    item["experiment"] = experiment
    item["ledger_class"] = (
        "CURRENT_DUAL_EXPERIMENT"
        if experiment == PAPER_EXPERIMENT
        else "LEGACY_ARCHIVE"
    )
    return item


def _history_rows(
    db: sqlite3.Connection,
    *,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    rows = db.execute(
        """SELECT id, strategy, variant, btc_market_id, eth_market_id,
                  signal_at, seconds_left, filled_shares, cost_per_share,
                  total_cost, btc_winner, eth_winner, winning_legs, payout,
                  pnl, settlement_status, settled_at, diagnostics_json
             FROM xpair_trials
            WHERE eligible=1
            ORDER BY signal_at DESC, id DESC
            LIMIT ? OFFSET ?""",
        (int(limit), int(offset)),
    ).fetchall()
    return [_history_item(row) for row in rows]


def paper_history_payload(
    *,
    limit: int = PAPER_HISTORY_DEFAULT_LIMIT,
    offset: int = 0,
    path: Path = PAPER_DB_PATH,
) -> dict[str, Any]:
    if _same_path(Path(path), PAPER_DB_PATH):
        migration = ensure_legacy_paper_migration()
    else:
        migration = {"importedNow": 0, "importedTotal": 0, "checkedSources": []}
    safe_limit = max(1, min(PAPER_HISTORY_MAX_LIMIT, int(limit)))
    safe_offset = max(0, int(offset))
    store = XPairStore(Path(path))
    try:
        total = int(
            store.db.execute(
                "SELECT COUNT(*) FROM xpair_trials WHERE eligible=1"
            ).fetchone()[0]
        )
        items = _history_rows(
            store.db,
            limit=safe_limit,
            offset=safe_offset,
        )
    finally:
        store.close()
    return {
        "persistent": True,
        "autoDelete": False,
        "ledgerPath": str(Path(path).resolve()),
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
        "hasMore": safe_offset + len(items) < total,
        "items": items,
        "migration": migration,
    }


def paper_dashboard_payload(path: Path = PAPER_DB_PATH) -> dict[str, Any]:
    if _same_path(Path(path), PAPER_DB_PATH):
        migration = ensure_legacy_paper_migration()
    else:
        migration = {"importedNow": 0, "importedTotal": 0, "checkedSources": []}
    store = XPairStore(Path(path))
    try:
        variants = {
            variant: _summary_query(store.db, variant=variant)
            for variant in PAPER_VARIANTS
        }
        summary = _summary_query(store.db)
        comparison = _comparison(store.db, variants)
        history_total = int(
            store.db.execute(
                "SELECT COUNT(*) FROM xpair_trials WHERE eligible=1"
            ).fetchone()[0]
        )
        recent = _history_rows(store.db, limit=PAPER_RECENT_LIMIT, offset=0)
    finally:
        store.close()
    return {
        **PAPER_RUNTIME.snapshot(),
        "paperOnly": True,
        "experiment": PAPER_EXPERIMENT,
        "entryRule": "FIRST_ELIGIBLE_ONCE_PER_VARIANT_PER_ALIGNED_MARKET",
        "minimumSecondsAfterStart": PAPER_MIN_SECONDS_AFTER_START,
        "minimumSecondsLeft": PAPER_MIN_SECONDS_LEFT,
        "usesSignedQuote": False,
        "independentOfLiveArm": True,
        "ledgerPath": str(Path(path).resolve()),
        "storage": {
            "persistent": True,
            "autoDelete": False,
            "historyTotal": history_total,
            "recentPreviewLimit": PAPER_RECENT_LIMIT,
            "historyPageSize": PAPER_HISTORY_DEFAULT_LIMIT,
            "importedLegacyRows": migration["importedTotal"],
        },
        "summary": summary,
        "variants": variants,
        "comparison": comparison,
        "recent": recent,
    }


def paper_simulation_loop() -> None:
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        PAPER_RUNTIME.update(
            "DISABLED_MISSING_CREDENTIALS",
            error="missing Binance credentials",
        )
        return

    migration = ensure_legacy_paper_migration()
    if migration["importedNow"]:
        base.STATE.log(
            f"PAPER_HISTORY_IMPORTED rows={migration['importedNow']}",
            "INFO",
        )
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
                        base.STATE.log(
                            f"PAPER_XPAIR_SETTLED count={settled}",
                            "INFO",
                        )

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
                        0.0,
                        (min(btc.end_ms, eth.end_ms) - now_ms) / 1000.0,
                    )
                    seconds_after_start = max(
                        0.0,
                        (now_ms - max(btc.start_ms, eth.start_ms)) / 1000.0,
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
                                    requested_stake=float(
                                        base.STATE.config.pair_budget_usdt
                                    ),
                                    btc_book_age_ms=_finite(
                                        preview.get("btcBookAgeMs")
                                    ),
                                    eth_book_age_ms=_finite(
                                        preview.get("ethBookAgeMs")
                                    ),
                                    cross_book_skew_ms=_finite(
                                        preview.get("crossBookSkewMs")
                                    ),
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
                                    f"PAPER_XPAIR_CAPTURED market={key} "
                                    f"variant={trial['variant']} "
                                    f"left={seconds_left:.1f}s "
                                    f"cost/share={trial['cost_per_share']:.6f}",
                                    "INFO",
                                )

                            if captured_now:
                                capture = {
                                    "marketKey": key,
                                    "variant": "+".join(
                                        str(item["variant"])
                                        for item in captured_now
                                    ),
                                    "variants": captured_now,
                                    "capturedAt": utc_iso(),
                                }
                                PAPER_RUNTIME.update(
                                    "PAPER_DUAL_ENTRY_CAPTURED",
                                    capture=capture,
                                )
                                after_capture = already_captured | {
                                    str(item["variant"])
                                    for item in captured_now
                                }
                                remaining = [
                                    item
                                    for item in PAPER_VARIANTS
                                    if item not in after_capture
                                ]
                                status = (
                                    f"BOTH_VARIANTS_CAPTURED_WAIT_SETTLEMENT market={key}"
                                    if not remaining
                                    else "ONE_VARIANT_CAPTURED_WAIT_OTHER "
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
