from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one occurrence, found {count}: {old[:120]!r}")
    write(path, content.replace(old, new, 1))


def replace_all(path: str, old: str, new: str, minimum: int = 1) -> None:
    content = read(path)
    count = content.count(old)
    if count < minimum:
        raise RuntimeError(f"{path}: expected at least {minimum} occurrences, found {count}: {old[:120]!r}")
    write(path, content.replace(old, new))


LOSS_STREAK_GUARD = '''from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


LOSS_STREAK_WARNING_LOSSES = 2
LOSS_STREAK_SHADOW_LOSSES = 3
LOSS_STREAK_RECOVERY_SAMPLES = 3
LOSS_STREAK_PROBATION_WINS = 2
LOSS_STREAK_REDUCED_STAKE_MULTIPLIER = 0.5


@dataclass(frozen=True)
class LossStreakGuardDecision:
    mode: str
    allowed: bool
    stake_multiplier: float
    consecutive_losses: int
    shadow_samples: int
    shadow_window_pnl: float | None
    probation_remaining: int
    trigger_market_id: int | None
    history_samples: int
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "allowed": self.allowed,
            "stakeMultiplier": self.stake_multiplier,
            "consecutiveLosses": self.consecutive_losses,
            "shadowSamples": self.shadow_samples,
            "shadowWindowPnl": self.shadow_window_pnl,
            "probationRemaining": self.probation_remaining,
            "triggerMarketId": self.trigger_market_id,
            "historySamples": self.history_samples,
            "reason": self.reason,
            "rule": (
                "two losses reduce the next stake to 50%; three losses enter "
                "SHADOW; the latest three causal paper PnLs must sum above zero "
                "to enter two-win half-stake probation"
            ),
        }


def _mapping(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        return dict(value)
    except (TypeError, ValueError):
        return {}


def _result(row: dict[str, Any]) -> str | None:
    value = str(row.get("status") or row.get("result") or "").upper()
    if value in {"WIN", "SETTLED_WIN"}:
        return "WIN"
    if value in {"LOSS", "SETTLED_LOSS"}:
        return "LOSS"
    return None


def _pnl(row: dict[str, Any]) -> float | None:
    raw = row.get("pnl")
    if raw is None:
        raw = row.get("pnl_usdt")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _market_id(row: dict[str, Any]) -> int | None:
    try:
        return int(row.get("market_id"))
    except (TypeError, ValueError):
        return None


def evaluate_loss_streak_guard(
    history: Iterable[Mapping[str, Any] | Any],
) -> LossStreakGuardDecision:
    """Replay settled, chronological source results without looking ahead.

    The source paper strategy remains active while live/test execution is in
    SHADOW, so those official paper PnLs form the causal recovery cohort.
    """
    mode = "NORMAL"
    consecutive_losses = 0
    shadow_pnls: list[float] = []
    probation_remaining = 0
    trigger_market_id: int | None = None
    processed = 0
    last_recovery_pnl: float | None = None

    for raw in history:
        row = _mapping(raw)
        result = _result(row)
        if result is None:
            continue
        processed += 1
        pnl = _pnl(row)
        market_id = _market_id(row)

        if mode == "NORMAL":
            consecutive_losses = consecutive_losses + 1 if result == "LOSS" else 0
            if consecutive_losses >= LOSS_STREAK_SHADOW_LOSSES:
                mode = "SHADOW"
                trigger_market_id = market_id
                shadow_pnls = []
                probation_remaining = 0
            continue

        if mode == "SHADOW":
            if pnl is None:
                continue
            shadow_pnls.append(pnl)
            if len(shadow_pnls) >= LOSS_STREAK_RECOVERY_SAMPLES:
                window = shadow_pnls[-LOSS_STREAK_RECOVERY_SAMPLES:]
                last_recovery_pnl = sum(window)
                if last_recovery_pnl > 0:
                    mode = "PROBATION"
                    probation_remaining = LOSS_STREAK_PROBATION_WINS
                    consecutive_losses = 0
            continue

        # PROBATION uses the continuously-running paper result as the exact
        # counterfactual for the half-stake live/test candidate.
        if result == "LOSS":
            mode = "SHADOW"
            trigger_market_id = market_id
            consecutive_losses = 0
            probation_remaining = 0
            shadow_pnls = []
            last_recovery_pnl = None
        else:
            probation_remaining = max(0, probation_remaining - 1)
            if probation_remaining == 0:
                mode = "NORMAL"
                consecutive_losses = 0
                shadow_pnls = []
                trigger_market_id = None

    if mode == "SHADOW":
        allowed = False
        multiplier = 0.0
        window = shadow_pnls[-LOSS_STREAK_RECOVERY_SAMPLES:]
        window_pnl = sum(window) if window else None
        reason = (
            f"SHADOW after three losses; {len(window)}/"
            f"{LOSS_STREAK_RECOVERY_SAMPLES} recovery samples, "
            f"window PnL {window_pnl:+.8f}" if window_pnl is not None else
            "SHADOW after three losses; no settled recovery sample yet"
        )
    elif mode == "PROBATION":
        allowed = True
        multiplier = LOSS_STREAK_REDUCED_STAKE_MULTIPLIER
        window_pnl = last_recovery_pnl
        reason = (
            f"PROBATION at 50% stake; {probation_remaining} consecutive paper "
            "wins still required"
        )
    else:
        allowed = True
        multiplier = (
            LOSS_STREAK_REDUCED_STAKE_MULTIPLIER
            if consecutive_losses >= LOSS_STREAK_WARNING_LOSSES
            else 1.0
        )
        window_pnl = None
        reason = (
            "NORMAL at 50% stake after two losses"
            if multiplier < 1.0
            else "NORMAL"
        )

    return LossStreakGuardDecision(
        mode=mode,
        allowed=allowed,
        stake_multiplier=multiplier,
        consecutive_losses=consecutive_losses,
        shadow_samples=len(shadow_pnls),
        shadow_window_pnl=window_pnl,
        probation_remaining=probation_remaining,
        trigger_market_id=trigger_market_id,
        history_samples=processed,
        reason=reason,
    )
'''
write("src/predict_bot/loss_streak_guard.py", LOSS_STREAK_GUARD)

