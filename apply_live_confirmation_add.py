from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def replace_method(
    text: str,
    name: str,
    replacement: str,
    *,
    occurrence: int = 1,
) -> str:
    pattern = re.compile(
        rf"^    def {re.escape(name)}\(.*?(?=^    def |\Z)",
        re.M | re.S,
    )
    matches = list(pattern.finditer(text))
    if occurrence < 1 or len(matches) < occurrence:
        raise RuntimeError(
            f"method {name} occurrence {occurrence} not found; "
            f"available={len(matches)}"
        )
    match = matches[occurrence - 1]
    return text[:match.start()] + replacement.rstrip() + "\n\n" + text[match.end():]


def patch_live_trading(text: str) -> str:
    if "LIVE_EXECUTION_MODE_CONFIRMATION_ADD" in text:
        raise RuntimeError("live confirmation-add patch already installed")

    text = replace_once(
        text,
        'LIVE_MAX_SELECTED_STRATEGIES = 4\nLIVE_DEFAULT_STRATEGY = "M01O_F1"\n',
        '''LIVE_MAX_SELECTED_STRATEGIES = 4
LIVE_EXECUTION_MODE_FIXED = "FIXED"
LIVE_EXECUTION_MODE_CONFIRMATION_ADD = "CONFIRMATION_ADD"
LIVE_EXECUTION_MODES = (
    LIVE_EXECUTION_MODE_FIXED,
    LIVE_EXECUTION_MODE_CONFIRMATION_ADD,
)
LIVE_CONFIRMATION_ADD_TRANCHES = 4
LIVE_DEFAULT_CONFIRMATION_ADD_STAKE_USDT = Decimal("1.00")
LIVE_DEFAULT_STRATEGY = "M01O_F1"
''',
        "insert confirmation-add constants",
    )

    text = replace_once(
        text,
        '''        "futuresLeadObserverEnabled": False,
        "futuresLeadObserverVersion": "F1",
        "strategyDrawdownControlEnabled": [False],
''',
        '''        "futuresLeadObserverEnabled": False,
        "futuresLeadObserverVersion": "F1",
        "strategyExecutionModes": [LIVE_EXECUTION_MODE_FIXED],
        "strategyInitialStakesUsdt": [float(LIVE_DEFAULT_MAX_STAKE_USDT)],
        "strategyConfirmationAddStakesUsdt": [
            float(LIVE_DEFAULT_CONFIRMATION_ADD_STAKE_USDT)
        ],
        "strategyDrawdownControlEnabled": [False],
''',
        "add confirmation-add defaults",
    )

    normalization = '''    def normalized_list_setting(name: str, fallback: Any) -> list[Any]:
        raw = candidate.get(name)
        if name in values:
            raw = values[name]
        if isinstance(raw, str):
            try:
                decoded = json.loads(raw)
                raw = decoded if isinstance(decoded, list) else [raw]
            except json.JSONDecodeError:
                raw = [raw]
        if not isinstance(raw, list):
            raw = [fallback] * len(strategies)
        default_value = raw[0] if raw else fallback
        return (raw + [default_value] * len(strategies))[:len(strategies)]

    strategy_execution_modes = [
        str(value or LIVE_EXECUTION_MODE_FIXED).strip().upper()
        for value in normalized_list_setting(
            "strategyExecutionModes", LIVE_EXECUTION_MODE_FIXED
        )
    ]
    invalid_modes = sorted(
        set(strategy_execution_modes) - set(LIVE_EXECUTION_MODES)
    )
    if invalid_modes:
        raise ValueError(
            "each strategy execution mode must be one of: "
            + ", ".join(LIVE_EXECUTION_MODES)
        )

    raw_initial_stakes = normalized_list_setting(
        "strategyInitialStakesUsdt", float(stakes[0])
    )
    raw_add_stakes = normalized_list_setting(
        "strategyConfirmationAddStakesUsdt",
        float(LIVE_DEFAULT_CONFIRMATION_ADD_STAKE_USDT),
    )
    strategy_initial_stakes: list[Decimal] = []
    strategy_add_stakes: list[Decimal] = []
    strategy_total_caps: list[Decimal] = []
    for index, strategy in enumerate(strategies):
        mode = strategy_execution_modes[index]
        initial = _decimal(raw_initial_stakes[index])
        add_stake = _decimal(raw_add_stakes[index])
        if mode == LIVE_EXECUTION_MODE_FIXED:
            initial = stakes[index]
        if initial is None or not (
            LIVE_MIN_CONFIGURABLE_STAKE_USDT
            <= initial
            <= LIVE_MAX_CONFIGURABLE_STAKE_USDT
        ):
            raise ValueError(
                "each initial live stake must be between "
                f"{LIVE_MIN_CONFIGURABLE_STAKE_USDT} and "
                f"{LIVE_MAX_CONFIGURABLE_STAKE_USDT}"
            )
        if add_stake is None or not (
            LIVE_MIN_CONFIGURABLE_STAKE_USDT
            <= add_stake
            <= LIVE_MAX_CONFIGURABLE_STAKE_USDT
        ):
            raise ValueError(
                "each confirmation add stake must be between "
                f"{LIVE_MIN_CONFIGURABLE_STAKE_USDT} and "
                f"{LIVE_MAX_CONFIGURABLE_STAKE_USDT}"
            )
        if mode == LIVE_EXECUTION_MODE_CONFIRMATION_ADD:
            if strategy not in CONFIRMATION_ADD_SOURCE_STRATEGIES:
                raise ValueError(
                    f"{strategy} does not support CONFIRMATION_ADD; supported: "
                    + ", ".join(CONFIRMATION_ADD_SOURCE_STRATEGIES)
                )
            if strategy in {
                "R_FUTURES_LEAD_REVERSE",
                "R_MICROPRICE_REVERSE",
                "R_CALIBRATED_VALUE_REVERSE",
            } or strategy.startswith("PAIR_ARB_"):
                raise ValueError(f"{strategy} may not use CONFIRMATION_ADD")
            total_cap = initial + add_stake * LIVE_CONFIRMATION_ADD_TRANCHES
            if total_cap > LIVE_MAX_CONFIGURABLE_STAKE_USDT:
                raise ValueError(
                    "initial stake plus four confirmation adds may not exceed "
                    f"{LIVE_MAX_CONFIGURABLE_STAKE_USDT} USDT"
                )
        else:
            total_cap = initial
        _stake_amount_wei(initial)
        _stake_amount_wei(add_stake)
        _stake_amount_wei(total_cap)
        strategy_initial_stakes.append(initial)
        strategy_add_stakes.append(add_stake)
        strategy_total_caps.append(total_cap)
    stakes = strategy_total_caps
'''
    text = replace_once(
        text,
        '''        _stake_amount_wei(stake)
        stakes.append(stake)
    min_win_rate = _float(candidate.get("minHourlyWinRatePct"))
''',
        '''        _stake_amount_wei(stake)
        stakes.append(stake)
''' + normalization + '''    min_win_rate = _float(candidate.get("minHourlyWinRatePct"))
''',
        "normalize confirmation-add rules",
    )

    text = replace_once(
        text,
        '''        "maxStakeUsdt": float(stakes[0]),
        "strategyStakesUsdt": [float(stake) for stake in stakes],
        "minHourlyWinRatePct": float(min_win_rate),
''',
        '''        "maxStakeUsdt": float(stakes[0]),
        "strategyStakesUsdt": [float(stake) for stake in stakes],
        "strategyExecutionModes": strategy_execution_modes,
        "strategyInitialStakesUsdt": [
            float(stake) for stake in strategy_initial_stakes
        ],
        "strategyConfirmationAddStakesUsdt": [
            float(stake) for stake in strategy_add_stakes
        ],
        "minHourlyWinRatePct": float(min_win_rate),
''',
        "return confirmation-add rules",
    )

    helper = '''
def live_strategy_execution_plan(
    rules: dict[str, Any], strategy: str
) -> dict[str, Any]:
    normalized = str(strategy or "").strip().upper()
    try:
        index = [
            str(value).strip().upper() for value in rules["strategies"]
        ].index(normalized)
        total_cap = Decimal(str(rules["strategyStakesUsdt"][index]))
        mode = str(
            rules.get("strategyExecutionModes", [])[index]
        ).strip().upper()
        initial = Decimal(
            str(rules.get("strategyInitialStakesUsdt", [])[index])
        )
        add_stake = Decimal(
            str(rules.get("strategyConfirmationAddStakesUsdt", [])[index])
        )
    except (KeyError, IndexError, TypeError, ValueError, InvalidOperation):
        try:
            index = [
                str(value).strip().upper() for value in rules["strategies"]
            ].index(normalized)
            total_cap = Decimal(str(rules["strategyStakesUsdt"][index]))
        except (KeyError, IndexError, TypeError, ValueError, InvalidOperation):
            total_cap = Decimal(str(rules.get("maxStakeUsdt") or 0))
        mode = LIVE_EXECUTION_MODE_FIXED
        initial = total_cap
        add_stake = LIVE_DEFAULT_CONFIRMATION_ADD_STAKE_USDT
    if mode not in LIVE_EXECUTION_MODES:
        mode = LIVE_EXECUTION_MODE_FIXED
    if mode == LIVE_EXECUTION_MODE_FIXED:
        initial = total_cap
    return {
        "strategy": normalized,
        "mode": mode,
        "initialStakeUsdt": initial,
        "addStakeUsdt": add_stake,
        "totalCapUsdt": total_cap,
        "tranches": LIVE_CONFIRMATION_ADD_TRANCHES,
    }


'''
    anchor = "\ndef evaluate_live_reliability_tags("
    if anchor not in text:
        raise RuntimeError("reliability evaluator anchor not found")
    text = text.replace(anchor, "\n" + helper + "def evaluate_live_reliability_tags(", 1)

    key_anchor = '            "strategyStakesUsdt",\n'
    key_count = text.count(key_anchor)
    if key_count < 2:
        raise RuntimeError(
            f"live rule persistence anchors missing; found {key_count}"
        )
    text = text.replace(
        key_anchor,
        '''            "strategyStakesUsdt",
            "strategyExecutionModes",
            "strategyInitialStakesUsdt",
            "strategyConfirmationAddStakesUsdt",
''',
    )

    schema = '''                CREATE TABLE IF NOT EXISTS live_confirmation_add_plans (
                    source_order_local_id INTEGER PRIMARY KEY,
                    strategy TEXT NOT NULL,
                    topic_id INTEGER NOT NULL,
                    market_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    configured_initial_stake_usdt REAL NOT NULL,
                    actual_initial_stake_usdt REAL,
                    add_stake_usdt REAL NOT NULL,
                    base_price REAL,
                    levels_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'PENDING_FILL',
                    created_at TEXT NOT NULL,
                    activated_at TEXT,
                    settled_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(source_order_local_id) REFERENCES live_orders(id)
                );
                CREATE INDEX IF NOT EXISTS live_confirmation_add_plans_market_idx
                    ON live_confirmation_add_plans(market_id, status);
'''
    text = replace_once(
        text,
        '''                CREATE INDEX IF NOT EXISTS live_confirmation_add_market_idx
                    ON live_confirmation_add_mirrors(market_id, status);
''',
        '''                CREATE INDEX IF NOT EXISTS live_confirmation_add_market_idx
                    ON live_confirmation_add_mirrors(market_id, status);
''' + schema,
        "create live confirmation-add plan table",
    )

    text = replace_once(
        text,
        '''        self._capture_filled_reliability_sample(local_id)

    def _capture_filled_reliability_sample''',
        '''        self._activate_live_confirmation_add_plan(local_id)
        self._capture_filled_reliability_sample(local_id)

    def _capture_filled_reliability_sample''',
        "activate confirmation plan after fill sync",
    )

    plan_methods = '''    def create_live_confirmation_add_plan(
        self,
        *,
        source_order_local_id: int,
        strategy: str,
        topic_id: int,
        market_id: int,
        side: str,
        token_id: str,
        configured_initial_stake_usdt: float,
        add_stake_usdt: float,
    ) -> None:
        normalized = str(strategy or "").strip().upper()
        if normalized not in CONFIRMATION_ADD_SOURCE_STRATEGIES:
            raise ValueError(
                f"{normalized} does not support live confirmation adds"
            )
        initial = float(configured_initial_stake_usdt)
        add = float(add_stake_usdt)
        if initial <= 0 or add <= 0:
            raise ValueError("confirmation-add amounts must be positive")
        now = utc_iso()
        with self.lock:
            self.db.execute(
                """INSERT OR IGNORE INTO live_confirmation_add_plans(
                       source_order_local_id, strategy, topic_id, market_id,
                       side, token_id, configured_initial_stake_usdt,
                       add_stake_usdt, status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING_FILL', ?, ?)""",
                (
                    int(source_order_local_id), normalized, int(topic_id),
                    int(market_id), str(side).upper(), str(token_id),
                    initial, add, now, now,
                ),
            )
            self.db.commit()

    def _activate_live_confirmation_add_plan(self, local_id: int) -> None:
        with self.lock:
            row = self.db.execute(
                """SELECT o.*, p.source_order_local_id
                     FROM live_orders AS o
                     JOIN live_confirmation_add_plans AS p
                       ON p.source_order_local_id=o.id
                    WHERE o.id=?""",
                (int(local_id),),
            ).fetchone()
            if row is None or ":" in str(row["strategy"] or ""):
                return
            filled_cost = _float(row["filled_usdt_amount"])
            quote_cost = self._wei_amount(row["quote_amount_in_wei"])
            positive_costs = [
                value for value in (filled_cost, quote_cost)
                if value is not None and value > 0
            ]
            if not positive_costs:
                return
            actual_initial = min(positive_costs)
            base_price = _float(row["quote_average_price"])
            if base_price is None:
                shares = _float(row["filled_share_qty"])
                base_price = (
                    actual_initial / shares
                    if shares is not None and shares > 0
                    else _float(row["signal_price"])
                )
            if base_price is None or not 0 < base_price < 1:
                return
            now = utc_iso()
            self.db.execute(
                """UPDATE live_confirmation_add_plans
                      SET actual_initial_stake_usdt=?, base_price=?,
                          levels_json=?, status='ACTIVE',
                          activated_at=COALESCE(activated_at, ?),
                          updated_at=?
                    WHERE source_order_local_id=?
                      AND status IN ('PENDING_FILL','ACTIVE')""",
                (
                    actual_initial,
                    base_price,
                    json.dumps(list(confirmation_add_levels(base_price))),
                    now,
                    now,
                    int(local_id),
                ),
            )
            self.db.commit()

    def live_confirmation_add_candidates(
        self, snapshot: dict[str, Any]
    ) -> list[dict[str, Any]]:
        try:
            market_id = int(snapshot["market_id"])
        except (KeyError, TypeError, ValueError):
            return []
        candidates: list[dict[str, Any]] = []
        with self.lock:
            plans = self.db.execute(
                """SELECT * FROM live_confirmation_add_plans
                    WHERE market_id=? AND status='ACTIVE'
                    ORDER BY source_order_local_id ASC""",
                (market_id,),
            ).fetchall()
            for plan in plans:
                side = str(plan["side"] or "").upper()
                safe, _reason = confirmation_add_book_is_safe(snapshot, side)
                if not safe:
                    continue
                prefix = side.lower()
                try:
                    ask = float(snapshot[f"{prefix}_ask"])
                    ask_size = float(snapshot[f"{prefix}_ask_size"])
                    levels = [
                        float(value)
                        for value in json.loads(plan["levels_json"] or "[]")
                    ]
                    seconds_left = float(snapshot["seconds_left"])
                    book_age_ms = float(snapshot["book_age_ms"])
                    book_skew_ms = float(snapshot["book_skew_ms"])
                except (
                    KeyError, TypeError, ValueError, json.JSONDecodeError
                ):
                    continue
                execution_limit = confirmation_add_execution_price(ask)
                if execution_limit is None:
                    continue
                for tranche_index in range(
                    1,
                    min(len(levels), LIVE_CONFIRMATION_ADD_TRANCHES + 1),
                ):
                    target = levels[tranche_index]
                    if ask + 1e-12 < target:
                        continue
                    ledger_strategy = (
                        f"{str(plan['strategy'])}:CONFIRM_ADD_{tranche_index}"
                    )
                    existing = self.db.execute(
                        """SELECT id FROM live_orders
                            WHERE strategy=? AND market_id=? LIMIT 1""",
                        (ledger_strategy, market_id),
                    ).fetchone()
                    if existing is not None:
                        continue
                    candidates.append(
                        {
                            "strategy": str(plan["strategy"]),
                            "ledgerStrategy": ledger_strategy,
                            "sourceOrderLocalId": int(
                                plan["source_order_local_id"]
                            ),
                            "trancheIndex": tranche_index,
                            "topicId": int(plan["topic_id"]),
                            "marketId": market_id,
                            "side": side,
                            "targetPrice": target,
                            "observedAsk": ask,
                            "observedAskSize": ask_size,
                            "executionLimit": float(execution_limit),
                            "stakeUsdt": float(plan["add_stake_usdt"]),
                            "secondsLeft": seconds_left,
                            "bookAgeMs": book_age_ms,
                            "bookSkewMs": book_skew_ms,
                            "timestamp": snapshot.get("timestamp") or utc_iso(),
                        }
                    )
                    # Queue only the earliest unmet tranche from one snapshot.
                    # A later current-market snapshot may advance the next
                    # stage after the durable attempt row exists.
                    break
        return candidates

    def live_confirmation_add_summary(self) -> dict[str, Any]:
        with self.lock:
            plans = [
                dict(row)
                for row in self.db.execute(
                    """SELECT * FROM live_confirmation_add_plans
                        ORDER BY source_order_local_id DESC LIMIT 100"""
                ).fetchall()
            ]
            for plan in plans:
                rows = self.db.execute(
                    """SELECT strategy, status, order_id,
                              filled_usdt_amount, filled_share_qty,
                              error_kind, error_message
                         FROM live_orders
                        WHERE market_id=? AND strategy LIKE ?
                        ORDER BY id ASC""",
                    (
                        int(plan["market_id"]),
                        f"{plan['strategy']}:CONFIRM_ADD_%",
                    ),
                ).fetchall()
                plan["tranches"] = [dict(row) for row in rows]
                try:
                    plan["levels"] = json.loads(plan.pop("levels_json"))
                except (TypeError, json.JSONDecodeError):
                    plan["levels"] = []
                plan.pop("token_id", None)
        active = [plan for plan in plans if plan["status"] == "ACTIVE"]
        return {
            "status": "ACTIVE" if active else "WAITING",
            "supportedSourceStrategies": list(
                CONFIRMATION_ADD_SOURCE_STRATEGIES
            ),
            "multipliers": [1.0, 1.1, 1.2, 1.3, 1.4],
            "minimumSecondsLeftExclusive": 30.0,
            "activePlans": len(active),
            "submittedAddOrders": sum(
                1 for plan in plans for row in plan["tranches"]
                if row.get("order_id")
            ),
            "filledAddOrders": sum(
                1 for plan in plans for row in plan["tranches"]
                if float(row.get("filled_usdt_amount") or 0) > 0
            ),
            "recentPlans": plans[:20],
        }

'''
    anchor = "    def record_confirmation_add_snapshot(\n"
    if anchor not in text:
        raise RuntimeError("record_confirmation_add_snapshot anchor missing")
    text = text.replace(anchor, plan_methods + anchor, 1)

    text = replace_once(
        text,
        '''    def _finalize_confirmation_add_mirror_locked(
        self,
        *,
        order_local_id: int,
        result: str,
        settled_at: str,
    ) -> None:
        row = self.db.execute(
''',
        '''    def _finalize_confirmation_add_mirror_locked(
        self,
        *,
        order_local_id: int,
        result: str,
        settled_at: str,
    ) -> None:
        normalized = str(result).upper()
        if normalized not in {"WIN", "LOSS"}:
            return
        self.db.execute(
            """UPDATE live_confirmation_add_plans
                  SET status='SETTLED', settled_at=?, updated_at=?
                WHERE source_order_local_id=?""",
            (settled_at, utc_iso(), int(order_local_id)),
        )
        row = self.db.execute(
''',
        "settle confirmation-add plan",
    )
    text = replace_once(
        text,
        '''        normalized = str(result).upper()
        if normalized not in {"WIN", "LOSS"}:
            return
        stake = float(row["hypothetical_stake_usdt"] or 0.0)
''',
        '''        stake = float(row["hypothetical_stake_usdt"] or 0.0)
''',
        "remove duplicate normalized settlement block",
    )

    text = replace_once(
        text,
        '''    ) -> None:
        normalized_strategy = self._loss_cooldown_strategy(strategy)
        normalized_result = str(result).upper()
''',
        '''    ) -> None:
        if ":CONFIRM_ADD_" in str(strategy or "").upper():
            return
        normalized_strategy = self._loss_cooldown_strategy(strategy)
        normalized_result = str(result).upper()
''',
        "exclude add tranches from cooldown",
    )

    performance_method = '''    def strategy_performance(self, strategy: str | None = None) -> dict[str, Any]:
        normalized = str(strategy or "").upper()
        pair_strategy = bool(normalized and normalized.startswith("PAIR_ARB_"))
        confirmation_family = normalized in CONFIRMATION_ADD_SOURCE_STRATEGIES
        with self.lock:
            if confirmation_family:
                pattern = f"{normalized}:CONFIRM_ADD_%"
                executed_row = self.db.execute(
                    """SELECT COUNT(DISTINCT market_id) AS executed
                         FROM live_orders
                        WHERE COALESCE(filled_usdt_amount, 0)>0
                          AND (strategy=? OR strategy LIKE ?)""",
                    (normalized, pattern),
                ).fetchone()
                row = self.db.execute(
                    """SELECT
                           COUNT(*) AS settled,
                           COALESCE(SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END),0) AS wins,
                           COALESCE(SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END),0) AS losses,
                           COALESCE(SUM(cost),0) AS cost,
                           COALESCE(SUM(payout),0) AS payout,
                           COALESCE(SUM(pnl),0) AS pnl
                         FROM (
                           SELECT o.market_id,
                                  MAX(s.result) AS result,
                                  SUM(s.cost_usdt) AS cost,
                                  SUM(s.payout_usdt) AS payout,
                                  SUM(s.pnl_usdt) AS pnl
                             FROM live_strategy_settlements AS s
                             JOIN live_orders AS o
                               ON o.id=s.order_local_id
                            WHERE o.strategy=? OR o.strategy LIKE ?
                            GROUP BY o.market_id
                         )""",
                    (normalized, pattern),
                ).fetchone()
            else:
                order_filter = (
                    " AND strategy LIKE ?" if pair_strategy
                    else " AND strategy=?" if strategy
                    else ""
                )
                settlement_filter = (
                    " WHERE order_local_id IN "
                    "(SELECT id FROM live_orders WHERE strategy LIKE ?)"
                    if pair_strategy
                    else " WHERE order_local_id IN "
                    "(SELECT id FROM live_orders WHERE strategy=?)"
                    if strategy
                    else ""
                )
                parameters: tuple[Any, ...] = (
                    (f"{normalized}:%",)
                    if pair_strategy
                    else (normalized,)
                    if strategy
                    else ()
                )
                executed_row = self.db.execute(
                    f"""SELECT COUNT(*) executed
                         FROM live_orders
                        WHERE COALESCE(filled_usdt_amount, 0)>0{order_filter}""",
                    parameters,
                ).fetchone()
                row = self.db.execute(
                    f"""SELECT
                           COUNT(*) settled,
                           COALESCE(SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END),0) wins,
                           COALESCE(SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END),0) losses,
                           COALESCE(SUM(cost_usdt),0) cost,
                           COALESCE(SUM(payout_usdt),0) payout,
                           COALESCE(SUM(pnl_usdt),0) pnl
                         FROM live_strategy_settlements{settlement_filter}""",
                    parameters,
                ).fetchone()
        executed = int(executed_row["executed"])
        settled = int(row["settled"])
        wins = int(row["wins"])
        losses = int(row["losses"])
        cost = float(row["cost"])
        payout = float(row["payout"])
        pnl = float(row["pnl"])
        return {
            "executedTrades": executed,
            "settledTrades": settled,
            "unsettledTrades": max(0, executed - settled),
            "wins": wins,
            "losses": losses,
            "winRatePct": ((wins / settled) * 100.0 if settled else None),
            "settledCostUsdt": cost,
            "settledPayoutUsdt": payout,
            "profitUsdt": pnl,
            "roiPct": ((pnl / cost) * 100.0 if cost > 0 else None),
            "includesConfirmationAddOrders": confirmation_family,
            "sampleUnit": "market" if confirmation_family else "order",
        }'''
    text = replace_method(text, "strategy_performance", performance_method)

    text = replace_once(
        text,
        '''        self.pending_futures_lead_hedges: dict[
            int, dict[str, dict[str, Any]]
        ] = {}
''',
        '''        self.pending_futures_lead_hedges: dict[
            int, dict[str, dict[str, Any]]
        ] = {}
        self.pending_confirmation_add_signals: set[str] = set()
''',
        "initialize add-on queue dedupe",
    )

    engine_snapshot = '''    def record_confirmation_add_snapshot(
        self, snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        """Advance Shadow mirrors and optionally queue live add-on tranches."""
        paper_result = self.ledger.record_confirmation_add_snapshot(snapshot)
        live_result = self._queue_live_confirmation_add_snapshot(snapshot)
        return {
            **paper_result,
            "queuedLiveAddOrders": live_result["queued"],
            "liveCandidateOrders": live_result["candidates"],
            "liveOrdersAffected": live_result["queued"] > 0,
        }

    def _queue_live_confirmation_add_snapshot(
        self, snapshot: dict[str, Any]
    ) -> dict[str, int]:
        with self.lock:
            rules = dict(self.live_rules)
            placement_enabled = self.runtime_enabled and self.armed
        if not placement_enabled:
            return {"candidates": 0, "queued": 0}
        candidates = self.ledger.live_confirmation_add_candidates(snapshot)
        queued = 0
        for candidate in candidates:
            strategy = str(candidate["strategy"]).upper()
            plan = live_strategy_execution_plan(rules, strategy)
            if plan["mode"] != LIVE_EXECUTION_MODE_CONFIRMATION_ADD:
                continue
            key = (
                f"{int(candidate['sourceOrderLocalId'])}:"
                f"{int(candidate['trancheIndex'])}"
            )
            with self.lock:
                if key in self.pending_confirmation_add_signals:
                    continue
                self.pending_confirmation_add_signals.add(key)
            signal = {
                "strategy": strategy,
                "topic_id": int(candidate["topicId"]),
                "market_id": int(candidate["marketId"]),
                "side": str(candidate["side"]),
                "entry_price": float(candidate["observedAsk"]),
                "seconds_left": float(candidate["secondsLeft"]),
                "signal_timestamp": str(candidate["timestamp"]),
                "book_age_ms": float(candidate["bookAgeMs"]),
                "book_skew_ms": float(candidate["bookSkewMs"]),
                "signal_prediction_book_age_ms": float(
                    candidate["bookAgeMs"]
                ),
                "signal_prediction_ask": float(candidate["observedAsk"]),
                "signal_prediction_ask_size": float(
                    candidate["observedAskSize"]
                ),
                "market_event_received_monotonic_ns": time.monotonic_ns(),
                "market_data_integrity_ok": True,
                "_live_confirmation_add": True,
                "_confirmation_add_key": key,
                "_confirmation_source_order_local_id": int(
                    candidate["sourceOrderLocalId"]
                ),
                "_confirmation_tranche_index": int(
                    candidate["trancheIndex"]
                ),
                "_confirmation_target_price": float(
                    candidate["targetPrice"]
                ),
                "_confirmation_execution_limit": float(
                    candidate["executionLimit"]
                ),
                "_live_ledger_strategy": str(candidate["ledgerStrategy"]),
                "_live_stake_override_usdt": float(candidate["stakeUsdt"]),
                "_live_enqueued_monotonic_ns": time.monotonic_ns(),
            }
            try:
                self.events.put_nowait(signal)
                queued += 1
            except queue.Full:
                with self.lock:
                    self.pending_confirmation_add_signals.discard(key)
                    self.dropped_signals += 1
                    self.status = "DEGRADED"
                    self.last_error = (
                        "live confirmation-add queue overflow; no order placed"
                    )
        return {"candidates": len(candidates), "queued": queued}'''
    text = replace_method(
        text,
        "record_confirmation_add_snapshot",
        engine_snapshot,
        occurrence=2,
    )

    text = replace_once(
        text,
        '''        strategy = str(signal.get("strategy") or rules["strategy"])
        try:
            strategy_index = list(rules["strategies"]).index(strategy)
            configured_stake = rules["strategyStakesUsdt"][strategy_index]
        except (KeyError, IndexError, TypeError, ValueError):
            configured_stake = rules["maxStakeUsdt"]
        stake = Decimal(str(configured_stake))
''',
        '''        strategy = str(
            signal.get("strategy") or rules["strategy"]
        ).upper()
        is_confirmation_add = signal.get("_live_confirmation_add") is True
        execution_plan = live_strategy_execution_plan(rules, strategy)
        configured_stake = execution_plan["initialStakeUsdt"]
        if is_confirmation_add:
            override = _decimal(signal.get("_live_stake_override_usdt"))
            if override is not None and override > 0:
                configured_stake = override
        stake = Decimal(str(configured_stake))
        ledger_strategy = (
            str(signal.get("_live_ledger_strategy") or "")
            if is_confirmation_add else strategy
        ) or strategy
''',
        "use add-on identity for blocked orders",
    )
    text = replace_once(
        text,
        '''            strategy=strategy,
            max_stake_usdt=float(stake),
''',
        '''            strategy=ledger_strategy,
            max_stake_usdt=float(stake),
''',
        "persist blocked add-on identity",
    )

    text = replace_once(
        text,
        '''        selected_strategy = signal_strategy
        strategy_index = list(rules["strategies"]).index(selected_strategy)
        max_stake = Decimal(str(rules["strategyStakesUsdt"][strategy_index]))
        if selected_strategy in LIVE_RESEARCH_STRATEGIES:
''',
        '''        selected_strategy = signal_strategy
        is_confirmation_add = signal.get("_live_confirmation_add") is True
        execution_plan = live_strategy_execution_plan(
            rules, selected_strategy
        )
        if is_confirmation_add:
            if (
                execution_plan["mode"]
                != LIVE_EXECUTION_MODE_CONFIRMATION_ADD
                or selected_strategy not in CONFIRMATION_ADD_SOURCE_STRATEGIES
            ):
                self._record_blocked_signal(
                    signal,
                    "BLOCKED_CONFIRMATION_ADD_DISABLED",
                    "順勢確認加碼目前未啟用或來源策略不支援",
                )
                return
            max_stake = _decimal(signal.get("_live_stake_override_usdt"))
            if (
                max_stake is None
                or max_stake <= 0
                or max_stake > execution_plan["addStakeUsdt"]
            ):
                self._record_blocked_signal(
                    signal,
                    "BLOCKED_INVALID_CONFIRMATION_ADD_STAKE",
                    "加碼金額缺失或超過已凍結的每階上限",
                )
                return
            try:
                if float(signal.get("seconds_left")) <= 30.0:
                    self._record_blocked_signal(
                        signal,
                        "SKIPPED_CONFIRMATION_ADD_LAST_30_SECONDS",
                        "剩餘時間不高於 30 秒；停止實單加碼",
                    )
                    return
            except (TypeError, ValueError):
                self._record_blocked_signal(
                    signal,
                    "BLOCKED_CONFIRMATION_ADD_TIME_UNAVAILABLE",
                    "加碼訊號缺少有效剩餘時間",
                )
                return
        else:
            max_stake = execution_plan["initialStakeUsdt"]
        if selected_strategy in LIVE_RESEARCH_STRATEGIES and not is_confirmation_add:
''',
        "select initial or add-on stake",
    )

    text = replace_once(
        text,
        '''            {"blocked": False}
            if selected_strategy.startswith("PAIR_ARB_")
''',
        '''            {"blocked": False}
            if selected_strategy.startswith("PAIR_ARB_") or is_confirmation_add
''',
        "freeze source hourly gate for add-ons",
    )
    text = replace_once(
        text,
        '''        reliability_is_safe, reliability_reason = self._reliability_gate_is_safe(
            signal, rules
        )
''',
        '''        reliability_is_safe, reliability_reason = (
            (True, "")
            if is_confirmation_add
            else self._reliability_gate_is_safe(signal, rules)
        )
''',
        "freeze source reliability gate",
    )
    text = replace_once(
        text,
        '''        if (
            drawdown_enabled
            and signal.get("_drawdown_control_validated") is not True
        ):
''',
        '''        if (
            not is_confirmation_add
            and drawdown_enabled
            and signal.get("_drawdown_control_validated") is not True
        ):
''',
        "freeze source drawdown gate",
    )
    text = replace_once(
        text,
        '''        if selected_strategy in LIVE_OBSERVER_STRATEGIES and observer_enabled:
''',
        '''        if (
            not is_confirmation_add
            and selected_strategy in LIVE_OBSERVER_STRATEGIES
            and observer_enabled
        ):
''',
        "freeze source observer gate",
    )
    text = replace_once(
        text,
        '''        if selected_strategy in {"M0W", "M01W"}:
''',
        '''        if not is_confirmation_add and selected_strategy in {"M0W", "M01W"}:
''',
        "skip original M gate for add-ons",
    )
    text = replace_once(
        text,
        '''        if selected_strategy == "M01O_F1":
''',
        '''        if not is_confirmation_add and selected_strategy == "M01O_F1":
''',
        "skip original F1 gate for add-ons",
    )
    text = replace_once(
        text,
        '''            maximum_reprice_limit = self._maximum_reprice_limit(
                signal, selected_strategy, signal_price
            )
''',
        '''            if is_confirmation_add:
                confirmation_limit = _decimal(
                    signal.get("_confirmation_execution_limit")
                )
                if (
                    confirmation_limit is None
                    or not Decimal("0") < confirmation_limit < Decimal("1")
                ):
                    raise ValueError(
                        "confirmation-add execution limit is unavailable"
                    )
                maximum_reprice_limit = confirmation_limit
            else:
                maximum_reprice_limit = self._maximum_reprice_limit(
                    signal, selected_strategy, signal_price
                )
''',
        "use Shadow-equivalent execution limit without source-only gates",
    )
    text = replace_once(
        text,
        '''            if (
                selected_strategy == "R_CALIBRATED_VALUE"
                and "RC_LOW_ENTRY" in rules["reliabilityGateTags"]
            ):
''',
        '''            if (
                not is_confirmation_add
                and selected_strategy == "R_CALIBRATED_VALUE"
                and "RC_LOW_ENTRY" in rules["reliabilityGateTags"]
            ):
''',
        "do not reapply RC_LOW_ENTRY ceiling to add-ons",
    )
    quote_gate_anchor = '''            if safe:
                safe, reason = self._reliability_quote_is_safe(
                    quote, selected_strategy, rules
                )
'''
    quote_gate_count = text.count(quote_gate_anchor)
    if quote_gate_count != 2:
        raise RuntimeError(
            "expected two reliability quote gates, found "
            f"{quote_gate_count}"
        )
    text = text.replace(
        quote_gate_anchor,
        '''            if safe and not is_confirmation_add:
                safe, reason = self._reliability_quote_is_safe(
                    quote, selected_strategy, rules
                )
''',
    )

    text = replace_once(
        text,
        '''        if self._strategy_loss_cooldown_enabled(rules, selected_strategy):
''',
        '''        if (
            not is_confirmation_add
            and self._strategy_loss_cooldown_enabled(rules, selected_strategy)
        ):
''',
        "do not consume cooldown on add-ons",
    )
    text = replace_once(
        text,
        '''        latest_verified_ask = Decimal(
            str(latest_prediction_book["latest_ask"])
        )
        book_diagnostics["signalPrice"] = float(signal_price)
''',
        '''        latest_verified_ask = Decimal(
            str(latest_prediction_book["latest_ask"])
        )
        if is_confirmation_add:
            target_price = _decimal(signal.get("_confirmation_target_price"))
            if (
                target_price is None
                or latest_verified_ask + Decimal("0.00000001") < target_price
            ):
                book_diagnostics["confirmationTargetPrice"] = (
                    float(target_price) if target_price is not None else None
                )
                self._record_prediction_book_block(
                    signal,
                    status="BLOCKED_CONFIRMATION_REVERSED",
                    error_kind="CONFIRMATION_TARGET_NO_LONGER_MET",
                    message=(
                        f"latest verified {side} ask "
                        f"{format(latest_verified_ask.normalize(), 'f')} "
                        "fell below the confirmation target"
                    ),
                    diagnostics=book_diagnostics,
                    enqueued_monotonic=enqueued_monotonic,
                    processing_started_monotonic=processing_started_monotonic,
                )
                return
        book_diagnostics["signalPrice"] = float(signal_price)
''',
        "revalidate threshold immediately before quote",
    )
    text = replace_once(
        text,
        '''        ledger_strategy = (
            f"{selected_strategy}:{side}"
            if selected_strategy.startswith("PAIR_ARB_")
            else selected_strategy
        )
        accepted_event_message = (
            f"{selected_strategy} {side} signal accepted for "
            f"{float(max_stake):.8g} USDT LIMIT quote"
        )
''',
        '''        ledger_strategy = (
            str(signal.get("_live_ledger_strategy") or "")
            if is_confirmation_add
            else (
                f"{selected_strategy}:{side}"
                if selected_strategy.startswith("PAIR_ARB_")
                else selected_strategy
            )
        ) or selected_strategy
        accepted_event_message = (
            (
                f"{ledger_strategy} threshold "
                f"{float(signal.get('_confirmation_target_price')):.6f} "
                f"accepted for {float(max_stake):.8g} USDT LIMIT quote"
            )
            if is_confirmation_add
            else (
                f"{selected_strategy} {side} signal accepted for "
                f"{float(max_stake):.8g} USDT LIMIT quote"
            )
        )
''',
        "assign durable add-on order identity",
    )
    text = replace_once(
        text,
        '''            reliability_context=signal,
            event_message=accepted_event_message,
''',
        '''            reliability_context=(None if is_confirmation_add else signal),
            event_message=accepted_event_message,
''',
        "prevent recursive Shadow sample",
    )
    text = replace_once(
        text,
        '''            return

        accepted_ledger_finished_monotonic = time.monotonic()
''',
        '''            return

        if (
            not is_confirmation_add
            and execution_plan["mode"]
            == LIVE_EXECUTION_MODE_CONFIRMATION_ADD
        ):
            self.ledger.create_live_confirmation_add_plan(
                source_order_local_id=local_id,
                strategy=selected_strategy,
                topic_id=int(reference["topic_id"]),
                market_id=market_id,
                side=side,
                token_id=token_id,
                configured_initial_stake_usdt=float(max_stake),
                add_stake_usdt=float(execution_plan["addStakeUsdt"]),
            )

        accepted_ledger_finished_monotonic = time.monotonic()
''',
        "freeze add-on plan at base signal acceptance",
    )
    text = replace_once(
        text,
        '''            max_stake = Decimal(str(self.live_rules["maxStakeUsdt"]))
            amount_in_wei = _stake_amount_wei(max_stake)
''',
        '''            execution_plans = [
                live_strategy_execution_plan(
                    self.live_rules, str(strategy)
                )
                for strategy in self.live_rules["strategies"]
            ]
            max_stake = max(
                max(
                    plan["initialStakeUsdt"],
                    plan["addStakeUsdt"]
                    if plan["mode"]
                    == LIVE_EXECUTION_MODE_CONFIRMATION_ADD
                    else plan["initialStakeUsdt"],
                )
                for plan in execution_plans
            )
            amount_in_wei = _stake_amount_wei(max_stake)
''',
        "preflight largest individual tranche across all strategy slots",
    )
    text = replace_once(
        text,
        '''                finally:
                    self.order_in_flight.clear()
''',
        '''                finally:
                    confirmation_key = signal.get("_confirmation_add_key")
                    if confirmation_key:
                        with self.lock:
                            self.pending_confirmation_add_signals.discard(
                                str(confirmation_key)
                            )
                    self.order_in_flight.clear()
''',
        "release queued add-on key",
    )
    text = replace_once(
        text,
        '''            "events": self.ledger.recent_events(),
            "updatedAt": utc_iso(),
''',
        '''            "confirmationAddLive": (
                self.ledger.live_confirmation_add_summary()
            ),
            "events": self.ledger.recent_events(),
            "updatedAt": utc_iso(),
''',
        "expose add-on live state",
    )
    text = replace_once(
        text,
        '''                "oneAttemptPerStrategyPerMarket": True,
                "retryAmbiguousPlacement": False,
''',
        '''                "oneAttemptPerStrategyPerMarket": True,
                "confirmationAdd": {
                    "optionalPerStrategySlot": True,
                    "supportedSources": list(
                        CONFIRMATION_ADD_SOURCE_STRATEGIES
                    ),
                    "multipliers": [1.0, 1.1, 1.2, 1.3, 1.4],
                    "minimumSecondsLeftExclusive": 30.0,
                    "eachTrancheOneAttempt": True,
                    "retryAmbiguousPlacement": False,
                },
                "retryAmbiguousPlacement": False,
''',
        "document add-on policy",
    )
    return text


