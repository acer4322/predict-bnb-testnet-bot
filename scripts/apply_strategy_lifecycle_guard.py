from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "src" / "predict_bot" / "live_trading.py"
PAGE = ROOT / "dashboard" / "app" / "page.tsx"
TEST = ROOT / "tests" / "test_live_trading.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one regex match, found {count}")
    return updated


def patch_live(text: str) -> str:
    if "build_strategy_lifecycle_summary" not in text:
        text = replace_once(
            text,
            "from .drawdown_control import MarketRegimeDrawdownController\n",
            "from .drawdown_control import MarketRegimeDrawdownController\n"
            "from .strategy_lifecycle_guard import build_strategy_lifecycle_summary\n",
            "lifecycle import",
        )

    if "def strategy_lifecycle_summary(" not in text:
        marker = "    def summary(self) -> dict[str, Any]:\n"
        method = '''    def strategy_lifecycle_summary(\n        self,\n        supported_strategies: tuple[str, ...] | list[str],\n        active_strategies: tuple[str, ...] | list[str] = (),\n    ) -> dict[str, Any]:\n        \"\"\"Return advisory lifecycle evidence for every live whitelist strategy.\"\"\"\n        with self.lock:\n            rows = [\n                dict(row)\n                for row in self.db.execute(\n                    \"\"\"SELECT o.strategy, o.market_id, s.cost_usdt,\n                              s.pnl_usdt, s.settled_at\n                         FROM live_orders AS o\n                         JOIN live_strategy_settlements AS s\n                           ON s.order_local_id=o.id\n                        ORDER BY s.settled_at ASC, o.id ASC\"\"\"\n                ).fetchall()\n            ]\n        return build_strategy_lifecycle_summary(\n            rows,\n            tuple(str(value) for value in supported_strategies),\n            tuple(str(value) for value in active_strategies),\n        )\n\n'''
        text = replace_once(text, marker, method + marker, "ledger lifecycle method")

    # The obsolete M0 Taipei-hour statistics must no longer sit on the real-order hot path.
    text = regex_once(
        text,
        r'''        hourly_guard = \(\n            \{"blocked": False\}\n            if selected_strategy\.startswith\("PAIR_ARB_"\) or is_confirmation_add\n            else self\._evaluate_cached_hourly_guard\(\n                at=str\(signal\.get\("signal_timestamp"\) or utc_iso\(\)\)\n            \)\n        \)\n        if hourly_guard\.get\("blocked"\) is True:\n            reasons = hourly_guard\.get\("reasons"\) or \["時段條件未通過"\]\n            message = \(\n                f"M0 台北時段閘門 \{hourly_guard\.get\('label'\) or ''\} 暫停新單："\n                \+ "；"\.join\(str\(reason\) for reason in reasons\)\n            \)\n            self\._record_blocked_signal\(\n                signal, "SKIPPED_M0_HOURLY_GUARD", message\n            \)\n            return\n\n''',
        "",
        "remove hourly hot-path blocker",
    )

    # No startup/maintenance refresh is needed once the old gate is advisory-dead.
    text = regex_once(
        text,
        r'''        if self\.m0_hourly_performance is not None:\n            self\._evaluate_hourly_guard\(\)\n            self\.next_hourly_guard_refresh = \(\n                time\.monotonic\(\) \+ LIVE_HOURLY_GUARD_REFRESH_SECONDS\n            \)\n''',
        "",
        "remove hourly startup refresh",
    )
    text = regex_once(
        text,
        r'''            if now >= self\.next_hourly_guard_refresh:\n                self\.next_hourly_guard_refresh = \(\n                    now \+ LIVE_HOURLY_GUARD_REFRESH_SECONDS\n                \)\n                self\._evaluate_hourly_guard\(\)\n''',
        "",
        "remove hourly maintenance refresh",
    )

    # Keep the state() parameter for old server/tests, but it is deliberately ignored.
    text = regex_once(
        text,
        r'''    def state\(\n        self,\n        m0_hourly_performance: dict\[str, Any\] \| None = None,\n        \*,\n        include_ledger: bool = True,\n    \) -> dict\[str, Any\]:\n        if m0_hourly_performance is not None:\n            self\._evaluate_hourly_guard\(performance=m0_hourly_performance\)\n        elif self\.m0_hourly_performance is not None:\n            with self\.lock:\n                guard_waiting = self\.hourly_guard_state\.get\("status"\) == "WAITING"\n            if guard_waiting:\n                self\._evaluate_hourly_guard\(\)\n        with self\.lock:\n''',
        '''    def state(\n        self,\n        m0_hourly_performance: dict[str, Any] | None = None,\n        *,\n        include_ledger: bool = True,\n    ) -> dict[str, Any]:\n        # Backward-compatible parameter only. The obsolete M0 hourly gate no\n        # longer evaluates or affects real orders.\n        _ = m0_hourly_performance\n        with self.lock:\n''',
        "disable hourly state evaluation",
    )

    text = replace_once(
        text,
        '                "hourlyGuard": dict(self.hourly_guard_state),\n',
        "",
        "remove hourly state payload",
    )

    text = replace_once(
        text,
        '            "overallPerformance": self.ledger.strategy_performance(),\n',
        '            "overallPerformance": self.ledger.strategy_performance(),\n'
        '            "strategyLifecycle": self.ledger.strategy_lifecycle_summary(\n'
        '                tuple(LIVE_SUPPORTED_STRATEGIES),\n'
        '                tuple(str(value) for value in rules["strategies"]),\n'
        '            ),\n',
        "add lifecycle state payload",
    )

    text = regex_once(
        text,
        r'''                "hourlyGuard": \{\n                    "timezone": "Asia/Taipei",\n                    "statisticsStrategy": "M0",\n                    "minWinRatePct": float\(\n                        rules\["minHourlyWinRatePct"\]\n                    \),\n                    "maxWinThenLossRatePct": float\(\n                        rules\["maxHourlyWinThenLossRatePct"\]\n                    \),\n                    "exactBoundaryAllowed": True,\n                \},\n''',
        '''                "strategyLifecycle": {\n                    "version": "STRATEGY_LIFECYCLE_GUARD_V1",\n                    "advisoryOnly": True,\n                    "automaticBlocking": False,\n                    "automaticStakeChanges": False,\n                    "statisticsBasis": "settled real-money parent-strategy markets",\n                },\n''',
        "replace hourly policy",
    )

    # The persisted hourly numeric fields remain readable for backward compatibility,
    # but the live-rules event must stop implying they still control execution.
    text = regex_once(
        text,
        r'''                f"各策略上限 \{previous\['strategyStakesUsdt'\]\}→"\n                f"\{updated\['strategyStakesUsdt'\]\} USDT；M0 每小時最低勝率 "\n                f"\{previous\['minHourlyWinRatePct'\]:\.8g\}%→"\n                f"\{updated\['minHourlyWinRatePct'\]:\.8g\}%；一勝一敗率上限 "\n                f"\{previous\['maxHourlyWinThenLossRatePct'\]:\.8g\}%→"\n                f"\{updated\['maxHourlyWinThenLossRatePct'\]:\.8g\}%；"\n''',
        '''                f"各策略上限 {previous['strategyStakesUsdt']}→"\n                f"{updated['strategyStakesUsdt']} USDT；"\n''',
        "remove obsolete hourly rule log",
    )

    return text