TESTS = '''from __future__ import annotations

import pytest

from predict_bot.live_trading import LiveM0WEngine, normalize_live_rules
from predict_bot.loss_streak_guard import evaluate_loss_streak_guard
from predict_bot.server import Store


def rows(results):
    return [
        {
            "market_id": index + 1,
            "status": f"SETTLED_{result}",
            "pnl": pnl,
        }
        for index, (result, pnl) in enumerate(results)
    ]


def test_two_losses_reduce_next_stake_without_halt() -> None:
    decision = evaluate_loss_streak_guard(rows([("LOSS", -5), ("LOSS", -5)]))
    assert decision.mode == "NORMAL"
    assert decision.allowed is True
    assert decision.stake_multiplier == pytest.approx(0.5)
    assert decision.consecutive_losses == 2


def test_three_losses_enter_shadow_until_latest_three_pnl_is_positive() -> None:
    history = rows([
        ("LOSS", -5), ("LOSS", -5), ("LOSS", -5),
        ("WIN", 2), ("LOSS", -1),
    ])
    decision = evaluate_loss_streak_guard(history)
    assert decision.mode == "SHADOW"
    assert decision.allowed is False
    assert decision.shadow_samples == 2

    decision = evaluate_loss_streak_guard(history + rows([("WIN", 2)]))
    # The helper above restarts market ids, which is fine: ordering is the input order.
    assert decision.mode == "PROBATION"
    assert decision.allowed is True
    assert decision.stake_multiplier == pytest.approx(0.5)
    assert decision.probation_remaining == 2
    assert decision.shadow_window_pnl == pytest.approx(3.0)


def test_probation_needs_two_wins_and_any_loss_returns_to_shadow() -> None:
    base = rows([
        ("LOSS", -5), ("LOSS", -5), ("LOSS", -5),
        ("WIN", 2), ("LOSS", -1), ("WIN", 2),
    ])
    one_win = evaluate_loss_streak_guard(base + rows([("WIN", 1)]))
    assert one_win.mode == "PROBATION"
    assert one_win.probation_remaining == 1

    recovered = evaluate_loss_streak_guard(
        base + rows([("WIN", 1), ("WIN", 1)])
    )
    assert recovered.mode == "NORMAL"
    assert recovered.stake_multiplier == pytest.approx(1.0)

    relapsed = evaluate_loss_streak_guard(base + rows([("LOSS", -5)]))
    assert relapsed.mode == "SHADOW"
    assert relapsed.allowed is False


def test_store_history_is_causal_and_chronological(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")
    for market_id, status, pnl in (
        (101, "SETTLED_WIN", 1.0),
        (102, "SETTLED_LOSS", -5.0),
        (103, "SETTLED_LOSS", -5.0),
        (104, "SETTLED_LOSS", -5.0),
    ):
        store.db.execute(
            """INSERT INTO trades(
                   strategy, topic_id, market_id, side, status, entry_price,
                   stake, shares, fees, pnl, opened_at, closed_at
               ) VALUES ('R_MICROPRICE', 1, ?, 'UP', ?, 0.5, 5, 10, 0, ?,
                         '2026-08-01T00:00:00+00:00',
                         '2026-08-01T00:05:00+00:00')""",
            (market_id, status, pnl),
        )
    store.db.commit()

    history = store.loss_streak_guard_history(
        "R_MICROPRICE", before_market_id=104, limit=1000
    )
    assert [row["market_id"] for row in history] == [101, 102, 103]
    assert evaluate_loss_streak_guard(history).stake_multiplier == pytest.approx(0.5)


def test_live_rules_persist_per_slot_loss_streak_toggle() -> None:
    rules = normalize_live_rules({
        "strategy": "R_MICROPRICE",
        "strategyLossStreakGuardEnabled": [True],
    })
    assert rules["strategyLossStreakGuardEnabled"] == [True]


def test_live_engine_reads_causal_paper_guard_state(tmp_path) -> None:
    history = rows([("LOSS", -5), ("LOSS", -5), ("LOSS", -5)])
    engine = LiveM0WEngine(
        api_key=None,
        api_secret=None,
        configured_enabled=False,
        credential_source="test",
        current_market=lambda: None,
        db_path=tmp_path / "live.db",
        loss_streak_guard_history=lambda strategy, before_market_id, limit: history,
    )
    decision = engine._loss_streak_guard_decision("R_MICROPRICE", 999)
    assert decision["mode"] == "SHADOW"
    assert decision["allowed"] is False
'''
write("tests/test_loss_streak_guard.py", TESTS)

