from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CURRENT_PROFILES = {
    "R_FUTURES_LEAD": "V2",
    "R_CALIBRATED_VALUE": "V6+DD20",
    "R_MICROPRICE": "V6+DD20",
}
BASELINE_STAKE_USDT = 4.0
ADAPTIVE_STAKES_USDT = (1.0, 2.0, 4.0)
SAME_MARKET_SIDE_CAP_USDT = 8.0
MIN_MODEL_TRADES = 30
BUCKET_PRIOR_TRADES = 12
MODEL_FEATURES = (
    "signal_strength",
    "entry_price",
    "visible_depth_log",
    "book_age_ms",
)


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile requires at least one value")
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _feature_value(trade: dict[str, Any], feature: str) -> float:
    if feature == "signal_strength":
        return abs(float(trade["signal"]))
    if feature == "visible_depth_log":
        return math.log1p(max(0.0, float(trade["visible_ask_size"])))
    return float(trade[feature])


def _bucket(value: float, cuts: list[float]) -> int:
    return sum(value > cut for cut in cuts)


def _unit_pnl(trade: dict[str, Any]) -> float:
    return float(trade["pnl"]) / float(trade["stake"])


def _fit_model(development: list[dict[str, Any]]) -> dict[str, Any]:
    prior_mean = sum(_unit_pnl(trade) for trade in development) / len(development)
    features: dict[str, Any] = {}
    for feature in MODEL_FEATURES:
        values = [_feature_value(trade, feature) for trade in development]
        cuts = [_quantile(values, 1 / 3), _quantile(values, 2 / 3)]
        buckets: dict[int, list[float]] = defaultdict(list)
        for trade in development:
            buckets[_bucket(_feature_value(trade, feature), cuts)].append(
                _unit_pnl(trade)
            )
        estimates = []
        counts = []
        for index in range(3):
            observations = buckets[index]
            counts.append(len(observations))
            estimates.append(
                (
                    sum(observations)
                    + prior_mean * BUCKET_PRIOR_TRADES
                )
                / (len(observations) + BUCKET_PRIOR_TRADES)
            )
        features[feature] = {
            "cuts": cuts,
            "bucketCounts": counts,
            "smoothedUnitPnl": estimates,
        }
    model = {
        "trained": True,
        "developmentTrades": len(development),
        "priorMeanUnitPnl": prior_mean,
        "bucketPriorTrades": BUCKET_PRIOR_TRADES,
        "features": features,
    }
    development_scores = [_score(trade, model) for trade in development]
    model["scoreCuts"] = [
        _quantile(development_scores, 1 / 3),
        _quantile(development_scores, 2 / 3),
    ]
    return model


def _score(trade: dict[str, Any], model: dict[str, Any]) -> float:
    estimates = []
    for feature, settings in model["features"].items():
        index = _bucket(_feature_value(trade, feature), settings["cuts"])
        estimates.append(float(settings["smoothedUnitPnl"][index]))
    return sum(estimates) / len(estimates)


def _planned_stake(
    trade: dict[str, Any], model: dict[str, Any]
) -> tuple[float, str, float | None]:
    if not model.get("trained"):
        return BASELINE_STAKE_USDT, "PRESERVE_UNDER_SAMPLED_ADVANTAGE", None
    score = _score(trade, model)
    low, high = model["scoreCuts"]
    if score <= low:
        return ADAPTIVE_STAKES_USDT[0], "WEAK_ZONE", score
    if score <= high:
        return ADAPTIVE_STAKES_USDT[1], "NORMAL_ZONE", score
    return ADAPTIVE_STAKES_USDT[2], "STRONG_ZONE", score


def _scaled_trade(
    source: dict[str, Any], stake: float, **extra: Any
) -> dict[str, Any]:
    result = {
        "strategy": source["strategy"],
        "split": source["split"],
        "market_id": int(source["market_id"]),
        "observation_id": int(source["observation_id"]),
        "timestamp": source["timestamp"],
        "side": source["side"],
        "winner": source["winner"],
        "entry_price": float(source["entry_price"]),
        "stake": stake,
        "pnl": _unit_pnl(source) * stake,
    }
    result.update(extra)
    return result


