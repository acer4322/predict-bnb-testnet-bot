from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import analyze_target_maker_taker_inventory_lifecycle_v1 as lifecycle

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "target_wallet_official_v1.db"
DEFAULT_PUBLIC_DATASET = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_controller_parameter_extraction_v1_report.json"
DEFAULT_ACTIONS = ROOT / "data" / "research" / "target_controller_parameter_extraction_v1_actions.csv"
DEFAULT_STATES = ROOT / "data" / "research" / "target_controller_parameter_extraction_v1_states.csv"
REPORT_VERSION = "TARGET_CONTROLLER_PARAMETER_EXTRACTION_V1"
DEFAULT_STRESS_START = "2026-08-16T03:40:00+08:00"
DEFAULT_STRESS_END = "2026-08-16T11:35:00+08:00"
EPS = 1e-9

ACTION_FIELDS = [
    "market_id", "regime", "parent_id", "taker_index_market", "is_first_taker",
    "side", "quote_type", "first_event_ms", "last_event_ms", "shares", "average_price",
    "notional_usdt", "phase", "seconds_left", "public_lag_ms", "predict_up_mid", "predict_down_mid",
    "prediction_favored_side", "prediction_favored_mid", "side_matches_prediction",
    "prior_maker_parent_count", "prior_taker_parent_count", "has_prior_maker_fill",
    "maker_up_before", "maker_down_before", "taker_up_before", "taker_down_before",
    "combined_up_before", "combined_down_before", "cash_before", "settle_up_before", "settle_down_before",
    "worst_case_pnl_before", "best_case_pnl_before", "payoff_gap_before", "abs_payoff_gap_before",
    "risk_deficit_before", "maker_payoff_gap_before", "maker_abs_payoff_gap_before",
    "cash_after_cf", "settle_up_after_cf", "settle_down_after_cf", "worst_case_pnl_after_cf",
    "best_case_pnl_after_cf", "payoff_gap_after_cf", "abs_payoff_gap_after_cf", "risk_deficit_after_cf",
    "delta_worst_case_pnl", "delta_best_case_pnl", "abs_gap_reduction", "risk_deficit_reduction",
    "portfolio_effect", "maker_relation", "hypothesis_cheap_lt_010", "hypothesis_expensive_gt_090",
]

STATE_FIELDS = [
    "market_id", "regime", "maker_parent_id", "maker_completed_ms", "maker_side", "maker_quote_type",
    "maker_shares", "maker_average_price", "phase", "seconds_left", "public_lag_ms",
    "predict_up_mid", "predict_down_mid", "prediction_favored_side", "prediction_favored_mid",
    "maker_up", "maker_down", "taker_up", "taker_down", "combined_up", "combined_down", "cash",
    "settle_up", "settle_down", "worst_case_pnl", "best_case_pnl", "risk_deficit",
    "payoff_gap", "abs_payoff_gap", "maker_payoff_gap", "maker_abs_payoff_gap",
    "next_taker_parent_id", "next_taker_delay_ms", "next_taker_side", "next_taker_quote_type",
    "next_taker_price", "next_taker_shares", "taker_within_5s", "taker_within_15s", "taker_within_30s",
    "overlap_taker_active",
]


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return dt


def _quantile(values: Iterable[float], q: float) -> float | None:
    xs = sorted(float(x) for x in values if math.isfinite(float(x)))
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    q = min(1.0, max(0.0, float(q)))
    pos = q * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def _distribution(values: Iterable[float]) -> dict[str, Any]:
    xs = [float(x) for x in values if math.isfinite(float(x))]
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "min": min(xs),
        "p10": _quantile(xs, 0.10),
        "p25": _quantile(xs, 0.25),
        "median": _quantile(xs, 0.50),
        "p75": _quantile(xs, 0.75),
        "p90": _quantile(xs, 0.90),
        "max": max(xs),
        "mean": statistics.fmean(xs),
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    denom = math.sqrt(sum(x * x for x in dx) * sum(y * y for y in dy))
    if denom <= EPS:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / denom


def _ols(xs: list[float], ys: list[float]) -> dict[str, Any]:
    if len(xs) != len(ys) or len(xs) < 3:
        return {"n": len(xs), "slope": None, "intercept": None, "pearson": None}
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    slope = None if denom <= EPS else sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    intercept = None if slope is None else my - slope * mx
    return {"n": len(xs), "slope": slope, "intercept": intercept, "pearson": _pearson(xs, ys)}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(resolved)


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(resolved)


def _signed_shares(quote_type: str, shares: float) -> float:
    return lifecycle._signed_token_shares(str(quote_type), float(shares))


@dataclass
class PortfolioState:
    maker_up: float = 0.0
    maker_down: float = 0.0
    taker_up: float = 0.0
    taker_down: float = 0.0
    maker_cash: float = 0.0
    taker_cash: float = 0.0

    def copy(self) -> "PortfolioState":
        return PortfolioState(**self.__dict__)