# Paper strategy registration and frozen parameters.
replace_once(
    "src/predict_bot/research_forward.py",
    'SHADOW_RESEARCH_STRATEGIES = (\n    "R_CALIBRATED_VALUE_CONTINUOUS_V2",',
    'SHADOW_RESEARCH_STRATEGIES = (\n    "R_CALIBRATED_VALUE_CONTINUOUS_V2",\n    "R_MICROPRICE_LOSS_STREAK_GUARD",',
)
replace_once(
    "src/predict_bot/research_forward.py",
    'RESEARCH_STRATEGIES = (*PRIMARY_RESEARCH_STRATEGIES, *SHADOW_RESEARCH_STRATEGIES)\n\nCONTINUOUS_CALIBRATION_RULES = {',
    'RESEARCH_STRATEGIES = (*PRIMARY_RESEARCH_STRATEGIES, *SHADOW_RESEARCH_STRATEGIES)\n\nLOSS_STREAK_GUARD_RULES = {\n    "R_MICROPRICE_LOSS_STREAK_GUARD": "R_MICROPRICE",\n}\nLOSS_STREAK_GUARD_STRATEGIES = tuple(LOSS_STREAK_GUARD_RULES)\n\nCONTINUOUS_CALIBRATION_RULES = {',
)
replace_once(
    "src/predict_bot/research_forward.py",
    '    "R_MICROPRICE": {"horizon": 180.0, "threshold": 0.20, "max_ask": 0.55},',
    '    "R_MICROPRICE": {"horizon": 180.0, "threshold": 0.20, "max_ask": 0.55},\n    "R_MICROPRICE_LOSS_STREAK_GUARD": {\n        "horizon": 180.0, "threshold": 0.20, "max_ask": 0.55,\n        "warning_losses": 2.0, "shadow_losses": 3.0,\n        "recovery_samples": 3.0, "probation_wins": 2.0,\n        "reduced_stake_multiplier": 0.5,\n    },',
)

