from __future__ import annotations

from pathlib import Path


PAGE = Path("dashboard/app/page.tsx")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"missing page.tsx integration anchor: {label}")
    return text.replace(old, new, 1)


def main() -> None:
    text = PAGE.read_text(encoding="utf-8")

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
        '    R_CONSENSUS: { ...EMPTY_SUMMARY },\n    R_CONFIRM_ADD_10: { ...EMPTY_SUMMARY },',
        '    R_CONSENSUS: { ...EMPTY_SUMMARY },\n'
        '    R_CONFIRM_ADD_10: { ...EMPTY_SUMMARY },\n'
        '    R_DECISION_RANK1: { ...EMPTY_SUMMARY },\n'
        '    R_DECISION_RANK2: { ...EMPTY_SUMMARY },',
        "default decision summaries",
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
    text = replace_once(
        text,
        strong_trend_branch,
        decision_branch,
        "decision strategy tabpanel branch",
    )

    PAGE.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