def patch_page(text: str) -> str:
    import_line = 'import MicropriceStrategyPanel, { MICROPRICE_STRATEGY_IDS } from "./microprice-strategy-panel";\n'
    lifecycle_import = 'import StrategyLifecycleGuardPanel, { type StrategyLifecycleState } from "./strategy-lifecycle-guard";\n'
    if lifecycle_import not in text:
        text = replace_once(text, import_line, import_line + lifecycle_import, "lifecycle component import")

    text = regex_once(
        text,
        r'''  hourlyGuard\?: \{\n    status\?: string; blocked\?: boolean; timezone\?: string; utcOffset\?: string;\n    hour\?: number; label\?: string; evaluatedAt\?: string;\n    settledTrades\?: number; wins\?: number; losses\?: number;\n    winRatePct\?: number \| null; winThenLossCount\?: number;\n    winThenLossOpportunities\?: number; winThenLossRatePct\?: number \| null;\n    minWinRatePct\?: number; maxWinThenLossRatePct\?: number;\n    reasons\?: string\[\]; resetAt\?: string \| null;\n  \};\n''',
        '  strategyLifecycle?: StrategyLifecycleState;\n',
        "replace hourly state type",
    )

    # Remove obsolete UI controls only; backend keeps the fields as inert legacy settings.
    text = regex_once(
        text,
        r'''        <label><span>M0 [^<]*勝率[^<]*</span><div className="live-rule-number"><input[^>]*value=\{rulesDraft\.minHourlyWinRatePct\}[^>]*/><b>%</b></div><small>.*?</small></label>\n''',
        "",
        "remove M0 win-rate control",
    )
    text = regex_once(
        text,
        r'''        <label><span>M0 一勝一敗率上限</span><div className="live-rule-number"><input[^>]*value=\{rulesDraft\.maxHourlyWinThenLossRatePct\}[^>]*/><b>%</b></div><small>.*?</small></label>\n''',
        "",
        "remove M0 win-loss control",
    )

    text = replace_once(
        text,
        '  const hourlyGuard = data?.hourlyGuard ?? {};\n',
        "",
        "remove hourly local data",
    )
    text = regex_once(
        text,
        r'''  const hourlyGuardStatus = String\(hourlyGuard\.status \?\? "WAITING"\)\.toUpperCase\(\);\n  const hourlyBlocked = hourlyGuard\.blocked === true;\n  const hourlyGuardClass = hourlyBlocked \? "blocked" : hourlyGuardStatus === "ALLOW" \? "allow" : "waiting";\n  const hourlyReasons = Array\.isArray\(hourlyGuard\.reasons\) \? hourlyGuard\.reasons : \[\];\n''',
        "",
        "remove hourly view state",
    )

    text = regex_once(
        text,
        r'''    <section className=\{`live-hourly-guard \$\{hourlyGuardClass\}`\} aria-label="M0 每小時實單閘門">.*?    </section>\n\n    <section className="live-policy-grid"''',
        '    <StrategyLifecycleGuardPanel data={data?.strategyLifecycle} />\n\n    <section className="live-policy-grid"',
        "replace native hourly panel",
    )

    return text