# Server: register config, causal history reader, paper strategy evaluation and live callback.
replace_once(
    "src/predict_bot/server.py",
    'from .live_trading import LiveM0WEngine, M01O_F1_MIN_SECONDS_LEFT\n',
    'from .live_trading import LiveM0WEngine, M01O_F1_MIN_SECONDS_LEFT\nfrom .loss_streak_guard import evaluate_loss_streak_guard\n',
)
replace_once(
    "src/predict_bot/server.py",
    '    FUTURES_LEAD_FILTER_STRATEGIES,\n',
    '    FUTURES_LEAD_FILTER_STRATEGIES,\n    LOSS_STREAK_GUARD_RULES,\n    LOSS_STREAK_GUARD_STRATEGIES,\n',
)
replace_once(
    "src/predict_bot/server.py",
    '    "strategy_r_microprice_stake": 5.0,\n',
    '    "strategy_r_microprice_stake": 5.0,\n    "strategy_r_microprice_loss_streak_guard_enabled": True,\n    "strategy_r_microprice_loss_streak_guard_stake": 5.0,\n',
)
replace_once(
    "src/predict_bot/server.py",
    '    def _init(self) -> None:\n',
    '''    def loss_streak_guard_history(
        self,
        strategy: str,
        before_market_id: int,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Return settled source-paper rows strictly before the candidate market."""
        normalized_limit = max(3, min(5000, int(limit)))
        with self.lock:
            rows = self.db.execute(
                """SELECT id, market_id, status, pnl, closed_at
                     FROM (
                       SELECT id, market_id, status, pnl, closed_at
                         FROM trades
                        WHERE strategy=? AND market_id < ?
                          AND status IN ('SETTLED_WIN','SETTLED_LOSS')
                        ORDER BY id DESC LIMIT ?
                     )
                    ORDER BY id ASC""",
                (str(strategy).upper(), int(before_market_id), normalized_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def _init(self) -> None:
''',
)
replace_once(
    "src/predict_bot/server.py",
    '            calibration_decision = None\n            source_strategy = None\n',
    '            calibration_decision = None\n            loss_streak_guard_decision = None\n            source_strategy = None\n',
)
replace_once(
    "src/predict_bot/server.py",
    '            elif strategy in {"R_OFI_EVENT_CUM", "R_OFI_EVENT_CUM_FILTERED"}:\n',
    '''            elif strategy in LOSS_STREAK_GUARD_STRATEGIES:
                source_strategy = LOSS_STREAK_GUARD_RULES[strategy]
                if not bool(cfg.get(f"strategy_{source_strategy.lower()}_enabled")):
                    continue
                guard_history = self.loss_streak_guard_history(
                    source_strategy,
                    before_market_id=market_id,
                    limit=1000,
                )
                loss_streak_guard_decision = evaluate_loss_streak_guard(
                    guard_history
                )
                if not loss_streak_guard_decision.allowed:
                    continue
                signal = research_signal_for_strategy(
                    source_strategy,
                    current,
                    None,
                    fee_bps=fee_bps,
                    slippage_bps=float(cfg["strategy_research_slippage_bps"]),
                )
            elif strategy in {"R_OFI_EVENT_CUM", "R_OFI_EVENT_CUM_FILTERED"}:
''',
)
replace_once(
    "src/predict_bot/server.py",
    '            stake = float(cfg[f"strategy_{prefix}_stake"])\n            if not is_shadow and exposure + stake > cap + 1e-12:\n',
    '            stake = float(cfg[f"strategy_{prefix}_stake"])\n            if loss_streak_guard_decision is not None:\n                stake *= loss_streak_guard_decision.stake_multiplier\n            if not is_shadow and exposure + stake > cap + 1e-12:\n',
)
replace_once(
    "src/predict_bot/server.py",
    '            if strategy in {\n                *CONTINUOUS_CALIBRATION_STRATEGIES,\n                *PAIRED_REVERSE_STRATEGIES,\n                *FUTURES_LEAD_FILTER_STRATEGIES,\n',
    '            if strategy in {\n                *LOSS_STREAK_GUARD_STRATEGIES,\n                *CONTINUOUS_CALIBRATION_STRATEGIES,\n                *PAIRED_REVERSE_STRATEGIES,\n                *FUTURES_LEAD_FILTER_STRATEGIES,\n',
)
replace_once(
    "src/predict_bot/server.py",
    '            if strategy in CONTINUOUS_CALIBRATION_STRATEGIES:\n                diagnostics.update(\n',
    '''            if loss_streak_guard_decision is not None:
                diagnostics.update(
                    {
                        "loss_streak_guard": loss_streak_guard_decision.as_dict(),
                        "source_strategy": source_strategy,
                        "official_history_only": True,
                        "current_market_excluded_from_history": True,
                        "dependency_rule": (
                            f"causal_prior_{source_strategy}_settlements_only"
                        ),
                    }
                )
            if strategy in CONTINUOUS_CALIBRATION_STRATEGIES:
                diagnostics.update(
''',
)
replace_once(
    "src/predict_bot/server.py",
    '                        *CONTINUOUS_CALIBRATION_STRATEGIES,\n                        *FUTURES_LEAD_FILTER_STRATEGIES,\n',
    '                        *LOSS_STREAK_GUARD_STRATEGIES,\n                        *CONTINUOUS_CALIBRATION_STRATEGIES,\n                        *FUTURES_LEAD_FILTER_STRATEGIES,\n',
)
replace_once(
    "src/predict_bot/server.py",
    '            if strategy in CONTINUOUS_CALIBRATION_STRATEGIES:\n                opened_candidate.update(\n',
    '''            if loss_streak_guard_decision is not None:
                opened_candidate.update(
                    {
                        "source_strategy": source_strategy,
                        "loss_streak_guard": loss_streak_guard_decision.as_dict(),
                        "stake_multiplier": (
                            loss_streak_guard_decision.stake_multiplier
                        ),
                    }
                )
            if strategy in CONTINUOUS_CALIBRATION_STRATEGIES:
                opened_candidate.update(
''',
)
replace_once(
    "src/predict_bot/server.py",
    '        drawdown_market_history=STORE.drawdown_control_market_history,\n',
    '        drawdown_market_history=STORE.drawdown_control_market_history,\n        loss_streak_guard_history=STORE.loss_streak_guard_history,\n',
)