def _summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        trades,
        key=lambda trade: (
            int(trade["observation_id"]),
            str(trade["strategy"]),
        ),
    )
    realized = sum(float(trade["pnl"]) for trade in ordered)
    cost = sum(float(trade["stake"]) * 1.02 for trade in ordered)
    gross_profit = sum(max(0.0, float(trade["pnl"])) for trade in ordered)
    gross_loss = -sum(min(0.0, float(trade["pnl"])) for trade in ordered)
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    consecutive_losses = 0
    max_consecutive_losses = 0
    for trade in ordered:
        pnl = float(trade["pnl"])
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        if pnl < 0:
            consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
        else:
            consecutive_losses = 0
    return {
        "trades": len(ordered),
        "wins": sum(float(trade["pnl"]) > 0 for trade in ordered),
        "losses": sum(float(trade["pnl"]) < 0 for trade in ordered),
        "realizedPnl": realized,
        "capitalAtRisk": sum(float(trade["stake"]) for trade in ordered),
        "filledCostWithFee": cost,
        "roi": realized / cost if cost else None,
        "profitFactor": gross_profit / gross_loss if gross_loss else None,
        "maxDrawdown": max_drawdown,
        "maxConsecutiveLosses": max_consecutive_losses,
        "averageStake": (
            sum(float(trade["stake"]) for trade in ordered) / len(ordered)
            if ordered
            else None
        ),
    }


def _difference(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "tradeDelta": after["trades"] - before["trades"],
        "pnlDelta": after["realizedPnl"] - before["realizedPnl"],
        "capitalAtRiskDelta": (
            after["capitalAtRisk"] - before["capitalAtRisk"]
        ),
        "roiDeltaPctPoints": (after["roi"] - before["roi"]) * 100,
        "maxDrawdownDelta": after["maxDrawdown"] - before["maxDrawdown"],
        "maxDrawdownReductionPct": (
            (before["maxDrawdown"] - after["maxDrawdown"])
            / before["maxDrawdown"]
            * 100
            if before["maxDrawdown"]
            else None
        ),
    }


def _select_source_trades(report: dict[str, Any]) -> list[dict[str, Any]]:
    selected = [
        trade
        for trade in report["trades"]
        if CURRENT_PROFILES.get(str(trade["strategy"]))
        == str(trade["observer_version"])
    ]
    return sorted(
        selected,
        key=lambda trade: (
            int(trade["observation_id"]),
            str(trade["strategy"]),
        ),
    )