def patch_test(text: str) -> str:
    replacement = '''def test_obsolete_hourly_guard_no_longer_blocks_real_order_hot_path(tmp_path: Path):\n    client = FakeTradingClient()\n    live = engine(\n        tmp_path,\n        client,\n        hourly_provider=lambda: m0_hourly_performance(\n            win_rate_pct=0.0,\n            win_then_loss_rate_pct=100.0,\n        ),\n    )\n    force_legacy_m0w_rules_for_test(live)\n\n    live.process_signal(signal())\n\n    assert len(client.quote_calls) == 1\n    assert len(client.place_calls) == 1\n    state = live.state()\n    assert "hourlyGuard" not in state\n    assert state["strategyLifecycle"]["automaticBlocking"] is False\n    assert state["strategyLifecycle"]["supportedStrategies"] == len(\n        LIVE_SUPPORTED_STRATEGIES\n    )\n\n\ndef test_strategy_lifecycle_state_marks_current_whitelist_selection_active(tmp_path: Path):\n    client = FakeTradingClient()\n    live = engine(tmp_path, client, hourly_provider=lambda: m0_hourly_performance())\n\n    state = live.state()\n    lifecycle = state["strategyLifecycle"]\n    rows = {item["strategy"]: item for item in lifecycle["strategies"]}\n\n    assert lifecycle["advisoryOnly"] is True\n    assert lifecycle["automaticStakeChanges"] is False\n    assert set(rows) == set(LIVE_SUPPORTED_STRATEGIES)\n    for strategy in state["strategies"]:\n        assert rows[strategy]["active"] is True\n\n\n'''
    return regex_once(
        text,
        r'''def test_hourly_guard_uses_cached_snapshot_on_order_hot_path\(tmp_path: Path\):.*?(?=def test_live_rules_select_strategy_and_change_exact_order_cap)''',
        replacement,
        "replace obsolete hourly tests",
    )


def check(live: str, page: str, test: str) -> None:
    required_live = [
        "def strategy_lifecycle_summary(",
        '"strategyLifecycle": self.ledger.strategy_lifecycle_summary(',
        '"automaticBlocking": False',
        "_ = m0_hourly_performance",
    ]
    forbidden_live = [
        'signal, "SKIPPED_M0_HOURLY_GUARD", message',
        '"hourlyGuard": dict(self.hourly_guard_state)',
    ]
    required_page = [
        "StrategyLifecycleGuardPanel",
        "strategyLifecycle?: StrategyLifecycleState",
        '<StrategyLifecycleGuardPanel data={data?.strategyLifecycle} />',
    ]
    forbidden_page = [
        "M0 HOURLY LIVE GUARD · ASIA/TAIPEI",
        "M0 每小時實單閘門",
        "rulesDraft.minHourlyWinRatePct",
        "rulesDraft.maxHourlyWinThenLossRatePct",
    ]
    required_test = [
        "test_obsolete_hourly_guard_no_longer_blocks_real_order_hot_path",
        "test_strategy_lifecycle_state_marks_current_whitelist_selection_active",
    ]
    for token in required_live:
        if token not in live:
            raise RuntimeError(f"live check missing: {token}")
    for token in forbidden_live:
        if token in live:
            raise RuntimeError(f"live check still contains obsolete token: {token}")
    for token in required_page:
        if token not in page:
            raise RuntimeError(f"page check missing: {token}")
    for token in forbidden_page:
        if token in page:
            raise RuntimeError(f"page check still contains obsolete token: {token}")
    for token in required_test:
        if token not in test:
            raise RuntimeError(f"test check missing: {token}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    live = LIVE.read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")
    test = TEST.read_text(encoding="utf-8")

    if args.check:
        check(live, page, test)
        print("Strategy Lifecycle Guard source patch is present and guarded.")
        return 0

    live = patch_live(live)
    page = patch_page(page)
    test = patch_test(test)
    check(live, page, test)

    LIVE.write_text(live, encoding="utf-8")
    PAGE.write_text(page, encoding="utf-8")
    TEST.write_text(test, encoding="utf-8")
    print("Applied Strategy Lifecycle Guard native source patch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
