from __future__ import annotations

import argparse
import difflib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "dashboard" / "app" / "page.tsx"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"native decision tab anchor {label!r} expected exactly once, found {count}"
        )
    return text.replace(old, new, 1)


def patched_page(original: str) -> str:
    text = original
    text = replace_once(
        text,
        'import StrongTrendGuardPanel from "./strong-trend-guard-panel";\n',
        'import StrongTrendGuardPanel from "./strong-trend-guard-panel";\n'
        'import DecisionStrategyPanel from "./decision-strategy-panel";\n',
        "component import",
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
        "default summaries",
    )
    text = replace_once(
        text,
        '    const validViews: StrategyView[] = ["live-m0w", "research", "reliability-shadow", "lead-observer", "m-series", "pair-arb", "legacy", "paused"];',
        '    const validViews: StrategyView[] = ["live-m0w", "research", "microprice-strategies", "calibrated-confirmation", "strong-trend-guard", "decision-strategy", "reliability-shadow", "lead-observer", "m-series", "pair-arb", "legacy", "paused"];',
        "session view allowlist",
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
        "non-config view",
    )
    text = replace_once(
        text,
        '    if (strategyView === "strong-trend-guard") return trade.strategy.startsWith("R_STRONG_TREND_GUARD_");\n',
        '    if (strategyView === "strong-trend-guard") return trade.strategy.startsWith("R_STRONG_TREND_GUARD_");\n'
        '    if (strategyView === "decision-strategy") return ["R_DECISION_RANK1", "R_DECISION_RANK2"].includes(String(trade.strategy));\n',
        "trade filter",
    )
    text = replace_once(
        text,
        'strategyView === "strong-trend-guard" ? "STRONG OPPOSING TREND · EIGHT SHADOWS" : strategyView === "reliability-shadow"',
        'strategyView === "strong-trend-guard" ? "STRONG OPPOSING TREND · EIGHT SHADOWS" : strategyView === "decision-strategy" ? "DECISION CONTROLLERS · RANK 1 + RANK 2" : strategyView === "reliability-shadow"',
        "heading eyebrow",
    )
    text = replace_once(
        text,
        'strategyView === "strong-trend-guard" ? "逆強趨勢阻擋測試" : strategyView === "reliability-shadow"',
        'strategyView === "strong-trend-guard" ? "逆強趨勢阻擋測試" : strategyView === "decision-strategy" ? "決策策略測試" : strategyView === "reliability-shadow"',
        "heading title",
    )

    strong_tab = (
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
        strong_tab,
        f"{strong_tab}\n{decision_tab}",
        "native tab button",
    )

    strong_branch = (
        'strategyView === "strong-trend-guard" ? '
        '<StrongTrendGuardPanel experiment={(state.researchForward as any)?.strongTrendGuardExperiment} /> : '
        'strategyView === "reliability-shadow"'
    )
    decision_branch = (
        'strategyView === "strong-trend-guard" ? '
        '<StrongTrendGuardPanel experiment={(state.researchForward as any)?.strongTrendGuardExperiment} /> : '
        'strategyView === "decision-strategy" ? '
        '<DecisionStrategyPanel experiment={(state.researchForward as any)?.decisionStrategyExperiment} /> : '
        'strategyView === "reliability-shadow"'
    )
    return replace_once(
        text,
        strong_branch,
        decision_branch,
        "native tabpanel branch",
    )


def validate_diff(original: str, patched: str) -> None:
    required = (
        'import DecisionStrategyPanel from "./decision-strategy-panel";',
        'id="decision-strategy-tab"',
        'aria-controls="decision-strategy-panel"',
        '<DecisionStrategyPanel experiment={(state.researchForward as any)?.decisionStrategyExperiment} />',
        'R_DECISION_RANK1',
        'R_DECISION_RANK2',
    )
    for token in required:
        if token not in patched:
            raise RuntimeError(f"native decision tab validation missing {token!r}")

    forbidden_changed_tokens = (
        "loadStatistics",
        'fetchDashboardJson<State>("/api/state"',
        "setStatisticsDown",
        "STATISTICS_REFRESH_MS",
        "REALTIME_REFRESH_MS",
    )
    changed_lines = [
        line
        for line in difflib.unified_diff(
            original.splitlines(),
            patched.splitlines(),
            lineterm="",
        )
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    for token in forbidden_changed_tokens:
        if any(token in line for line in changed_lines):
            raise RuntimeError(
                f"native decision tab patch illegally touches data-loading token {token!r}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply/check the native Rank 1/Rank 2 main-dashboard tab source patch."
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    original = PAGE.read_text(encoding="utf-8")
    patched = patched_page(original)
    validate_diff(original, patched)

    if args.check:
        if original != patched:
            raise SystemExit(
                "decision strategy native tab is not applied; run this script without --check"
            )
        print("decision strategy native tab source is present")
        return

    if original == patched:
        print("decision strategy native tab source is already present")
        return

    PAGE.write_text(patched, encoding="utf-8")
    print("applied native decision strategy tab to dashboard/app/page.tsx")


if __name__ == "__main__":
    main()