def _simulate_policy(
    selected: list[dict[str, Any]],
    models: dict[str, dict[str, Any]],
    *,
    adaptive_sizing: bool,
    microprice_cooldown: bool,
    same_market_side_cap: float | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    executed_trades: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    loss_streaks: dict[str, int] = defaultdict(int)
    exposure: dict[tuple[int, str], float] = defaultdict(float)
    for trade in selected:
        strategy = str(trade["strategy"])
        if (
            microprice_cooldown
            and strategy == "R_MICROPRICE"
            and loss_streaks[strategy] >= 2
        ):
            loss_streaks[strategy] = 0
            decisions.append({
                "strategy": strategy,
                "split": trade["split"],
                "market_id": int(trade["market_id"]),
                "observation_id": int(trade["observation_id"]),
                "decision": "SKIP_TWO_LOSS_COOLDOWN",
                "plannedStake": 0.0,
                "appliedStake": 0.0,
            })
            continue

        if adaptive_sizing:
            planned, zone, score = _planned_stake(trade, models[strategy])
        else:
            planned, zone, score = BASELINE_STAKE_USDT, "FIXED_4", None
        applied = planned
        if same_market_side_cap is not None:
            exposure_key = (int(trade["market_id"]), str(trade["side"]))
            remaining = max(0.0, same_market_side_cap - exposure[exposure_key])
            allowed = [
                stake
                for stake in ADAPTIVE_STAKES_USDT
                if stake <= planned and stake <= remaining
            ]
            applied = max(allowed, default=0.0)
        decision = zone if applied == planned else (
            "SKIP_COMBINATION_CAP" if applied == 0 else f"{zone}_CAP_REDUCED"
        )
        decisions.append({
            "strategy": strategy,
            "split": trade["split"],
            "market_id": int(trade["market_id"]),
            "observation_id": int(trade["observation_id"]),
            "decision": decision,
            "score": score,
            "plannedStake": planned,
            "appliedStake": applied,
        })
        if applied == 0:
            continue
        if same_market_side_cap is not None:
            exposure[(int(trade["market_id"]), str(trade["side"]))] += applied
        executed = _scaled_trade(
            trade,
            applied,
            decision=decision,
            advantageScore=score,
        )
        executed_trades.append(executed)
        if microprice_cooldown and strategy == "R_MICROPRICE":
            if float(executed["pnl"]) < 0:
                loss_streaks[strategy] += 1
            else:
                loss_streaks[strategy] = 0
    return executed_trades, decisions


def run(source_path: Path) -> dict[str, Any]:
    source = json.loads(source_path.read_text(encoding="utf-8"))
    selected = _select_source_trades(source)
    models: dict[str, dict[str, Any]] = {}
    for strategy in CURRENT_PROFILES:
        development = [
            trade
            for trade in selected
            if trade["strategy"] == strategy
            and trade["split"] == "development"
        ]
        if len(development) < MIN_MODEL_TRADES:
            models[strategy] = {
                "trained": False,
                "developmentTrades": len(development),
                "minimumRequired": MIN_MODEL_TRADES,
                "policy": "preserve fixed 4 USDT because the development cohort is too small",
            }
        else:
            models[strategy] = _fit_model(development)

    baseline = [
        _scaled_trade(
            trade,
            BASELINE_STAKE_USDT,
            decision="FIXED_BASELINE",
        )
        for trade in selected
    ]

    after, decisions = _simulate_policy(
        selected,
        models,
        adaptive_sizing=True,
        microprice_cooldown=True,
        same_market_side_cap=SAME_MARKET_SIDE_CAP_USDT,
    )

    ablation_configs = {
        "adaptiveSizingOnly": (True, False, None),
        "micropriceCooldownOnly": (False, True, None),
        "combinationCapOnly": (False, False, SAME_MARKET_SIDE_CAP_USDT),
        "cooldownAndCombinationCap": (
            False,
            True,
            SAME_MARKET_SIDE_CAP_USDT,
        ),
        "adaptiveSizingAndCooldown": (True, True, None),
        "fullV1": (True, True, SAME_MARKET_SIDE_CAP_USDT),
    }
    ablations: dict[str, Any] = {}
    for name, (use_sizing, use_cooldown, cap) in ablation_configs.items():
        variant, variant_decisions = _simulate_policy(
            selected,
            models,
            adaptive_sizing=use_sizing,
            microprice_cooldown=use_cooldown,
            same_market_side_cap=cap,
        )
        variant_oos = [
            trade for trade in variant if trade["split"] != "development"
        ]
        baseline_oos = [
            trade for trade in baseline if trade["split"] != "development"
        ]
        variant_summary = _summarize(variant)
        variant_oos_summary = _summarize(variant_oos)
        ablations[name] = {
            "overall": variant_summary,
            "outOfSample": variant_oos_summary,
            "splits": {
                split: _summarize([
                    trade for trade in variant if trade["split"] == split
                ])
                for split in ("development", "validation", "holdout")
            },
            "outOfSampleDifferenceVsBefore": _difference(
                _summarize(baseline_oos), variant_oos_summary
            ),
            "decisionCounts": dict(
                Counter(item["decision"] for item in variant_decisions)
            ),
        }

    split_results: dict[str, Any] = {}
    for split in ("development", "validation", "holdout"):
        before_summary = _summarize(
            [trade for trade in baseline if trade["split"] == split]
        )
        after_summary = _summarize(
            [trade for trade in after if trade["split"] == split]
        )
        split_results[split] = {
            "before": before_summary,
            "after": after_summary,
            "difference": _difference(before_summary, after_summary),
        }
    out_of_sample_before = [
        trade for trade in baseline if trade["split"] != "development"
    ]
    out_of_sample_after = [
        trade for trade in after if trade["split"] != "development"
    ]

    by_strategy: dict[str, Any] = {}
    for strategy in CURRENT_PROFILES:
        before_summary = _summarize(
            [trade for trade in baseline if trade["strategy"] == strategy]
        )
        after_summary = _summarize(
            [trade for trade in after if trade["strategy"] == strategy]
        )
        before_oos = _summarize([
            trade
            for trade in baseline
            if trade["strategy"] == strategy
            and trade["split"] != "development"
        ])
        after_oos = _summarize([
            trade
            for trade in after
            if trade["strategy"] == strategy
            and trade["split"] != "development"
        ])
        by_strategy[strategy] = {
            "overall": {
                "before": before_summary,
                "after": after_summary,
                "difference": _difference(before_summary, after_summary),
            },
            "outOfSample": {
                "before": before_oos,
                "after": after_oos,
                "difference": _difference(before_oos, after_oos),
            },
        }

    before_summary = _summarize(baseline)
    after_summary = _summarize(after)
    oos_before_summary = _summarize(out_of_sample_before)
    oos_after_summary = _summarize(out_of_sample_after)
    cooldown_baseline_comparison = {}
    for scope, before_value, after_value in (
        (
            "overall",
            ablations["micropriceCooldownOnly"]["overall"],
            after_summary,
        ),
        (
            "outOfSample",
            ablations["micropriceCooldownOnly"]["outOfSample"],
            oos_after_summary,
        ),
    ):
        cooldown_baseline_comparison[scope] = {
            "before": before_value,
            "after": after_value,
            "difference": _difference(before_value, after_value),
        }
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceReport": str(source_path.resolve()),
        "sourceWindow": source["window"],
        "dataCoverage": source["dataCoverage"],
        "method": {
            "usesRecordedTradesOrOrders": False,
            "sourceProfiles": CURRENT_PROFILES,
            "beforePolicy": "current gates with fixed 4 USDT per strategy",
            "developmentPolicy": "first 60% markets fit the score; no later outcome changes thresholds",
            "afterPolicy": {
                "featuresKnownAtEntry": MODEL_FEATURES,
                "adaptiveStakesUsdt": ADAPTIVE_STAKES_USDT,
                "minimumDevelopmentTrades": MIN_MODEL_TRADES,
                "micropriceCooldown": "after two executed official losses, skip one candidate and reset; skipped outcome is ignored",
                "sameMarketSameSideCapUsdt": SAME_MARKET_SIDE_CAP_USDT,
            },
            "causal": True,
            "primaryEvidence": "validation plus holdout (last 40% markets)",
        },
        "models": models,
        "ablations": ablations,
        "existingMicropriceCooldownBaselineComparison": (
            cooldown_baseline_comparison
        ),
        "overall": {
            "before": before_summary,
            "after": after_summary,
            "difference": _difference(before_summary, after_summary),
        },
        "outOfSample": {
            "before": oos_before_summary,
            "after": oos_after_summary,
            "difference": _difference(oos_before_summary, oos_after_summary),
        },
        "splits": split_results,
        "byStrategy": by_strategy,
        "decisionCounts": dict(Counter(item["decision"] for item in decisions)),
        "stakeCounts": dict(Counter(str(item["appliedStake"]) for item in decisions)),
        "decisions": decisions,
        "afterTrades": after,
    }