def patch_dashboard(text: str) -> str:
    if "strategyExecutionModes" in text:
        raise RuntimeError("dashboard confirmation-add controls already installed")

    text = replace_once(
        text,
        '''type LiveRules = {
  strategy: string; strategies: string[]; maxStakeUsdt: number; strategyStakesUsdt: number[];
''',
        '''type LiveExecutionMode = "FIXED" | "CONFIRMATION_ADD";
type LiveRules = {
  strategy: string; strategies: string[]; maxStakeUsdt: number; strategyStakesUsdt: number[];
  strategyExecutionModes: LiveExecutionMode[];
  strategyInitialStakesUsdt: number[];
  strategyConfirmationAddStakesUsdt: number[];
''',
        "extend LiveRules type",
    )
    text = replace_once(
        text,
        '''  strategy: "M01O_F1", strategies: ["M01O_F1"], maxStakeUsdt: 1, strategyStakesUsdt: [1],
''',
        '''  strategy: "M01O_F1", strategies: ["M01O_F1"], maxStakeUsdt: 1, strategyStakesUsdt: [1],
  strategyExecutionModes: ["FIXED"],
  strategyInitialStakesUsdt: [1],
  strategyConfirmationAddStakesUsdt: [1],
''',
        "add dashboard defaults",
    )
    parser_anchor = '''    const strategyLossCooldownEnabled = Array.isArray(value.strategyLossCooldownEnabled)
      ? value.strategyLossCooldownEnabled.slice(0, strategies.length).map(Boolean)
      : strategies.map(() => false);
'''
    text = replace_once(
        text,
        parser_anchor,
        parser_anchor + '''    const strategyExecutionModes = Array.isArray(value.strategyExecutionModes)
      ? value.strategyExecutionModes.slice(0, strategies.length).map(mode => mode === "CONFIRMATION_ADD" ? "CONFIRMATION_ADD" : "FIXED") as LiveExecutionMode[]
      : strategies.map(() => "FIXED" as LiveExecutionMode);
    const strategyInitialStakesUsdt = Array.isArray(value.strategyInitialStakesUsdt)
      ? value.strategyInitialStakesUsdt.slice(0, strategies.length).map(Number)
      : strategyStakesUsdt.map(Number);
    const strategyConfirmationAddStakesUsdt = Array.isArray(value.strategyConfirmationAddStakesUsdt)
      ? value.strategyConfirmationAddStakesUsdt.slice(0, strategies.length).map(Number)
      : strategies.map(() => 1);
''',
        "parse dashboard confirmation rules",
    )
    text = replace_once(
        text,
        '''    if (!strategies.length || strategyStakesUsdt.length !== strategies.length || ![...strategyStakesUsdt, maxStakeUsdt, minHourlyWinRatePct, maxHourlyWinThenLossRatePct].every(Number.isFinite)) return null;
    if (strategyObserverEnabled.length !== strategies.length || strategyObserverVersions.length !== strategies.length || strategyDrawdownControlEnabled.length !== strategies.length || strategyLossCooldownEnabled.length !== strategies.length) return null;
    return { strategy: strategies[0], strategies, maxStakeUsdt: strategyStakesUsdt[0], strategyStakesUsdt, minHourlyWinRatePct, maxHourlyWinThenLossRatePct, futuresLeadObserverEnabled: strategyObserverEnabled[0], futuresLeadObserverVersion: strategyObserverVersions[0], strategyObserverEnabled, strategyObserverVersions, strategyDrawdownControlEnabled, strategyLossCooldownEnabled, reliabilityGateTags };
''',
        '''    if (!strategies.length || strategyStakesUsdt.length !== strategies.length || ![...strategyStakesUsdt, ...strategyInitialStakesUsdt, ...strategyConfirmationAddStakesUsdt, maxStakeUsdt, minHourlyWinRatePct, maxHourlyWinThenLossRatePct].every(Number.isFinite)) return null;
    if (strategyObserverEnabled.length !== strategies.length || strategyObserverVersions.length !== strategies.length || strategyDrawdownControlEnabled.length !== strategies.length || strategyLossCooldownEnabled.length !== strategies.length || strategyExecutionModes.length !== strategies.length || strategyInitialStakesUsdt.length !== strategies.length || strategyConfirmationAddStakesUsdt.length !== strategies.length) return null;
    return { strategy: strategies[0], strategies, maxStakeUsdt: strategyStakesUsdt[0], strategyStakesUsdt, strategyExecutionModes, strategyInitialStakesUsdt, strategyConfirmationAddStakesUsdt, minHourlyWinRatePct, maxHourlyWinThenLossRatePct, futuresLeadObserverEnabled: strategyObserverEnabled[0], futuresLeadObserverVersion: strategyObserverVersions[0], strategyObserverEnabled, strategyObserverVersions, strategyDrawdownControlEnabled, strategyLossCooldownEnabled, reliabilityGateTags };
''',
        "validate dashboard confirmation arrays",
    )
    text = replace_once(
        text,
        '''const LIVE_OBSERVER_STRATEGIES = new Set([
''',
        '''const LIVE_CONFIRMATION_ADD_SOURCE_STRATEGIES = new Set([
  "R_MICROPRICE",
  "R_CALIBRATED_VALUE",
  "M01O_F1",
]);
const LIVE_OBSERVER_STRATEGIES = new Set([
''',
        "declare dashboard confirmation sources",
    )
    text = replace_once(
        text,
        '''    const strategySummary = rulesDraft.strategies.map((strategy, index) => `${strategy} ${rulesDraft.strategyStakesUsdt[index]} USDT`).join("、");
''',
        '''    const strategySummary = rulesDraft.strategies.map((strategy, index) => {
      const mode = rulesDraft.strategyExecutionModes[index] ?? "FIXED";
      if (mode === "CONFIRMATION_ADD") {
        const initial = rulesDraft.strategyInitialStakesUsdt[index] ?? 1;
        const add = rulesDraft.strategyConfirmationAddStakesUsdt[index] ?? 1;
        return `${strategy} 順勢確認：初始 ${initial}、每階 ${add}、總上限 ${initial + add * 4} USDT`;
      }
      return `${strategy} 固定 ${rulesDraft.strategyStakesUsdt[index]} USDT`;
    }).join("、");
''',
        "summarize confirmation mode",
    )
    text = replace_once(
        text,
        '''    const drawdownControls = [...rulesDraft.strategyDrawdownControlEnabled];
    const lossCooldowns = [...rulesDraft.strategyLossCooldownEnabled];
''',
        '''    const drawdownControls = [...rulesDraft.strategyDrawdownControlEnabled];
    const lossCooldowns = [...rulesDraft.strategyLossCooldownEnabled];
    const executionModes = [...rulesDraft.strategyExecutionModes];
    const initialStakes = [...rulesDraft.strategyInitialStakesUsdt];
    const addStakes = [...rulesDraft.strategyConfirmationAddStakesUsdt];
''',
        "copy confirmation arrays",
    )
    text = replace_once(
        text,
        '''      drawdownControls.splice(index);
      lossCooldowns.splice(index);
''',
        '''      drawdownControls.splice(index);
      lossCooldowns.splice(index);
      executionModes.splice(index);
      initialStakes.splice(index);
      addStakes.splice(index);
''',
        "remove confirmation arrays with slot",
    )
    text = replace_once(
        text,
        '''      drawdownControls[index] = drawdownControls[index] ?? false;
      lossCooldowns[index] = lossCooldowns[index] ?? false;
''',
        '''      drawdownControls[index] = drawdownControls[index] ?? false;
      lossCooldowns[index] = lossCooldowns[index] ?? false;
      executionModes[index] = LIVE_CONFIRMATION_ADD_SOURCE_STRATEGIES.has(value)
        ? executionModes[index] ?? "FIXED"
        : "FIXED";
      initialStakes[index] = initialStakes[index] ?? stakes[index] ?? 1;
      addStakes[index] = addStakes[index] ?? 1;
      stakes[index] = executionModes[index] === "CONFIRMATION_ADD"
        ? initialStakes[index] + addStakes[index] * 4
        : initialStakes[index];
''',
        "initialize slot confirmation values",
    )
    text = replace_once(
        text,
        '''    onRulesUpdate("strategyDrawdownControlEnabled", drawdownControls);
    onRulesUpdate("strategyLossCooldownEnabled", lossCooldowns);
''',
        '''    onRulesUpdate("strategyDrawdownControlEnabled", drawdownControls);
    onRulesUpdate("strategyLossCooldownEnabled", lossCooldowns);
    onRulesUpdate("strategyExecutionModes", executionModes);
    onRulesUpdate("strategyInitialStakesUsdt", initialStakes);
    onRulesUpdate("strategyConfirmationAddStakesUsdt", addStakes);
''',
        "update slot confirmation arrays",
    )
    text = replace_once(
        text,
        '''  const updateStrategyStake = (index: number, value: number) => {
    const stakes = [...rulesDraft.strategyStakesUsdt];
    stakes[index] = value;
    onRulesUpdate("strategyStakesUsdt", stakes);
    if (index === 0) onRulesUpdate("maxStakeUsdt", value);
  };
''',
        '''  const updateStrategyExposure = (index: number, mode: LiveExecutionMode, initial: number, add: number) => {
    const modes = [...rulesDraft.strategyExecutionModes];
    const initials = [...rulesDraft.strategyInitialStakesUsdt];
    const adds = [...rulesDraft.strategyConfirmationAddStakesUsdt];
    const totals = [...rulesDraft.strategyStakesUsdt];
    modes[index] = mode;
    initials[index] = initial;
    adds[index] = add;
    totals[index] = mode === "CONFIRMATION_ADD" ? initial + add * 4 : initial;
    onRulesUpdate("strategyExecutionModes", modes);
    onRulesUpdate("strategyInitialStakesUsdt", initials);
    onRulesUpdate("strategyConfirmationAddStakesUsdt", adds);
    onRulesUpdate("strategyStakesUsdt", totals);
    if (index === 0) onRulesUpdate("maxStakeUsdt", totals[index]);
  };
  const updateStrategyExecutionMode = (index: number, mode: LiveExecutionMode) => {
    const supported = LIVE_CONFIRMATION_ADD_SOURCE_STRATEGIES.has(rulesDraft.strategies[index]);
    updateStrategyExposure(index, mode === "CONFIRMATION_ADD" && supported ? mode : "FIXED", rulesDraft.strategyInitialStakesUsdt[index] ?? rulesDraft.strategyStakesUsdt[index] ?? 1, rulesDraft.strategyConfirmationAddStakesUsdt[index] ?? 1);
  };
  const updateStrategyInitialStake = (index: number, value: number) => updateStrategyExposure(index, rulesDraft.strategyExecutionModes[index] ?? "FIXED", value, rulesDraft.strategyConfirmationAddStakesUsdt[index] ?? 1);
  const updateStrategyAddStake = (index: number, value: number) => updateStrategyExposure(index, rulesDraft.strategyExecutionModes[index] ?? "FIXED", rulesDraft.strategyInitialStakesUsdt[index] ?? 1, value);
''',
        "replace fixed stake updater",
    )

    old_slot = '''          return <label key={`live-strategy-${index}`}><span>實單策略 {index + 1}</span><select aria-label={`實單策略 ${index + 1}`} disabled={!slotEnabled} value={selected} onChange={event => updateStrategySlot(index, event.target.value)}>{index > 0 && <option value="">不啟用第{LIVE_STRATEGY_SLOT_NAMES[index]}策略</option>}{strategyOptions.filter(strategy => strategy === selected || !rulesDraft.strategies.includes(strategy)).map(strategy => <option key={strategy} value={strategy}>{LIVE_STRATEGY_LABELS[strategy] ?? strategy}</option>)}</select><div className="live-rule-number"><input type="number" min={data?.configurableStakeRangeUsdt?.min ?? .01} max={data?.configurableStakeRangeUsdt?.max ?? 100} step="0.01" disabled={!selected} value={rulesDraft.strategyStakesUsdt[index] ?? rulesDraft.strategyStakesUsdt[0]} onChange={event => updateStrategyStake(index, Number(event.target.value))} /><b>USDT</b></div><small>{index === 3 ? "第四格固定不使用 Observer；只執行所選策略本身" : index === 1 ? "Lead＋Reverse 仍會先取得兩腿 signed quote；其餘策略可獨立執行" : `策略 ${index + 1} 每筆／每組互補單的獨立上限`}</small></label>;
'''
    new_slot = '''          const mode = rulesDraft.strategyExecutionModes[index] ?? "FIXED";
          const confirmationSupported = LIVE_CONFIRMATION_ADD_SOURCE_STRATEGIES.has(selected);
          const initialStake = rulesDraft.strategyInitialStakesUsdt[index] ?? rulesDraft.strategyStakesUsdt[index] ?? 1;
          const addStake = rulesDraft.strategyConfirmationAddStakesUsdt[index] ?? 1;
          const totalExposure = mode === "CONFIRMATION_ADD" ? initialStake + addStake * 4 : initialStake;
          return <label key={`live-strategy-${index}`}><span>實單策略 {index + 1}</span><select aria-label={`實單策略 ${index + 1}`} disabled={!slotEnabled} value={selected} onChange={event => updateStrategySlot(index, event.target.value)}>{index > 0 && <option value="">不啟用第{LIVE_STRATEGY_SLOT_NAMES[index]}策略</option>}{strategyOptions.filter(strategy => strategy === selected || !rulesDraft.strategies.includes(strategy)).map(strategy => <option key={strategy} value={strategy}>{LIVE_STRATEGY_LABELS[strategy] ?? strategy}</option>)}</select><select aria-label={`實單策略 ${index + 1} 資金模式`} disabled={!selected} value={mode} onChange={event => updateStrategyExecutionMode(index, event.target.value as LiveExecutionMode)}><option value="FIXED">固定一次下單</option><option value="CONFIRMATION_ADD" disabled={!confirmationSupported}>順勢確認加碼 Shadow 實單版</option></select>{mode === "CONFIRMATION_ADD" ? <><div className="live-rule-number"><input type="number" min={data?.configurableStakeRangeUsdt?.min ?? .01} max={data?.configurableStakeRangeUsdt?.max ?? 100} step="0.01" value={initialStake} onChange={event => updateStrategyInitialStake(index, Number(event.target.value))} /><b>初始 USDT</b></div><div className="live-rule-number"><input type="number" min={data?.configurableStakeRangeUsdt?.min ?? .01} max={data?.configurableStakeRangeUsdt?.max ?? 100} step="0.01" value={addStake} onChange={event => updateStrategyAddStake(index, Number(event.target.value))} /><b>每階 USDT</b></div><small>1.1×／1.2×／1.3×／1.4× 各加一次；單市場總曝險 {totalExposure.toFixed(2)} USDT，剩餘 ≤30 秒停止。</small></> : <><div className="live-rule-number"><input type="number" min={data?.configurableStakeRangeUsdt?.min ?? .01} max={data?.configurableStakeRangeUsdt?.max ?? 100} step="0.01" value={initialStake} onChange={event => updateStrategyInitialStake(index, Number(event.target.value))} /><b>USDT</b></div><small>固定一次下單金額</small></>}</label>;
'''
    text = replace_once(text, old_slot, new_slot, "render confirmation controls")
    text = text.replace(
        "每個策略／每個市場硬上限 1 USDT",
        "每個策略依所選資金模式使用獨立單市場曝險上限",
    )
    return text


