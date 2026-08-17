from __future__ import annotations

import math
from typing import Any, Iterable


VERSION = "DECISION_STRATEGY_NATIVE_V2"
RANK1_STRATEGY = "R_DECISION_RANK1"
RANK2_STRATEGY = "R_DECISION_RANK2"
STRATEGIES = (RANK1_STRATEGY, RANK2_STRATEGY)
FAMILY_SOURCES = {
    "FUTURES_LEAD": "R_FUTURES_LEAD",
    "CALIBRATED_VALUE": "R_CALIBRATED_VALUE",
    "CONSENSUS": "R_CONSENSUS",
}
EXCLUDED_FAMILIES = ("M01", "MICROPRICE", "OFI")
SOURCE_STRATEGIES = frozenset(FAMILY_SOURCES.values())

STAKE_USDT = 5.0
SLIPPAGE_BPS = 50.0
MAX_BOOK_AGE_MS = 2_000.0
MAX_BOOK_SKEW_MS = 500.0
MAX_SPREAD = 0.03
MAX_ENTRY = 0.95

PRIOR_WINS = 2.0
PRIOR_LOSSES = 2.0
RANK1_HISTORY = 60
RANK1_HALF_LIFE = 20.0
RANK1_MIN_HISTORY = 10
RANK1_MIN_SUPPORTERS = 2
RANK1_DIRECTION_SHARE = 0.67
RANK2_HISTORY = 30
RANK2_HALF_LIFE = 10.0
RANK2_MIN_CONTEXT_HISTORY = 8
RANK2_EDGE_MARGIN = 0.03
RANK2_CLEAR_CHAMPION_PROBABILITY = 0.72
RANK2_CLEAR_CHAMPION_EDGE_LEAD = 0.05
RANK2_FAMILY_CAP = 0.50

TREND_MIN_ELAPSED_SECONDS = 30.0
TREND_MIN_MOVE_BPS = 2.5
TREND_MIN_PATH_ER = 0.40
TREND_MIN_SAMPLES = 3
TREND_MAX_SPOT_AGE_MS = 2_000.0


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def context_key(
    *,
    side: str,
    entry_price: float,
    elapsed_seconds: float,
    start_move_bps: float,
    path_er: float,
) -> tuple[str, str, str, str]:
    phase = (
        "EARLY"
        if elapsed_seconds < 60.0
        else "MID"
        if elapsed_seconds < 180.0
        else "LATE"
    )
    if abs(start_move_bps) < 0.75:
        alignment = "FLAT"
    elif (side == "UP" and start_move_bps > 0) or (
        side == "DOWN" and start_move_bps < 0
    ):
        alignment = "ALIGNED"
    else:
        alignment = "OPPOSED"
    price_bucket = (
        "P_LT030"
        if entry_price < 0.30
        else "P_030_055"
        if entry_price < 0.55
        else "P_055_075"
        if entry_price < 0.75
        else "P_GE075"
    )
    er_bucket = (
        "ER_LOW"
        if path_er < 0.30
        else "ER_MID"
        if path_er < 0.60
        else "ER_HIGH"
    )
    return phase, alignment, price_bucket, er_bucket


def normalized_pnl(row: Any) -> float | None:
    stake = finite(row["stake"])
    pnl = finite(row["pnl"])
    if stake is None or stake <= 0 or pnl is None:
        return None
    return max(-5.10, min(10.0, pnl * STAKE_USDT / stake))


def weighted_stats(
    rows: Iterable[Any],
    *,
    limit: int,
    half_life: float,
) -> dict[str, Any]:
    usable: list[float] = []
    for row in list(rows)[-limit:]:
        pnl = normalized_pnl(row)
        if pnl is not None:
            usable.append(pnl)
    if not usable:
        return {"n": 0, "utility": 0.0, "p": 0.5, "weightSum": 0.0}
    weights = [
        0.5 ** ((len(usable) - 1 - index) / half_life)
        for index in range(len(usable))
    ]
    weight_sum = sum(weights)
    weighted_wins = sum(
        weight for weight, pnl in zip(weights, usable) if pnl > 0
    )
    return {
        "n": len(usable),
        "utility": sum(
            weight * pnl for weight, pnl in zip(weights, usable)
        ) / weight_sum,
        "p": (weighted_wins + PRIOR_WINS)
        / (weight_sum + PRIOR_WINS + PRIOR_LOSSES),
        "weightSum": weight_sum,
    }


def effective_break_even(entry_price: float, fee_rate_bps: float) -> float:
    return entry_price + min(entry_price, 1.0 - entry_price) * (
        fee_rate_bps / 10_000.0
    )


