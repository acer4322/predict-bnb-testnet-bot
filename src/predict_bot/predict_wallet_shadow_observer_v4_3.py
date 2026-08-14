from __future__ import annotations

import math
import os
import sqlite3
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v3 as v3
from . import predict_wallet_shadow_observer_v4_1 as v4_1
from . import predict_wallet_shadow_observer_v4_2 as v4_2
from . import predict_wallet_shadow_v0_capital_s1 as capital_s1
from . import predict_wallet_target_accounting as target_accounting


VERSION = "PREDICT_WALLET_SHADOW_V0_5_1_TARGET_ACCOUNTING"
LEGACY_CAPITAL_COHORTS_ENABLED = os.environ.get(
    "PREDICT_WALLET_SHADOW_LEGACY_COHORTS_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
TARGET_SIMILARITY_CACHE_MS = 5_000
TARGET_RATIO_CACHE_MS = 60_000


def _residual(rows: list[dict[str, Any]], cutoff_ms: int | None = None) -> dict[str, Any]:
    selected = [row for row in rows if cutoff_ms is None or int(row["event_ms"]) <= cutoff_ms]
    up = sum(float(row.get("shares") or 0.0) for row in selected if row.get("side") == "UP")
    down = sum(float(row.get("shares") or 0.0) for row in selected if row.get("side") == "DOWN")
    up_cost = sum(
        float(row.get("shares") or 0.0) * float(row.get("price") or 0.0)
        for row in selected
        if row.get("side") == "UP"
    )
    down_cost = sum(
        float(row.get("shares") or 0.0) * float(row.get("price") or 0.0)
        for row in selected
        if row.get("side") == "DOWN"
    )
    return {
        "side": "UP" if up > down else "DOWN" if down > up else None,
        "capitalSide": "UP" if up_cost > down_cost else "DOWN" if down_cost > up_cost else None,
        "upShares": up,
        "downShares": down,
        "upCostUsdt": up_cost,
        "downCostUsdt": down_cost,
        "legs": len(selected),
    }


class WalletShadowObserver(v4_2.WalletShadowObserver):
    """Dashboard-ready Spot/Strike forward cohort with bounded storage and target diagnostics."""

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=v4_2.SIMULATION_DB_PATH) -> None:
        self.forward_event: dict[str, Any] | None = None
        self.forward_decision: dict[str, Any] | None = None
        self.forward_last_block: dict[str, Any] | None = None
        self.forward_last_cleanup_deleted = {"events": 0, "decisions": 0, "results": 0}
        self.forward_similarity_cache: dict[str, Any] | None = None
        self.forward_similarity_cache_at_ms = 0
        self.simulation_db: sqlite3.Connection | None = None
        self.simulation_db_path = Path(simulation_db_path)
        self.simulation_db_error: str | None = None
        self.capital_s1_state = capital_s1.CapitalS1State()
        self.capital_s1_active_market = False
        self.capital_s1_recent_events: list[dict[str, Any]] = []
        self.cap100_active_market = False
        self.cap100_used_capital_usdt = 0.0
        self.cap100_truncated = False
        self.cap100_recent_events: list[dict[str, Any]] = []
        self.min1_active_market = False
        self.min1_used_capital_usdt = 0.0
        self.min1_maker_up_shares = 0.0
        self.min1_maker_down_shares = 0.0
        self.min1_taker_up_shares = 0.0
        self.min1_taker_down_shares = 0.0
        self.min1_last_taker_signature: str | None = None
        self.min1_last_taker_side: str | None = None
        self.min1_taker_side_switches = 0
        self.min1_taker_blocked = False
        self.min1_recent_events: list[dict[str, Any]] = []
        self.growth_active_market = False
        self.growth_market_budget_usdt = 0.0
        self.growth_used_capital_usdt = 0.0
        self.growth_maker_up_shares = self.growth_maker_down_shares = 0.0
        self.growth_taker_up_shares = self.growth_taker_down_shares = 0.0
        self.growth_last_taker_signature: str | None = None
        self.growth_last_taker_side: str | None = None
        self.growth_taker_side_switches = 0
        self.growth_taker_blocked = False
        self.growth_recent_events: list[dict[str, Any]] = []
        self.time20_active_market = False
        self.time20_market_budget_usdt = 0.0
        self.time20_used_capital_usdt = 0.0
        self.time20_unlocked_fraction = 0.0
        self.time20_maker_up_shares = self.time20_maker_down_shares = 0.0
        self.time20_taker_up_shares = self.time20_taker_down_shares = 0.0
        self.time20_last_taker_signature: str | None = None
        self.time20_last_taker_side: str | None = None
        self.time20_taker_side_switches = 0
        self.time20_taker_blocked = False
        self.time20_recent_events: list[dict[str, Any]] = []
        self.batched_active_market = False
        self.batched_market_budget_usdt = 0.0
        self.batched_maker_capital_usdt = 0.0
        self.batched_taker_capital_usdt = 0.0
        self.batched_pending_maker_shares = {"UP": 0.0, "DOWN": 0.0}
        self.batched_maker_up_shares = self.batched_maker_down_shares = 0.0
        self.batched_taker_up_shares = self.batched_taker_down_shares = 0.0
        self.batched_core_candidate_side: str | None = None
        self.batched_core_stability_count = 0
        self.batched_last_taker_signature: str | None = None
        self.batched_last_taker_side: str | None = None
        self.batched_taker_side_switches = 0
        self.batched_taker_blocked = False
        self.batched_recent_events: list[dict[str, Any]] = []
        self.target_ratio_cache: dict[str, Any] | None = None
        self.target_ratio_cache_at_ms = 0

        # Bypass v4_2.__init__: its read-only simulation DB open is fatal when the
        # source DB is temporarily unavailable. The rest of Wallet Shadow must stay up.
        v4_1.WalletShadowObserver.__init__(self, db_path)

        try:
            self.simulation_db = sqlite3.connect(
                f"file:{self.simulation_db_path.as_posix()}?mode=ro",
                uri=True,
                check_same_thread=False,
                timeout=2.0,
            )
            self.simulation_db.row_factory = sqlite3.Row
        except (sqlite3.Error, OSError) as exc:
            self.simulation_db = None
            self.simulation_db_error = str(exc)[:500]

        with self.db_lock:
            row = self.db.execute(
                "SELECT deployed_at_ms FROM wallet_spot_strike_forward_meta WHERE cohort=?",
                (v4_2.COHORT,),
            ).fetchone()
            if row is None:
                self.forward_deployed_at_ms = base._now_ms()
                self.db.execute(
                    "INSERT INTO wallet_spot_strike_forward_meta(cohort,deployed_at_ms,policy_json) VALUES (?,?,?)",
                    (
                        v4_2.COHORT,
                        self.forward_deployed_at_ms,
                        base.json.dumps(self._forward_config(), separators=(",", ":")),
                    ),
                )
                self.db.commit()
            else:
                self.forward_deployed_at_ms = int(row[0])

            row = self.db.execute(
                "SELECT deployed_at_ms,excluded_market_id FROM wallet_shadow_capital_s1_meta WHERE cohort=?",
                (capital_s1.COHORT,),
            ).fetchone()
            if row is None:
                self.capital_s1_deployed_at_ms = base._now_ms()
                self.capital_s1_excluded_market_id: int | None = None
                self.db.execute(
                    "INSERT INTO wallet_shadow_capital_s1_meta(cohort,deployed_at_ms,excluded_market_id,policy_json) VALUES (?,?,?,?)",
                    (
                        capital_s1.COHORT,
                        self.capital_s1_deployed_at_ms,
                        None,
                        base.json.dumps(self._capital_s1_config(), separators=(",", ":")),
                    ),
                )
                self.db.commit()
            else:
                self.capital_s1_deployed_at_ms = int(row["deployed_at_ms"])
                self.capital_s1_excluded_market_id = int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None

            row = self.db.execute(
                "SELECT deployed_at_ms,excluded_market_id FROM wallet_shadow_cap100_meta WHERE cohort=?",
                (capital_s1.CAP100_COHORT,),
            ).fetchone()
            if row is None:
                self.cap100_deployed_at_ms = base._now_ms()
                self.cap100_excluded_market_id: int | None = None
                self.db.execute(
                    "INSERT INTO wallet_shadow_cap100_meta(cohort,deployed_at_ms,excluded_market_id,policy_json) VALUES (?,?,?,?)",
                    (
                        capital_s1.CAP100_COHORT,
                        self.cap100_deployed_at_ms,
                        None,
                        base.json.dumps(self._cap100_config(), separators=(",", ":")),
                    ),
                )
                self.db.commit()
            else:
                self.cap100_deployed_at_ms = int(row["deployed_at_ms"])
                self.cap100_excluded_market_id = int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None

            row = self.db.execute(
                "SELECT deployed_at_ms,excluded_market_id FROM wallet_shadow_min1_meta WHERE cohort=?",
                (capital_s1.MIN1_EXEC_CAP100_COHORT,),
            ).fetchone()
            if row is None:
                self.min1_deployed_at_ms = base._now_ms()
                self.min1_excluded_market_id: int | None = None
                self.db.execute(
                    "INSERT INTO wallet_shadow_min1_meta(cohort,deployed_at_ms,excluded_market_id,policy_json) VALUES (?,?,?,?)",
                    (
                        capital_s1.MIN1_EXEC_CAP100_COHORT,
                        self.min1_deployed_at_ms,
                        None,
                        base.json.dumps(self._min1_config(), separators=(",", ":")),
                    ),
                )
                self.db.commit()
            else:
                self.min1_deployed_at_ms = int(row["deployed_at_ms"])
                self.min1_excluded_market_id = int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None

            row = self.db.execute(
                "SELECT deployed_at_ms,excluded_market_id FROM wallet_shadow_growth_meta WHERE cohort=?",
                (capital_s1.MIN1_WALLET_GROWTH_COHORT,),
            ).fetchone()
            if row is None:
                self.growth_deployed_at_ms = base._now_ms()
                self.growth_excluded_market_id: int | None = None
                self.db.execute(
                    "INSERT INTO wallet_shadow_growth_meta(cohort,deployed_at_ms,excluded_market_id,policy_json) VALUES (?,?,?,?)",
                    (capital_s1.MIN1_WALLET_GROWTH_COHORT, self.growth_deployed_at_ms, None,
                     base.json.dumps(self._growth_config(), separators=(",", ":"))),
                )
                self.db.commit()
            else:
                self.growth_deployed_at_ms = int(row["deployed_at_ms"])
                self.growth_excluded_market_id = int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None

            row = self.db.execute(
                "SELECT deployed_at_ms,excluded_market_id FROM wallet_shadow_growth_meta WHERE cohort=?",
                (capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT,),
            ).fetchone()
            if row is None:
                self.time20_deployed_at_ms = base._now_ms()
                self.time20_excluded_market_id: int | None = None
                self.db.execute(
                    "INSERT INTO wallet_shadow_growth_meta(cohort,deployed_at_ms,excluded_market_id,policy_json) VALUES (?,?,?,?)",
                    (capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT, self.time20_deployed_at_ms, None,
                     base.json.dumps(self._time20_config(), separators=(",", ":"))),
                )
                self.db.commit()
            else:
                self.time20_deployed_at_ms = int(row["deployed_at_ms"])
                self.time20_excluded_market_id = int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None

            row = self.db.execute(
                "SELECT deployed_at_ms,excluded_market_id FROM wallet_shadow_growth_meta WHERE cohort=?",
                (capital_s1.MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT,),
            ).fetchone()
            if row is None:
                self.batched_deployed_at_ms = base._now_ms()
                self.batched_excluded_market_id: int | None = None
                self.db.execute(
                    "INSERT INTO wallet_shadow_growth_meta(cohort,deployed_at_ms,excluded_market_id,policy_json) VALUES (?,?,?,?)",
                    (capital_s1.MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT, self.batched_deployed_at_ms, None,
                     base.json.dumps(self._batched_config(), separators=(",", ":"))),
                )
                self.db.commit()
            else:
                self.batched_deployed_at_ms = int(row["deployed_at_ms"])
                self.batched_excluded_market_id = int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None

        self._backfill_target_market_results()

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            self.db.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_wallet_spot_strike_forward_events_time
                    ON wallet_spot_strike_forward_events(cohort, decision_at_ms);
                CREATE INDEX IF NOT EXISTS idx_wallet_spot_strike_forward_decisions_time
                    ON wallet_spot_strike_forward_decisions(cohort, decision_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_shadow_capital_s1_meta (
                    cohort TEXT PRIMARY KEY,
                    deployed_at_ms INTEGER NOT NULL,
                    excluded_market_id INTEGER,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_shadow_capital_s1_markets (
                    cohort TEXT NOT NULL,
                    wallet TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    started_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(cohort, wallet, market_id)
                );
                CREATE TABLE IF NOT EXISTS wallet_shadow_capital_s1_events (
                    id TEXT PRIMARY KEY,
                    cohort TEXT NOT NULL,
                    wallet TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    source_event_id TEXT NOT NULL,
                    source_at_ms INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    role TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL,
                    original_shares REAL NOT NULL,
                    scaled_shares REAL NOT NULL,
                    accepted INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(cohort, source_event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_shadow_capital_s1_events_market
                    ON wallet_shadow_capital_s1_events(cohort, wallet, market_id, source_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_shadow_capital_s1_results (
                    cohort TEXT NOT NULL,
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
                    gross_roi REAL,
                    maker_cost_usdt REAL NOT NULL,
                    taker_cost_usdt REAL NOT NULL,
                    taker_fee_usdt REAL NOT NULL,
                    fee_net_pnl_usdt REAL NOT NULL,
                    fee_net_roi REAL,
                    stress_1tick_cost_usdt REAL NOT NULL,
                    stress_1tick_pnl_usdt REAL NOT NULL,
                    stress_1tick_roi REAL,
                    stress_2tick_cost_usdt REAL NOT NULL,
                    stress_2tick_pnl_usdt REAL NOT NULL,
                    stress_2tick_roi REAL,
                    PRIMARY KEY(cohort, wallet, market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_shadow_capital_s1_results_time
                    ON wallet_shadow_capital_s1_results(cohort, wallet, resolved_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_shadow_cap100_meta (
                    cohort TEXT PRIMARY KEY,
                    deployed_at_ms INTEGER NOT NULL,
                    excluded_market_id INTEGER,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_shadow_cap100_markets (
                    cohort TEXT NOT NULL,
                    wallet TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    started_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(cohort, wallet, market_id)
                );
                CREATE TABLE IF NOT EXISTS wallet_shadow_cap100_events (
                    id TEXT PRIMARY KEY,
                    cohort TEXT NOT NULL,
                    wallet TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    source_s1_event_id TEXT NOT NULL,
                    source_at_ms INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    requested_shares REAL NOT NULL,
                    executed_shares REAL NOT NULL,
                    principal_cost_usdt REAL NOT NULL,
                    fee_usdt REAL NOT NULL,
                    capital_cost_usdt REAL NOT NULL,
                    truncated INTEGER NOT NULL,
                    blocked INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(cohort, source_s1_event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_shadow_cap100_events_market
                    ON wallet_shadow_cap100_events(cohort, wallet, market_id, source_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_shadow_cap100_results (
                    cohort TEXT NOT NULL,
                    wallet TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    winner TEXT NOT NULL,
                    resolved_at_ms INTEGER NOT NULL,
                    traded INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    fill_count INTEGER NOT NULL,
                    truncated INTEGER NOT NULL,
                    truncated_event_count INTEGER NOT NULL,
                    blocked_event_count INTEGER NOT NULL,
                    capital_cost_usdt REAL NOT NULL,
                    payout_usdt REAL NOT NULL,
                    net_pnl_usdt REAL NOT NULL,
                    net_roi REAL,
                    baseline_s1_capital_usdt REAL NOT NULL,
                    baseline_s1_pnl_usdt REAL NOT NULL,
                    baseline_s1_roi REAL,
                    pnl_delta_usdt REAL NOT NULL,
                    PRIMARY KEY(cohort, wallet, market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_shadow_cap100_results_time
                    ON wallet_shadow_cap100_results(cohort, wallet, resolved_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_shadow_min1_meta (
                    cohort TEXT PRIMARY KEY,
                    deployed_at_ms INTEGER NOT NULL,
                    excluded_market_id INTEGER,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_shadow_min1_markets (
                    cohort TEXT NOT NULL,
                    wallet TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    started_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(cohort, wallet, market_id)
                );
                CREATE TABLE IF NOT EXISTS wallet_shadow_min1_events (
                    id TEXT PRIMARY KEY,
                    cohort TEXT NOT NULL,
                    wallet TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    source_event_id TEXT,
                    at_ms INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    role TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL,
                    desired_shares REAL NOT NULL,
                    minimum_shares REAL,
                    requested_shares REAL NOT NULL,
                    executed_shares REAL NOT NULL,
                    principal_cost_usdt REAL NOT NULL,
                    fee_usdt REAL NOT NULL,
                    capital_cost_usdt REAL NOT NULL,
                    minimum_uplift INTEGER NOT NULL,
                    cap_truncated INTEGER NOT NULL,
                    blocked INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(cohort, source_event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_shadow_min1_events_market
                    ON wallet_shadow_min1_events(cohort, wallet, market_id, at_ms);
                CREATE TABLE IF NOT EXISTS wallet_shadow_min1_results (
                    cohort TEXT NOT NULL,
                    wallet TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    winner TEXT NOT NULL,
                    resolved_at_ms INTEGER NOT NULL,
                    traded INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    fill_count INTEGER NOT NULL,
                    capital_cost_usdt REAL NOT NULL,
                    payout_usdt REAL NOT NULL,
                    net_pnl_usdt REAL NOT NULL,
                    net_roi REAL,
                    minimum_uplift_events INTEGER NOT NULL,
                    cap_truncated_events INTEGER NOT NULL,
                    blocked_events INTEGER NOT NULL,
                    cap_hit INTEGER NOT NULL,
                    PRIMARY KEY(cohort, wallet, market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_shadow_min1_results_time
                    ON wallet_shadow_min1_results(cohort, wallet, resolved_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_shadow_growth_meta (
                    cohort TEXT PRIMARY KEY, deployed_at_ms INTEGER NOT NULL,
                    excluded_market_id INTEGER, policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_shadow_growth_markets (
                    cohort TEXT NOT NULL, wallet TEXT NOT NULL, market_id INTEGER NOT NULL,
                    title TEXT, started_at_ms INTEGER NOT NULL, available_cash_at_start_usdt REAL NOT NULL,
                    planned_budget_usdt REAL NOT NULL, PRIMARY KEY(cohort,wallet,market_id)
                );
                CREATE TABLE IF NOT EXISTS wallet_shadow_growth_events (
                    id TEXT PRIMARY KEY, cohort TEXT NOT NULL, wallet TEXT NOT NULL, market_id INTEGER NOT NULL,
                    source_event_id TEXT, at_ms INTEGER NOT NULL, event_type TEXT NOT NULL, role TEXT NOT NULL,
                    side TEXT NOT NULL, price REAL, desired_shares REAL NOT NULL, minimum_shares REAL,
                    requested_shares REAL NOT NULL, executed_shares REAL NOT NULL, principal_cost_usdt REAL NOT NULL,
                    fee_usdt REAL NOT NULL, capital_cost_usdt REAL NOT NULL, minimum_uplift INTEGER NOT NULL,
                    budget_truncated INTEGER NOT NULL, blocked INTEGER NOT NULL, reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL, UNIQUE(cohort,source_event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_shadow_growth_events_market
                    ON wallet_shadow_growth_events(cohort,wallet,market_id,at_ms);
                CREATE TABLE IF NOT EXISTS wallet_shadow_growth_results (
                    cohort TEXT NOT NULL, wallet TEXT NOT NULL, market_id INTEGER NOT NULL, title TEXT,
                    winner TEXT NOT NULL, resolved_at_ms INTEGER NOT NULL, traded INTEGER NOT NULL,
                    status TEXT NOT NULL, fill_count INTEGER NOT NULL, planned_budget_usdt REAL NOT NULL,
                    capital_cost_usdt REAL NOT NULL, payout_usdt REAL NOT NULL, net_pnl_usdt REAL NOT NULL,
                    net_roi REAL, minimum_uplift_events INTEGER NOT NULL, blocked_events INTEGER NOT NULL,
                    budget_hit INTEGER NOT NULL, PRIMARY KEY(cohort,wallet,market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_shadow_growth_results_time
                    ON wallet_shadow_growth_results(cohort,wallet,resolved_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_shadow_batched_pending (
                    cohort TEXT NOT NULL, wallet TEXT NOT NULL, market_id INTEGER NOT NULL,
                    side TEXT NOT NULL, pending_shares REAL NOT NULL, updated_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(cohort,wallet,market_id,side)
                );
                CREATE TABLE IF NOT EXISTS wallet_shadow_target_event_context (
                    leg_id TEXT PRIMARY KEY,
                    wallet TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    event_ms INTEGER NOT NULL,
                    observed_at_ms INTEGER NOT NULL,
                    event_delay_ms INTEGER NOT NULL,
                    scheduled_seconds_left REAL,
                    observed_seconds_left REAL,
                    up_bid REAL,
                    up_ask REAL,
                    down_bid REAL,
                    down_ask REAL,
                    core_side TEXT,
                    core_source TEXT,
                    causal_context INTEGER NOT NULL,
                    context_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_shadow_target_context_market
                    ON wallet_shadow_target_event_context(wallet,market_id,event_ms);
                CREATE TABLE IF NOT EXISTS wallet_shadow_target_market_results (
                    wallet TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    winner TEXT NOT NULL,
                    resolved_at_ms INTEGER NOT NULL,
                    historical_reconstruction INTEGER NOT NULL,
                    accounting_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    event_count INTEGER NOT NULL,
                    maker_event_count INTEGER NOT NULL,
                    taker_event_count INTEGER NOT NULL,
                    buy_notional_usdt REAL NOT NULL,
                    sell_proceeds_usdt REAL NOT NULL,
                    collateral_fees_usdt REAL NOT NULL,
                    payout_usdt REAL NOT NULL,
                    net_pnl_usdt REAL NOT NULL,
                    net_roi REAL,
                    maker_notional_usdt REAL NOT NULL,
                    maker_pnl_usdt REAL NOT NULL,
                    taker_notional_usdt REAL NOT NULL,
                    taker_pnl_usdt REAL NOT NULL,
                    gross_up_shares REAL NOT NULL,
                    gross_down_shares REAL NOT NULL,
                    share_fees_up REAL NOT NULL,
                    share_fees_down REAL NOT NULL,
                    net_up_shares REAL NOT NULL,
                    net_down_shares REAL NOT NULL,
                    share_conviction_side TEXT,
                    capital_conviction_side TEXT,
                    share_direction_correct INTEGER,
                    capital_direction_correct INTEGER,
                    PRIMARY KEY(wallet,market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_shadow_target_results_time
                    ON wallet_shadow_target_market_results(wallet,resolved_at_ms);
                """
            )
            self.db.commit()

    def _capital_s1_config(self) -> dict[str, Any]:
        return {
            "cohort": capital_s1.COHORT,
            "makerShareScale": capital_s1.SHARE_SCALE,
            "makerSourceShares": 18.0,
            "makerEffectiveShares": 1.0,
            "takerSourceCapShares": capital_s1.TAKER_SOURCE_CAP_SHARES,
            "takerEffectiveCapShares": capital_s1.TAKER_SOURCE_CAP_SHARES * capital_s1.SHARE_SCALE,
            "maxTakerSideSwitches": capital_s1.MAX_TAKER_SIDE_SWITCHES,
            "takerFeeBps": capital_s1.TAKER_FEE_BPS,
            "entryPolicy": "retain every V0 Maker fill proxy; retain V0 Taker intents until a second side switch, then block later Taker only",
        }

    def _cap100_config(self) -> dict[str, Any]:
        return {
            "cohort": capital_s1.CAP100_COHORT,
            "sourceCohort": capital_s1.COHORT,
            "maximumCapitalPerMarketUsdt": capital_s1.CAP100_USDT,
            "capitalIncludesTakerFee": True,
            "takerFeeBps": capital_s1.TAKER_FEE_BPS,
            "lastFillPolicy": "shrink the final eligible fill to remaining fee-inclusive capital; block all later eligible fills",
        }

    def _min1_config(self) -> dict[str, Any]:
        return {
            "cohort": capital_s1.MIN1_EXEC_CAP100_COHORT,
            "minimumOrderPrincipalUsdt": capital_s1.MINIMUM_ORDER_NOTIONAL_USDT,
            "maximumCapitalPerMarketUsdt": capital_s1.CAP100_USDT,
            "capitalIncludesTakerFee": True,
            "takerFeeBps": capital_s1.TAKER_FEE_BPS,
            "desiredMakerShares": 1.0,
            "desiredTakerCapShares": 2.0,
            "maxTakerSideSwitches": capital_s1.MAX_TAKER_SIDE_SWITCHES,
            "inventoryPolicy": "minimum-order executed shares update independent Maker/Taker inventory and independently trigger Taker correction",
        }

    def _growth_config(self) -> dict[str, Any]:
        return {
            "cohort": capital_s1.MIN1_WALLET_GROWTH_COHORT,
            "initialWalletUsdt": capital_s1.INITIAL_WALLET_USDT,
            "minimumOrderPrincipalUsdt": capital_s1.MINIMUM_ORDER_NOTIONAL_USDT,
            "marketBudgetFractionOfAvailableCash": capital_s1.MARKET_BUDGET_FRACTION,
            "permanentWalletCap": None,
            "capitalIncludesTakerFee": True,
            "allocationPolicy": "at market start lock max($1, 20% of available cash); unused budget remains cash; settlement payout returns to cash",
        }

    def _time20_config(self) -> dict[str, Any]:
        return {
            **self._growth_config(),
            "cohort": capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT,
            "timeTranches": 5,
            "trancheFraction": 0.20,
            "trancheSeconds": 60,
            "carryUnusedForwardWithinMarket": True,
            "futureTrancheBorrowing": False,
            "allocationPolicy": "lock the market budget at start, then cumulatively unlock 20% during each elapsed minute",
        }

    def _batched_config(self) -> dict[str, Any]:
        return {
            **self._growth_config(),
            "cohort": capital_s1.MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT,
            "sourceMakerUnitShares": capital_s1.SOURCE_MAKER_UNIT_SHARES,
            "researchMakerSignalShares": capital_s1.RESEARCH_MAKER_UNIT_SHARES,
            "sourceToResearchRatio": "18:1",
            "makerBudgetFraction": capital_s1.MAKER_BUDGET_FRACTION,
            "takerReserveFraction": capital_s1.TAKER_BUDGET_FRACTION,
            "coreStabilityConfirmations": capital_s1.CORE_STABILITY_CONFIRMATIONS,
            "makerPolicy": "accumulate same-side scaled Maker signals until the pending batch is worth at least $1; never uplift each source event independently",
            "takerPolicy": "after two same-side core observations, buy only the current opposing net residual; Taker size is not rounded to an 18-share tier",
            "ratioPolicy": "18 is retained only as the empirically common target Maker source unit; it is not treated as a fixed Maker:Taker ratio",
        }

    def _reset_market(self, market_id: int, bucket: int | None, title: str | None) -> None:
        super()._reset_market(market_id, bucket, title)
        if not LEGACY_CAPITAL_COHORTS_ENABLED:
            self.capital_s1_active_market = False
            self.cap100_active_market = False
            self.min1_active_market = False
            self.growth_active_market = False
            self.time20_active_market = False
            self.batched_active_market = False
            self.capital_s1_recent_events = []
            self.cap100_recent_events = []
            self.min1_recent_events = []
            self.growth_recent_events = []
            self.time20_recent_events = []
            self.batched_recent_events = []
            return
        with self.db_lock:
            if self.capital_s1_excluded_market_id is None:
                self.capital_s1_excluded_market_id = int(market_id)
                self.db.execute(
                    "UPDATE wallet_shadow_capital_s1_meta SET excluded_market_id=? WHERE cohort=?",
                    (int(market_id), capital_s1.COHORT),
                )
                self.db.commit()
            registered = self.db.execute(
                "SELECT 1 FROM wallet_shadow_capital_s1_markets WHERE cohort=? AND wallet=? AND market_id=?",
                (capital_s1.COHORT, self.wallet, int(market_id)),
            ).fetchone()
            self.capital_s1_active_market = bool(registered) or int(market_id) != self.capital_s1_excluded_market_id
            if self.capital_s1_active_market:
                self.db.execute(
                    "INSERT OR IGNORE INTO wallet_shadow_capital_s1_markets(cohort,wallet,market_id,title,started_at_ms) VALUES (?,?,?,?,?)",
                    (capital_s1.COHORT, self.wallet, int(market_id), title, base._now_ms()),
                )
                self.db.commit()
            prior = [dict(row) for row in self.db.execute(
                "SELECT * FROM wallet_shadow_capital_s1_events WHERE cohort=? AND wallet=? AND market_id=? ORDER BY source_at_ms,id",
                (capital_s1.COHORT, self.wallet, int(market_id)),
            )]
        self.capital_s1_state = capital_s1.CapitalS1State()
        for item in prior:
            if str(item["event_type"]) == "TAKER_INTENT":
                next_state, _ = capital_s1.reduce_source_event(
                    self.capital_s1_state,
                    event_type="TAKER_INTENT",
                    side=str(item["side"]),
                    original_shares=float(item["original_shares"]),
                )
                self.capital_s1_state = next_state
        self.capital_s1_recent_events = []
        for item in reversed(prior[-40:]):
            try:
                payload = base.json.loads(str(item.get("payload_json") or "{}"))
            except (TypeError, ValueError):
                payload = {}
            self.capital_s1_recent_events.append(payload if isinstance(payload, dict) else {})

        with self.db_lock:
            if self.cap100_excluded_market_id is None:
                self.cap100_excluded_market_id = int(market_id)
                self.db.execute(
                    "UPDATE wallet_shadow_cap100_meta SET excluded_market_id=? WHERE cohort=?",
                    (int(market_id), capital_s1.CAP100_COHORT),
                )
                self.db.commit()
            cap_registered = self.db.execute(
                "SELECT 1 FROM wallet_shadow_cap100_markets WHERE cohort=? AND wallet=? AND market_id=?",
                (capital_s1.CAP100_COHORT, self.wallet, int(market_id)),
            ).fetchone()
            self.cap100_active_market = bool(cap_registered) or int(market_id) != self.cap100_excluded_market_id
            if self.cap100_active_market:
                self.db.execute(
                    "INSERT OR IGNORE INTO wallet_shadow_cap100_markets(cohort,wallet,market_id,title,started_at_ms) VALUES (?,?,?,?,?)",
                    (capital_s1.CAP100_COHORT, self.wallet, int(market_id), title, base._now_ms()),
                )
                self.db.commit()
            cap_prior = [dict(row) for row in self.db.execute(
                "SELECT * FROM wallet_shadow_cap100_events WHERE cohort=? AND wallet=? AND market_id=? ORDER BY source_at_ms,id",
                (capital_s1.CAP100_COHORT, self.wallet, int(market_id)),
            )]
        self.cap100_used_capital_usdt = sum(float(item["capital_cost_usdt"] or 0.0) for item in cap_prior)
        self.cap100_truncated = any(bool(item["truncated"]) for item in cap_prior)
        self.cap100_recent_events = []
        for item in reversed(cap_prior[-40:]):
            try:
                cap_payload = base.json.loads(str(item.get("payload_json") or "{}"))
            except (TypeError, ValueError):
                cap_payload = {}
            self.cap100_recent_events.append(cap_payload if isinstance(cap_payload, dict) else {})

        with self.db_lock:
            if self.min1_excluded_market_id is None:
                self.min1_excluded_market_id = int(market_id)
                self.db.execute(
                    "UPDATE wallet_shadow_min1_meta SET excluded_market_id=? WHERE cohort=?",
                    (int(market_id), capital_s1.MIN1_EXEC_CAP100_COHORT),
                )
                self.db.commit()
            min1_registered = self.db.execute(
                "SELECT 1 FROM wallet_shadow_min1_markets WHERE cohort=? AND wallet=? AND market_id=?",
                (capital_s1.MIN1_EXEC_CAP100_COHORT, self.wallet, int(market_id)),
            ).fetchone()
            self.min1_active_market = bool(min1_registered) or int(market_id) != self.min1_excluded_market_id
            if self.min1_active_market:
                self.db.execute(
                    "INSERT OR IGNORE INTO wallet_shadow_min1_markets(cohort,wallet,market_id,title,started_at_ms) VALUES (?,?,?,?,?)",
                    (capital_s1.MIN1_EXEC_CAP100_COHORT, self.wallet, int(market_id), title, base._now_ms()),
                )
                self.db.commit()
            min1_prior = [dict(row) for row in self.db.execute(
                "SELECT * FROM wallet_shadow_min1_events WHERE cohort=? AND wallet=? AND market_id=? ORDER BY at_ms,id",
                (capital_s1.MIN1_EXEC_CAP100_COHORT, self.wallet, int(market_id)),
            )]
        self.min1_used_capital_usdt = sum(float(item["capital_cost_usdt"] or 0.0) for item in min1_prior)
        self.min1_maker_up_shares = sum(float(item["executed_shares"] or 0.0) for item in min1_prior if item["role"] == "MAKER" and item["side"] == "UP")
        self.min1_maker_down_shares = sum(float(item["executed_shares"] or 0.0) for item in min1_prior if item["role"] == "MAKER" and item["side"] == "DOWN")
        self.min1_taker_up_shares = sum(float(item["executed_shares"] or 0.0) for item in min1_prior if item["role"] == "TAKER" and item["side"] == "UP")
        self.min1_taker_down_shares = sum(float(item["executed_shares"] or 0.0) for item in min1_prior if item["role"] == "TAKER" and item["side"] == "DOWN")
        self.min1_last_taker_signature = None
        self.min1_last_taker_side = None
        self.min1_taker_side_switches = 0
        self.min1_taker_blocked = False
        self.min1_recent_events = []
        for item in min1_prior:
            try:
                min1_payload = base.json.loads(str(item.get("payload_json") or "{}"))
            except (TypeError, ValueError):
                min1_payload = {}
            if isinstance(min1_payload, dict):
                if item["role"] == "TAKER":
                    self.min1_last_taker_signature = str(min1_payload.get("takerSignature") or "") or self.min1_last_taker_signature
                    self.min1_last_taker_side = str(min1_payload.get("lastTakerSide") or "") or self.min1_last_taker_side
                    self.min1_taker_side_switches = int(min1_payload.get("takerSideSwitches") or self.min1_taker_side_switches)
                    self.min1_taker_blocked = bool(min1_payload.get("takerBlocked"))
        for item in reversed(min1_prior[-40:]):
            try:
                min1_payload = base.json.loads(str(item.get("payload_json") or "{}"))
            except (TypeError, ValueError):
                min1_payload = {}
            self.min1_recent_events.append(min1_payload if isinstance(min1_payload, dict) else {})

        with self.db_lock:
            if self.growth_excluded_market_id is None:
                self.growth_excluded_market_id = int(market_id)
                self.db.execute("UPDATE wallet_shadow_growth_meta SET excluded_market_id=? WHERE cohort=?",
                                (int(market_id), capital_s1.MIN1_WALLET_GROWTH_COHORT))
                self.db.commit()
            existing_growth = self.db.execute(
                "SELECT planned_budget_usdt FROM wallet_shadow_growth_markets WHERE cohort=? AND wallet=? AND market_id=?",
                (capital_s1.MIN1_WALLET_GROWTH_COHORT, self.wallet, int(market_id)),
            ).fetchone()
            spent_all = float(self.db.execute(
                "SELECT COALESCE(SUM(capital_cost_usdt),0) FROM wallet_shadow_growth_events WHERE cohort=? AND wallet=?",
                (capital_s1.MIN1_WALLET_GROWTH_COHORT, self.wallet),
            ).fetchone()[0])
            payouts_all = float(self.db.execute(
                "SELECT COALESCE(SUM(payout_usdt),0) FROM wallet_shadow_growth_results WHERE cohort=? AND wallet=?",
                (capital_s1.MIN1_WALLET_GROWTH_COHORT, self.wallet),
            ).fetchone()[0])
            available_cash = max(0.0, capital_s1.INITIAL_WALLET_USDT - spent_all + payouts_all)
            self.growth_active_market = bool(existing_growth) or int(market_id) != self.growth_excluded_market_id
            if self.growth_active_market and existing_growth is None:
                budget = capital_s1.planned_market_budget(available_cash)
                self.db.execute(
                    """INSERT INTO wallet_shadow_growth_markets(
                           cohort,wallet,market_id,title,started_at_ms,available_cash_at_start_usdt,planned_budget_usdt
                       ) VALUES (?,?,?,?,?,?,?)""",
                    (capital_s1.MIN1_WALLET_GROWTH_COHORT, self.wallet, int(market_id), title,
                     base._now_ms(), available_cash, budget),
                )
                self.db.commit()
                self.growth_market_budget_usdt = budget
            else:
                self.growth_market_budget_usdt = float(existing_growth["planned_budget_usdt"]) if existing_growth else 0.0
            growth_prior = [dict(row) for row in self.db.execute(
                "SELECT * FROM wallet_shadow_growth_events WHERE cohort=? AND wallet=? AND market_id=? ORDER BY at_ms,id",
                (capital_s1.MIN1_WALLET_GROWTH_COHORT, self.wallet, int(market_id)),
            )]
        self.growth_used_capital_usdt = sum(float(item["capital_cost_usdt"] or 0) for item in growth_prior)
        self.growth_maker_up_shares = sum(float(x["executed_shares"] or 0) for x in growth_prior if x["role"] == "MAKER" and x["side"] == "UP")
        self.growth_maker_down_shares = sum(float(x["executed_shares"] or 0) for x in growth_prior if x["role"] == "MAKER" and x["side"] == "DOWN")
        self.growth_taker_up_shares = sum(float(x["executed_shares"] or 0) for x in growth_prior if x["role"] == "TAKER" and x["side"] == "UP")
        self.growth_taker_down_shares = sum(float(x["executed_shares"] or 0) for x in growth_prior if x["role"] == "TAKER" and x["side"] == "DOWN")
        self.growth_last_taker_signature = self.growth_last_taker_side = None
        self.growth_taker_side_switches = 0
        self.growth_taker_blocked = False
        self.growth_recent_events = []
        for item in growth_prior:
            try: gp = base.json.loads(str(item.get("payload_json") or "{}"))
            except (TypeError, ValueError): gp = {}
            if isinstance(gp, dict) and item["role"] == "TAKER":
                self.growth_last_taker_signature = str(gp.get("takerSignature") or "") or self.growth_last_taker_signature
                self.growth_last_taker_side = str(gp.get("lastTakerSide") or "") or self.growth_last_taker_side
                self.growth_taker_side_switches = int(gp.get("takerSideSwitches") or self.growth_taker_side_switches)
                self.growth_taker_blocked = bool(gp.get("takerBlocked"))
        for item in reversed(growth_prior[-40:]):
            try: gp = base.json.loads(str(item.get("payload_json") or "{}"))
            except (TypeError, ValueError): gp = {}
            self.growth_recent_events.append(gp if isinstance(gp, dict) else {})

        with self.db_lock:
            if self.time20_excluded_market_id is None:
                self.time20_excluded_market_id = int(market_id)
                self.db.execute("UPDATE wallet_shadow_growth_meta SET excluded_market_id=? WHERE cohort=?",
                                (int(market_id), capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT))
                self.db.commit()
            existing_time20 = self.db.execute(
                "SELECT planned_budget_usdt FROM wallet_shadow_growth_markets WHERE cohort=? AND wallet=? AND market_id=?",
                (capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT, self.wallet, int(market_id)),
            ).fetchone()
            time20_spent = float(self.db.execute(
                "SELECT COALESCE(SUM(capital_cost_usdt),0) FROM wallet_shadow_growth_events WHERE cohort=? AND wallet=?",
                (capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT, self.wallet)).fetchone()[0])
            time20_payouts = float(self.db.execute(
                "SELECT COALESCE(SUM(payout_usdt),0) FROM wallet_shadow_growth_results WHERE cohort=? AND wallet=?",
                (capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT, self.wallet)).fetchone()[0])
            time20_cash = max(0.0, capital_s1.INITIAL_WALLET_USDT-time20_spent+time20_payouts)
            self.time20_active_market = bool(existing_time20) or int(market_id) != self.time20_excluded_market_id
            if self.time20_active_market and existing_time20 is None:
                budget = capital_s1.planned_market_budget(time20_cash)
                self.db.execute(
                    """INSERT INTO wallet_shadow_growth_markets(
                           cohort,wallet,market_id,title,started_at_ms,available_cash_at_start_usdt,planned_budget_usdt
                       ) VALUES (?,?,?,?,?,?,?)""",
                    (capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT, self.wallet, int(market_id), title,
                     base._now_ms(), time20_cash, budget))
                self.db.commit()
                self.time20_market_budget_usdt = budget
            else:
                self.time20_market_budget_usdt = float(existing_time20["planned_budget_usdt"]) if existing_time20 else 0.0
            time20_prior = [dict(row) for row in self.db.execute(
                "SELECT * FROM wallet_shadow_growth_events WHERE cohort=? AND wallet=? AND market_id=? ORDER BY at_ms,id",
                (capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT, self.wallet, int(market_id)))]
        self.time20_used_capital_usdt = sum(float(x["capital_cost_usdt"] or 0) for x in time20_prior)
        self.time20_unlocked_fraction = 0.0
        self.time20_maker_up_shares = sum(float(x["executed_shares"] or 0) for x in time20_prior if x["role"] == "MAKER" and x["side"] == "UP")
        self.time20_maker_down_shares = sum(float(x["executed_shares"] or 0) for x in time20_prior if x["role"] == "MAKER" and x["side"] == "DOWN")
        self.time20_taker_up_shares = sum(float(x["executed_shares"] or 0) for x in time20_prior if x["role"] == "TAKER" and x["side"] == "UP")
        self.time20_taker_down_shares = sum(float(x["executed_shares"] or 0) for x in time20_prior if x["role"] == "TAKER" and x["side"] == "DOWN")
        self.time20_last_taker_signature = self.time20_last_taker_side = None
        self.time20_taker_side_switches = 0; self.time20_taker_blocked = False; self.time20_recent_events = []
        for item in time20_prior:
            try: tp = base.json.loads(str(item.get("payload_json") or "{}"))
            except (TypeError, ValueError): tp = {}
            if isinstance(tp, dict):
                self.time20_unlocked_fraction = max(self.time20_unlocked_fraction, float(tp.get("unlockedFraction") or 0))
                if item["role"] == "TAKER":
                    self.time20_last_taker_signature = str(tp.get("takerSignature") or "") or self.time20_last_taker_signature
                    self.time20_last_taker_side = str(tp.get("lastTakerSide") or "") or self.time20_last_taker_side
                    self.time20_taker_side_switches = int(tp.get("takerSideSwitches") or self.time20_taker_side_switches)
                    self.time20_taker_blocked = bool(tp.get("takerBlocked"))
        for item in reversed(time20_prior[-40:]):
            try: tp = base.json.loads(str(item.get("payload_json") or "{}"))
            except (TypeError, ValueError): tp = {}
            self.time20_recent_events.append(tp if isinstance(tp, dict) else {})
        self._reset_batched_market(market_id, title)

    def _reset_batched_market(self, market_id: int, title: str | None) -> None:
        cohort = capital_s1.MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT
        with self.db_lock:
            if self.batched_excluded_market_id is None:
                self.batched_excluded_market_id = int(market_id)
                self.db.execute(
                    "UPDATE wallet_shadow_growth_meta SET excluded_market_id=? WHERE cohort=?",
                    (int(market_id), cohort),
                )
                self.db.commit()
            registered = self.db.execute(
                "SELECT planned_budget_usdt FROM wallet_shadow_growth_markets WHERE cohort=? AND wallet=? AND market_id=?",
                (cohort, self.wallet, int(market_id)),
            ).fetchone()
            spent = float(self.db.execute(
                "SELECT COALESCE(SUM(capital_cost_usdt),0) FROM wallet_shadow_growth_events WHERE cohort=? AND wallet=?",
                (cohort, self.wallet),
            ).fetchone()[0])
            payouts = float(self.db.execute(
                "SELECT COALESCE(SUM(payout_usdt),0) FROM wallet_shadow_growth_results WHERE cohort=? AND wallet=?",
                (cohort, self.wallet),
            ).fetchone()[0])
            available = max(0.0, capital_s1.INITIAL_WALLET_USDT - spent + payouts)
            self.batched_active_market = bool(registered) or int(market_id) != self.batched_excluded_market_id
            if self.batched_active_market and registered is None:
                budget = capital_s1.planned_market_budget(available)
                self.db.execute(
                    """INSERT INTO wallet_shadow_growth_markets(
                           cohort,wallet,market_id,title,started_at_ms,available_cash_at_start_usdt,planned_budget_usdt
                       ) VALUES (?,?,?,?,?,?,?)""",
                    (cohort, self.wallet, int(market_id), title, base._now_ms(), available, budget),
                )
                self.db.commit()
                self.batched_market_budget_usdt = budget
            else:
                self.batched_market_budget_usdt = float(registered["planned_budget_usdt"]) if registered else 0.0
            prior = [dict(row) for row in self.db.execute(
                "SELECT * FROM wallet_shadow_growth_events WHERE cohort=? AND wallet=? AND market_id=? ORDER BY at_ms,id",
                (cohort, self.wallet, int(market_id)),
            )]
        self.batched_maker_capital_usdt = sum(
            float(x["capital_cost_usdt"] or 0) for x in prior if x["role"] == "MAKER"
        )
        self.batched_taker_capital_usdt = sum(
            float(x["capital_cost_usdt"] or 0) for x in prior if x["role"] == "TAKER"
        )
        self.batched_maker_up_shares = sum(float(x["executed_shares"] or 0) for x in prior if x["role"] == "MAKER" and x["side"] == "UP")
        self.batched_maker_down_shares = sum(float(x["executed_shares"] or 0) for x in prior if x["role"] == "MAKER" and x["side"] == "DOWN")
        self.batched_taker_up_shares = sum(float(x["executed_shares"] or 0) for x in prior if x["role"] == "TAKER" and x["side"] == "UP")
        self.batched_taker_down_shares = sum(float(x["executed_shares"] or 0) for x in prior if x["role"] == "TAKER" and x["side"] == "DOWN")
        with self.db_lock:
            pending_rows = self.db.execute(
                "SELECT side,pending_shares FROM wallet_shadow_batched_pending WHERE cohort=? AND wallet=? AND market_id=?",
                (cohort, self.wallet, int(market_id)),
            ).fetchall()
        self.batched_pending_maker_shares = {"UP": 0.0, "DOWN": 0.0}
        for pending_row in pending_rows:
            if pending_row["side"] in {"UP", "DOWN"}:
                self.batched_pending_maker_shares[str(pending_row["side"])] = float(pending_row["pending_shares"] or 0)
        self.batched_last_taker_signature = self.batched_last_taker_side = None
        self.batched_taker_side_switches = 0
        self.batched_taker_blocked = False
        self.batched_core_candidate_side = None
        self.batched_core_stability_count = 0
        self.batched_recent_events = []
        for item in prior:
            try: payload = base.json.loads(str(item.get("payload_json") or "{}"))
            except (TypeError, ValueError): payload = {}
            if not isinstance(payload, dict):
                continue
            if item["role"] == "TAKER":
                self.batched_last_taker_signature = str(payload.get("takerSignature") or "") or self.batched_last_taker_signature
                self.batched_last_taker_side = str(payload.get("lastTakerSide") or "") or self.batched_last_taker_side
                self.batched_taker_side_switches = int(payload.get("takerSideSwitches") or self.batched_taker_side_switches)
                self.batched_taker_blocked = bool(payload.get("takerBlocked"))
        for item in reversed(prior[-40:]):
            try: payload = base.json.loads(str(item.get("payload_json") or "{}"))
            except (TypeError, ValueError): payload = {}
            self.batched_recent_events.append(payload if isinstance(payload, dict) else {})

    def _persist_batched_pending(self, side: str) -> None:
        if self.market_id is None or side not in {"UP", "DOWN"}:
            return
        with self.db_lock:
            self.db.execute(
                """INSERT INTO wallet_shadow_batched_pending(cohort,wallet,market_id,side,pending_shares,updated_at_ms)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(cohort,wallet,market_id,side) DO UPDATE SET
                       pending_shares=excluded.pending_shares,updated_at_ms=excluded.updated_at_ms""",
                (capital_s1.MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT, self.wallet, self.market_id,
                 side, self.batched_pending_maker_shares[side], base._now_ms()),
            )
            self.db.commit()

    def _emit_min1(
        self,
        *,
        event_type: str,
        role: str,
        side: str,
        price: float | None,
        desired_shares: float,
        source_event_id: str | None,
        reason: str,
        taker_signature: str | None = None,
    ) -> None:
        if not self.min1_active_market or self.market_id is None or side not in {"UP", "DOWN"}:
            return
        if source_event_id:
            with self.db_lock:
                exists = self.db.execute(
                    "SELECT 1 FROM wallet_shadow_min1_events WHERE cohort=? AND source_event_id=?",
                    (capital_s1.MIN1_EXEC_CAP100_COHORT, source_event_id),
                ).fetchone()
            if exists is not None:
                return
        px = base._finite(price)
        if event_type == "TAKER_BLOCKED":
            remaining = max(0.0, capital_s1.CAP100_USDT - self.min1_used_capital_usdt)
            decision = {
                "desiredShares": desired_shares,
                "minimumShares": capital_s1.MINIMUM_ORDER_NOTIONAL_USDT / px if px and px > 0 else None,
                "requestedShares": 0.0,
                "executedShares": 0.0,
                "principalCostUsdt": 0.0,
                "feeUsdt": 0.0,
                "capitalCostUsdt": 0.0,
                "remainingBeforeUsdt": remaining,
                "remainingAfterUsdt": remaining,
                "minimumUplift": False,
                "capTruncated": False,
                "blocked": True,
                "reason": "TAKER_CHURN_GUARD",
            }
        else:
            decision = capital_s1.min1_exec_cap100_fill(
                used_capital_usdt=self.min1_used_capital_usdt,
                role=role,
                price=px or 0.0,
                desired_shares=desired_shares,
            )
        now_ms = base._now_ms()
        identity = source_event_id or taker_signature or f"{event_type}:{side}:{self.min1_used_capital_usdt:.9f}"
        event_id = f"{capital_s1.MIN1_EXEC_CAP100_COHORT}:{self.market_id}:{now_ms}:{identity}"
        payload = {
            "id": event_id,
            "cohort": capital_s1.MIN1_EXEC_CAP100_COHORT,
            "marketId": self.market_id,
            "sourceEventId": source_event_id,
            "atMs": now_ms,
            "eventType": event_type,
            "role": role,
            "side": side,
            "price": px,
            **decision,
            "usedCapitalAfterUsdt": self.min1_used_capital_usdt + float(decision["capitalCostUsdt"]),
            "strategyReason": reason,
            "takerSignature": taker_signature,
            "lastTakerSide": self.min1_last_taker_side,
            "takerSideSwitches": self.min1_taker_side_switches,
            "takerBlocked": self.min1_taker_blocked,
        }
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_shadow_min1_events(
                       id,cohort,wallet,market_id,source_event_id,at_ms,event_type,role,side,price,
                       desired_shares,minimum_shares,requested_shares,executed_shares,principal_cost_usdt,
                       fee_usdt,capital_cost_usdt,minimum_uplift,cap_truncated,blocked,reason,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id, capital_s1.MIN1_EXEC_CAP100_COHORT, self.wallet, self.market_id,
                    source_event_id, now_ms, event_type, role, side, px,
                    decision["desiredShares"], decision["minimumShares"], decision["requestedShares"],
                    decision["executedShares"], decision["principalCostUsdt"], decision["feeUsdt"],
                    decision["capitalCostUsdt"], 1 if decision["minimumUplift"] else 0,
                    1 if decision["capTruncated"] else 0, 1 if decision["blocked"] else 0,
                    str(decision["reason"]), base.json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()
        executed = float(decision["executedShares"])
        if role == "MAKER" and side == "UP":
            self.min1_maker_up_shares += executed
        elif role == "MAKER":
            self.min1_maker_down_shares += executed
        elif side == "UP":
            self.min1_taker_up_shares += executed
        else:
            self.min1_taker_down_shares += executed
        self.min1_used_capital_usdt += float(decision["capitalCostUsdt"])
        self.min1_recent_events.insert(0, payload)
        self.min1_recent_events = self.min1_recent_events[:40]

    def _advance_min1(self, new_events: list[base.ShadowEvent], book: dict[str, Any], core: dict[str, Any]) -> None:
        if not self.min1_active_market:
            return
        for event in new_events:
            if event.event_type != "MAKER_FILL_PROXY":
                continue
            self._emit_min1(
                event_type="MAKER_FILL_PROXY",
                role="MAKER",
                side=event.side,
                price=event.price,
                desired_shares=1.0,
                source_event_id=event.id,
                reason="independent minimum-$1 Maker fill proxy",
            )
        maker_delta = self.min1_maker_up_shares - self.min1_maker_down_shares
        maker_side = "UP" if maker_delta > 1e-9 else "DOWN" if maker_delta < -1e-9 else None
        core_side = str(core.get("side") or "")
        if not maker_side or core_side not in {"UP", "DOWN"} or maker_side == core_side:
            return
        tier = max(1, math.ceil(abs(maker_delta)))
        signature = f"{core_side}:{maker_side}:{tier}"
        if signature == self.min1_last_taker_signature:
            return
        self.min1_last_taker_signature = signature
        switches = self.min1_taker_side_switches
        if self.min1_last_taker_side in {"UP", "DOWN"} and self.min1_last_taker_side != core_side:
            switches += 1
        self.min1_taker_side_switches = switches
        if switches > capital_s1.MAX_TAKER_SIDE_SWITCHES:
            self.min1_taker_blocked = True
        if self.min1_taker_blocked:
            self._emit_min1(
                event_type="TAKER_BLOCKED",
                role="TAKER",
                side=core_side,
                price=base._finite(book.get("upAsk" if core_side == "UP" else "downAsk")),
                desired_shares=0.0,
                source_event_id=None,
                reason="second Taker side switch; churn guard blocks correction",
                taker_signature=signature,
            )
            return
        self.min1_last_taker_side = core_side
        self._emit_min1(
            event_type="TAKER_INTENT",
            role="TAKER",
            side=core_side,
            price=base._finite(book.get("upAsk" if core_side == "UP" else "downAsk")),
            desired_shares=2.0,
            source_event_id=None,
            reason="minimum-order inventory Maker residual opposes independent core direction",
            taker_signature=signature,
        )

    def _emit_growth(self, *, event_type: str, role: str, side: str, price: float | None,
                     desired_shares: float, source_event_id: str | None, reason: str,
                     taker_signature: str | None = None) -> None:
        if not self.growth_active_market or self.market_id is None or side not in {"UP", "DOWN"}:
            return
        if source_event_id:
            with self.db_lock:
                if self.db.execute("SELECT 1 FROM wallet_shadow_growth_events WHERE cohort=? AND source_event_id=?",
                                   (capital_s1.MIN1_WALLET_GROWTH_COHORT, source_event_id)).fetchone():
                    return
        px = base._finite(price)
        decision = capital_s1.min1_exec_cap100_fill(
            used_capital_usdt=self.growth_used_capital_usdt, role=role, price=px or 0.0,
            desired_shares=desired_shares, capital_limit_usdt=self.growth_market_budget_usdt,
        )
        if event_type == "TAKER_BLOCKED":
            decision.update({"executedShares": 0.0, "principalCostUsdt": 0.0, "feeUsdt": 0.0,
                             "capitalCostUsdt": 0.0, "blocked": True, "reason": "TAKER_CHURN_GUARD"})
        now_ms = base._now_ms()
        identity = source_event_id or taker_signature or f"{event_type}:{side}:{self.growth_used_capital_usdt:.9f}"
        event_id = f"{capital_s1.MIN1_WALLET_GROWTH_COHORT}:{self.market_id}:{now_ms}:{identity}"
        payload = {
            "id": event_id, "cohort": capital_s1.MIN1_WALLET_GROWTH_COHORT,
            "marketId": self.market_id, "sourceEventId": source_event_id, "atMs": now_ms,
            "eventType": event_type, "role": role, "side": side, "price": px, **decision,
            "plannedMarketBudgetUsdt": self.growth_market_budget_usdt,
            "usedCapitalAfterUsdt": self.growth_used_capital_usdt + float(decision["capitalCostUsdt"]),
            "strategyReason": reason, "takerSignature": taker_signature,
            "lastTakerSide": self.growth_last_taker_side,
            "takerSideSwitches": self.growth_taker_side_switches, "takerBlocked": self.growth_taker_blocked,
        }
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_shadow_growth_events(
                       id,cohort,wallet,market_id,source_event_id,at_ms,event_type,role,side,price,
                       desired_shares,minimum_shares,requested_shares,executed_shares,principal_cost_usdt,
                       fee_usdt,capital_cost_usdt,minimum_uplift,budget_truncated,blocked,reason,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (event_id, capital_s1.MIN1_WALLET_GROWTH_COHORT, self.wallet, self.market_id,
                 source_event_id, now_ms, event_type, role, side, px, decision["desiredShares"],
                 decision["minimumShares"], decision["requestedShares"], decision["executedShares"],
                 decision["principalCostUsdt"], decision["feeUsdt"], decision["capitalCostUsdt"],
                 1 if decision["minimumUplift"] else 0, 1 if decision["capTruncated"] else 0,
                 1 if decision["blocked"] else 0, str(decision["reason"]),
                 base.json.dumps(payload, separators=(",", ":"), default=str)),
            )
            self.db.commit()
        executed = float(decision["executedShares"])
        if role == "MAKER" and side == "UP": self.growth_maker_up_shares += executed
        elif role == "MAKER": self.growth_maker_down_shares += executed
        elif side == "UP": self.growth_taker_up_shares += executed
        else: self.growth_taker_down_shares += executed
        self.growth_used_capital_usdt += float(decision["capitalCostUsdt"])
        self.growth_recent_events.insert(0, payload)
        self.growth_recent_events = self.growth_recent_events[:40]

    def _advance_growth(self, new_events: list[base.ShadowEvent], book: dict[str, Any], core: dict[str, Any]) -> None:
        if not self.growth_active_market or self.growth_market_budget_usdt < 1.0:
            return
        for event in new_events:
            if event.event_type == "MAKER_FILL_PROXY":
                self._emit_growth(event_type="MAKER_FILL_PROXY", role="MAKER", side=event.side,
                                  price=event.price, desired_shares=1.0, source_event_id=event.id,
                                  reason="wallet-growth minimum-$1 Maker fill proxy")
        delta = self.growth_maker_up_shares - self.growth_maker_down_shares
        maker_side = "UP" if delta > 1e-9 else "DOWN" if delta < -1e-9 else None
        core_side = str(core.get("side") or "")
        if not maker_side or core_side not in {"UP", "DOWN"} or maker_side == core_side:
            return
        signature = f"{core_side}:{maker_side}:{max(1, math.ceil(abs(delta)))}"
        if signature == self.growth_last_taker_signature: return
        self.growth_last_taker_signature = signature
        if self.growth_last_taker_side in {"UP", "DOWN"} and self.growth_last_taker_side != core_side:
            self.growth_taker_side_switches += 1
        if self.growth_taker_side_switches > capital_s1.MAX_TAKER_SIDE_SWITCHES:
            self.growth_taker_blocked = True
        if self.growth_taker_blocked:
            self._emit_growth(event_type="TAKER_BLOCKED", role="TAKER", side=core_side,
                              price=base._finite(book.get("upAsk" if core_side == "UP" else "downAsk")),
                              desired_shares=0, source_event_id=None, reason="second Taker side switch",
                              taker_signature=signature)
            return
        self.growth_last_taker_side = core_side
        self._emit_growth(event_type="TAKER_INTENT", role="TAKER", side=core_side,
                          price=base._finite(book.get("upAsk" if core_side == "UP" else "downAsk")),
                          desired_shares=2, source_event_id=None,
                          reason="wallet-growth inventory residual opposes core", taker_signature=signature)

    def _emit_time20(self, *, event_type: str, role: str, side: str, price: float | None,
                     desired_shares: float, source_event_id: str | None, reason: str,
                     taker_signature: str | None = None) -> None:
        if not self.time20_active_market or self.market_id is None or side not in {"UP", "DOWN"}:
            return
        if source_event_id:
            with self.db_lock:
                if self.db.execute("SELECT 1 FROM wallet_shadow_growth_events WHERE cohort=? AND source_event_id=?",
                                   (capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT, source_event_id)).fetchone():
                    return
        px = base._finite(price)
        unlocked_budget = self.time20_market_budget_usdt * self.time20_unlocked_fraction
        decision = capital_s1.min1_exec_cap100_fill(
            used_capital_usdt=self.time20_used_capital_usdt, role=role, price=px or 0,
            desired_shares=desired_shares, capital_limit_usdt=unlocked_budget)
        if event_type == "TAKER_BLOCKED":
            decision.update({"executedShares": 0.0, "principalCostUsdt": 0.0, "feeUsdt": 0.0,
                             "capitalCostUsdt": 0.0, "blocked": True, "reason": "TAKER_CHURN_GUARD"})
        now_ms = base._now_ms(); identity = source_event_id or taker_signature or f"{event_type}:{side}:{self.time20_used_capital_usdt:.9f}"
        event_id = f"{capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT}:{self.market_id}:{now_ms}:{identity}"
        payload = {
            "id": event_id, "cohort": capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT,
            "marketId": self.market_id, "sourceEventId": source_event_id, "atMs": now_ms,
            "eventType": event_type, "role": role, "side": side, "price": px, **decision,
            "plannedMarketBudgetUsdt": self.time20_market_budget_usdt,
            "unlockedFraction": self.time20_unlocked_fraction, "unlockedBudgetUsdt": unlocked_budget,
            "usedCapitalAfterUsdt": self.time20_used_capital_usdt+float(decision["capitalCostUsdt"]),
            "strategyReason": reason, "takerSignature": taker_signature,
            "lastTakerSide": self.time20_last_taker_side, "takerSideSwitches": self.time20_taker_side_switches,
            "takerBlocked": self.time20_taker_blocked,
        }
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_shadow_growth_events(
                       id,cohort,wallet,market_id,source_event_id,at_ms,event_type,role,side,price,
                       desired_shares,minimum_shares,requested_shares,executed_shares,principal_cost_usdt,
                       fee_usdt,capital_cost_usdt,minimum_uplift,budget_truncated,blocked,reason,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (event_id, capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT, self.wallet, self.market_id,
                 source_event_id, now_ms, event_type, role, side, px, decision["desiredShares"],
                 decision["minimumShares"], decision["requestedShares"], decision["executedShares"],
                 decision["principalCostUsdt"], decision["feeUsdt"], decision["capitalCostUsdt"],
                 1 if decision["minimumUplift"] else 0, 1 if decision["capTruncated"] else 0,
                 1 if decision["blocked"] else 0, str(decision["reason"]),
                 base.json.dumps(payload, separators=(",", ":"), default=str)))
            self.db.commit()
        executed = float(decision["executedShares"])
        if role == "MAKER" and side == "UP": self.time20_maker_up_shares += executed
        elif role == "MAKER": self.time20_maker_down_shares += executed
        elif side == "UP": self.time20_taker_up_shares += executed
        else: self.time20_taker_down_shares += executed
        self.time20_used_capital_usdt += float(decision["capitalCostUsdt"])
        self.time20_recent_events.insert(0, payload); self.time20_recent_events = self.time20_recent_events[:40]

    def _advance_time20(self, new_events: list[base.ShadowEvent], book: dict[str, Any], core: dict[str, Any]) -> None:
        if not self.time20_active_market or self.time20_market_budget_usdt < 1.0:
            return
        self.time20_unlocked_fraction = capital_s1.time20_unlocked_fraction(base._finite(book.get("secondsLeft")))
        for event in new_events:
            if event.event_type == "MAKER_FILL_PROXY":
                self._emit_time20(event_type="MAKER_FILL_PROXY", role="MAKER", side=event.side,
                                  price=event.price, desired_shares=1, source_event_id=event.id,
                                  reason="time-tranched minimum-$1 Maker fill proxy")
        delta = self.time20_maker_up_shares-self.time20_maker_down_shares
        maker_side = "UP" if delta > 1e-9 else "DOWN" if delta < -1e-9 else None
        core_side = str(core.get("side") or "")
        if not maker_side or core_side not in {"UP", "DOWN"} or maker_side == core_side: return
        signature = f"{core_side}:{maker_side}:{max(1,math.ceil(abs(delta)))}"
        if signature == self.time20_last_taker_signature: return
        self.time20_last_taker_signature = signature
        if self.time20_last_taker_side in {"UP", "DOWN"} and self.time20_last_taker_side != core_side:
            self.time20_taker_side_switches += 1
        if self.time20_taker_side_switches > capital_s1.MAX_TAKER_SIDE_SWITCHES: self.time20_taker_blocked = True
        if self.time20_taker_blocked:
            self._emit_time20(event_type="TAKER_BLOCKED", role="TAKER", side=core_side,
                              price=base._finite(book.get("upAsk" if core_side == "UP" else "downAsk")),
                              desired_shares=0, source_event_id=None, reason="second Taker side switch",
                              taker_signature=signature); return
        self.time20_last_taker_side = core_side
        self._emit_time20(event_type="TAKER_INTENT", role="TAKER", side=core_side,
                          price=base._finite(book.get("upAsk" if core_side == "UP" else "downAsk")),
                          desired_shares=2, source_event_id=None,
                          reason="time-tranched inventory residual opposes core", taker_signature=signature)

    def _emit_batched(
        self, *, event_type: str, role: str, side: str, price: float | None,
        desired_shares: float, source_event_id: str | None, reason: str,
        taker_signature: str | None = None,
    ) -> bool:
        if not self.batched_active_market or self.market_id is None or side not in {"UP", "DOWN"}:
            return False
        cohort = capital_s1.MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT
        px = base._finite(price)
        role_cap = self.batched_market_budget_usdt * (
            capital_s1.MAKER_BUDGET_FRACTION if role == "MAKER" else capital_s1.TAKER_BUDGET_FRACTION
        )
        role_used = self.batched_maker_capital_usdt if role == "MAKER" else self.batched_taker_capital_usdt
        decision = capital_s1.min1_exec_cap100_fill(
            used_capital_usdt=role_used,
            role=role,
            price=px or 0,
            desired_shares=desired_shares,
            capital_limit_usdt=role_cap,
        )
        if event_type == "TAKER_BLOCKED":
            decision.update({"executedShares": 0.0, "principalCostUsdt": 0.0, "feeUsdt": 0.0,
                             "capitalCostUsdt": 0.0, "blocked": True, "reason": "TAKER_CHURN_GUARD"})
        now_ms = base._now_ms()
        identity = source_event_id or taker_signature or f"{event_type}:{side}:{role_used:.9f}"
        event_id = f"{cohort}:{self.market_id}:{now_ms}:{identity}"
        payload = {
            "id": event_id, "cohort": cohort, "marketId": self.market_id,
            "sourceEventId": source_event_id, "atMs": now_ms, "eventType": event_type,
            "role": role, "side": side, "price": px, **decision,
            "plannedMarketBudgetUsdt": self.batched_market_budget_usdt,
            "makerBudgetUsdt": self.batched_market_budget_usdt * capital_s1.MAKER_BUDGET_FRACTION,
            "takerReserveUsdt": self.batched_market_budget_usdt * capital_s1.TAKER_BUDGET_FRACTION,
            "makerCapitalAfterUsdt": self.batched_maker_capital_usdt + (float(decision["capitalCostUsdt"]) if role == "MAKER" else 0),
            "takerCapitalAfterUsdt": self.batched_taker_capital_usdt + (float(decision["capitalCostUsdt"]) if role == "TAKER" else 0),
            "pendingMakerShares": dict(self.batched_pending_maker_shares),
            "strategyReason": reason, "takerSignature": taker_signature,
            "lastTakerSide": self.batched_last_taker_side,
            "takerSideSwitches": self.batched_taker_side_switches,
            "takerBlocked": self.batched_taker_blocked,
            "coreStabilityCount": self.batched_core_stability_count,
        }
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_shadow_growth_events(
                       id,cohort,wallet,market_id,source_event_id,at_ms,event_type,role,side,price,
                       desired_shares,minimum_shares,requested_shares,executed_shares,principal_cost_usdt,
                       fee_usdt,capital_cost_usdt,minimum_uplift,budget_truncated,blocked,reason,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (event_id, cohort, self.wallet, self.market_id, source_event_id, now_ms, event_type, role,
                 side, px, decision["desiredShares"], decision["minimumShares"], decision["requestedShares"],
                 decision["executedShares"], decision["principalCostUsdt"], decision["feeUsdt"],
                 decision["capitalCostUsdt"], 1 if decision["minimumUplift"] else 0,
                 1 if decision["capTruncated"] else 0, 1 if decision["blocked"] else 0,
                 str(decision["reason"]), base.json.dumps(payload, separators=(",", ":"), default=str)),
            )
            self.db.commit()
        executed = float(decision["executedShares"])
        capital = float(decision["capitalCostUsdt"])
        if role == "MAKER":
            self.batched_maker_capital_usdt += capital
            if side == "UP": self.batched_maker_up_shares += executed
            else: self.batched_maker_down_shares += executed
        else:
            self.batched_taker_capital_usdt += capital
            if side == "UP": self.batched_taker_up_shares += executed
            else: self.batched_taker_down_shares += executed
        self.batched_recent_events.insert(0, payload)
        self.batched_recent_events = self.batched_recent_events[:40]
        return executed > 0

    def _advance_batched(self, new_events: list[base.ShadowEvent], book: dict[str, Any], core: dict[str, Any]) -> None:
        if not self.batched_active_market or self.batched_market_budget_usdt < 1.0:
            return
        for event in new_events:
            if event.event_type != "MAKER_FILL_PROXY" or event.side not in {"UP", "DOWN"}:
                continue
            px = base._finite(event.price)
            if px is None or px <= 0:
                continue
            self.batched_pending_maker_shares[event.side] += capital_s1.RESEARCH_MAKER_UNIT_SHARES
            self._persist_batched_pending(event.side)
            pending = self.batched_pending_maker_shares[event.side]
            if pending * px + 1e-9 < capital_s1.MINIMUM_ORDER_NOTIONAL_USDT:
                continue
            self.batched_pending_maker_shares[event.side] = 0.0
            self._persist_batched_pending(event.side)
            filled = self._emit_batched(
                event_type="MAKER_BATCH_FILL", role="MAKER", side=event.side, price=px,
                desired_shares=pending, source_event_id=event.id,
                reason="same-side 18-to-1 scaled Maker signals accumulated above the $1 venue floor",
            )
            if not filled:
                self.batched_pending_maker_shares[event.side] = pending
                self._persist_batched_pending(event.side)

        core_side = str(core.get("side") or "")
        if core_side not in {"UP", "DOWN"}:
            self.batched_core_candidate_side = None
            self.batched_core_stability_count = 0
            return
        if core_side == self.batched_core_candidate_side:
            self.batched_core_stability_count += 1
        else:
            self.batched_core_candidate_side = core_side
            self.batched_core_stability_count = 1
        if self.batched_core_stability_count < capital_s1.CORE_STABILITY_CONFIRMATIONS:
            return

        up = self.batched_maker_up_shares + self.batched_taker_up_shares
        down = self.batched_maker_down_shares + self.batched_taker_down_shares
        opposing_residual = max(0.0, down - up) if core_side == "UP" else max(0.0, up - down)
        if opposing_residual <= 1e-9:
            return
        signature = f"{core_side}:{math.floor(opposing_residual * 1000) / 1000:.3f}"
        if signature == self.batched_last_taker_signature:
            return
        self.batched_last_taker_signature = signature
        if self.batched_last_taker_side in {"UP", "DOWN"} and self.batched_last_taker_side != core_side:
            self.batched_taker_side_switches += 1
        if self.batched_taker_side_switches > capital_s1.MAX_TAKER_SIDE_SWITCHES:
            self.batched_taker_blocked = True
        if self.batched_taker_blocked:
            self._emit_batched(
                event_type="TAKER_BLOCKED", role="TAKER", side=core_side,
                price=base._finite(book.get("upAsk" if core_side == "UP" else "downAsk")),
                desired_shares=0, source_event_id=None, reason="second Taker side switch",
                taker_signature=signature,
            )
            return
        self.batched_last_taker_side = core_side
        self._emit_batched(
            event_type="TAKER_RESIDUAL_CORRECTION", role="TAKER", side=core_side,
            price=base._finite(book.get("upAsk" if core_side == "UP" else "downAsk")),
            desired_shares=opposing_residual, source_event_id=None,
            reason="stable core direction corrects only the current opposing net residual without 18-share rounding",
            taker_signature=signature,
        )

    def _advance_cap100(self, s1_event: dict[str, Any]) -> None:
        if not self.cap100_active_market or self.market_id is None or s1_event.get("accepted") is not True:
            return
        source_id = str(s1_event.get("id") or "")
        price = base._finite(s1_event.get("price"))
        shares = base._finite(s1_event.get("scaledShares"))
        if not source_id or price is None or shares is None or shares <= 0:
            return
        with self.db_lock:
            exists = self.db.execute(
                "SELECT 1 FROM wallet_shadow_cap100_events WHERE cohort=? AND source_s1_event_id=?",
                (capital_s1.CAP100_COHORT, source_id),
            ).fetchone()
        if exists is not None:
            return
        decision = capital_s1.cap100_fill(
            used_capital_usdt=self.cap100_used_capital_usdt,
            role=str(s1_event.get("role") or ""),
            price=price,
            requested_shares=shares,
        )
        payload = {
            "id": f"{capital_s1.CAP100_COHORT}:{source_id}",
            "cohort": capital_s1.CAP100_COHORT,
            "marketId": self.market_id,
            "sourceS1EventId": source_id,
            "atMs": int(s1_event.get("atMs") or base._now_ms()),
            "role": s1_event.get("role"),
            "side": s1_event.get("side"),
            "price": price,
            **decision,
            "usedCapitalAfterUsdt": self.cap100_used_capital_usdt + float(decision["capitalCostUsdt"]),
        }
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_shadow_cap100_events(
                       id,cohort,wallet,market_id,source_s1_event_id,source_at_ms,role,side,price,
                       requested_shares,executed_shares,principal_cost_usdt,fee_usdt,capital_cost_usdt,
                       truncated,blocked,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    payload["id"], capital_s1.CAP100_COHORT, self.wallet, self.market_id, source_id,
                    payload["atMs"], payload["role"], payload["side"], price,
                    decision["requestedShares"], decision["executedShares"], decision["principalCostUsdt"],
                    decision["feeUsdt"], decision["capitalCostUsdt"], 1 if decision["truncated"] else 0,
                    1 if decision["blocked"] else 0,
                    base.json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()
        self.cap100_used_capital_usdt += float(decision["capitalCostUsdt"])
        self.cap100_truncated = self.cap100_truncated or bool(decision["truncated"])
        self.cap100_recent_events.insert(0, payload)
        self.cap100_recent_events = self.cap100_recent_events[:40]

    def _advance_capital_s1(self, new_events: list[base.ShadowEvent]) -> None:
        if not self.capital_s1_active_market or self.market_id is None:
            return
        for event in new_events:
            if event.event_type not in {"MAKER_FILL_PROXY", "TAKER_INTENT"}:
                continue
            with self.db_lock:
                exists = self.db.execute(
                    "SELECT payload_json FROM wallet_shadow_capital_s1_events WHERE cohort=? AND source_event_id=?",
                    (capital_s1.COHORT, event.id),
                ).fetchone()
            if exists is not None:
                try:
                    existing_payload = base.json.loads(str(exists["payload_json"] or "{}"))
                except (TypeError, ValueError):
                    existing_payload = {}
                if isinstance(existing_payload, dict):
                    self._advance_cap100(existing_payload)
                continue
            next_state, decision = capital_s1.reduce_source_event(
                self.capital_s1_state,
                event_type=event.event_type,
                side=event.side,
                original_shares=event.shares,
            )
            self.capital_s1_state = next_state
            accepted = bool(decision["accepted"]) and event.price is not None
            reason = str(decision["reason"]) if event.price is not None else "INVALID_SOURCE_PRICE"
            payload = {
                "id": f"{capital_s1.COHORT}:{event.id}",
                "cohort": capital_s1.COHORT,
                "marketId": self.market_id,
                "sourceEventId": event.id,
                "atMs": event.at_ms,
                "eventType": event.event_type,
                "role": event.role,
                "side": event.side,
                "price": event.price,
                "originalShares": event.shares,
                "scaledShares": float(decision["scaledShares"]) if accepted else 0.0,
                "accepted": accepted,
                "reason": reason,
                "takerSideSwitches": next_state.taker_side_switches,
                "takerBlocked": next_state.taker_blocked,
            }
            with self.db_lock:
                self.db.execute(
                    """INSERT OR IGNORE INTO wallet_shadow_capital_s1_events(
                           id,cohort,wallet,market_id,source_event_id,source_at_ms,event_type,role,
                           side,price,original_shares,scaled_shares,accepted,reason,payload_json
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        payload["id"], capital_s1.COHORT, self.wallet, self.market_id, event.id,
                        event.at_ms, event.event_type, event.role, event.side, event.price,
                        event.shares, payload["scaledShares"], 1 if accepted else 0, reason,
                        base.json.dumps(payload, separators=(",", ":"), default=str),
                    ),
                )
                self.db.commit()
            self.capital_s1_recent_events.insert(0, payload)
            self.capital_s1_recent_events = self.capital_s1_recent_events[:40]
            self._advance_cap100(payload)

    def _advance_shadow(self, book: dict[str, Any], core: dict[str, Any]) -> None:
        before = len(self.shadow_events)
        super()._advance_shadow(book, core)
        if not LEGACY_CAPITAL_COHORTS_ENABLED:
            return
        new_events = self.shadow_events[before:]
        self._advance_capital_s1(new_events)
        self._advance_min1(new_events, book, core)
        self._advance_growth(new_events, book, core)
        self._advance_time20(new_events, book, core)
        self._advance_batched(new_events, book, core)

    def stop(self) -> None:
        if self.simulation_db is not None:
            self.simulation_db.close()
            self.simulation_db = None
        v4_1.WalletShadowObserver.stop(self)

    def _current_spot_snapshot(self, bucket: int) -> dict[str, Any] | None:
        if self.simulation_db is None:
            return None
        return v4_2.WalletShadowObserver._current_spot_snapshot(self, bucket)

    def _advance_spot_strike(self, book: dict[str, Any]) -> None:
        if self.simulation_db is None:
            if self.forward_decision is not None:
                return
            seconds_left = base._finite(book.get("secondsLeft"))
            if seconds_left is not None and 0 < seconds_left <= v4_2.DECISION_SECONDS:
                self._block_forward(
                    "SIMULATION_DB_UNAVAILABLE",
                    seconds_left,
                    simulationDbError=self.simulation_db_error,
                )
            return
        super()._advance_spot_strike(book)

    def _persist_target_leg(self, leg: dict[str, Any], raw: dict[str, Any]) -> None:
        super()._persist_target_leg(leg, raw)
        observed_at_ms = base._now_ms()
        event_ms = int(leg["eventMs"])
        market = raw.get("market") if isinstance(raw.get("market"), dict) else {}
        end_ms = base._iso_ms(market.get("boostEndsAt"))
        scheduled_seconds_left = (end_ms - event_ms) / 1000.0 if end_ms is not None else None
        book = dict(getattr(self, "last_predict_book", {}) or {})
        core = dict(getattr(self, "last_core", {}) or {})
        observed_seconds_left = base._finite(book.get("secondsLeft"))
        event_delay_ms = max(0, observed_at_ms - event_ms)
        causal_context = event_delay_ms <= 5_000
        context = {
            "provenance": "LIVE_OBSERVER_CONTEXT" if causal_context else "LATE_OBSERVER_CONTEXT",
            "eventDelayMs": event_delay_ms,
            "scheduledSecondsLeft": scheduled_seconds_left,
            "observedSecondsLeft": observed_seconds_left,
            "book": book,
            "core": core,
        }
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_shadow_target_event_context(
                       leg_id,wallet,market_id,event_ms,observed_at_ms,event_delay_ms,
                       scheduled_seconds_left,observed_seconds_left,up_bid,up_ask,down_bid,down_ask,
                       core_side,core_source,causal_context,context_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    leg["legId"], self.wallet, int(leg["marketId"]), event_ms, observed_at_ms,
                    event_delay_ms, scheduled_seconds_left, observed_seconds_left,
                    base._finite(book.get("upBid")), base._finite(book.get("upAsk")),
                    base._finite(book.get("downBid")), base._finite(book.get("downAsk")),
                    core.get("side"), core.get("source"), 1 if causal_context else 0,
                    base.json.dumps(context, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()

    def _target_event_rows(self, market_id: int) -> list[dict[str, Any]]:
        with self.db_lock:
            return [dict(row) for row in self.db.execute(
                """SELECT leg_id,role,side,quote_type,order_hash,event_ms,price,shares,raw_json
                     FROM wallet_shadow_target_events
                    WHERE wallet=? AND market_id=? ORDER BY event_ms,leg_id""",
                (self.wallet, int(market_id)),
            )]

    def _store_target_market_result(
        self,
        market_id: int,
        market: dict[str, Any],
        winner: str,
        *,
        resolved_at_ms: int | None = None,
        historical_reconstruction: bool = False,
    ) -> None:
        events = self._target_event_rows(market_id)
        if not events:
            return
        result = target_accounting.account_target_market(events, winner=winner, wallet=self.wallet)
        title = str(market.get("title") or market.get("question") or "") or None
        with self.db_lock:
            self.db.execute(
                """INSERT INTO wallet_shadow_target_market_results(
                       wallet,market_id,title,winner,resolved_at_ms,historical_reconstruction,
                       accounting_mode,status,event_count,maker_event_count,taker_event_count,
                       buy_notional_usdt,sell_proceeds_usdt,collateral_fees_usdt,payout_usdt,
                       net_pnl_usdt,net_roi,maker_notional_usdt,maker_pnl_usdt,
                       taker_notional_usdt,taker_pnl_usdt,gross_up_shares,gross_down_shares,
                       share_fees_up,share_fees_down,net_up_shares,net_down_shares,
                       share_conviction_side,capital_conviction_side,
                       share_direction_correct,capital_direction_correct
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(wallet,market_id) DO UPDATE SET
                       title=excluded.title,winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,
                       historical_reconstruction=excluded.historical_reconstruction,
                       accounting_mode=excluded.accounting_mode,status=excluded.status,
                       event_count=excluded.event_count,maker_event_count=excluded.maker_event_count,
                       taker_event_count=excluded.taker_event_count,buy_notional_usdt=excluded.buy_notional_usdt,
                       sell_proceeds_usdt=excluded.sell_proceeds_usdt,
                       collateral_fees_usdt=excluded.collateral_fees_usdt,payout_usdt=excluded.payout_usdt,
                       net_pnl_usdt=excluded.net_pnl_usdt,net_roi=excluded.net_roi,
                       maker_notional_usdt=excluded.maker_notional_usdt,maker_pnl_usdt=excluded.maker_pnl_usdt,
                       taker_notional_usdt=excluded.taker_notional_usdt,taker_pnl_usdt=excluded.taker_pnl_usdt,
                       gross_up_shares=excluded.gross_up_shares,gross_down_shares=excluded.gross_down_shares,
                       share_fees_up=excluded.share_fees_up,share_fees_down=excluded.share_fees_down,
                       net_up_shares=excluded.net_up_shares,net_down_shares=excluded.net_down_shares,
                       share_conviction_side=excluded.share_conviction_side,
                       capital_conviction_side=excluded.capital_conviction_side,
                       share_direction_correct=excluded.share_direction_correct,
                       capital_direction_correct=excluded.capital_direction_correct""",
                (
                    self.wallet, int(market_id), title, winner, int(resolved_at_ms or base._now_ms()),
                    1 if historical_reconstruction else 0, result["accountingMode"], result["status"],
                    result["eventCount"], result["maker"]["events"], result["taker"]["events"],
                    result["buyNotionalUsdt"], result["sellProceedsUsdt"], result["collateralFeesUsdt"],
                    result["payoutUsdt"], result["netPnlUsdt"], result["netRoi"],
                    result["maker"]["notionalUsdt"], result["maker"]["netPnlUsdt"],
                    result["taker"]["notionalUsdt"], result["taker"]["netPnlUsdt"],
                    result["up"]["grossBought"], result["down"]["grossBought"],
                    result["up"]["shareFees"], result["down"]["shareFees"],
                    result["up"]["netShares"], result["down"]["netShares"],
                    result["shareConvictionSide"], result["capitalConvictionSide"],
                    None if result["shareDirectionCorrect"] is None else int(result["shareDirectionCorrect"]),
                    None if result["capitalDirectionCorrect"] is None else int(result["capitalDirectionCorrect"]),
                ),
            )
            self.db.commit()

    def _backfill_target_market_results(self) -> None:
        with self.db_lock:
            rows = [dict(row) for row in self.db.execute(
                """SELECT s.market_id,s.title,s.winner,s.resolved_at_ms
                     FROM wallet_shadow_market_results s
                     JOIN (SELECT DISTINCT market_id FROM wallet_shadow_target_events WHERE wallet=?) e
                       ON e.market_id=s.market_id
                     LEFT JOIN wallet_shadow_target_market_results t
                       ON t.wallet=? AND t.market_id=s.market_id
                    WHERE s.wallet=? AND t.market_id IS NULL
                    ORDER BY s.resolved_at_ms""",
                (self.wallet, self.wallet, self.wallet),
            )]
        for row in rows:
            self._store_target_market_result(
                int(row["market_id"]), {"title": row.get("title")}, str(row["winner"]),
                resolved_at_ms=int(row["resolved_at_ms"]), historical_reconstruction=True,
            )

    def _target_performance_snapshot(self) -> dict[str, Any]:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            summary = self.db.execute(
                """SELECT COUNT(*) settled_markets,
                          SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END) wins,
                          SUM(CASE WHEN status='LOSS' THEN 1 ELSE 0 END) losses,
                          SUM(CASE WHEN status='FLAT' THEN 1 ELSE 0 END) flats,
                          COALESCE(SUM(buy_notional_usdt),0) buy_notional,
                          COALESCE(SUM(sell_proceeds_usdt),0) sell_proceeds,
                          COALESCE(SUM(payout_usdt),0) payout,
                          COALESCE(SUM(net_pnl_usdt),0) pnl,
                          COALESCE(SUM(maker_pnl_usdt),0) maker_pnl,
                          COALESCE(SUM(taker_pnl_usdt),0) taker_pnl,
                          SUM(CASE WHEN share_direction_correct=1 THEN 1 ELSE 0 END) share_correct,
                          SUM(CASE WHEN share_direction_correct IS NOT NULL THEN 1 ELSE 0 END) share_comparable,
                          SUM(CASE WHEN capital_direction_correct=1 THEN 1 ELSE 0 END) capital_correct,
                          SUM(CASE WHEN capital_direction_correct IS NOT NULL THEN 1 ELSE 0 END) capital_comparable,
                          SUM(historical_reconstruction) reconstructed
                     FROM wallet_shadow_target_market_results
                    WHERE wallet=? AND resolved_at_ms>=?""",
                (self.wallet, cutoff),
            ).fetchone()
            recent = [dict(row) for row in self.db.execute(
                """SELECT market_id,title,winner,resolved_at_ms,historical_reconstruction,status,event_count,
                          buy_notional_usdt,payout_usdt,net_pnl_usdt,net_roi,
                          maker_pnl_usdt,taker_pnl_usdt,net_up_shares,net_down_shares,
                          share_conviction_side,capital_conviction_side,
                          share_direction_correct,capital_direction_correct
                     FROM wallet_shadow_target_market_results
                    WHERE wallet=? AND resolved_at_ms>=?
                    ORDER BY resolved_at_ms DESC LIMIT 30""",
                (self.wallet, cutoff),
            )]
            context = self.db.execute(
                """SELECT COUNT(*) total,SUM(causal_context) causal,
                          SUM(CASE WHEN scheduled_seconds_left IS NOT NULL THEN 1 ELSE 0 END) scheduled
                     FROM wallet_shadow_target_event_context WHERE wallet=? AND event_ms>=?""",
                (self.wallet, cutoff),
            ).fetchone()
        data = dict(summary or {})
        settled = int(data.get("settled_markets") or 0)
        wins = int(data.get("wins") or 0)
        buy_notional = float(data.get("buy_notional") or 0.0)
        pnl = float(data.get("pnl") or 0.0)
        share_comparable = int(data.get("share_comparable") or 0)
        capital_comparable = int(data.get("capital_comparable") or 0)
        return {
            "windowDays": self.retention_days,
            "settledMarkets": settled,
            "wins": wins,
            "losses": int(data.get("losses") or 0),
            "flats": int(data.get("flats") or 0),
            "winRate": wins / settled if settled else None,
            "buyNotionalUsdt": buy_notional,
            "sellProceedsUsdt": float(data.get("sell_proceeds") or 0.0),
            "payoutUsdt": float(data.get("payout") or 0.0),
            "netPnlUsdt": pnl,
            "netRoi": pnl / buy_notional if buy_notional else None,
            "makerNetPnlUsdt": float(data.get("maker_pnl") or 0.0),
            "takerNetPnlUsdt": float(data.get("taker_pnl") or 0.0),
            "historicallyReconstructedMarkets": int(data.get("reconstructed") or 0),
            "targetOfficialAccuracy": {
                "shareComparable": share_comparable,
                "shareCorrect": int(data.get("share_correct") or 0),
                "shareAccuracy": int(data.get("share_correct") or 0) / share_comparable if share_comparable else None,
                "capitalComparable": capital_comparable,
                "capitalCorrect": int(data.get("capital_correct") or 0),
                "capitalAccuracy": int(data.get("capital_correct") or 0) / capital_comparable if capital_comparable else None,
            },
            "eventContextCoverage": {
                "captured": int(context["total"] or 0),
                "causal": int(context["causal"] or 0),
                "scheduledTiming": int(context["scheduled"] or 0),
                "causalDefinition": "observer delay <= 5000 ms",
            },
            "recentMarkets": recent,
            "accountingCaveat": "Official payout plus retained target match cash flows. Share fees are deducted from inventory; historical reconstruction is target accounting only and never joins forward strategy cohorts.",
        }

    def _current_target_execution_snapshot(self, target: dict[str, Any]) -> dict[str, Any]:
        if self.market_id is None:
            return {"marketId": None, "parents": [], "changePoints": {}}
        with self.db_lock:
            contexts = [dict(row) for row in self.db.execute(
                """SELECT e.role,e.order_hash,MIN(e.event_ms) first_event_ms,
                          MAX(c.scheduled_seconds_left) scheduled_seconds_left,
                          MAX(c.observed_seconds_left) observed_seconds_left,
                          MIN(c.event_delay_ms) event_delay_ms,MAX(c.causal_context) causal_context
                     FROM wallet_shadow_target_events e
                     LEFT JOIN wallet_shadow_target_event_context c ON c.leg_id=e.leg_id
                    WHERE e.wallet=? AND e.market_id=?
                    GROUP BY e.role,e.order_hash""",
                (self.wallet, int(self.market_id)),
            )]
            raw_rows = [dict(row) for row in self.db.execute(
                """SELECT role,side,price,shares,event_ms FROM wallet_shadow_target_events
                    WHERE wallet=? AND market_id=? ORDER BY event_ms,leg_id""",
                (self.wallet, int(self.market_id)),
            )]
        context_by_parent = {
            f"{row['role']}:{row['order_hash']}": row for row in contexts if row.get("order_hash")
        }
        final = _residual(raw_rows)
        enriched = []
        for parent in target.get("events", []):
            item = dict(parent)
            context = context_by_parent.get(str(item.get("id")), {})
            item.update({
                "scheduledSecondsLeft": context.get("scheduled_seconds_left"),
                "observedSecondsLeft": context.get("observed_seconds_left"),
                "eventDelayMs": context.get("event_delay_ms"),
                "causalContext": bool(context.get("causal_context")),
            })
            price = base._finite(item.get("averagePrice")) or 0.0
            if price <= 0.12 and item.get("side") != final.get("capitalSide"):
                reason = "LOW_PRICE_INSURANCE_OR_TAIL_HEDGE"
            elif item.get("role") == "MAKER" and abs(float(item.get("shares") or 0) / 18.0 - round(float(item.get("shares") or 0) / 18.0)) < 1e-6:
                reason = "MAKER_18_SHARE_BASE_UNIT_ACCUMULATION"
            elif item.get("role") == "MAKER":
                reason = "MAKER_PASSIVE_ACCUMULATION"
            elif item.get("side") == final.get("capitalSide"):
                reason = "TAKER_DIRECTIONAL_ADD_OR_RESIDUAL_CORRECTION"
            else:
                reason = "TAKER_REBALANCE_OR_INSURANCE"
            item["inferredReason"] = reason
            item["reasonConfidence"] = "STRUCTURAL_EVIDENCE_ONLY"
            enriched.append(item)
        chronological = sorted(enriched, key=lambda item: int(item.get("firstEventMs") or 0))
        first_maker = next((item for item in chronological if item.get("role") == "MAKER"), None)
        first_taker = next((item for item in chronological if item.get("role") == "TAKER"), None)
        return {
            "marketId": self.market_id,
            "finalShareConvictionSide": final.get("side"),
            "finalCapitalConvictionSide": final.get("capitalSide"),
            "changePoints": {
                "firstMaker": {"eventMs": first_maker.get("firstEventMs"), "scheduledSecondsLeft": first_maker.get("scheduledSecondsLeft")} if first_maker else None,
                "firstTaker": {"eventMs": first_taker.get("firstEventMs"), "scheduledSecondsLeft": first_taker.get("scheduledSecondsLeft")} if first_taker else None,
            },
            "parents": enriched,
            "warning": "Reasons are observable-structure hypotheses, not proof of the target's private implementation.",
        }

    def _seed_pending_settlements(self) -> None:
        super()._seed_pending_settlements()
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            target_rows = self.db.execute(
                """SELECT DISTINCT e.market_id
                     FROM wallet_shadow_target_events e
                     LEFT JOIN wallet_shadow_target_market_results r
                       ON r.wallet=e.wallet AND r.market_id=e.market_id
                    WHERE e.wallet=? AND e.event_ms>=? AND r.market_id IS NULL""",
                (self.wallet, cutoff),
            ).fetchall()
            rows = self.db.execute(
                """
                SELECT e.market_id
                  FROM wallet_spot_strike_forward_events e
                  LEFT JOIN wallet_spot_strike_forward_results r
                    ON r.cohort=e.cohort AND r.market_id=e.market_id
                 WHERE e.cohort=? AND e.decision_at_ms>=? AND r.market_id IS NULL
                """,
                (v4_2.COHORT, cutoff),
            ).fetchall()
            capital_rows = self.db.execute(
                """
                SELECT m.market_id
                  FROM wallet_shadow_capital_s1_markets m
                  LEFT JOIN wallet_shadow_capital_s1_results r
                    ON r.cohort=m.cohort AND r.wallet=m.wallet AND r.market_id=m.market_id
                 WHERE m.cohort=? AND m.wallet=? AND m.started_at_ms>=? AND r.market_id IS NULL
                """,
                (capital_s1.COHORT, self.wallet, cutoff),
            ).fetchall()
            cap100_rows = self.db.execute(
                """SELECT m.market_id FROM wallet_shadow_cap100_markets m
                     LEFT JOIN wallet_shadow_cap100_results r
                       ON r.cohort=m.cohort AND r.wallet=m.wallet AND r.market_id=m.market_id
                    WHERE m.cohort=? AND m.wallet=? AND m.started_at_ms>=? AND r.market_id IS NULL""",
                (capital_s1.CAP100_COHORT, self.wallet, cutoff),
            ).fetchall()
            min1_rows = self.db.execute(
                """SELECT m.market_id FROM wallet_shadow_min1_markets m
                     LEFT JOIN wallet_shadow_min1_results r
                       ON r.cohort=m.cohort AND r.wallet=m.wallet AND r.market_id=m.market_id
                    WHERE m.cohort=? AND m.wallet=? AND m.started_at_ms>=? AND r.market_id IS NULL""",
                (capital_s1.MIN1_EXEC_CAP100_COHORT, self.wallet, cutoff),
            ).fetchall()
            growth_rows = self.db.execute(
                """SELECT m.market_id FROM wallet_shadow_growth_markets m
                     LEFT JOIN wallet_shadow_growth_results r
                       ON r.cohort=m.cohort AND r.wallet=m.wallet AND r.market_id=m.market_id
                    WHERE m.cohort=? AND m.wallet=? AND m.started_at_ms>=? AND r.market_id IS NULL""",
                (capital_s1.MIN1_WALLET_GROWTH_COHORT, self.wallet, cutoff),
            ).fetchall()
            time20_rows = self.db.execute(
                """SELECT m.market_id FROM wallet_shadow_growth_markets m
                     LEFT JOIN wallet_shadow_growth_results r
                       ON r.cohort=m.cohort AND r.wallet=m.wallet AND r.market_id=m.market_id
                    WHERE m.cohort=? AND m.wallet=? AND m.started_at_ms>=? AND r.market_id IS NULL""",
                (capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT, self.wallet, cutoff),
            ).fetchall()
            batched_rows = self.db.execute(
                """SELECT m.market_id FROM wallet_shadow_growth_markets m
                     LEFT JOIN wallet_shadow_growth_results r
                       ON r.cohort=m.cohort AND r.wallet=m.wallet AND r.market_id=m.market_id
                    WHERE m.cohort=? AND m.wallet=? AND m.started_at_ms>=? AND r.market_id IS NULL""",
                (capital_s1.MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT, self.wallet, cutoff),
            ).fetchall()
        self.pending_settlement_ids.update(int(row[0]) for row in target_rows)
        self.pending_settlement_ids.update(int(row[0]) for row in rows)
        self.pending_settlement_ids.update(int(row[0]) for row in capital_rows)
        self.pending_settlement_ids.update(int(row[0]) for row in cap100_rows)
        self.pending_settlement_ids.update(int(row[0]) for row in min1_rows)
        self.pending_settlement_ids.update(int(row[0]) for row in growth_rows)
        self.pending_settlement_ids.update(int(row[0]) for row in time20_rows)
        self.pending_settlement_ids.update(int(row[0]) for row in batched_rows)

    def _store_market_result(self, market_id: int, market: dict[str, Any], winner: str) -> None:
        super()._store_market_result(market_id, market, winner)
        self._store_target_market_result(market_id, market, winner, historical_reconstruction=False)
        with self.db_lock:
            registered = self.db.execute(
                "SELECT title FROM wallet_shadow_capital_s1_markets WHERE cohort=? AND wallet=? AND market_id=?",
                (capital_s1.COHORT, self.wallet, int(market_id)),
            ).fetchone()
            if registered is None:
                return
            events = [dict(row) for row in self.db.execute(
                """SELECT role,side,price,scaled_shares,accepted
                     FROM wallet_shadow_capital_s1_events
                    WHERE cohort=? AND wallet=? AND market_id=? ORDER BY source_at_ms,id""",
                (capital_s1.COHORT, self.wallet, int(market_id)),
            )]
        result = capital_s1.market_economics(events, winner)
        title = str(market.get("title") or market.get("question") or registered["title"] or "") or None
        with self.db_lock:
            self.db.execute(
                """INSERT INTO wallet_shadow_capital_s1_results(
                       cohort,wallet,market_id,title,winner,resolved_at_ms,traded,status,fill_count,
                       cost_usdt,payout_usdt,gross_pnl_usdt,gross_roi,maker_cost_usdt,taker_cost_usdt,
                       taker_fee_usdt,fee_net_pnl_usdt,fee_net_roi,
                       stress_1tick_cost_usdt,stress_1tick_pnl_usdt,stress_1tick_roi,
                       stress_2tick_cost_usdt,stress_2tick_pnl_usdt,stress_2tick_roi
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(cohort,wallet,market_id) DO UPDATE SET
                       title=excluded.title,winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,
                       traded=excluded.traded,status=excluded.status,fill_count=excluded.fill_count,
                       cost_usdt=excluded.cost_usdt,payout_usdt=excluded.payout_usdt,
                       gross_pnl_usdt=excluded.gross_pnl_usdt,gross_roi=excluded.gross_roi,
                       maker_cost_usdt=excluded.maker_cost_usdt,taker_cost_usdt=excluded.taker_cost_usdt,
                       taker_fee_usdt=excluded.taker_fee_usdt,fee_net_pnl_usdt=excluded.fee_net_pnl_usdt,
                       fee_net_roi=excluded.fee_net_roi,stress_1tick_cost_usdt=excluded.stress_1tick_cost_usdt,
                       stress_1tick_pnl_usdt=excluded.stress_1tick_pnl_usdt,stress_1tick_roi=excluded.stress_1tick_roi,
                       stress_2tick_cost_usdt=excluded.stress_2tick_cost_usdt,
                       stress_2tick_pnl_usdt=excluded.stress_2tick_pnl_usdt,stress_2tick_roi=excluded.stress_2tick_roi""",
                (
                    capital_s1.COHORT, self.wallet, int(market_id), title, winner, base._now_ms(),
                    1 if result["traded"] else 0, result["status"], result["fillCount"],
                    result["costUsdt"], result["payoutUsdt"], result["grossPnlUsdt"], result["grossRoi"],
                    result["makerCostUsdt"], result["takerCostUsdt"], result["takerFeeUsdt"],
                    result["feeNetPnlUsdt"], result["feeNetRoi"],
                    result["stress1CostUsdt"], result["stress1PnlUsdt"], result["stress1Roi"],
                    result["stress2CostUsdt"], result["stress2PnlUsdt"], result["stress2Roi"],
                ),
            )
            self.db.commit()

        with self.db_lock:
            growth_market = self.db.execute(
                "SELECT title,planned_budget_usdt FROM wallet_shadow_growth_markets WHERE cohort=? AND wallet=? AND market_id=?",
                (capital_s1.MIN1_WALLET_GROWTH_COHORT, self.wallet, int(market_id)),
            ).fetchone()
            growth_events = [dict(row) for row in self.db.execute(
                """SELECT side,executed_shares,capital_cost_usdt,minimum_uplift,blocked
                     FROM wallet_shadow_growth_events WHERE cohort=? AND wallet=? AND market_id=? ORDER BY at_ms,id""",
                (capital_s1.MIN1_WALLET_GROWTH_COHORT, self.wallet, int(market_id)),
            )] if growth_market else []
        if growth_market is not None:
            fills = [x for x in growth_events if float(x["executed_shares"] or 0) > 0]
            cost = sum(float(x["capital_cost_usdt"] or 0) for x in fills)
            payout = sum(float(x["executed_shares"] or 0) for x in fills if x["side"] == winner)
            pnl = payout - cost
            status = "NO_TRADE" if not fills else "WIN" if pnl > 1e-9 else "LOSS" if pnl < -1e-9 else "FLAT"
            blocked = sum(1 for x in growth_events if bool(x["blocked"]))
            budget = float(growth_market["planned_budget_usdt"])
            with self.db_lock:
                self.db.execute(
                    """INSERT INTO wallet_shadow_growth_results(
                           cohort,wallet,market_id,title,winner,resolved_at_ms,traded,status,fill_count,
                           planned_budget_usdt,capital_cost_usdt,payout_usdt,net_pnl_usdt,net_roi,
                           minimum_uplift_events,blocked_events,budget_hit
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(cohort,wallet,market_id) DO UPDATE SET
                           winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,traded=excluded.traded,
                           status=excluded.status,fill_count=excluded.fill_count,capital_cost_usdt=excluded.capital_cost_usdt,
                           payout_usdt=excluded.payout_usdt,net_pnl_usdt=excluded.net_pnl_usdt,net_roi=excluded.net_roi,
                           minimum_uplift_events=excluded.minimum_uplift_events,blocked_events=excluded.blocked_events,
                           budget_hit=excluded.budget_hit""",
                    (capital_s1.MIN1_WALLET_GROWTH_COHORT, self.wallet, int(market_id),
                     str(market.get("title") or growth_market["title"] or "") or None, winner, base._now_ms(),
                     1 if fills else 0, status, len(fills), budget, cost, payout, pnl,
                     pnl / cost if cost else None, sum(1 for x in growth_events if bool(x["minimum_uplift"])),
                     blocked, 1 if blocked or cost >= budget - 1e-6 else 0),
                )
                self.db.commit()

        with self.db_lock:
            time20_market = self.db.execute(
                "SELECT title,planned_budget_usdt FROM wallet_shadow_growth_markets WHERE cohort=? AND wallet=? AND market_id=?",
                (capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT, self.wallet, int(market_id))).fetchone()
            time20_events = [dict(row) for row in self.db.execute(
                """SELECT side,executed_shares,capital_cost_usdt,minimum_uplift,blocked
                     FROM wallet_shadow_growth_events WHERE cohort=? AND wallet=? AND market_id=? ORDER BY at_ms,id""",
                (capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT, self.wallet, int(market_id)))] if time20_market else []
        if time20_market is not None:
            fills = [x for x in time20_events if float(x["executed_shares"] or 0) > 0]
            cost = sum(float(x["capital_cost_usdt"] or 0) for x in fills)
            payout = sum(float(x["executed_shares"] or 0) for x in fills if x["side"] == winner)
            pnl = payout-cost; blocked = sum(1 for x in time20_events if bool(x["blocked"]))
            status = "NO_TRADE" if not fills else "WIN" if pnl > 1e-9 else "LOSS" if pnl < -1e-9 else "FLAT"
            budget = float(time20_market["planned_budget_usdt"])
            with self.db_lock:
                self.db.execute(
                    """INSERT INTO wallet_shadow_growth_results(
                           cohort,wallet,market_id,title,winner,resolved_at_ms,traded,status,fill_count,
                           planned_budget_usdt,capital_cost_usdt,payout_usdt,net_pnl_usdt,net_roi,
                           minimum_uplift_events,blocked_events,budget_hit
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(cohort,wallet,market_id) DO UPDATE SET
                           winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,traded=excluded.traded,
                           status=excluded.status,fill_count=excluded.fill_count,capital_cost_usdt=excluded.capital_cost_usdt,
                           payout_usdt=excluded.payout_usdt,net_pnl_usdt=excluded.net_pnl_usdt,net_roi=excluded.net_roi,
                           minimum_uplift_events=excluded.minimum_uplift_events,blocked_events=excluded.blocked_events,
                           budget_hit=excluded.budget_hit""",
                    (capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT, self.wallet, int(market_id),
                     str(market.get("title") or time20_market["title"] or "") or None, winner, base._now_ms(),
                     1 if fills else 0, status, len(fills), budget, cost, payout, pnl, pnl/cost if cost else None,
                     sum(1 for x in time20_events if bool(x["minimum_uplift"])), blocked,
                     1 if blocked or cost >= budget-1e-6 else 0))
                self.db.commit()

        with self.db_lock:
            batched_market = self.db.execute(
                "SELECT title,planned_budget_usdt FROM wallet_shadow_growth_markets WHERE cohort=? AND wallet=? AND market_id=?",
                (capital_s1.MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT, self.wallet, int(market_id)),
            ).fetchone()
            batched_events = [dict(row) for row in self.db.execute(
                """SELECT side,executed_shares,capital_cost_usdt,minimum_uplift,blocked
                     FROM wallet_shadow_growth_events WHERE cohort=? AND wallet=? AND market_id=? ORDER BY at_ms,id""",
                (capital_s1.MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT, self.wallet, int(market_id)),
            )] if batched_market else []
        if batched_market is not None:
            fills = [x for x in batched_events if float(x["executed_shares"] or 0) > 0]
            cost = sum(float(x["capital_cost_usdt"] or 0) for x in fills)
            payout = sum(float(x["executed_shares"] or 0) for x in fills if x["side"] == winner)
            pnl = payout - cost
            blocked = sum(1 for x in batched_events if bool(x["blocked"]))
            status = "NO_TRADE" if not fills else "WIN" if pnl > 1e-9 else "LOSS" if pnl < -1e-9 else "FLAT"
            budget = float(batched_market["planned_budget_usdt"])
            with self.db_lock:
                self.db.execute(
                    """INSERT INTO wallet_shadow_growth_results(
                           cohort,wallet,market_id,title,winner,resolved_at_ms,traded,status,fill_count,
                           planned_budget_usdt,capital_cost_usdt,payout_usdt,net_pnl_usdt,net_roi,
                           minimum_uplift_events,blocked_events,budget_hit
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(cohort,wallet,market_id) DO UPDATE SET
                           winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,traded=excluded.traded,
                           status=excluded.status,fill_count=excluded.fill_count,capital_cost_usdt=excluded.capital_cost_usdt,
                           payout_usdt=excluded.payout_usdt,net_pnl_usdt=excluded.net_pnl_usdt,net_roi=excluded.net_roi,
                           minimum_uplift_events=excluded.minimum_uplift_events,blocked_events=excluded.blocked_events,
                           budget_hit=excluded.budget_hit""",
                    (capital_s1.MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT, self.wallet, int(market_id),
                     str(market.get("title") or batched_market["title"] or "") or None, winner, base._now_ms(),
                     1 if fills else 0, status, len(fills), budget, cost, payout, pnl, pnl / cost if cost else None,
                     sum(1 for x in batched_events if bool(x["minimum_uplift"])), blocked,
                     1 if blocked or cost >= budget - 1e-6 else 0),
                )
                self.db.commit()

        with self.db_lock:
            min1_registered = self.db.execute(
                "SELECT title FROM wallet_shadow_min1_markets WHERE cohort=? AND wallet=? AND market_id=?",
                (capital_s1.MIN1_EXEC_CAP100_COHORT, self.wallet, int(market_id)),
            ).fetchone()
            if min1_registered is None:
                return
            min1_events = [dict(row) for row in self.db.execute(
                """SELECT side,executed_shares,capital_cost_usdt,minimum_uplift,cap_truncated,blocked
                     FROM wallet_shadow_min1_events
                    WHERE cohort=? AND wallet=? AND market_id=? ORDER BY at_ms,id""",
                (capital_s1.MIN1_EXEC_CAP100_COHORT, self.wallet, int(market_id)),
            )]
        min1_fills = [item for item in min1_events if float(item["executed_shares"] or 0.0) > 0]
        min1_capital = sum(float(item["capital_cost_usdt"] or 0.0) for item in min1_fills)
        min1_payout = sum(float(item["executed_shares"] or 0.0) for item in min1_fills if str(item["side"]) == winner)
        min1_pnl = min1_payout - min1_capital
        min1_status = "NO_TRADE" if not min1_fills else "WIN" if min1_pnl > 1e-9 else "LOSS" if min1_pnl < -1e-9 else "FLAT"
        min1_blocked = sum(1 for item in min1_events if bool(item["blocked"]))
        with self.db_lock:
            self.db.execute(
                """INSERT INTO wallet_shadow_min1_results(
                       cohort,wallet,market_id,title,winner,resolved_at_ms,traded,status,fill_count,
                       capital_cost_usdt,payout_usdt,net_pnl_usdt,net_roi,minimum_uplift_events,
                       cap_truncated_events,blocked_events,cap_hit
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(cohort,wallet,market_id) DO UPDATE SET
                       title=excluded.title,winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,
                       traded=excluded.traded,status=excluded.status,fill_count=excluded.fill_count,
                       capital_cost_usdt=excluded.capital_cost_usdt,payout_usdt=excluded.payout_usdt,
                       net_pnl_usdt=excluded.net_pnl_usdt,net_roi=excluded.net_roi,
                       minimum_uplift_events=excluded.minimum_uplift_events,
                       cap_truncated_events=excluded.cap_truncated_events,blocked_events=excluded.blocked_events,
                       cap_hit=excluded.cap_hit""",
                (
                    capital_s1.MIN1_EXEC_CAP100_COHORT, self.wallet, int(market_id),
                    str(market.get("title") or market.get("question") or min1_registered["title"] or "") or None,
                    winner, base._now_ms(), 1 if min1_fills else 0, min1_status, len(min1_fills),
                    min1_capital, min1_payout, min1_pnl, min1_pnl / min1_capital if min1_capital else None,
                    sum(1 for item in min1_events if bool(item["minimum_uplift"])),
                    sum(1 for item in min1_events if bool(item["cap_truncated"])),
                    min1_blocked, 1 if min1_blocked or min1_capital >= capital_s1.CAP100_USDT - 1e-6 else 0,
                ),
            )
            self.db.commit()

        with self.db_lock:
            cap_registered = self.db.execute(
                "SELECT title FROM wallet_shadow_cap100_markets WHERE cohort=? AND wallet=? AND market_id=?",
                (capital_s1.CAP100_COHORT, self.wallet, int(market_id)),
            ).fetchone()
            if cap_registered is None:
                return
            cap_events = [dict(row) for row in self.db.execute(
                """SELECT role,side,price,executed_shares,capital_cost_usdt,truncated,blocked
                     FROM wallet_shadow_cap100_events
                    WHERE cohort=? AND wallet=? AND market_id=? ORDER BY source_at_ms,id""",
                (capital_s1.CAP100_COHORT, self.wallet, int(market_id)),
            )]
            baseline = self.db.execute(
                "SELECT cost_usdt,taker_fee_usdt,fee_net_pnl_usdt,fee_net_roi FROM wallet_shadow_capital_s1_results WHERE cohort=? AND wallet=? AND market_id=?",
                (capital_s1.COHORT, self.wallet, int(market_id)),
            ).fetchone()
        executed = [item for item in cap_events if float(item["executed_shares"] or 0.0) > 0]
        capital_cost = sum(float(item["capital_cost_usdt"] or 0.0) for item in executed)
        payout = sum(float(item["executed_shares"] or 0.0) for item in executed if str(item["side"]) == winner)
        net_pnl = payout - capital_cost
        status = "NO_TRADE" if not executed else "WIN" if net_pnl > 1e-9 else "LOSS" if net_pnl < -1e-9 else "FLAT"
        baseline_capital = float(baseline["cost_usdt"] or 0.0) + float(baseline["taker_fee_usdt"] or 0.0) if baseline else 0.0
        baseline_pnl = float(baseline["fee_net_pnl_usdt"] or 0.0) if baseline else 0.0
        with self.db_lock:
            self.db.execute(
                """INSERT INTO wallet_shadow_cap100_results(
                       cohort,wallet,market_id,title,winner,resolved_at_ms,traded,status,fill_count,
                       truncated,truncated_event_count,blocked_event_count,capital_cost_usdt,payout_usdt,
                       net_pnl_usdt,net_roi,baseline_s1_capital_usdt,baseline_s1_pnl_usdt,baseline_s1_roi,pnl_delta_usdt
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(cohort,wallet,market_id) DO UPDATE SET
                       title=excluded.title,winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,
                       traded=excluded.traded,status=excluded.status,fill_count=excluded.fill_count,
                       truncated=excluded.truncated,truncated_event_count=excluded.truncated_event_count,
                       blocked_event_count=excluded.blocked_event_count,capital_cost_usdt=excluded.capital_cost_usdt,
                       payout_usdt=excluded.payout_usdt,net_pnl_usdt=excluded.net_pnl_usdt,net_roi=excluded.net_roi,
                       baseline_s1_capital_usdt=excluded.baseline_s1_capital_usdt,
                       baseline_s1_pnl_usdt=excluded.baseline_s1_pnl_usdt,baseline_s1_roi=excluded.baseline_s1_roi,
                       pnl_delta_usdt=excluded.pnl_delta_usdt""",
                (
                    capital_s1.CAP100_COHORT, self.wallet, int(market_id),
                    str(market.get("title") or market.get("question") or cap_registered["title"] or "") or None,
                    winner, base._now_ms(), 1 if executed else 0, status, len(executed),
                    1 if any(bool(item["truncated"]) for item in cap_events) else 0,
                    sum(1 for item in cap_events if bool(item["truncated"])),
                    sum(1 for item in cap_events if bool(item["blocked"])),
                    capital_cost, payout, net_pnl, net_pnl / capital_cost if capital_cost else None,
                    baseline_capital, baseline_pnl, float(baseline["fee_net_roi"]) if baseline and baseline["fee_net_roi"] is not None else None,
                    net_pnl - baseline_pnl,
                ),
            )
            self.db.commit()

    def _cleanup_retention(self, *, force: bool = False) -> None:
        super()._cleanup_retention(force=force)
        cutoff = base._now_ms() - self.retention_ms
        deleted: dict[str, int] = {}
        with self.db_lock:
            cur = self.db.execute(
                "DELETE FROM wallet_spot_strike_forward_events WHERE cohort=? AND decision_at_ms<?",
                (v4_2.COHORT, cutoff),
            )
            deleted["events"] = max(0, cur.rowcount)
            cur = self.db.execute(
                "DELETE FROM wallet_spot_strike_forward_decisions WHERE cohort=? AND decision_at_ms<?",
                (v4_2.COHORT, cutoff),
            )
            deleted["decisions"] = max(0, cur.rowcount)
            cur = self.db.execute(
                "DELETE FROM wallet_spot_strike_forward_results WHERE cohort=? AND resolved_at_ms<?",
                (v4_2.COHORT, cutoff),
            )
            deleted["results"] = max(0, cur.rowcount)
            cur = self.db.execute(
                "DELETE FROM wallet_shadow_capital_s1_events WHERE cohort=? AND wallet=? AND source_at_ms<?",
                (capital_s1.COHORT, self.wallet, cutoff),
            )
            deleted["capitalS1Events"] = max(0, cur.rowcount)
            cur = self.db.execute(
                "DELETE FROM wallet_shadow_capital_s1_results WHERE cohort=? AND wallet=? AND resolved_at_ms<?",
                (capital_s1.COHORT, self.wallet, cutoff),
            )
            deleted["capitalS1Results"] = max(0, cur.rowcount)
            self.db.execute(
                """DELETE FROM wallet_shadow_capital_s1_markets
                    WHERE cohort=? AND wallet=? AND started_at_ms<?
                      AND market_id NOT IN (
                          SELECT market_id FROM wallet_shadow_capital_s1_events WHERE cohort=? AND wallet=?
                      )""",
                (capital_s1.COHORT, self.wallet, cutoff, capital_s1.COHORT, self.wallet),
            )
            self.db.execute(
                "DELETE FROM wallet_shadow_cap100_events WHERE cohort=? AND wallet=? AND source_at_ms<?",
                (capital_s1.CAP100_COHORT, self.wallet, cutoff),
            )
            self.db.execute(
                "DELETE FROM wallet_shadow_cap100_results WHERE cohort=? AND wallet=? AND resolved_at_ms<?",
                (capital_s1.CAP100_COHORT, self.wallet, cutoff),
            )
            self.db.execute(
                "DELETE FROM wallet_shadow_min1_events WHERE cohort=? AND wallet=? AND at_ms<?",
                (capital_s1.MIN1_EXEC_CAP100_COHORT, self.wallet, cutoff),
            )
            self.db.execute(
                "DELETE FROM wallet_shadow_min1_results WHERE cohort=? AND wallet=? AND resolved_at_ms<?",
                (capital_s1.MIN1_EXEC_CAP100_COHORT, self.wallet, cutoff),
            )
            self.db.execute("DELETE FROM wallet_shadow_growth_events WHERE cohort=? AND wallet=? AND at_ms<?",
                            (capital_s1.MIN1_WALLET_GROWTH_COHORT, self.wallet, cutoff))
            self.db.execute("DELETE FROM wallet_shadow_growth_results WHERE cohort=? AND wallet=? AND resolved_at_ms<?",
                            (capital_s1.MIN1_WALLET_GROWTH_COHORT, self.wallet, cutoff))
            self.db.execute("DELETE FROM wallet_shadow_growth_events WHERE cohort=? AND wallet=? AND at_ms<?",
                            (capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT, self.wallet, cutoff))
            self.db.execute("DELETE FROM wallet_shadow_growth_results WHERE cohort=? AND wallet=? AND resolved_at_ms<?",
                            (capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT, self.wallet, cutoff))
            self.db.execute("DELETE FROM wallet_shadow_growth_events WHERE cohort=? AND wallet=? AND at_ms<?",
                            (capital_s1.MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT, self.wallet, cutoff))
            self.db.execute("DELETE FROM wallet_shadow_growth_results WHERE cohort=? AND wallet=? AND resolved_at_ms<?",
                            (capital_s1.MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT, self.wallet, cutoff))
            self.db.execute(
                "DELETE FROM wallet_shadow_batched_pending WHERE cohort=? AND wallet=? AND updated_at_ms<?",
                (capital_s1.MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT, self.wallet, cutoff),
            )
            self.db.execute(
                "DELETE FROM wallet_shadow_target_event_context WHERE wallet=? AND observed_at_ms<?",
                (self.wallet, cutoff),
            )
            self.db.execute(
                "DELETE FROM wallet_shadow_target_market_results WHERE wallet=? AND resolved_at_ms<?",
                (self.wallet, cutoff),
            )
            self.db.commit()
        self.forward_last_cleanup_deleted = deleted

    def _capital_s1_performance(self) -> dict[str, Any]:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            summary = self.db.execute(
                """SELECT COUNT(*) settled,
                          SUM(CASE WHEN traded=1 THEN 1 ELSE 0 END) traded,
                          SUM(CASE WHEN traded=1 AND status='WIN' THEN 1 ELSE 0 END) wins,
                          SUM(CASE WHEN traded=1 AND status='LOSS' THEN 1 ELSE 0 END) losses,
                          COALESCE(SUM(cost_usdt),0) cost,
                          COALESCE(SUM(gross_pnl_usdt),0) gross_pnl,
                          COALESCE(SUM(taker_fee_usdt),0) fees,
                          COALESCE(SUM(fee_net_pnl_usdt),0) fee_pnl,
                          COALESCE(SUM(stress_1tick_cost_usdt),0) stress1_cost,
                          COALESCE(SUM(stress_1tick_pnl_usdt),0) stress1_pnl,
                          COALESCE(SUM(stress_2tick_cost_usdt),0) stress2_cost,
                          COALESCE(SUM(stress_2tick_pnl_usdt),0) stress2_pnl,
                          COALESCE(AVG(CASE WHEN traded=1 THEN cost_usdt END),0) average_cost,
                          COALESCE(MAX(CASE WHEN traded=1 THEN cost_usdt END),0) maximum_cost
                     FROM wallet_shadow_capital_s1_results
                    WHERE cohort=? AND wallet=? AND resolved_at_ms>=?""",
                (capital_s1.COHORT, self.wallet, cutoff),
            ).fetchone()
            source_events = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_shadow_capital_s1_events WHERE cohort=? AND wallet=? AND source_at_ms>=?",
                (capital_s1.COHORT, self.wallet, cutoff),
            ).fetchone()[0])
            accepted_events = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_shadow_capital_s1_events WHERE cohort=? AND wallet=? AND source_at_ms>=? AND accepted=1",
                (capital_s1.COHORT, self.wallet, cutoff),
            ).fetchone()[0])
            markets = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_shadow_capital_s1_markets WHERE cohort=? AND wallet=? AND started_at_ms>=?",
                (capital_s1.COHORT, self.wallet, cutoff),
            ).fetchone()[0])
            recent = [dict(row) for row in self.db.execute(
                """SELECT market_id,winner,status,fill_count,cost_usdt,gross_pnl_usdt,gross_roi,
                          fee_net_pnl_usdt,fee_net_roi,stress_1tick_roi,stress_2tick_roi,resolved_at_ms
                     FROM wallet_shadow_capital_s1_results
                    WHERE cohort=? AND wallet=? AND resolved_at_ms>=?
                    ORDER BY resolved_at_ms DESC LIMIT 30""",
                (capital_s1.COHORT, self.wallet, cutoff),
            )]
            ordered = [dict(row) for row in self.db.execute(
                """SELECT status,gross_pnl_usdt FROM wallet_shadow_capital_s1_results
                    WHERE cohort=? AND wallet=? AND resolved_at_ms>=? AND traded=1
                    ORDER BY resolved_at_ms,market_id""",
                (capital_s1.COHORT, self.wallet, cutoff),
            )]
        data = dict(summary) if summary is not None else {}
        cost = float(data.get("cost") or 0.0)
        gross_pnl = float(data.get("gross_pnl") or 0.0)
        stress1_cost = float(data.get("stress1_cost") or 0.0)
        stress2_cost = float(data.get("stress2_cost") or 0.0)
        traded = int(data.get("traded") or 0)
        wins = int(data.get("wins") or 0)
        equity = peak = max_drawdown = 0.0
        loss_streak = longest_loss_streak = 0
        for item in ordered:
            equity += float(item["gross_pnl_usdt"] or 0.0)
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
            if str(item["status"]) == "LOSS":
                loss_streak += 1
                longest_loss_streak = max(longest_loss_streak, loss_streak)
            else:
                loss_streak = 0
        return {
            "deploymentBoundaryMs": self.capital_s1_deployed_at_ms,
            "excludedDeploymentMarketId": self.capital_s1_excluded_market_id,
            "markets": markets,
            "settledMarkets": int(data.get("settled") or 0),
            "pendingMarkets": max(0, markets - int(data.get("settled") or 0)),
            "tradedMarkets": traded,
            "wins": wins,
            "losses": int(data.get("losses") or 0),
            "winRate": wins / traded if traded else None,
            "sourceEvents": source_events,
            "acceptedEvents": accepted_events,
            "eventRetention": accepted_events / source_events if source_events else None,
            "grossCostUsdt": cost,
            "grossPnlUsdt": gross_pnl,
            "grossRoi": gross_pnl / cost if cost else None,
            "takerFeeUsdt": float(data.get("fees") or 0.0),
            "feeNetPnlUsdt": float(data.get("fee_pnl") or 0.0),
            "feeNetRoi": float(data.get("fee_pnl") or 0.0) / (cost + float(data.get("fees") or 0.0)) if cost else None,
            "stress1TickRoi": float(data.get("stress1_pnl") or 0.0) / stress1_cost if stress1_cost else None,
            "stress2TickRoi": float(data.get("stress2_pnl") or 0.0) / stress2_cost if stress2_cost else None,
            "averageCostPerMarketUsdt": float(data.get("average_cost") or 0.0),
            "maximumCostPerMarketUsdt": float(data.get("maximum_cost") or 0.0),
            "maxDrawdownUsdt": max_drawdown,
            "longestLossStreak": longest_loss_streak,
            "recentMarkets": recent,
        }

    def _cap100_performance(self) -> dict[str, Any]:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            summary = self.db.execute(
                """SELECT COUNT(*) settled,
                          SUM(CASE WHEN traded=1 THEN 1 ELSE 0 END) traded,
                          SUM(CASE WHEN traded=1 AND status='WIN' THEN 1 ELSE 0 END) wins,
                          SUM(CASE WHEN traded=1 AND status='LOSS' THEN 1 ELSE 0 END) losses,
                          SUM(CASE WHEN truncated=1 THEN 1 ELSE 0 END) truncated_markets,
                          COALESCE(SUM(truncated_event_count),0) truncated_events,
                          COALESCE(SUM(blocked_event_count),0) blocked_events,
                          COALESCE(SUM(capital_cost_usdt),0) capital,
                          COALESCE(SUM(net_pnl_usdt),0) pnl,
                          COALESCE(SUM(baseline_s1_pnl_usdt),0) baseline_pnl,
                          COALESCE(SUM(pnl_delta_usdt),0) pnl_delta,
                          COALESCE(AVG(CASE WHEN traded=1 THEN capital_cost_usdt END),0) average_capital,
                          COALESCE(MAX(capital_cost_usdt),0) maximum_capital
                     FROM wallet_shadow_cap100_results
                    WHERE cohort=? AND wallet=? AND resolved_at_ms>=?""",
                (capital_s1.CAP100_COHORT, self.wallet, cutoff),
            ).fetchone()
            markets = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_shadow_cap100_markets WHERE cohort=? AND wallet=? AND started_at_ms>=?",
                (capital_s1.CAP100_COHORT, self.wallet, cutoff),
            ).fetchone()[0])
            recent = [dict(row) for row in self.db.execute(
                """SELECT market_id,winner,status,fill_count,truncated,truncated_event_count,
                          blocked_event_count,capital_cost_usdt,net_pnl_usdt,net_roi,
                          baseline_s1_pnl_usdt,pnl_delta_usdt,resolved_at_ms
                     FROM wallet_shadow_cap100_results
                    WHERE cohort=? AND wallet=? AND resolved_at_ms>=?
                    ORDER BY resolved_at_ms DESC LIMIT 30""",
                (capital_s1.CAP100_COHORT, self.wallet, cutoff),
            )]
            ordered = [dict(row) for row in self.db.execute(
                """SELECT status,net_pnl_usdt FROM wallet_shadow_cap100_results
                    WHERE cohort=? AND wallet=? AND resolved_at_ms>=? AND traded=1
                    ORDER BY resolved_at_ms,market_id""",
                (capital_s1.CAP100_COHORT, self.wallet, cutoff),
            )]
        data = dict(summary) if summary is not None else {}
        settled = int(data.get("settled") or 0)
        traded = int(data.get("traded") or 0)
        wins = int(data.get("wins") or 0)
        truncated_markets = int(data.get("truncated_markets") or 0)
        capital = float(data.get("capital") or 0.0)
        pnl = float(data.get("pnl") or 0.0)
        equity = peak = max_drawdown = 0.0
        loss_streak = longest_loss_streak = 0
        for item in ordered:
            equity += float(item["net_pnl_usdt"] or 0.0)
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
            if str(item["status"]) == "LOSS":
                loss_streak += 1
                longest_loss_streak = max(longest_loss_streak, loss_streak)
            else:
                loss_streak = 0
        return {
            "deploymentBoundaryMs": self.cap100_deployed_at_ms,
            "excludedDeploymentMarketId": self.cap100_excluded_market_id,
            "markets": markets,
            "settledMarkets": settled,
            "pendingMarkets": max(0, markets - settled),
            "tradedMarkets": traded,
            "wins": wins,
            "losses": int(data.get("losses") or 0),
            "winRate": wins / traded if traded else None,
            "truncatedMarkets": truncated_markets,
            "truncatedMarketRate": truncated_markets / settled if settled else None,
            "truncatedEvents": int(data.get("truncated_events") or 0),
            "blockedEvents": int(data.get("blocked_events") or 0),
            "capitalCostUsdt": capital,
            "netPnlUsdt": pnl,
            "netRoi": pnl / capital if capital else None,
            "baselineS1PnlUsdt": float(data.get("baseline_pnl") or 0.0),
            "pnlDeltaVsS1Usdt": float(data.get("pnl_delta") or 0.0),
            "averageCapitalPerMarketUsdt": float(data.get("average_capital") or 0.0),
            "maximumCapitalPerMarketUsdt": float(data.get("maximum_capital") or 0.0),
            "maxDrawdownUsdt": max_drawdown,
            "longestLossStreak": longest_loss_streak,
            "recentMarkets": recent,
        }

    def _min1_performance(self) -> dict[str, Any]:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            summary = self.db.execute(
                """SELECT COUNT(*) settled,
                          SUM(CASE WHEN traded=1 THEN 1 ELSE 0 END) traded,
                          SUM(CASE WHEN traded=1 AND status='WIN' THEN 1 ELSE 0 END) wins,
                          SUM(CASE WHEN traded=1 AND status='LOSS' THEN 1 ELSE 0 END) losses,
                          SUM(CASE WHEN cap_hit=1 THEN 1 ELSE 0 END) cap_hit_markets,
                          COALESCE(SUM(capital_cost_usdt),0) capital,
                          COALESCE(SUM(net_pnl_usdt),0) pnl,
                          COALESCE(SUM(minimum_uplift_events),0) uplift_events,
                          COALESCE(SUM(cap_truncated_events),0) truncated_events,
                          COALESCE(SUM(blocked_events),0) blocked_events,
                          COALESCE(AVG(CASE WHEN traded=1 THEN capital_cost_usdt END),0) average_capital,
                          COALESCE(MAX(capital_cost_usdt),0) maximum_capital
                     FROM wallet_shadow_min1_results
                    WHERE cohort=? AND wallet=? AND resolved_at_ms>=?""",
                (capital_s1.MIN1_EXEC_CAP100_COHORT, self.wallet, cutoff),
            ).fetchone()
            markets = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_shadow_min1_markets WHERE cohort=? AND wallet=? AND started_at_ms>=?",
                (capital_s1.MIN1_EXEC_CAP100_COHORT, self.wallet, cutoff),
            ).fetchone()[0])
            event_summary = self.db.execute(
                """SELECT COUNT(*) events,
                          SUM(CASE WHEN executed_shares>0 THEN 1 ELSE 0 END) executed,
                          SUM(CASE WHEN minimum_uplift=1 THEN 1 ELSE 0 END) uplift,
                          SUM(CASE WHEN blocked=1 THEN 1 ELSE 0 END) blocked
                     FROM wallet_shadow_min1_events
                    WHERE cohort=? AND wallet=? AND at_ms>=?""",
                (capital_s1.MIN1_EXEC_CAP100_COHORT, self.wallet, cutoff),
            ).fetchone()
            recent = [dict(row) for row in self.db.execute(
                """SELECT market_id,winner,status,fill_count,capital_cost_usdt,net_pnl_usdt,net_roi,
                          minimum_uplift_events,cap_truncated_events,blocked_events,cap_hit,resolved_at_ms
                     FROM wallet_shadow_min1_results
                    WHERE cohort=? AND wallet=? AND resolved_at_ms>=?
                    ORDER BY resolved_at_ms DESC LIMIT 30""",
                (capital_s1.MIN1_EXEC_CAP100_COHORT, self.wallet, cutoff),
            )]
            ordered = [dict(row) for row in self.db.execute(
                """SELECT status,net_pnl_usdt FROM wallet_shadow_min1_results
                    WHERE cohort=? AND wallet=? AND resolved_at_ms>=? AND traded=1
                    ORDER BY resolved_at_ms,market_id""",
                (capital_s1.MIN1_EXEC_CAP100_COHORT, self.wallet, cutoff),
            )]
        data = dict(summary) if summary is not None else {}
        event_data = dict(event_summary) if event_summary is not None else {}
        settled = int(data.get("settled") or 0)
        traded = int(data.get("traded") or 0)
        wins = int(data.get("wins") or 0)
        cap_hits = int(data.get("cap_hit_markets") or 0)
        capital = float(data.get("capital") or 0.0)
        pnl = float(data.get("pnl") or 0.0)
        equity = peak = max_drawdown = 0.0
        loss_streak = longest_loss_streak = 0
        for item in ordered:
            equity += float(item["net_pnl_usdt"] or 0.0)
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
            if str(item["status"]) == "LOSS":
                loss_streak += 1
                longest_loss_streak = max(longest_loss_streak, loss_streak)
            else:
                loss_streak = 0
        return {
            "deploymentBoundaryMs": self.min1_deployed_at_ms,
            "excludedDeploymentMarketId": self.min1_excluded_market_id,
            "markets": markets,
            "settledMarkets": settled,
            "pendingMarkets": max(0, markets - settled),
            "tradedMarkets": traded,
            "wins": wins,
            "losses": int(data.get("losses") or 0),
            "winRate": wins / traded if traded else None,
            "events": int(event_data.get("events") or 0),
            "executedEvents": int(event_data.get("executed") or 0),
            "minimumUpliftEvents": int(event_data.get("uplift") or 0),
            "minimumUpliftRate": int(event_data.get("uplift") or 0) / int(event_data.get("events") or 1),
            "blockedEvents": int(event_data.get("blocked") or 0),
            "capHitMarkets": cap_hits,
            "capHitMarketRate": cap_hits / settled if settled else None,
            "capitalCostUsdt": capital,
            "netPnlUsdt": pnl,
            "netRoi": pnl / capital if capital else None,
            "averageCapitalPerMarketUsdt": float(data.get("average_capital") or 0.0),
            "maximumCapitalPerMarketUsdt": float(data.get("maximum_capital") or 0.0),
            "maxDrawdownUsdt": max_drawdown,
            "longestLossStreak": longest_loss_streak,
            "recentMarkets": recent,
        }

    def _growth_performance(self, cohort: str = capital_s1.MIN1_WALLET_GROWTH_COHORT) -> dict[str, Any]:
        is_time20 = cohort == capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT
        is_batched = cohort == capital_s1.MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            result_rows = [dict(row) for row in self.db.execute(
                """SELECT market_id,winner,status,fill_count,planned_budget_usdt,capital_cost_usdt,
                          payout_usdt,net_pnl_usdt,net_roi,minimum_uplift_events,blocked_events,
                          budget_hit,resolved_at_ms FROM wallet_shadow_growth_results
                    WHERE cohort=? AND wallet=? AND resolved_at_ms>=? ORDER BY resolved_at_ms,market_id""",
                (cohort, self.wallet, cutoff),
            )]
            spent_all = float(self.db.execute(
                "SELECT COALESCE(SUM(capital_cost_usdt),0) FROM wallet_shadow_growth_events WHERE cohort=? AND wallet=?",
                (cohort, self.wallet)).fetchone()[0])
            payout_all = float(self.db.execute(
                "SELECT COALESCE(SUM(payout_usdt),0) FROM wallet_shadow_growth_results WHERE cohort=? AND wallet=?",
                (cohort, self.wallet)).fetchone()[0])
            markets = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_shadow_growth_markets WHERE cohort=? AND wallet=? AND started_at_ms>=?",
                (cohort, self.wallet, cutoff)).fetchone()[0])
            events = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_shadow_growth_events WHERE cohort=? AND wallet=? AND at_ms>=?",
                (cohort, self.wallet, cutoff)).fetchone()[0])
        settled = len(result_rows); traded = sum(1 for x in result_rows if int(x.get("fill_count") or 0) > 0)
        wins = sum(1 for x in result_rows if x["status"] == "WIN")
        pnl = sum(float(x["net_pnl_usdt"] or 0) for x in result_rows)
        cost = sum(float(x["capital_cost_usdt"] or 0) for x in result_rows)
        equity = peak = max_dd = 0.0; loss = longest = 0
        for x in result_rows:
            equity += float(x["net_pnl_usdt"] or 0); peak = max(peak, equity); max_dd = max(max_dd, peak-equity)
            if x["status"] == "LOSS": loss += 1; longest = max(longest, loss)
            else: loss = 0
        available = max(0.0, capital_s1.INITIAL_WALLET_USDT - spent_all + payout_all)
        return {
            "deploymentBoundaryMs": self.batched_deployed_at_ms if is_batched else self.time20_deployed_at_ms if is_time20 else self.growth_deployed_at_ms,
            "excludedDeploymentMarketId": self.batched_excluded_market_id if is_batched else self.time20_excluded_market_id if is_time20 else self.growth_excluded_market_id,
            "initialWalletUsdt": capital_s1.INITIAL_WALLET_USDT,
            "availableCashUsdt": available,
            "realizedWalletValueUsdt": capital_s1.INITIAL_WALLET_USDT + sum(float(x["net_pnl_usdt"] or 0) for x in result_rows),
            "walletGrowthRate": sum(float(x["net_pnl_usdt"] or 0) for x in result_rows) / capital_s1.INITIAL_WALLET_USDT,
            "markets": markets, "settledMarkets": settled, "pendingMarkets": max(0, markets-settled),
            "events": events, "tradedMarkets": traded, "wins": wins, "losses": sum(1 for x in result_rows if x["status"] == "LOSS"),
            "winRate": wins/traded if traded else None, "netPnlUsdt": pnl, "netRoiOnDeployedCapital": pnl/cost if cost else None,
            "budgetHitMarkets": sum(1 for x in result_rows if bool(x["budget_hit"])),
            "blockedEvents": sum(int(x["blocked_events"] or 0) for x in result_rows),
            "maxDrawdownUsdt": max_dd, "longestLossStreak": longest,
            "bankrupt": available < capital_s1.MINIMUM_ORDER_NOTIONAL_USDT,
            "recentMarkets": list(reversed(result_rows[-30:])),
        }

    def _forward_performance(self) -> dict[str, Any]:
        payload = v4_2.WalletShadowObserver._forward_performance(self)
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            recent_decisions = [
                dict(row)
                for row in self.db.execute(
                    """
                    SELECT market_id,market_bucket,decision_at_ms,seconds_left,decision,reason,
                           side,observed_ask,start_price,spot_price,displacement_bps,
                           spot_age_ms,prediction_receipt_age_ms
                      FROM wallet_spot_strike_forward_decisions
                     WHERE cohort=? AND decision_at_ms>=?
                     ORDER BY decision_at_ms DESC LIMIT 30
                    """,
                    (v4_2.COHORT, cutoff),
                )
            ]
            stored = {
                "events": int(
                    self.db.execute(
                        "SELECT COUNT(*) FROM wallet_spot_strike_forward_events WHERE cohort=? AND decision_at_ms>=?",
                        (v4_2.COHORT, cutoff),
                    ).fetchone()[0]
                ),
                "decisions": int(
                    self.db.execute(
                        "SELECT COUNT(*) FROM wallet_spot_strike_forward_decisions WHERE cohort=? AND decision_at_ms>=?",
                        (v4_2.COHORT, cutoff),
                    ).fetchone()[0]
                ),
                "results": int(
                    self.db.execute(
                        "SELECT COUNT(*) FROM wallet_spot_strike_forward_results WHERE cohort=? AND resolved_at_ms>=?",
                        (v4_2.COHORT, cutoff),
                    ).fetchone()[0]
                ),
            }
        decisions = int(payload.get("decisions") or 0)
        trades = int(payload.get("events") or 0)
        payload.update(
            {
                "windowDays": self.retention_days,
                "tradeRate": trades / decisions if decisions else None,
                "skips": max(0, decisions - trades),
                "storedRows": stored,
                "recentDecisions": recent_decisions,
            }
        )
        return payload

    def _forward_target_similarity(self) -> dict[str, Any]:
        now_ms = base._now_ms()
        if (
            self.forward_similarity_cache is not None
            and now_ms - self.forward_similarity_cache_at_ms < TARGET_SIMILARITY_CACHE_MS
        ):
            return dict(self.forward_similarity_cache)

        cutoff = now_ms - self.retention_ms
        with self.db_lock:
            decisions = [
                dict(row)
                for row in self.db.execute(
                    """
                    SELECT market_id,decision_at_ms,decision,side
                      FROM wallet_spot_strike_forward_decisions
                     WHERE cohort=? AND decision_at_ms>=? AND side IN ('UP','DOWN')
                     ORDER BY decision_at_ms
                    """,
                    (v4_2.COHORT, cutoff),
                )
            ]
            target_rows = [
                dict(row)
                for row in self.db.execute(
                    """
                    SELECT market_id,event_ms,side,shares,price
                      FROM wallet_shadow_target_events
                     WHERE wallet=? AND event_ms>=? AND role='TAKER' AND quote_type='BID'
                       AND side IN ('UP','DOWN') AND shares IS NOT NULL
                     ORDER BY market_id,event_ms
                    """,
                    (self.wallet, cutoff),
                )
            ]

        by_market: dict[int, list[dict[str, Any]]] = {}
        for row in target_rows:
            by_market.setdefault(int(row["market_id"]), []).append(row)

        side_at_matches = side_at_total = 0
        side_final_matches = side_final_total = 0
        capital_at_matches = capital_at_total = 0
        capital_final_matches = capital_final_total = 0
        trade_decisions = skip_signals = 0
        latest: dict[str, Any] | None = None

        for decision in decisions:
            side = str(decision.get("side") or "")
            market_id = int(decision["market_id"])
            at_ms = int(decision["decision_at_ms"])
            target = by_market.get(market_id, [])
            at_decision = _residual(target, at_ms)
            final = _residual(target)
            trade_decisions += int(decision.get("decision") == "TRADE")
            skip_signals += int(decision.get("decision") == "SKIP")

            if at_decision["side"] in {"UP", "DOWN"}:
                side_at_total += 1
                side_at_matches += int(side == at_decision["side"])
            if final["side"] in {"UP", "DOWN"}:
                side_final_total += 1
                side_final_matches += int(side == final["side"])
            if at_decision["capitalSide"] in {"UP", "DOWN"}:
                capital_at_total += 1
                capital_at_matches += int(side == at_decision["capitalSide"])
            if final["capitalSide"] in {"UP", "DOWN"}:
                capital_final_total += 1
                capital_final_matches += int(side == final["capitalSide"])

            latest = {
                "marketId": market_id,
                "decisionAtMs": at_ms,
                "decision": decision.get("decision"),
                "side": side,
                "targetAtDecision": at_decision,
                "targetFinalSoFar": final,
            }

        result = {
            "windowDays": self.retention_days,
            "signalDecisions": len(decisions),
            "tradeSignals": trade_decisions,
            "skipSignals": skip_signals,
            "atDecisionComparable": side_at_total,
            "atDecisionMatches": side_at_matches,
            "atDecisionMatchRate": side_at_matches / side_at_total if side_at_total else None,
            "finalComparable": side_final_total,
            "finalMatches": side_final_matches,
            "finalMatchRate": side_final_matches / side_final_total if side_final_total else None,
            "capitalAtDecisionComparable": capital_at_total,
            "capitalAtDecisionMatches": capital_at_matches,
            "capitalAtDecisionMatchRate": capital_at_matches / capital_at_total if capital_at_total else None,
            "capitalFinalComparable": capital_final_total,
            "capitalFinalMatches": capital_final_matches,
            "capitalFinalMatchRate": capital_final_matches / capital_final_total if capital_final_total else None,
            "latest": latest,
            "note": "target Taker fills are diagnostic only and never drive the Spot/Strike decision",
        }
        self.forward_similarity_cache = dict(result)
        self.forward_similarity_cache_at_ms = now_ms
        return result

    def _target_order_ratio_evidence(self) -> dict[str, Any]:
        now_ms = base._now_ms()
        if self.target_ratio_cache is not None and now_ms - self.target_ratio_cache_at_ms < TARGET_RATIO_CACHE_MS:
            return dict(self.target_ratio_cache)
        cutoff = now_ms - self.retention_ms
        with self.db_lock:
            grouped = [dict(row) for row in self.db.execute(
                """SELECT role,order_hash,market_id,side,SUM(shares) shares
                     FROM wallet_shadow_target_events
                    WHERE wallet=? AND event_ms>=? AND shares>0 AND order_hash IS NOT NULL
                    GROUP BY role,order_hash,market_id,side""",
                (self.wallet, cutoff),
            )]
        evidence: dict[str, Any] = {}
        for role in ("MAKER", "TAKER"):
            values = sorted(float(x["shares"]) for x in grouped if x["role"] == role)
            multiples = sum(abs(value / 18.0 - round(value / 18.0)) < 1e-6 for value in values)
            exact = sum(abs(value - 18.0) < 1e-6 for value in values)
            evidence[role.lower()] = {
                "aggregatedOrders": len(values),
                "medianShares": values[len(values) // 2] if values else None,
                "exact18ShareOrders": exact,
                "exact18ShareRate": exact / len(values) if values else None,
                "multipleOf18Orders": multiples,
                "multipleOf18Rate": multiples / len(values) if values else None,
            }
        result = {
            **evidence,
            "interpretation": "18 shares is a strong target Maker unit but not a target Maker:Taker ratio; target Taker sizing is continuous and rarely an 18-share multiple",
            "researchUse": "retain 18-to-1 only for scaling each inferred Maker source unit; size Taker from net residual without 18-share tier rounding",
        }
        self.target_ratio_cache = dict(result)
        self.target_ratio_cache_at_ms = now_ms
        return result

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        payload["targetPerformance"] = self._target_performance_snapshot()
        target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        execution = self._current_target_execution_snapshot(target)
        if execution.get("parents"):
            target["events"] = execution["parents"]
        payload["target"] = target
        payload["targetExecutionAnalysis"] = execution
        forward = payload.get("spotStrikeForward") if isinstance(payload.get("spotStrikeForward"), dict) else {}
        forward["availability"] = {
            "simulationDbAvailable": self.simulation_db is not None,
            "simulationDbPath": str(self.simulation_db_path),
            "simulationDbError": self.simulation_db_error,
        }
        forward["retention"] = {
            "days": self.retention_days,
            "bounded": True,
            "lastCleanupDeleted": dict(self.forward_last_cleanup_deleted),
            "policy": "Spot/Strike decisions, events, and settled results use the same rolling retention window as Wallet Shadow",
        }
        forward["targetSimilarity"] = self._forward_target_similarity()
        payload["spotStrikeForward"] = forward
        payload["capitalS1"] = {
            "cohort": capital_s1.COHORT,
            "paperOnly": True,
            "forwardOnly": True,
            "liveOrdersAffected": False,
            "historicalBackfill": False,
            "status": "ACTIVE" if self.capital_s1_active_market else "WAITING_NEXT_COMPLETE_MARKET",
            "config": self._capital_s1_config(),
            "current": {
                "marketId": self.market_id,
                "active": self.capital_s1_active_market,
                "lastTakerSide": self.capital_s1_state.last_taker_side,
                "takerSideSwitches": self.capital_s1_state.taker_side_switches,
                "takerBlocked": self.capital_s1_state.taker_blocked,
                "events": list(self.capital_s1_recent_events),
            },
            "performance": self._capital_s1_performance(),
            "accountingCaveat": "Maker fills remain inferred proxies; gross excludes queue position and maker rebates. Fee/stress views add 200 bps Taker cost and 1/2 tick adverse Taker slippage.",
            "promotion": "isolated paper cohort; not in live allowlist; no automatic activation",
        }
        payload["capitalS1Cap100Stress"] = {
            "cohort": capital_s1.CAP100_COHORT,
            "sourceCohort": capital_s1.COHORT,
            "paperOnly": True,
            "forwardOnly": True,
            "liveOrdersAffected": False,
            "historicalBackfill": False,
            "status": "ACTIVE" if self.cap100_active_market else "WAITING_NEXT_COMPLETE_MARKET",
            "config": self._cap100_config(),
            "current": {
                "marketId": self.market_id,
                "active": self.cap100_active_market,
                "usedCapitalUsdt": self.cap100_used_capital_usdt,
                "remainingCapitalUsdt": max(0.0, capital_s1.CAP100_USDT - self.cap100_used_capital_usdt),
                "truncated": self.cap100_truncated,
                "events": list(self.cap100_recent_events),
            },
            "performance": self._cap100_performance(),
            "researchQuestion": "Does a hard fee-inclusive $100 per-market capital ceiling truncate the causal sequence and worsen PnL versus uncapped S1?",
            "promotion": "stress-only paper cohort; never routes orders",
        }
        payload["min1ExecCap100"] = {
            "cohort": capital_s1.MIN1_EXEC_CAP100_COHORT,
            "paperOnly": True,
            "forwardOnly": True,
            "liveOrdersAffected": False,
            "historicalBackfill": False,
            "status": "ACTIVE" if self.min1_active_market else "WAITING_NEXT_COMPLETE_MARKET",
            "config": self._min1_config(),
            "current": {
                "marketId": self.market_id,
                "active": self.min1_active_market,
                "usedCapitalUsdt": self.min1_used_capital_usdt,
                "remainingCapitalUsdt": max(0.0, capital_s1.CAP100_USDT - self.min1_used_capital_usdt),
                "makerUpShares": self.min1_maker_up_shares,
                "makerDownShares": self.min1_maker_down_shares,
                "takerUpShares": self.min1_taker_up_shares,
                "takerDownShares": self.min1_taker_down_shares,
                "makerResidualShares": self.min1_maker_up_shares - self.min1_maker_down_shares,
                "lastTakerSide": self.min1_last_taker_side,
                "takerSideSwitches": self.min1_taker_side_switches,
                "takerBlocked": self.min1_taker_blocked,
                "events": list(self.min1_recent_events),
            },
            "performance": self._min1_performance(),
            "executionCaveat": "All orders satisfy the $1 principal floor, but Maker fills remain book-through/ask-touch proxies without queue position or depth proof.",
            "promotion": "executable-constraint paper stress only; never routes orders",
        }
        growth_perf = self._growth_performance()
        payload["min1WalletGrowth"] = {
            "cohort": capital_s1.MIN1_WALLET_GROWTH_COHORT, "paperOnly": True, "forwardOnly": True,
            "liveOrdersAffected": False, "historicalBackfill": False,
            "status": "BANKRUPT" if growth_perf["bankrupt"] else "ACTIVE" if self.growth_active_market else "WAITING_NEXT_COMPLETE_MARKET",
            "config": self._growth_config(),
            "current": {
                "marketId": self.market_id, "active": self.growth_active_market,
                "plannedBudgetUsdt": self.growth_market_budget_usdt,
                "usedCapitalUsdt": self.growth_used_capital_usdt,
                "remainingMarketBudgetUsdt": max(0.0, self.growth_market_budget_usdt-self.growth_used_capital_usdt),
                "makerResidualShares": self.growth_maker_up_shares-self.growth_maker_down_shares,
                "takerSideSwitches": self.growth_taker_side_switches, "takerBlocked": self.growth_taker_blocked,
                "events": list(self.growth_recent_events),
            },
            "performance": growth_perf,
            "executionCaveat": "Orders satisfy the $1 principal floor and cannot spend unsettled/locked cash; Maker fills remain causal fill proxies.",
            "promotion": "autonomous growth paper cohort; never routes orders",
        }
        time20_perf = self._growth_performance(capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT)
        unlocked_budget = self.time20_market_budget_usdt * self.time20_unlocked_fraction
        payload["min1WalletGrowthTime20"] = {
            "cohort": capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT,
            "paperOnly": True,
            "forwardOnly": True,
            "liveOrdersAffected": False,
            "historicalBackfill": False,
            "status": (
                "BANKRUPT"
                if time20_perf["bankrupt"]
                else "ACTIVE"
                if self.time20_active_market
                else "WAITING_NEXT_COMPLETE_MARKET"
            ),
            "config": self._time20_config(),
            "current": {
                "marketId": self.market_id,
                "active": self.time20_active_market,
                "plannedBudgetUsdt": self.time20_market_budget_usdt,
                "unlockedFraction": self.time20_unlocked_fraction,
                "unlockedBudgetUsdt": unlocked_budget,
                "usedCapitalUsdt": self.time20_used_capital_usdt,
                "remainingUnlockedBudgetUsdt": max(0.0, unlocked_budget - self.time20_used_capital_usdt),
                "remainingMarketBudgetUsdt": max(
                    0.0, self.time20_market_budget_usdt - self.time20_used_capital_usdt
                ),
                "makerResidualShares": self.time20_maker_up_shares - self.time20_maker_down_shares,
                "takerSideSwitches": self.time20_taker_side_switches,
                "takerBlocked": self.time20_taker_blocked,
                "events": list(self.time20_recent_events),
            },
            "performance": time20_perf,
            "executionCaveat": (
                "The market budget is unlocked cumulatively in five one-minute tranches; unused unlocked "
                "capital carries forward inside the market, future tranches cannot be borrowed, and every "
                "order still satisfies the $1 principal floor. Maker fills remain causal fill proxies."
            ),
            "promotion": "time-staged autonomous growth paper cohort; never routes orders",
        }
        batched_perf = self._growth_performance(capital_s1.MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT)
        payload["min1BatchedMakerTakerReserve"] = {
            "cohort": capital_s1.MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT,
            "paperOnly": True,
            "forwardOnly": True,
            "liveOrdersAffected": False,
            "historicalBackfill": False,
            "status": "BANKRUPT" if batched_perf["bankrupt"] else "ACTIVE" if self.batched_active_market else "WAITING_NEXT_COMPLETE_MARKET",
            "config": self._batched_config(),
            "targetRatioEvidence": self._target_order_ratio_evidence(),
            "current": {
                "marketId": self.market_id,
                "active": self.batched_active_market,
                "plannedBudgetUsdt": self.batched_market_budget_usdt,
                "makerBudgetUsdt": self.batched_market_budget_usdt * capital_s1.MAKER_BUDGET_FRACTION,
                "takerReserveUsdt": self.batched_market_budget_usdt * capital_s1.TAKER_BUDGET_FRACTION,
                "makerCapitalUsdt": self.batched_maker_capital_usdt,
                "takerCapitalUsdt": self.batched_taker_capital_usdt,
                "pendingMakerShares": dict(self.batched_pending_maker_shares),
                "makerResidualShares": self.batched_maker_up_shares - self.batched_maker_down_shares,
                "netResidualShares": (
                    self.batched_maker_up_shares + self.batched_taker_up_shares
                    - self.batched_maker_down_shares - self.batched_taker_down_shares
                ),
                "coreCandidateSide": self.batched_core_candidate_side,
                "coreStabilityCount": self.batched_core_stability_count,
                "takerSideSwitches": self.batched_taker_side_switches,
                "takerBlocked": self.batched_taker_blocked,
                "events": list(self.batched_recent_events),
            },
            "performance": batched_perf,
            "executionCaveat": "Maker signals are batched to the venue floor and capped at 20% of the market budget; Taker keeps an 80% reserve and corrects only stable net residual. Maker fills remain causal proxies without queue proof.",
            "promotion": "new forward-only paper cohort; never routes orders",
        }
        return payload


class _Handler(base._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_3Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"target={observer.wallet}; retention={v3.RETENTION_DAYS}d; "
        f"spotStrikeForward={v4_2.COHORT}; capitalS1={capital_s1.COHORT}; cap100={capital_s1.CAP100_COHORT}; "
        f"min1={capital_s1.MIN1_EXEC_CAP100_COHORT}; "
        f"growth={capital_s1.MIN1_WALLET_GROWTH_COHORT}; "
        f"time20={capital_s1.MIN1_WALLET_GROWTH_TIME20_COHORT}; "
        f"batched={capital_s1.MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT}; "
        f"simulationDbAvailable={observer.simulation_db is not None}; "
        f"paper only; apiKeyConfigured={bool(observer.api_key)}; db={base.DB_PATH}",
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