def _apply_leg(state: PortfolioState, role: str, side: str, quote_type: str, shares: float, price: float) -> None:
    role_l = str(role).lower()
    side_l = str(side).lower()
    if role_l not in {"maker", "taker"} or side_l not in {"up", "down"}:
        return
    signed = _signed_shares(quote_type, shares)
    setattr(state, f"{role_l}_{side_l}", getattr(state, f"{role_l}_{side_l}") + signed)
    setattr(state, f"{role_l}_cash", getattr(state, f"{role_l}_cash") - signed * float(price))


def _apply_parent_cf(state: PortfolioState, parent: dict[str, Any]) -> PortfolioState:
    out = state.copy()
    _apply_leg(
        out,
        str(parent["role"]),
        str(parent["side"]),
        str(parent["quote_type"]),
        float(parent["shares"]),
        float(parent["average_price"]),
    )
    return out


def _portfolio_metrics(state: PortfolioState) -> dict[str, float]:
    combined_up = state.maker_up + state.taker_up
    combined_down = state.maker_down + state.taker_down
    cash = state.maker_cash + state.taker_cash
    settle_up = cash + combined_up
    settle_down = cash + combined_down
    worst = min(settle_up, settle_down)
    best = max(settle_up, settle_down)
    gap = settle_up - settle_down
    maker_settle_up = state.maker_cash + state.maker_up
    maker_settle_down = state.maker_cash + state.maker_down
    maker_gap = maker_settle_up - maker_settle_down
    return {
        "maker_up": state.maker_up,
        "maker_down": state.maker_down,
        "taker_up": state.taker_up,
        "taker_down": state.taker_down,
        "combined_up": combined_up,
        "combined_down": combined_down,
        "cash": cash,
        "settle_up": settle_up,
        "settle_down": settle_down,
        "worst_case_pnl": worst,
        "best_case_pnl": best,
        "risk_deficit": max(0.0, -worst),
        "payoff_gap": gap,
        "abs_payoff_gap": abs(gap),
        "maker_payoff_gap": maker_gap,
        "maker_abs_payoff_gap": abs(maker_gap),
    }


def _portfolio_effect(before: dict[str, float], after: dict[str, float]) -> str:
    wc = after["worst_case_pnl"] - before["worst_case_pnl"]
    gap_reduction = before["abs_payoff_gap"] - after["abs_payoff_gap"]
    if wc > EPS and gap_reduction > EPS:
        return "RISK_REDUCING"
    if gap_reduction > EPS:
        return "GAP_REDUCING_EXPENSIVE"
    if wc > EPS:
        return "TAIL_IMPROVING"
    if after["abs_payoff_gap"] > before["abs_payoff_gap"] + EPS:
        return "EXPOSURE_ADD"
    return "NEUTRAL"


def _maker_relation(maker_gap: float, parent: dict[str, Any]) -> str:
    signed = _signed_shares(str(parent["quote_type"]), float(parent["shares"]))
    effect = signed if str(parent["side"]).upper() == "UP" else -signed
    if abs(maker_gap) <= EPS or abs(effect) <= EPS:
        return "MAKER_BALANCED_OR_UNKNOWN"
    return "OPPOSE_MAKER_HEAVY" if maker_gap * effect < 0 else "WITH_MAKER_HEAVY"