# Live executor: persisted per-slot setting, causal paper replay, fail-closed blocking and half stake.
replace_once(
    "src/predict_bot/live_trading.py",
    'from .drawdown_control import MarketRegimeDrawdownController\n',
    'from .drawdown_control import MarketRegimeDrawdownController\nfrom .loss_streak_guard import evaluate_loss_streak_guard\n',
)
replace_once(
    "src/predict_bot/live_trading.py",
    '        "strategyLossCooldownEnabled": [False],\n',
    '        "strategyLossCooldownEnabled": [False],\n        "strategyLossStreakGuardEnabled": [False],\n',
)
replace_once(
    "src/predict_bot/live_trading.py",
    '    raw_reliability_tags = candidate.get("reliabilityGateTags")\n',
    '''    raw_loss_streak_guard_enabled = candidate.get(
        "strategyLossStreakGuardEnabled"
    )
    if "strategyLossStreakGuardEnabled" in values:
        raw_loss_streak_guard_enabled = values[
            "strategyLossStreakGuardEnabled"
        ]
    if isinstance(raw_loss_streak_guard_enabled, str):
        try:
            raw_loss_streak_guard_enabled = json.loads(
                raw_loss_streak_guard_enabled
            )
        except json.JSONDecodeError:
            raw_loss_streak_guard_enabled = None
    if not isinstance(raw_loss_streak_guard_enabled, list):
        raw_loss_streak_guard_enabled = [False] * len(strategies)
    strategy_loss_streak_guard_enabled = [
        _boolean(value)
        for value in (
            raw_loss_streak_guard_enabled + [False] * len(strategies)
        )[:len(strategies)]
    ]
    raw_reliability_tags = candidate.get("reliabilityGateTags")
''',
)
replace_once(
    "src/predict_bot/live_trading.py",
    '        "strategyLossCooldownEnabled": strategy_loss_cooldown_enabled,\n',
    '        "strategyLossCooldownEnabled": strategy_loss_cooldown_enabled,\n        "strategyLossStreakGuardEnabled": strategy_loss_streak_guard_enabled,\n',
)
replace_all(
    "src/predict_bot/live_trading.py",
    '            "strategyLossCooldownEnabled",\n',
    '            "strategyLossCooldownEnabled",\n            "strategyLossStreakGuardEnabled",\n',
    minimum=3,
)
replace_all(
    "src/predict_bot/live_trading.py",
    '                    "strategyLossCooldownEnabled",\n',
    '                    "strategyLossCooldownEnabled",\n                    "strategyLossStreakGuardEnabled",\n',
    minimum=1,
)
replace_all(
    "src/predict_bot/live_trading.py",
    '                                "strategyLossCooldownEnabled",\n',
    '                                "strategyLossCooldownEnabled",\n                                "strategyLossStreakGuardEnabled",\n',
    minimum=1,
)
replace_once(
    "src/predict_bot/live_trading.py",
    '        drawdown_market_history: Callable[\n            [int, int], list[dict[str, Any]]\n        ] | None = None,\n',
    '        drawdown_market_history: Callable[\n            [int, int], list[dict[str, Any]]\n        ] | None = None,\n        loss_streak_guard_history: Callable[\n            [str, int, int], list[dict[str, Any]]\n        ] | None = None,\n',
)
replace_once(
    "src/predict_bot/live_trading.py",
    '        self.drawdown_market_history = drawdown_market_history\n',
    '        self.drawdown_market_history = drawdown_market_history\n        self.loss_streak_guard_history = loss_streak_guard_history\n',
)
replace_once(
    "src/predict_bot/live_trading.py",
    '    def _loss_cooldown_is_safe(\n',
    '''    @staticmethod
    def _strategy_loss_streak_guard_enabled(
        rules: dict[str, Any], strategy: str
    ) -> bool:
        if strategy.startswith("PAIR_ARB_"):
            return False
        try:
            index = list(rules["strategies"]).index(strategy)
            return bool(rules["strategyLossStreakGuardEnabled"][index])
        except (KeyError, IndexError, TypeError, ValueError):
            return False

    def _loss_streak_guard_decision(
        self, strategy: str, before_market_id: int
    ) -> dict[str, Any]:
        callback = self.loss_streak_guard_history
        if callback is None:
            return {
                "mode": "ERROR",
                "allowed": False,
                "stakeMultiplier": 0.0,
                "consecutiveLosses": 0,
                "shadowSamples": 0,
                "shadowWindowPnl": None,
                "probationRemaining": 0,
                "historySamples": 0,
                "reason": "paper settlement history callback is unavailable",
            }
        try:
            history = callback(str(strategy).upper(), int(before_market_id), 1000)
            return evaluate_loss_streak_guard(history).as_dict()
        except Exception as exc:
            return {
                "mode": "ERROR",
                "allowed": False,
                "stakeMultiplier": 0.0,
                "consecutiveLosses": 0,
                "shadowSamples": 0,
                "shadowWindowPnl": None,
                "probationRemaining": 0,
                "historySamples": 0,
                "reason": f"paper settlement history failed: {str(exc)[:240]}",
            }

    def _loss_cooldown_is_safe(
''',
)
replace_once(
    "src/predict_bot/live_trading.py",
    '        if (\n            not is_confirmation_add\n            and self._strategy_loss_cooldown_enabled(rules, selected_strategy)\n        ):\n',
    '''        if (
            not is_confirmation_add
            and self._strategy_loss_streak_guard_enabled(
                rules, selected_strategy
            )
        ):
            loss_streak_decision = self._loss_streak_guard_decision(
                selected_strategy, market_id
            )
            if loss_streak_decision.get("allowed") is not True:
                self._record_blocked_signal(
                    signal,
                    "SKIPPED_THREE_LOSS_GUARD",
                    str(loss_streak_decision.get("reason") or "loss streak guard blocked"),
                    diagnostics=loss_streak_decision,
                )
                return
            multiplier = _decimal(
                loss_streak_decision.get("stakeMultiplier")
            )
            if multiplier is None or not Decimal("0") < multiplier <= Decimal("1"):
                self._record_blocked_signal(
                    signal,
                    "BLOCKED_THREE_LOSS_GUARD_ERROR",
                    "loss streak guard returned an invalid stake multiplier",
                    diagnostics=loss_streak_decision,
                )
                return
            max_stake = (max_stake * multiplier).quantize(
                Decimal("0.000000000000000001"), rounding=ROUND_DOWN
            )
            if max_stake < LIVE_MIN_CONFIGURABLE_STAKE_USDT:
                self._record_blocked_signal(
                    signal,
                    "BLOCKED_THREE_LOSS_GUARD_MIN_STAKE",
                    "loss streak guard reduced stake below the exchange minimum",
                    diagnostics=loss_streak_decision,
                )
                return
            amount_in_wei = _stake_amount_wei(max_stake)
            signal["loss_streak_guard"] = loss_streak_decision

        if (
            not is_confirmation_add
            and self._strategy_loss_cooldown_enabled(rules, selected_strategy)
        ):
''',
)
replace_once(
    "src/predict_bot/live_trading.py",
    '                "strategyLossCooldownStates": [\n',
    '''                "strategyLossStreakGuardStates": [
                    {
                        **self._loss_streak_guard_decision(
                            strategy, 2**63 - 1
                        ),
                        "strategy": strategy,
                        "enabled": self._strategy_loss_streak_guard_enabled(
                            rules, strategy
                        ),
                    }
                    for strategy in rules["strategies"]
                ],
                "strategyLossCooldownStates": [
''',
)

