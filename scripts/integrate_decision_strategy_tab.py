from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "dashboard" / "app" / "page.tsx"
RENDER_TEST = ROOT / "dashboard" / "tests" / "rendered-html.test.mjs"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"missing integration anchor: {label}")
    return text.replace(old, new, 1)


def patch_page(text: str) -> str:
    text = replace_once(
        text,
        'import StrongTrendGuardPanel from "./strong-trend-guard-panel";\n',
        'import StrongTrendGuardPanel from "./strong-trend-guard-panel";\n'
        'import DecisionStrategyTestPanel from "./decision-strategy-test-panel";\n',
        "decision panel import",
    )
    text = replace_once(
        text,
        '| "R_CONSENSUS" | "R_CONFIRM_ADD_10";',
        '| "R_CONSENSUS" | "R_CONFIRM_ADD_10" | "R_DECISION_RANK1" | "R_DECISION_RANK2";',
        "strategy id union",
    )
    text = replace_once(
        text,
        'type StrategyView = "live-m0w" | "research" | "microprice-strategies" | "calibrated-confirmation" | "strong-trend-guard" | "reliability-shadow" | "lead-observer" | "m-series" | "pair-arb" | "legacy" | "paused";',
        'type StrategyView = "live-m0w" | "research" | "microprice-strategies" | "calibrated-confirmation" | "strong-trend-guard" | "decision-strategy" | "reliability-shadow" | "lead-observer" | "m-series" | "pair-arb" | "legacy" | "paused";',
        "strategy view union",
    )
    text = replace_once(
        text,
        '  liveM0W?: LiveM0WState | null;\n};',
        '  liveM0W?: LiveM0WState | null;\n'
        '  decisionStrategyTest?: any;\n'
        '};',
        "state decision payload",
    )
    text = replace_once(
        text,
        '    R_CONSENSUS: { ...EMPTY_SUMMARY },\n    R_CONFIRM_ADD_10: { ...EMPTY_SUMMARY },',
        '    R_CONSENSUS: { ...EMPTY_SUMMARY },\n'
        '    R_CONFIRM_ADD_10: { ...EMPTY_SUMMARY },\n'
        '    R_DECISION_RANK1: { ...EMPTY_SUMMARY },\n'
        '    R_DECISION_RANK2: { ...EMPTY_SUMMARY },',
        "default decision summaries",
    )
    text = replace_once(
        text,
        '    const validViews: StrategyView[] = ["live-m0w", "research", "reliability-shadow", "lead-observer", "m-series", "pair-arb", "legacy", "paused"];',
        '    const validViews: StrategyView[] = ["live-m0w", "research", "microprice-strategies", "calibrated-confirmation", "strong-trend-guard", "decision-strategy", "reliability-shadow", "lead-observer", "m-series", "pair-arb", "legacy", "paused"];',
        "session strategy views",
    )
    text = replace_once(
        text,
        '          liveM0W: next.liveM0W\n',
        '          decisionStrategyTest: next.decisionStrategyTest,\n'
        '          liveM0W: next.liveM0W\n',
        "statistics decision payload",
    )
    text = replace_once(
        text,
        '  R_OFI_EVENT_CUM: "研究實單 · 累積事件級 OFI",\n};',
        '  R_OFI_EVENT_CUM: "研究實單 · 累積事件級 OFI",\n'
        '  R_DECISION_RANK1: "決策策略 · Rank 1 效用加權共識",\n'
        '  R_DECISION_RANK2: "決策策略 · Rank 2 同情境冠軍",\n'
        '};',
        "live strategy labels",
    )
    text = replace_once(
        text,
        '  const isNonConfigView = isLiveView || isReliabilityView || strategyView === "microprice-strategies" || strategyView === "calibrated-confirmation" || strategyView === "strong-trend-guard";',
        '  const isNonConfigView = isLiveView || isReliabilityView || strategyView === "microprice-strategies" || strategyView === "calibrated-confirmation" || strategyView === "strong-trend-guard" || strategyView === "decision-strategy";',
        "non-config decision view",
    )
    text = replace_once(
        text,
        '    if (strategyView === "strong-trend-guard") return trade.strategy.startsWith("R_STRONG_TREND_GUARD_");\n',
        '    if (strategyView === "strong-trend-guard") return trade.strategy.startsWith("R_STRONG_TREND_GUARD_");\n'
        '    if (strategyView === "decision-strategy") return ["R_DECISION_RANK1", "R_DECISION_RANK2"].includes(String(trade.strategy));\n',
        "decision trade filter",
    )
    text = replace_once(
        text,
        'strategyView === "strong-trend-guard" ? "STRONG OPPOSING TREND · EIGHT SHADOWS" : strategyView === "reliability-shadow"',
        'strategyView === "strong-trend-guard" ? "STRONG OPPOSING TREND · EIGHT SHADOWS" : strategyView === "decision-strategy" ? "DECISION CONTROLLERS · RANK 1 + RANK 2" : strategyView === "reliability-shadow"',
        "decision heading eyebrow",
    )
    text = replace_once(
        text,
        'strategyView === "strong-trend-guard" ? "逆強趨勢阻擋測試" : strategyView === "reliability-shadow"',
        'strategyView === "strong-trend-guard" ? "逆強趨勢阻擋測試" : strategyView === "decision-strategy" ? "決策策略測試" : strategyView === "reliability-shadow"',
        "decision heading title",
    )

    strong_trend_tab = (
        '          <button type="button" role="tab" id="strong-trend-guard-tab" '
        'aria-controls="strong-trend-guard-panel" aria-selected={strategyView === "strong-trend-guard"} '
        'className={strategyView === "strong-trend-guard" ? "active shadow-tag" : "shadow-tag"} '
        'onClick={() => setStrategyView("strong-trend-guard")}><strong>逆強趨勢阻擋</strong>'
        '<span>8 組來源策略 · Forward Paper A/B</span></button>'
    )
    decision_tab = (
        '          <button type="button" role="tab" id="decision-strategy-tab" '
        'aria-controls="decision-strategy-panel" aria-selected={strategyView === "decision-strategy"} '
        'className={strategyView === "decision-strategy" ? "active shadow-tag" : "shadow-tag"} '
        'onClick={() => setStrategyView("decision-strategy")}><strong>決策策略</strong>'
        '<span>Rank 1＋Rank 2 · 原生 Forward Paper</span></button>'
    )
    text = replace_once(
        text,
        strong_trend_tab,
        f"{strong_trend_tab}\n{decision_tab}",
        "decision strategy tab button",
    )

    strong_trend_branch = (
        'strategyView === "strong-trend-guard" ? '
        '<StrongTrendGuardPanel experiment={(state.researchForward as any)?.strongTrendGuardExperiment} /> : '
        'strategyView === "reliability-shadow"'
    )
    decision_branch = (
        'strategyView === "strong-trend-guard" ? '
        '<StrongTrendGuardPanel experiment={(state.researchForward as any)?.strongTrendGuardExperiment} /> : '
        'strategyView === "decision-strategy" ? '
        '<DecisionStrategyTestPanel payload={state} onReset={resetStrategy} resetStates={resetStates} /> : '
        'strategyView === "reliability-shadow"'
    )
    return replace_once(
        text,
        strong_trend_branch,
        decision_branch,
        "decision strategy tabpanel branch",
    )