def cap_family_weights(weights: dict[str, float]) -> dict[str, float]:
    positive = {
        family: float(weight)
        for family, weight in weights.items()
        if float(weight) > 0
    }
    if len(positive) <= 1:
        return positive
    total = sum(positive.values())
    return {
        family: min(weight, total - weight)
        for family, weight in positive.items()
    }


def rank1_decision(
    families: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    scored: list[dict[str, Any]] = []
    for family, item in families.items():
        source = item.get("source")
        stats = item.get("rank1") or {}
        if source is None or int(stats.get("n") or 0) < RANK1_MIN_HISTORY:
            continue
        utility = float(stats.get("utility") or 0.0)
        probability = float(stats.get("p") or 0.5)
        score = max(0.0, utility / STAKE_USDT) * max(
            0.0, 2.0 * probability - 1.0
        )
        scored.append(
            {
                "family": family,
                "source": source,
                "stats": stats,
                "score": score,
                "side": str(source["side"]).upper(),
            }
        )
    if len(scored) < RANK1_MIN_SUPPORTERS:
        return {
            "status": "WAITING_HISTORY",
            "reason": "fewer than two families have ten causal settled samples",
        }
    side_weights = {
        side: sum(item["score"] for item in scored if item["side"] == side)
        for side in ("UP", "DOWN")
    }
    total_weight = sum(side_weights.values())
    if total_weight <= 0:
        return {
            "status": "NO_POSITIVE_WEIGHT",
            "reason": "utility and shrunk hit rate produce no positive weight",
            "sideWeights": side_weights,
        }
    side = max(side_weights, key=side_weights.get)
    agreement = side_weights[side] / total_weight
    supporters = [
        item
        for item in scored
        if item["side"] == side and item["score"] > 0
    ]
    if (
        len(supporters) < RANK1_MIN_SUPPORTERS
        or agreement + 1e-12 < RANK1_DIRECTION_SHARE
    ):
        return {
            "status": "NO_CONSENSUS",
            "reason": "requires two positive supporters and 67% direction weight",
            "agreementWeight": agreement,
            "sideWeights": side_weights,
        }
    mean_utility = sum(
        float(item["stats"].get("utility") or 0.0)
        for item in supporters
    ) / len(supporters)
    if mean_utility <= 0:
        return {
            "status": "NON_POSITIVE_SUPPORT_UTILITY",
            "reason": "supporter mean utility is not positive",
            "agreementWeight": agreement,
        }
    selected = max(
        supporters,
        key=lambda item: (
            float(item["score"]),
            str(item["source"].get("opened_at") or ""),
            int(item["source"].get("id") or 0),
        ),
    )
    return {
        "status": "CANDIDATE",
        "reason": "utility-weighted family consensus passed",
        "side": side,
        "selectedFamily": selected["family"],
        "selectedSourceTradeId": int(selected["source"]["id"]),
        "estimatedProbability": float(selected["stats"].get("p") or 0.5),
        "agreementWeight": agreement,
        "supporters": [item["family"] for item in supporters],
        "familyScores": {
            item["family"]: float(item["score"]) for item in scored
        },
        "sideWeights": side_weights,
        "meanSupportUtility": mean_utility,
    }


def rank2_decision(
    families: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    eligible: list[dict[str, Any]] = []
    for family, item in families.items():
        source = item.get("source")
        source_context = item.get("sourceContext")
        stats = item.get("rank2") or {}
        if (
            source is None
            or source_context is None
            or int(stats.get("n") or 0) < RANK2_MIN_CONTEXT_HISTORY
        ):
            continue
        probability = float(stats.get("p") or 0.5)
        entry = float(source["entry_price"])
        fee_bps = float(source.get("fee_rate_bps") or 200.0)
        break_even = effective_break_even(entry, fee_bps)
        edge = probability - break_even
        if edge + 1e-12 < RANK2_EDGE_MARGIN:
            continue
        eligible.append(
            {
                "family": family,
                "source": source,
                "probability": probability,
                "breakEven": break_even,
                "edge": edge,
                "side": str(source["side"]).upper(),
                "context": source_context,
            }
        )
    if not eligible:
        return {
            "status": "WAITING_CONTEXT_EDGE",
            "reason": "no family has eight same-context samples and 3pp net edge",
        }

    raw_weights = {item["family"]: item["edge"] for item in eligible}
    capped_weights = cap_family_weights(raw_weights)
    side_weights = {
        side: sum(
            capped_weights.get(item["family"], 0.0)
            for item in eligible
            if item["side"] == side
        )
        for side in ("UP", "DOWN")
    }
    total_weight = sum(side_weights.values())

    qualifying_sides = {
        side: [item for item in eligible if item["side"] == side]
        for side in ("UP", "DOWN")
    }
    qualifying_sides = {
        side: items for side, items in qualifying_sides.items() if len(items) >= 2
    }

    ordered = sorted(
        eligible,
        key=lambda item: (
            item["probability"],
            item["edge"],
            str(item["source"].get("opened_at") or ""),
        ),
        reverse=True,
    )
    champion = ordered[0]
    runner_edge = ordered[1]["edge"] if len(ordered) > 1 else 0.0
    clear_champion = bool(
        champion["probability"] >= RANK2_CLEAR_CHAMPION_PROBABILITY
        and champion["edge"] - runner_edge + 1e-12
        >= RANK2_CLEAR_CHAMPION_EDGE_LEAD
    )

    if qualifying_sides:
        side = max(qualifying_sides, key=lambda key: side_weights[key])
        supporters = qualifying_sides[side]
        selected = max(
            supporters,
            key=lambda item: (
                capped_weights.get(item["family"], 0.0),
                item["probability"],
                item["edge"],
            ),
        )
        reason = "two same-side context-qualified families passed"
    elif clear_champion:
        side = str(champion["side"])
        supporters = [champion]
        selected = champion
        reason = "72% clear same-context champion passed"
    else:
        leading_side = max(side_weights, key=side_weights.get)
        return {
            "status": "NO_CONTEXT_CHAMPION",
            "reason": "requires two same-side families or a 72% clear champion",
            "agreementWeight": (
                side_weights[leading_side] / total_weight
                if total_weight > 0
                else 0.0
            ),
            "rawWeights": raw_weights,
            "cappedWeights": capped_weights,
            "sideWeights": side_weights,
        }

    agreement = side_weights[side] / total_weight if total_weight > 0 else 0.0
    return {
        "status": "CANDIDATE",
        "reason": reason,
        "side": side,
        "selectedFamily": selected["family"],
        "selectedSourceTradeId": int(selected["source"]["id"]),
        "estimatedProbability": float(selected["probability"]),
        "agreementWeight": agreement,
        "supporters": [item["family"] for item in supporters],
        "clearChampion": clear_champion,
        "rawWeights": raw_weights,
        "cappedWeights": capped_weights,
        "sideWeights": side_weights,
        "selectedContext": list(selected["context"]),
        "selectedSourceBreakEven": float(selected["breakEven"]),
        "selectedSourceEdge": float(selected["edge"]),
    }


def rules_payload() -> dict[str, Any]:
    return {
        "entryMode": "NATIVE_EVENT_DRIVEN",
        "stakeUsdt": STAKE_USDT,
        "slippageBps": SLIPPAGE_BPS,
        "maximumBookAgeMs": MAX_BOOK_AGE_MS,
        "maximumBookSkewMs": MAX_BOOK_SKEW_MS,
        "maximumSpread": MAX_SPREAD,
        "oneTradePerMarket": True,
        "rank1": {
            "history": RANK1_HISTORY,
            "halfLife": RANK1_HALF_LIFE,
            "minimumHistory": RANK1_MIN_HISTORY,
            "minimumSupporters": RANK1_MIN_SUPPORTERS,
            "minimumDirectionShare": RANK1_DIRECTION_SHARE,
            "score": "max(0,utility/5)*max(0,2p-1)",
        },
        "rank2": {
            "history": RANK2_HISTORY,
            "halfLife": RANK2_HALF_LIFE,
            "minimumSameContextHistory": RANK2_MIN_CONTEXT_HISTORY,
            "minimumNetEdge": RANK2_EDGE_MARGIN,
            "familyCap": RANK2_FAMILY_CAP,
            "clearChampionProbability": RANK2_CLEAR_CHAMPION_PROBABILITY,
            "clearChampionEdgeLead": RANK2_CLEAR_CHAMPION_EDGE_LEAD,
            "context": ["phase", "alignment", "entryPrice", "pathEr"],
            "historyScope": "forward contexts recorded after deployment",
        },
        "trendGate": {
            "minimumElapsedSeconds": TREND_MIN_ELAPSED_SECONDS,
            "minimumMoveBps": TREND_MIN_MOVE_BPS,
            "minimumPathEr": TREND_MIN_PATH_ER,
            "minimumSamples": TREND_MIN_SAMPLES,
            "maximumSpotAgeMs": TREND_MAX_SPOT_AGE_MS,
            "missingDataPolicy": "BLOCK",
        },
    }