# Dashboard types/defaults, card and per-slot persistent control.
replace_once(
    "dashboard/app/page.tsx",
    '  | "R_MICROPRICE" | "R_MICROPRICE_REVERSE" |',
    '  | "R_MICROPRICE" | "R_MICROPRICE_LOSS_STREAK_GUARD" | "R_MICROPRICE_REVERSE" |',
)
replace_once(
    "dashboard/app/page.tsx",
    '  strategyLossCooldownEnabled: boolean[];\n  reliabilityGateTags:',
    '  strategyLossCooldownEnabled: boolean[];\n  strategyLossStreakGuardEnabled: boolean[];\n  reliabilityGateTags:',
)
replace_once(
    "dashboard/app/page.tsx",
    '  strategyLossCooldownEnabled: [false],\n  reliabilityGateTags:',
    '  strategyLossCooldownEnabled: [false],\n  strategyLossStreakGuardEnabled: [false],\n  reliabilityGateTags:',
)
replace_once(
    "dashboard/app/page.tsx",
    '  strategyLossCooldownStates?: Array<{\n',
    '''  strategyLossStreakGuardStates?: Array<{
    strategy?: string; enabled?: boolean; mode?: string; allowed?: boolean;
    stakeMultiplier?: number; consecutiveLosses?: number; shadowSamples?: number;
    shadowWindowPnl?: number | null; probationRemaining?: number;
    triggerMarketId?: number | null; historySamples?: number; reason?: string;
  }>;
  strategyLossCooldownStates?: Array<{
''',
)
replace_once(
    "dashboard/app/page.tsx",
    '  { id: "R_MICROPRICE", title: "Microprice 深度失衡", rule: "剩餘 180 秒，以 UP／DOWN 第一檔數量失衡差決定方向。", tone: "cyan" },',
    '  { id: "R_MICROPRICE", title: "Microprice 深度失衡", rule: "剩餘 180 秒，以 UP／DOWN 第一檔數量失衡差決定方向。", tone: "cyan" },\n  { id: "R_MICROPRICE_LOSS_STREAK_GUARD", title: "Microprice 三連敗 Guard", rule: "依因果順序重播原始 R_MICROPRICE 結算：2 敗後半倉、3 敗切 Shadow；最近 3 筆 Shadow PnL 合計 > 0 後，以半倉連勝 2 筆才完全恢復。", tone: "rose", shadow: true },',
)
replace_once(
    "dashboard/app/page.tsx",
    '  const updateStrategyLossCooldown = (index: number, enabled: boolean) => {\n    const controls = [...rulesDraft.strategyLossCooldownEnabled];\n    controls[index] = enabled;\n    onRulesUpdate("strategyLossCooldownEnabled", controls);\n  };\n',
    '''  const updateStrategyLossCooldown = (index: number, enabled: boolean) => {
    const controls = [...rulesDraft.strategyLossCooldownEnabled];
    controls[index] = enabled;
    onRulesUpdate("strategyLossCooldownEnabled", controls);
  };
  const updateStrategyLossStreakGuard = (index: number, enabled: boolean) => {
    const controls = [...rulesDraft.strategyLossStreakGuardEnabled];
    controls[index] = enabled;
    onRulesUpdate("strategyLossStreakGuardEnabled", controls);
  };
''',
)
replace_once(
    "dashboard/app/page.tsx",
    '    const reliabilitySummary = `可靠候選 ${rulesDraft.reliabilityGateTags.length ? rulesDraft.reliabilityGateTags.join("＋") : "全部關閉"}`;\n    const summary = `${strategySummary}、${observerSummary}、${drawdownSummary}、${cooldownSummary}、${reliabilitySummary}',
    '    const lossStreakSummary = rulesDraft.strategies.map((strategy, index) => (\n      `${strategy} 三連敗 Guard ${rulesDraft.strategyLossStreakGuardEnabled[index] ? "開啟" : "關閉"}`\n    )).join("、");\n    const reliabilitySummary = `可靠候選 ${rulesDraft.reliabilityGateTags.length ? rulesDraft.reliabilityGateTags.join("＋") : "全部關閉"}`;\n    const summary = `${strategySummary}、${observerSummary}、${drawdownSummary}、${cooldownSummary}、${lossStreakSummary}、${reliabilitySummary}',
)
replace_once(
    "dashboard/app/page.tsx",
    '        <label><span>M0 每小時最低勝率</span>',
    '''        {LIVE_STRATEGY_SLOT_INDEXES.map(index => {
          const selected = rulesDraft.strategies[index];
          const guardState = data?.strategyLossStreakGuardStates?.[index];
          const mode = guardState?.mode ?? "NORMAL";
          const pnl = guardState?.shadowWindowPnl;
          const guardStatus = mode === "SHADOW"
            ? `SHADOW；樣本 ${guardState?.shadowSamples ?? 0}/3；最近視窗 PnL ${pnl == null ? "—" : formatUsd(pnl)}`
            : mode === "PROBATION"
              ? `半倉恢復期；尚需 ${guardState?.probationRemaining ?? 2} 勝`
              : `NORMAL；目前連敗 ${guardState?.consecutiveLosses ?? 0}；倍率 ${guardState?.stakeMultiplier ?? 1}`;
          return <label key={`live-loss-streak-guard-${index}`}><span>策略 {index + 1} 三連敗 Guard</span><select aria-label={`策略 ${index + 1} 是否使用三連敗 Guard`} disabled={!selected} value={rulesDraft.strategyLossStreakGuardEnabled[index] ? "enabled" : "disabled"} onChange={event => updateStrategyLossStreakGuard(index, event.target.value === "enabled")}><option value="disabled">不使用三連敗 Guard</option><option value="enabled">2 敗半倉；3 敗 Shadow；3 筆 PnL 正值恢復</option></select><small>{selected ? `${guardStatus}；狀態由該策略既有紙上官方結算因果重播，重啟後不歸零` : "請先選擇此槽位的實單策略"}</small></label>;
        })}
        <label><span>M0 每小時最低勝率</span>''',
)
replace_once(
    "dashboard/app/page.tsx",
    'state.liveM0W?.rules?.strategyDrawdownControlEnabled, state.liveM0W?.rules?.strategyLossCooldownEnabled]);',
    'state.liveM0W?.rules?.strategyDrawdownControlEnabled, state.liveM0W?.rules?.strategyLossCooldownEnabled, state.liveM0W?.rules?.strategyLossStreakGuardEnabled]);',
)