def patch_render_test(text: str) -> str:
    """Update regression assertions without ever blocking dashboard startup."""
    strategy_assertion = (
        '  assert.match(page, /type StrategyView = .*"decision-strategy".*;/);'
    )
    if strategy_assertion not in text:
        lines = text.splitlines(keepends=True)
        for index, line in enumerate(lines):
            if "assert.match(page, /type StrategyView =" in line:
                newline = "\r\n" if line.endswith("\r\n") else "\n"
                lines[index] = strategy_assertion + newline
                text = "".join(lines)
                break

    decision_assertions = (
        '  assert.match(page, /id="decision-strategy-tab"/);\n'
        '  assert.match(page, /aria-controls="decision-strategy-panel"/);\n'
        '  assert.match(page, /DecisionStrategyTestPanel payload=\\{state\\}/);\n'
        '  assert.match(page, /R_DECISION_RANK1/);\n'
        '  assert.match(page, /R_DECISION_RANK2/);\n'
    )
    if 'assert.match(page, /id="decision-strategy-tab"/);' not in text:
        anchor = '  assert.match(page, /id="reliability-shadow-tab"/);'
        if anchor in text:
            text = text.replace(anchor, decision_assertions + anchor, 1)
    return text


def main() -> None:
    page_before = PAGE.read_text(encoding="utf-8")
    page_after = patch_page(page_before)
    if page_after != page_before:
        PAGE.write_text(page_after, encoding="utf-8")

    if RENDER_TEST.exists():
        test_before = RENDER_TEST.read_text(encoding="utf-8")
        test_after = patch_render_test(test_before)
        if test_after != test_before:
            RENDER_TEST.write_text(test_after, encoding="utf-8")


if __name__ == "__main__":
    main()