def patch_tests(text: str) -> str:
    if "test_confirmation_add_live_rules_derive_total_exposure" in text:
        raise RuntimeError("confirmation-add tests already installed")
    tests = '''


def test_confirmation_add_live_rules_derive_total_exposure():
    rules = live_trading.normalize_live_rules(
        {
            "strategies": ["R_MICROPRICE"],
            "strategyStakesUsdt": [1.0],
            "strategyExecutionModes": ["CONFIRMATION_ADD"],
            "strategyInitialStakesUsdt": [1.0],
            "strategyConfirmationAddStakesUsdt": [0.5],
        }
    )
    assert rules["strategyStakesUsdt"] == pytest.approx([3.0])
    assert rules["strategyInitialStakesUsdt"] == pytest.approx([1.0])
    assert rules["strategyConfirmationAddStakesUsdt"] == pytest.approx([0.5])
    plan = live_trading.live_strategy_execution_plan(rules, "R_MICROPRICE")
    assert float(plan["initialStakeUsdt"]) == pytest.approx(1.0)
    assert float(plan["addStakeUsdt"]) == pytest.approx(0.5)
    assert float(plan["totalCapUsdt"]) == pytest.approx(3.0)


def test_confirmation_add_rejects_unsupported_live_strategy():
    with pytest.raises(ValueError, match="does not support CONFIRMATION_ADD"):
        live_trading.normalize_live_rules(
            {
                "strategies": ["R_OFI"],
                "strategyStakesUsdt": [1.0],
                "strategyExecutionModes": ["CONFIRMATION_ADD"],
                "strategyInitialStakesUsdt": [1.0],
                "strategyConfirmationAddStakesUsdt": [1.0],
            }
        )


def test_live_confirmation_add_plan_activates_and_dedupes(tmp_path: Path):
    ledger = LiveLedger(tmp_path / "confirmation-add.db")
    local_id = ledger.record_accepted_signal(
        topic_id=101,
        market_id=202,
        side="UP",
        token_id="up-token",
        signal_price=0.40,
        account_type="SPOT",
        signal_at="2026-08-03T00:00:00+00:00",
        strategy="R_MICROPRICE",
        max_stake_usdt=1.0,
        requested_amount_wei=str(10**18),
        reliability_context=None,
        event_message="base accepted",
    )
    assert local_id is not None
    ledger.create_live_confirmation_add_plan(
        source_order_local_id=local_id,
        strategy="R_MICROPRICE",
        topic_id=101,
        market_id=202,
        side="UP",
        token_id="up-token",
        configured_initial_stake_usdt=1.0,
        add_stake_usdt=0.5,
    )
    ledger.update_order(
        local_id,
        status="FILLED",
        filled_usdt_amount=1.0,
        filled_share_qty=2.5,
        quote_average_price=0.40,
        quote_amount_in_wei=str(10**18),
    )
    ledger._activate_live_confirmation_add_plan(local_id)
    snapshot = {
        "timestamp": "2026-08-03T00:01:00+00:00",
        "market_id": 202,
        "seconds_left": 120.0,
        "up_ask": 0.44,
        "up_bid": 0.43,
        "up_ask_size": 100.0,
        "down_ask": 0.56,
        "down_bid": 0.55,
        "down_ask_size": 100.0,
        "book_age_ms": 100.0,
        "book_skew_ms": 10.0,
        "up_book_timestamp_ms": 1000,
        "down_book_timestamp_ms": 1000,
    }
    candidates = ledger.live_confirmation_add_candidates(snapshot)
    assert [item["trancheIndex"] for item in candidates] == [1]
    assert candidates[0]["stakeUsdt"] == pytest.approx(0.5)
    assert candidates[0]["ledgerStrategy"] == "R_MICROPRICE:CONFIRM_ADD_1"
    add_id = ledger.record_accepted_signal(
        topic_id=101,
        market_id=202,
        side="UP",
        token_id="up-token",
        signal_price=0.44,
        account_type="SPOT",
        signal_at="2026-08-03T00:01:00+00:00",
        strategy="R_MICROPRICE:CONFIRM_ADD_1",
        max_stake_usdt=0.5,
        requested_amount_wei=str(5 * 10**17),
        reliability_context=None,
        event_message="add accepted",
    )
    assert add_id is not None
    assert ledger.live_confirmation_add_candidates(snapshot) == []
'''
    return text.rstrip() + tests.rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=".")
    parser.add_argument("--run-tests", action="store_true")
    args = parser.parse_args()

    root = Path(args.project).resolve()
    files = {
        "live": root / "src/predict_bot/live_trading.py",
        "dashboard": root / "dashboard/app/page.tsx",
        "tests": root / "tests/test_live_trading.py",
    }
    for path in files.values():
        if not path.exists():
            print(f"Missing required file: {path}", file=sys.stderr)
            return 2

    originals = {name: path.read_text(encoding="utf-8") for name, path in files.items()}
    try:
        updated = {
            "live": patch_live_trading(originals["live"]),
            "dashboard": patch_dashboard(originals["dashboard"]),
            "tests": patch_tests(originals["tests"]),
        }
        ast.parse(updated["live"], filename=str(files["live"]))
        ast.parse(updated["tests"], filename=str(files["tests"]))
    except Exception as exc:
        print(f"Patch preparation failed: {exc}", file=sys.stderr)
        return 3

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = root / f"live-confirmation-add-backup-{stamp}"
    for path in files.values():
        destination = backup / path.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)

    try:
        for name, path in files.items():
            path.write_text(updated[name].rstrip() + "\n", encoding="utf-8", newline="\n")
        commands = [
            [sys.executable, "-m", "py_compile", str(files["live"]), str(files["tests"])],
            ["git", "diff", "--check"],
        ]
        if args.run_tests:
            commands.extend([
                [sys.executable, "-m", "pytest", "tests/test_live_trading.py", "-q"],
                ["npm", "run", "build"],
            ])
        for command in commands:
            print("+", " ".join(command))
            cwd = root / "dashboard" if command[:3] == ["npm", "run", "build"] else root
            result = subprocess.run(command, cwd=cwd)
            if result.returncode != 0:
                raise RuntimeError(
                    f"command failed ({result.returncode}): " + " ".join(command)
                )
    except Exception as exc:
        for path in files.values():
            shutil.copy2(backup / path.relative_to(root), path)
        print("Validation failed; original files restored.", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print(f"Backup kept at: {backup}", file=sys.stderr)
        return 4

    print()
    print("Live confirmation-add patch installed.")
    print(f"Backup: {backup}")
    print("Changed:")
    for path in files.values():
        print(f"  {path.relative_to(root)}")
    print("Supported: R_MICROPRICE, R_CALIBRATED_VALUE, M01O_F1")
    print("Maximum exposure: initial + four add-on stakes <= 100 USDT")
    print("Every add-on stage has a durable one-attempt ledger identity.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