def _fmt(value: float | None, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _markdown(report: dict[str, Any]) -> str:
    overall = report["overall"]
    oos = report["outOfSample"]
    current_oos = report["existingMicropriceCooldownBaselineComparison"][
        "outOfSample"
    ]
    lines = [
        "# 補強缺點、強化優點 V1：七日市場回測",
        "",
        f"- 市場視窗：{report['sourceWindow']['startTaipei']} 至 {report['sourceWindow']['endTaipei']}",
        f"- 市場資料：{report['dataCoverage']['candidateMarkets']:,} 個正式市場、{report['dataCoverage']['observationRows']:,} 筆觀測",
        "- 基準：目前三策略與 Observer／回撤組合，每策略固定 4 USDT",
        "- 企劃後：逐策略 1／2／4 USDT 優勢分級、Microprice 兩敗冷卻、同市場同方向 8 USDT 上限",
        "- 主要判讀：前 60% 建模；後 40% validation + holdout 樣本外驗證",
        "",
        "## 組合前後",
        "",
        "| 區段 | 版本 | 交易 | PnL | 投入本金 | ROI | Profit Factor | 最大回撤 | 最長連敗 | 平均金額 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, section in (("完整七日", overall), ("樣本外後 40%", oos)):
        for version in ("before", "after"):
            value = section[version]
            lines.append(
                f"| {label} | {'企劃前' if version == 'before' else '企劃後'} | "
                f"{value['trades']} | {_fmt(value['realizedPnl'])} | "
                f"{_fmt(value['capitalAtRisk'])} | {_fmt(value['roi'] * 100, 2)}% | "
                f"{_fmt(value['profitFactor'], 3)} | {_fmt(value['maxDrawdown'])} | "
                f"{value['maxConsecutiveLosses']} | {_fmt(value['averageStake'], 2)} |"
            )
    lines += [
        "",
        "## 樣本外差異",
        "",
        f"- PnL：{_fmt(oos['before']['realizedPnl'])} → {_fmt(oos['after']['realizedPnl'])}（{_fmt(oos['difference']['pnlDelta'])}）",
        f"- 投入本金：{_fmt(oos['before']['capitalAtRisk'])} → {_fmt(oos['after']['capitalAtRisk'])}（{_fmt(oos['difference']['capitalAtRiskDelta'])}）",
        f"- ROI：{_fmt(oos['before']['roi'] * 100, 2)}% → {_fmt(oos['after']['roi'] * 100, 2)}%",
        f"- 最大回撤：{_fmt(oos['before']['maxDrawdown'])} → {_fmt(oos['after']['maxDrawdown'])}（降低 {_fmt(oos['difference']['maxDrawdownReductionPct'], 2)}%）",
        "",
        "## 以既有 Microprice 冷卻為起點（樣本外）",
        "",
        f"- PnL：{_fmt(current_oos['before']['realizedPnl'])} → {_fmt(current_oos['after']['realizedPnl'])}（{_fmt(current_oos['difference']['pnlDelta'])}）",
        f"- 投入本金：{_fmt(current_oos['before']['capitalAtRisk'])} → {_fmt(current_oos['after']['capitalAtRisk'])}",
        f"- ROI：{_fmt(current_oos['before']['roi'] * 100, 2)}% → {_fmt(current_oos['after']['roi'] * 100, 2)}%",
        f"- 最大回撤：{_fmt(current_oos['before']['maxDrawdown'])} → {_fmt(current_oos['after']['maxDrawdown'])}（降低 {_fmt(current_oos['difference']['maxDrawdownReductionPct'], 2)}%）",
        "",
        "## 模組消融（樣本外後 40%）",
        "",
        "| 版本 | PnL | 投入本金 | ROI | 最大回撤 | PnL 對基準差異 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| 固定 4 USDT 基準 | {_fmt(oos['before']['realizedPnl'])} | {_fmt(oos['before']['capitalAtRisk'])} | {_fmt(oos['before']['roi'] * 100, 2)}% | {_fmt(oos['before']['maxDrawdown'])} | 0.0000 |",
    ]
    for name, result in report["ablations"].items():
        value = result["outOfSample"]
        lines.append(
            f"| {name} | {_fmt(value['realizedPnl'])} | {_fmt(value['capitalAtRisk'])} | "
            f"{_fmt(value['roi'] * 100, 2)}% | {_fmt(value['maxDrawdown'])} | "
            f"{_fmt(result['outOfSampleDifferenceVsBefore']['pnlDelta'])} |"
        )
    lines += [
        "",
        "## 逐策略",
        "",
        "| 策略 | 區段 | 企劃前 PnL | 企劃後 PnL | 差異 | 前回撤 | 後回撤 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for strategy, sections in report["byStrategy"].items():
        for key, label in (("overall", "完整七日"), ("outOfSample", "樣本外")):
            section = sections[key]
            lines.append(
                f"| {strategy} | {label} | {_fmt(section['before']['realizedPnl'])} | "
                f"{_fmt(section['after']['realizedPnl'])} | {_fmt(section['difference']['pnlDelta'])} | "
                f"{_fmt(section['before']['maxDrawdown'])} | {_fmt(section['after']['maxDrawdown'])} |"
            )
    lines += [
        "",
        "## 決策分布",
        "",
        "```json",
        json.dumps(report["decisionCounts"], ensure_ascii=False, indent=2),
        "```",
        "",
        "完整七日包含建模區段，因此只作描述；是否值得推進以後 40% 樣本外結果為準。這是歷史市場路徑回放，不是未來獲利保證，也不會修改 paper 或 live 狀態。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/backtests/seven-day-observer-market-replay-20260731-014422.json"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/backtests"))
    args = parser.parse_args()
    report = run(args.source)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = args.output_dir / f"adaptive-strength-weakness-v1-{stamp}"
    json_path = base.with_suffix(".json")
    md_path = base.with_suffix(".md")
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(_markdown(report), encoding="utf-8")
    print(json_path)
    print(md_path)
    print(json.dumps({
        "overall": report["overall"],
        "outOfSample": report["outOfSample"],
        "decisionCounts": report["decisionCounts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