class StrictPublicIndex:
    """Strict-past public as-of index. Equal timestamps are deliberately excluded."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self.times: dict[int, list[int]] = {}
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            time_col = "decision_sampled_at_ms" if "decision_sampled_at_ms" in fields else "sampled_at_ms" if "sampled_at_ms" in fields else None
            if time_col is None or "market_id" not in fields:
                return
            for raw in reader:
                try:
                    market_id = int(float(raw["market_id"]))
                    sampled_ms = int(float(raw[time_col]))
                except (TypeError, ValueError):
                    continue
                row = dict(raw)
                row["sampled_ms"] = sampled_ms
                self.rows[market_id].append(row)
        for market_id, rows in self.rows.items():
            rows.sort(key=lambda row: int(row["sampled_ms"]))
            self.times[market_id] = [int(row["sampled_ms"]) for row in rows]

    def asof(self, market_id: int, at_ms: int, max_lag_ms: int) -> dict[str, Any] | None:
        rows = self.rows.get(int(market_id))
        times = self.times.get(int(market_id))
        if not rows or not times:
            return None
        pos = bisect.bisect_left(times, int(at_ms)) - 1
        if pos < 0:
            return None
        row = rows[pos]
        lag = int(at_ms) - int(row["sampled_ms"])
        if lag <= 0 or lag > max(0, int(max_lag_ms)):
            return None
        seconds = _finite(row.get("seconds_left"))
        phase = str(row.get("macro_phase") or "").upper()
        if phase not in {"OPEN", "MID", "TAIL"}:
            phase = "OPEN" if seconds is not None and seconds > 180 else "MID" if seconds is not None and seconds > 60 else "TAIL" if seconds is not None else "UNKNOWN"
        up = _finite(row.get("predict_up_mid"))
        down = _finite(row.get("predict_down_mid"))
        if up is None:
            bid, ask = _finite(row.get("predict_up_bid")), _finite(row.get("predict_up_ask"))
            if bid is not None and ask is not None:
                up = (bid + ask) / 2.0
        if down is None:
            bid, ask = _finite(row.get("predict_down_bid")), _finite(row.get("predict_down_ask"))
            if bid is not None and ask is not None:
                down = (bid + ask) / 2.0
        if up is None and down is not None:
            up = 1.0 - down
        if down is None and up is not None:
            down = 1.0 - up
        favored = "UNKNOWN"
        favored_mid = None
        if up is not None and down is not None:
            favored = "UP" if up >= down else "DOWN"
            favored_mid = max(up, down)
        return {
            "sampled_ms": int(row["sampled_ms"]),
            "lag_ms": lag,
            "seconds_left": seconds,
            "phase": phase,
            "predict_up_mid": up,
            "predict_down_mid": down,
            "favored_side": favored,
            "favored_mid": favored_mid,
        }


def _window_label(at_ms: int, ordinary: tuple[int, int], stress: tuple[int, int]) -> str | None:
    if ordinary[0] <= at_ms < ordinary[1]:
        return "ORDINARY_CONTROL"
    if stress[0] <= at_ms < stress[1]:
        return "STRESS_2026_08_16"
    return None


def _best_threshold(rows: list[dict[str, Any]], feature: str, label: str) -> dict[str, Any]:
    pairs = []
    for row in rows:
        x = _finite(row.get(feature))
        y = row.get(label)
        if x is None or y in (None, ""):
            continue
        pairs.append((x, int(y)))
    positives = sum(y for _, y in pairs)
    negatives = len(pairs) - positives
    if len(pairs) < 10 or positives == 0 or negatives == 0:
        return {"feature": feature, "label": label, "n": len(pairs), "threshold": None}
    xs = sorted({x for x, _ in pairs})
    if len(xs) > 250:
        candidates = sorted({_quantile(xs, i / 250) for i in range(1, 250)})
    else:
        candidates = xs
    best: dict[str, Any] | None = None
    for threshold in candidates:
        if threshold is None:
            continue
        tp = sum(1 for x, y in pairs if x >= threshold and y == 1)
        fp = sum(1 for x, y in pairs if x >= threshold and y == 0)
        fn = positives - tp
        tn = negatives - fp
        tpr = tp / positives
        fpr = fp / negatives
        tnr = tn / negatives
        bal = (tpr + tnr) / 2.0
        youden = tpr - fpr
        candidate = {
            "feature": feature, "label": label, "n": len(pairs), "positives": positives, "negatives": negatives,
            "threshold": threshold, "tpr": tpr, "fpr": fpr, "balancedAccuracy": bal, "youdenJ": youden,
            "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        }
        if best is None or (candidate["youdenJ"], candidate["balancedAccuracy"]) > (best["youdenJ"], best["balancedAccuracy"]):
            best = candidate
    return best or {"feature": feature, "label": label, "n": len(pairs), "threshold": None}


def _evaluate_threshold(rows: list[dict[str, Any]], feature: str, label: str, threshold: float | None) -> dict[str, Any]:
    if threshold is None:
        return {"n": 0, "threshold": None}
    pairs = []
    for row in rows:
        x = _finite(row.get(feature))
        y = row.get(label)
        if x is not None and y not in (None, ""):
            pairs.append((x, int(y)))
    positives = sum(y for _, y in pairs)
    negatives = len(pairs) - positives
    tp = sum(1 for x, y in pairs if x >= threshold and y == 1)
    fp = sum(1 for x, y in pairs if x >= threshold and y == 0)
    fn = positives - tp
    tn = negatives - fp
    tpr = tp / positives if positives else None
    fpr = fp / negatives if negatives else None
    tnr = tn / negatives if negatives else None
    bal = (tpr + tnr) / 2.0 if tpr is not None and tnr is not None else None
    return {
        "n": len(pairs), "positives": positives, "negatives": negatives, "threshold": threshold,
        "tpr": tpr, "fpr": fpr, "balancedAccuracy": bal,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
    }


def _action_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"actions": 0}
    effects = Counter(str(row["portfolio_effect"]) for row in rows)
    first = [row for row in rows if int(row["is_first_taker"]) == 1]
    later = [row for row in rows if int(row["is_first_taker"]) == 0]
    risk = [row for row in rows if str(row["portfolio_effect"]) in {"RISK_REDUCING", "TAIL_IMPROVING"}]
    expensive = [row for row in risk if float(row["average_price"]) > 0.90]
    cheap = [row for row in risk if float(row["average_price"]) < 0.10]
    return {
        "actions": len(rows),
        "markets": len({int(row["market_id"]) for row in rows}),
        "portfolioEffects": dict(effects),
        "portfolioEffectRates": {key: value / len(rows) for key, value in effects.items()},
        "firstTaker": {
            "actions": len(first),
            "buyBidActions": sum(str(row["quote_type"]).upper() == "BID" for row in first),
            "secondsLeft": _distribution(float(row["seconds_left"]) for row in first if row["seconds_left"] not in (None, "")),
            "price": _distribution(float(row["average_price"]) for row in first),
            "shares": _distribution(float(row["shares"]) for row in first),
            "notionalUsdt": _distribution(float(row["notional_usdt"]) for row in first),
            "sideMatchesPredictionRate": (
                (lambda known: (sum(int(row["side_matches_prediction"]) for row in known) / len(known)) if known else None)(
                    [row for row in first if row["side_matches_prediction"] not in (None, "")]
                )
            ),
        },
        "laterTaker": {
            "actions": len(later),
            "price": _distribution(float(row["average_price"]) for row in later),
            "preRiskDeficit": _distribution(float(row["risk_deficit_before"]) for row in later),
            "preAbsPayoffGap": _distribution(float(row["abs_payoff_gap_before"]) for row in later),
        },
        "riskReducing": {
            "actions": len(risk),
            "price": _distribution(float(row["average_price"]) for row in risk),
            "shares": _distribution(float(row["shares"]) for row in risk),
            "preRiskDeficit": _distribution(float(row["risk_deficit_before"]) for row in risk),
            "riskDeficitReduction": _distribution(float(row["risk_deficit_reduction"]) for row in risk),
            "absGapReduction": _distribution(float(row["abs_gap_reduction"]) for row in risk),
            "cheapLt010Actions": len(cheap),
            "expensiveGt090Actions": len(expensive),
        },
    }


def _state_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"states": 0}
    taker15 = [row for row in rows if int(row["taker_within_15s"]) == 1]
    no15 = [row for row in rows if int(row["taker_within_15s"]) == 0]
    return {
        "states": len(rows),
        "markets": len({int(row["market_id"]) for row in rows}),
        "takerWithin5sRate": sum(int(row["taker_within_5s"]) for row in rows) / len(rows),
        "takerWithin15sRate": len(taker15) / len(rows),
        "takerWithin30sRate": sum(int(row["taker_within_30s"]) for row in rows) / len(rows),
        "overlapTakerActiveRate": sum(int(row["overlap_taker_active"]) for row in rows) / len(rows),
        "all": {
            "riskDeficit": _distribution(float(row["risk_deficit"]) for row in rows),
            "absPayoffGap": _distribution(float(row["abs_payoff_gap"]) for row in rows),
            "makerAbsPayoffGap": _distribution(float(row["maker_abs_payoff_gap"]) for row in rows),
        },
        "takerWithin15s": {
            "states": len(taker15),
            "riskDeficit": _distribution(float(row["risk_deficit"]) for row in taker15),
            "absPayoffGap": _distribution(float(row["abs_payoff_gap"]) for row in taker15),
            "makerAbsPayoffGap": _distribution(float(row["maker_abs_payoff_gap"]) for row in taker15),
        },
        "noTakerWithin15s": {
            "states": len(no15),
            "riskDeficit": _distribution(float(row["risk_deficit"]) for row in no15),
            "absPayoffGap": _distribution(float(row["abs_payoff_gap"]) for row in no15),
            "makerAbsPayoffGap": _distribution(float(row["maker_abs_payoff_gap"]) for row in no15),
        },
    }


def _candidate_parameters(ordinary_actions: list[dict[str, Any]], ordinary_states: list[dict[str, Any]]) -> dict[str, Any]:
    first = [row for row in ordinary_actions if int(row["is_first_taker"]) == 1 and str(row["quote_type"]).upper() == "BID"]
    later = [row for row in ordinary_actions if int(row["is_first_taker"]) == 0]
    risk = [row for row in later if str(row["portfolio_effect"]) in {"RISK_REDUCING", "TAIL_IMPROVING"}]
    oppose = [row for row in risk if str(row["maker_relation"]) == "OPPOSE_MAKER_HEAVY"]
    expensive = [row for row in risk if float(row["average_price"]) > 0.90]
    cheap = [row for row in risk if float(row["average_price"]) < 0.10]
    maker_last_by_market: dict[int, float] = {}
    for row in ordinary_states:
        seconds = _finite(row.get("seconds_left"))
        if seconds is None:
            continue
        market_id = int(row["market_id"])
        maker_last_by_market[market_id] = min(maker_last_by_market.get(market_id, seconds), seconds)

    risk_x = [float(row["risk_deficit_before"]) for row in risk]
    size_y = [float(row["shares"]) for row in risk]
    gap_x = [float(row["abs_payoff_gap_before"]) for row in risk]

    thresholds = {
        "riskDeficitToTaker15s": _best_threshold(ordinary_states, "risk_deficit", "taker_within_15s"),
        "absPayoffGapToTaker15s": _best_threshold(ordinary_states, "abs_payoff_gap", "taker_within_15s"),
        "makerAbsPayoffGapToTaker15s": _best_threshold(ordinary_states, "maker_abs_payoff_gap", "taker_within_15s"),
    }
    return {
        "initialSeed": {
            "definition": "first observed Target Taker BID parent in each market; no time/price threshold used to label it",
            "actions": len(first),
            "secondsLeft": _distribution(float(row["seconds_left"]) for row in first if row["seconds_left"] not in (None, "")),
            "maxObservedPrice": max((float(row["average_price"]) for row in first), default=None),
            "price": _distribution(float(row["average_price"]) for row in first),
            "shares": _distribution(float(row["shares"]) for row in first),
            "notionalUsdt": _distribution(float(row["notional_usdt"]) for row in first),
        },
        "makerStop": {
            "definition": "distribution of the final observed Maker-completion seconds_left per ordinary market with strict-past public context",
            "secondsLeft": _distribution(maker_last_by_market.values()),
        },
        "interventionBoundary": thresholds,
        "normalRiskReducingTaker": {
            "actions": len(risk),
            "preRiskDeficit": _distribution(risk_x),
            "preAbsPayoffGap": _distribution(gap_x),
            "price": _distribution(float(row["average_price"]) for row in risk),
            "shares": _distribution(size_y),
            "opposesMakerHeavyRate": len(oppose) / len(risk) if risk else None,
        },
        "sizeController": {
            "sharesVsPreRiskDeficit": _ols(risk_x, size_y),
            "sharesVsPreAbsPayoffGap": _ols(gap_x, size_y),
            "shareToGapRatio": _distribution(
                float(row["shares"]) / float(row["abs_payoff_gap_before"])
                for row in risk if float(row["abs_payoff_gap_before"]) > EPS
            ),
        },
        "cheapInsuranceHypothesis": {
            "definition": "risk-reducing later Taker actions with observed average price < 0.10; hypothesis check only, not the extracted threshold",
            "actions": len(cheap),
            "preRiskDeficit": _distribution(float(row["risk_deficit_before"]) for row in cheap),
            "shares": _distribution(float(row["shares"]) for row in cheap),
            "riskDeficitReduction": _distribution(float(row["risk_deficit_reduction"]) for row in cheap),
        },
        "emergencyHypothesis": {
            "definition": "risk-reducing later Taker actions with observed average price > 0.90; hypothesis check only, not the extracted threshold",
            "actions": len(expensive),
            "preRiskDeficit": _distribution(float(row["risk_deficit_before"]) for row in expensive),
            "shares": _distribution(float(row["shares"]) for row in expensive),
            "riskDeficitReduction": _distribution(float(row["risk_deficit_reduction"]) for row in expensive),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract Target inventory/risk controller boundaries directly from official Maker/Taker fills. No EBM fitting."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--public-dataset", type=Path, default=DEFAULT_PUBLIC_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--actions", type=Path, default=DEFAULT_ACTIONS)
    parser.add_argument("--states", type=Path, default=DEFAULT_STATES)
    parser.add_argument("--asset", default="BTC")
    parser.add_argument("--stress-start", default=DEFAULT_STRESS_START)
    parser.add_argument("--stress-end", default=DEFAULT_STRESS_END)
    parser.add_argument("--ordinary-start", default="")
    parser.add_argument("--ordinary-end", default="")
    parser.add_argument("--max-public-lag-ms", type=int, default=2000)
    args = parser.parse_args()

    stress_start_dt = _parse_iso(args.stress_start)
    stress_end_dt = _parse_iso(args.stress_end)
    if stress_end_dt <= stress_start_dt:
        raise SystemExit("--stress-end must be after --stress-start")
    span = stress_end_dt - stress_start_dt
    ordinary_start_dt = _parse_iso(args.ordinary_start) if args.ordinary_start else stress_start_dt - timedelta(days=1)
    ordinary_end_dt = _parse_iso(args.ordinary_end) if args.ordinary_end else ordinary_start_dt + span
    ordinary = (int(ordinary_start_dt.timestamp() * 1000), int(ordinary_end_dt.timestamp() * 1000))
    stress = (int(stress_start_dt.timestamp() * 1000), int(stress_end_dt.timestamp() * 1000))

    print(REPORT_VERSION, flush=True)
    print("[1/5] Load official Target fill legs and parent orders...", flush=True)
    db = lifecycle._connect_ro(args.db)
    try:
        events, event_audit = lifecycle._load_official_events(db, asset=str(args.asset).upper())
        parents = lifecycle._build_parents(events)
    finally:
        db.close()

    public = StrictPublicIndex(args.public_dataset)
    by_market_events: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_market_parents: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_market_events[int(event["market_id"])].append(event)
    for parent in parents:
        by_market_parents[int(parent["market_id"])].append(parent)
    for market_parents in by_market_parents.values():
        market_parents.sort(key=lambda row: (int(row["first_event_ms"]), int(row["last_event_ms"]), str(row["parent_id"])))

    action_rows: list[dict[str, Any]] = []
    state_rows: list[dict[str, Any]] = []
    print("[2/5] Reconstruct strict-past portfolio state at every Taker onset...", flush=True)

    for market_id, market_events in sorted(by_market_events.items()):
        ordered_events = sorted(market_events, key=lambda row: (int(row["event_ms"]), str(row["leg_id"])))
        market_parents = by_market_parents.get(market_id, [])
        parent_map = {str(row["parent_id"]): row for row in market_parents}
        first_key: dict[str, tuple[int, str]] = {}
        last_key: dict[str, tuple[int, str]] = {}
        for event in ordered_events:
            pid = str(event["parent_id"])
            key = (int(event["event_ms"]), str(event["leg_id"]))
            first_key[pid] = min(first_key.get(pid, key), key)
            last_key[pid] = max(last_key.get(pid, key), key)

        state = PortfolioState()
        before_parent: dict[str, PortfolioState] = {}
        maker_seen: set[str] = set()
        taker_seen: set[str] = set()
        taker_index = 0

        for event in ordered_events:
            pid = str(event["parent_id"])
            key = (int(event["event_ms"]), str(event["leg_id"]))
            if key == first_key[pid]:
                before_parent[pid] = state.copy()
            _apply_leg(
                state,
                str(event["role"]), str(event["side"]), str(event["quote_type"]),
                float(event["shares"]), float(event["price"]),
            )
            if key != last_key[pid]:
                continue
            parent = parent_map[pid]
            completed_ms = int(parent["last_event_ms"])
            role = str(parent["role"]).upper()
            if role == "MAKER":
                maker_seen.add(pid)
                regime = _window_label(completed_ms, ordinary, stress)
                if regime is not None:
                    metrics = _portfolio_metrics(state)
                    pub = public.asof(market_id, completed_ms, int(args.max_public_lag_ms))
                    future_takers = [
                        row for row in market_parents
                        if str(row["role"]).upper() == "TAKER" and int(row["first_event_ms"]) > completed_ms
                    ]
                    future_takers.sort(key=lambda row: (int(row["first_event_ms"]), int(row["last_event_ms"]), str(row["parent_id"])))
                    next_taker = future_takers[0] if future_takers else None
                    delay = int(next_taker["first_event_ms"]) - completed_ms if next_taker is not None else None
                    overlap_taker = any(
                        str(other["role"]).upper() == "TAKER"
                        and int(other["first_event_ms"]) <= completed_ms < int(other["last_event_ms"])
                        for other in market_parents
                    )
                    state_rows.append({
                        "market_id": market_id,
                        "regime": regime,
                        "maker_parent_id": pid,
                        "maker_completed_ms": completed_ms,
                        "maker_side": str(parent["side"]),
                        "maker_quote_type": str(parent["quote_type"]),
                        "maker_shares": float(parent["shares"]),
                        "maker_average_price": float(parent["average_price"]),
                        "phase": pub["phase"] if pub else "UNKNOWN",
                        "seconds_left": pub["seconds_left"] if pub and pub["seconds_left"] is not None else "",
                        "public_lag_ms": pub["lag_ms"] if pub else "",
                        "predict_up_mid": pub["predict_up_mid"] if pub and pub["predict_up_mid"] is not None else "",
                        "predict_down_mid": pub["predict_down_mid"] if pub and pub["predict_down_mid"] is not None else "",
                        "prediction_favored_side": pub["favored_side"] if pub else "UNKNOWN",
                        "prediction_favored_mid": pub["favored_mid"] if pub and pub["favored_mid"] is not None else "",
                        **metrics,
                        "next_taker_parent_id": str(next_taker["parent_id"]) if next_taker else "",
                        "next_taker_delay_ms": delay if delay is not None else "",
                        "next_taker_side": str(next_taker["side"]) if next_taker else "",
                        "next_taker_quote_type": str(next_taker["quote_type"]) if next_taker else "",
                        "next_taker_price": float(next_taker["average_price"]) if next_taker else "",
                        "next_taker_shares": float(next_taker["shares"]) if next_taker else "",
                        "taker_within_5s": int(delay is not None and delay <= 5000),
                        "taker_within_15s": int(delay is not None and delay <= 15000),
                        "taker_within_30s": int(delay is not None and delay <= 30000),
                        "overlap_taker_active": int(overlap_taker),
                    })
                continue

            if role != "TAKER":
                continue
            taker_index += 1
            regime = _window_label(int(parent["first_event_ms"]), ordinary, stress)
            before = before_parent[pid]
            before_m = _portfolio_metrics(before)
            after_cf = _apply_parent_cf(before, parent)
            after_m = _portfolio_metrics(after_cf)
            if regime is not None:
                pub = public.asof(market_id, int(parent["first_event_ms"]), int(args.max_public_lag_ms))
                effect = _portfolio_effect(before_m, after_m)
                side_matches = ""
                if pub and pub["favored_side"] in {"UP", "DOWN"} and str(parent["quote_type"]).upper() == "BID":
                    side_matches = int(str(parent["side"]).upper() == pub["favored_side"])
                action_rows.append({
                    "market_id": market_id,
                    "regime": regime,
                    "parent_id": pid,
                    "taker_index_market": taker_index,
                    "is_first_taker": int(taker_index == 1),
                    "side": str(parent["side"]),
                    "quote_type": str(parent["quote_type"]),
                    "first_event_ms": int(parent["first_event_ms"]),
                    "last_event_ms": int(parent["last_event_ms"]),
                    "shares": float(parent["shares"]),
                    "average_price": float(parent["average_price"]),
                    "notional_usdt": float(parent["shares"]) * float(parent["average_price"]),
                    "phase": pub["phase"] if pub else "UNKNOWN",
                    "seconds_left": pub["seconds_left"] if pub and pub["seconds_left"] is not None else "",
                    "public_lag_ms": pub["lag_ms"] if pub else "",
                    "predict_up_mid": pub["predict_up_mid"] if pub and pub["predict_up_mid"] is not None else "",
                    "predict_down_mid": pub["predict_down_mid"] if pub and pub["predict_down_mid"] is not None else "",
                    "prediction_favored_side": pub["favored_side"] if pub else "UNKNOWN",
                    "prediction_favored_mid": pub["favored_mid"] if pub and pub["favored_mid"] is not None else "",
                    "side_matches_prediction": side_matches,
                    "prior_maker_parent_count": len(maker_seen),
                    "prior_taker_parent_count": len(taker_seen),
                    "has_prior_maker_fill": int(
                        abs(before.maker_up) > EPS or abs(before.maker_down) > EPS or abs(before.maker_cash) > EPS
                    ),
                    "maker_up_before": before_m["maker_up"],
                    "maker_down_before": before_m["maker_down"],
                    "taker_up_before": before_m["taker_up"],
                    "taker_down_before": before_m["taker_down"],
                    "combined_up_before": before_m["combined_up"],
                    "combined_down_before": before_m["combined_down"],
                    "cash_before": before_m["cash"],
                    "settle_up_before": before_m["settle_up"],
                    "settle_down_before": before_m["settle_down"],
                    "worst_case_pnl_before": before_m["worst_case_pnl"],
                    "best_case_pnl_before": before_m["best_case_pnl"],
                    "payoff_gap_before": before_m["payoff_gap"],
                    "abs_payoff_gap_before": before_m["abs_payoff_gap"],
                    "risk_deficit_before": before_m["risk_deficit"],
                    "maker_payoff_gap_before": before_m["maker_payoff_gap"],
                    "maker_abs_payoff_gap_before": before_m["maker_abs_payoff_gap"],
                    "cash_after_cf": after_m["cash"],
                    "settle_up_after_cf": after_m["settle_up"],
                    "settle_down_after_cf": after_m["settle_down"],
                    "worst_case_pnl_after_cf": after_m["worst_case_pnl"],
                    "best_case_pnl_after_cf": after_m["best_case_pnl"],
                    "payoff_gap_after_cf": after_m["payoff_gap"],
                    "abs_payoff_gap_after_cf": after_m["abs_payoff_gap"],
                    "risk_deficit_after_cf": after_m["risk_deficit"],
                    "delta_worst_case_pnl": after_m["worst_case_pnl"] - before_m["worst_case_pnl"],
                    "delta_best_case_pnl": after_m["best_case_pnl"] - before_m["best_case_pnl"],
                    "abs_gap_reduction": before_m["abs_payoff_gap"] - after_m["abs_payoff_gap"],
                    "risk_deficit_reduction": before_m["risk_deficit"] - after_m["risk_deficit"],
                    "portfolio_effect": effect,
                    "maker_relation": _maker_relation(before_m["maker_payoff_gap"], parent),
                    "hypothesis_cheap_lt_010": int(float(parent["average_price"]) < 0.10),
                    "hypothesis_expensive_gt_090": int(float(parent["average_price"]) > 0.90),
                })
            taker_seen.add(pid)

    action_rows.sort(key=lambda row: (int(row["first_event_ms"]), int(row["market_id"]), str(row["parent_id"])))
    state_rows.sort(key=lambda row: (int(row["maker_completed_ms"]), int(row["market_id"]), str(row["maker_parent_id"])))
    print("[3/5] Write action/state research tables...", flush=True)
    _write_csv(args.actions, action_rows, ACTION_FIELDS)
    _write_csv(args.states, state_rows, STATE_FIELDS)

    ordinary_actions = [row for row in action_rows if row["regime"] == "ORDINARY_CONTROL"]
    stress_actions = [row for row in action_rows if row["regime"] == "STRESS_2026_08_16"]
    ordinary_states = [row for row in state_rows if row["regime"] == "ORDINARY_CONTROL"]
    stress_states = [row for row in state_rows if row["regime"] == "STRESS_2026_08_16"]

    print("[4/5] Extract ordinary controller parameters and freeze intervention thresholds...", flush=True)
    params = _candidate_parameters(ordinary_actions, ordinary_states)
    stress_threshold_validation = {}
    for key, ordinary_result in params["interventionBoundary"].items():
        stress_threshold_validation[key] = _evaluate_threshold(
            stress_states,
            str(ordinary_result.get("feature")),
            str(ordinary_result.get("label")),
            _finite(ordinary_result.get("threshold")),
        )

    report = {
        "reportVersion": REPORT_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "paperResearchOnly": True,
        "noModelFit": True,
        "noStrategyPromotion": True,
        "strictPastPublicContext": True,
        "purpose": "Test the Data -> Logic -> Decision/Portfolio Controller -> Maker/Taker execution hypothesis by extracting controller boundaries directly from Target actions and post-Maker states.",
        "windows": {
            "ordinaryControl": {
                "start": ordinary_start_dt.isoformat(), "end": ordinary_end_dt.isoformat(),
                "definition": "same wall-clock span exactly 24h before stress unless overridden",
            },
            "stress": {
                "start": stress_start_dt.isoformat(), "end": stress_end_dt.isoformat(),
                "definition": "frozen 2026-08-16 Maker-loss stress window used by Maker Objective / State-Machine V1",
            },
        },
        "sources": {
            "db": str(args.db),
            "publicDataset": str(args.public_dataset),
            "eventTable": "wallet_shadow_target_events via existing lifecycle loader",
            "eventAudit": event_audit,
            "publicJoin": "bisect_left(timestamp) - 1; equal timestamp excluded; max lag enforced",
        },
        "accounting": {
            "signedShares": "BID=+shares, ASK=-shares",
            "cash": "cash -= signed_shares * price",
            "settleIfUp": "cash + combined UP signed holdings",
            "settleIfDown": "cash + combined DOWN signed holdings",
            "worstCasePnl": "min(settleIfUp, settleIfDown)",
            "riskDeficit": "max(0, -worstCasePnl)",
            "note": "research proxy uses observed fills and ignores explicit fees; official settlement rows are not used to classify actions",
        },
        "ordinary": {
            "actions": _action_summary(ordinary_actions),
            "postMakerDecisionStates": _state_summary(ordinary_states),
        },
        "stress": {
            "actions": _action_summary(stress_actions),
            "postMakerDecisionStates": _state_summary(stress_states),
        },
        "candidateParametersFromOrdinaryOnly": params,
        "ordinaryFrozenThresholdsAppliedToStress": stress_threshold_validation,
        "diagnostics": {
            "actionRows": len(action_rows),
            "stateRows": len(state_rows),
            "ordinaryActionRows": len(ordinary_actions),
            "stressActionRows": len(stress_actions),
            "ordinaryStateRows": len(ordinary_states),
            "stressStateRows": len(stress_states),
            "unknownPublicActionRows": sum(str(row["phase"]) == "UNKNOWN" for row in action_rows),
            "unknownPublicStateRows": sum(str(row["phase"]) == "UNKNOWN" for row in state_rows),
        },
        "falsificationGuide": {
            "seed": "Initial-seed hypothesis strengthens if first Taker BID timing/price/size are concentrated and side often agrees with strict-past Prediction bias; broad/random distributions weaken it.",
            "inventoryTolerance": "Controller hypothesis strengthens if ordinary post-Maker states show a separable risk/gap boundary for Taker intervention within 15s rather than immediate repair at any non-zero imbalance.",
            "sizeController": "Positive stable shares-vs-risk/gap scaling supports amount control by a portfolio controller; near-zero relation weakens that claim.",
            "cheapInsurance": "<0.10 is retained only as the user's falsification window. The report separately emits the full empirical risk-reducing price distribution so a real limit can be extracted instead of assumed.",
            "emergency": ">0.90 is retained only as the user's falsification window. Emergency interpretation strengthens if these actions begin from unusually high risk deficit and materially reduce it.",
            "stress": "If ordinary-extracted intervention thresholds materially lose discrimination in stress while risk deficit/size explode, that supports a fixed controller being saturated by the 8/16 regime rather than a totally different strategy.",
        },
        "caveats": [
            "This V1 extracts controller parameters from observed fills, not hidden unfilled/cancelled orders.",
            "Maker quote aggressiveness/rail distance needs 8778 reconstructed book geometry and belongs in a later join; this script does not fabricate it.",
            "The post-Maker state table samples Maker completion boundaries, not every idle millisecond, so HOLD-vs-TAKER inference is conditional on a recent Maker completion.",
            "No EBM, clustering, threshold tuning on stress, or paper/live execution is performed.",
        ],
    }
    print("[5/5] Write report...", flush=True)
    _write_json(args.report, report)

    print(f"ordinary_actions={len(ordinary_actions)} stress_actions={len(stress_actions)}")
    print(f"ordinary_states={len(ordinary_states)} stress_states={len(stress_states)}")
    for key, result in params["interventionBoundary"].items():
        print(f"{key}: threshold={result.get('threshold')} youden={result.get('youdenJ')} bal_acc={result.get('balancedAccuracy')}")
    print(f"report={args.report}")
    print(f"actions={args.actions}")
    print(f"states={args.states}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