# Pad the new per-slot array whenever strategy slots are rebuilt. Existing code
# already pads cooldown and drawdown arrays; mirror every matching assignment.
page = read("dashboard/app/page.tsx")
needle = 'strategyLossCooldownEnabled: Array.from({ length: nextStrategies.length }, (_, index) => current.strategyLossCooldownEnabled[index] ?? false),'
if needle in page:
    page = page.replace(
        needle,
        needle + '\n      strategyLossStreakGuardEnabled: Array.from({ length: nextStrategies.length }, (_, index) => current.strategyLossStreakGuardEnabled[index] ?? false),'
    )
else:
    # The current UI uses generic object updates for most fields; the default
    # merge still guarantees the field. Keep a hard assertion so future drift
    # is visible in CI instead of silently dropping the rule.
    if "strategyLossStreakGuardEnabled" not in page:
        raise RuntimeError("dashboard did not receive loss streak guard field")
write("dashboard/app/page.tsx", page)

replace_once(
    "dashboard/tests/rendered-html.test.mjs",
    '  assert.match(page, /R_MICROPRICE/);\n',
    '  assert.match(page, /R_MICROPRICE/);\n  assert.match(page, /R_MICROPRICE_LOSS_STREAK_GUARD/);\n  assert.match(page, /三連敗 Guard/);\n',
)

# Documentation: clarify the persisted rule and causal recovery source.
replace_once(
    "README.md",
    '- `POST /api/live-rules`：更新持久化正式規則（策略、每策略資金上限、M0 時段勝率／一勝一敗率門檻、可靠性候選標籤）\n',
    '- `POST /api/live-rules`：更新持久化正式規則（策略、每策略資金上限、M0 時段勝率／一勝一敗率門檻、回撤控制、兩連敗冷卻、三連敗 Shadow Guard、可靠性候選標籤）\n',
)

print("loss streak guard patch applied")
