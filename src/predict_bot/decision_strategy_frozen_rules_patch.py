from __future__ import annotations

import math
from functools import wraps
from typing import Any

from . import decision_strategy_shadows as _decision


PATCH_VERSION = "DECISION_STRATEGY_FROZEN_RULES_V2"
PRIOR_STRENGTH = 4.0
RANK1_HISTORY = 60
RANK1_HALF_LIFE = 20.0
RANK1_MIN_HISTORY = 10
RANK1_MIN_SUPPORTERS = 2
RANK1_DIRECTION_SHARE = 0.67
RANK2_HISTORY = 30
RANK2_HALF_LIFE = 10.0
RANK2_MIN_CONTEXT_HISTORY = 8
RANK2_MIN_SCORED_FAMILIES = 2
RANK2_CLEAR_CHAMPION_PROBABILITY = 0.72
RANK2_CLEAR_CHAMPION_EDGE_LEAD = 0.05
RANK2_FAMILY_CAP = 0.50
RANK2_EDGE_MARGIN = 0.03
HISTORY_QUERY_LIMIT = 2_000


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalized_pnl(row: dict[str, Any]) -> float | None:
    stake = _finite(row.get("stake"))
    pnl = _finite(row.get("pnl"))
    if stake is None or stake <= 0 or pnl is None:
        return None
    return max(-5.10, min(10.0, pnl * _decision.STAKE_USDT / stake))


def _exp_stats(
    rows: list[dict[str, Any]],
    *,
    limit: int,
    half_life: float,
    prior: float = PRIOR_STRENGTH,
) -> dict[str, Any]:
    selected = rows[-limit:]
    normalized: list[tuple[dict[str, Any], float]] = []
    for row in selected:
        pnl = _normalized_pnl(row)
        if pnl is not None:
            normalized.append((row, pnl))
    if not normalized:
        return {
            "n": 0,
            "utility": 0.0,
            "p": 0.5,
            "averageWin": 0.0,
            "averageLoss": -5.0,
            "weightSum": 0.0,
        }
    weights = [
        0.5 ** ((len(normalized) - 1 - index) / half_life)
        for index in range(len(normalized))
    ]
    weight_sum = sum(weights)
    utility = sum(
        weight * pnl for weight, (_, pnl) in zip(weights, normalized)
    ) / weight_sum
    weighted_wins = sum(
        weight * (1.0 if pnl > 0 else 0.0)
        for weight, (_, pnl) in zip(weights, normalized)
    )
    probability = (weighted_wins + 0.5 * prior) / (weight_sum + prior)
    wins = [
        (weight, pnl)
        for weight, (_, pnl) in zip(weights, normalized)
        if pnl > 0
    ]
    losses = [
        (weight, pnl)
        for weight, (_, pnl) in zip(weights, normalized)
        if pnl <= 0
    ]
    average_win = (
        sum(weight * pnl for weight, pnl in wins)
        / sum(weight for weight, _ in wins)
        if wins
        else 0.0
    )
    average_loss = (
        sum(weight * pnl for weight, pnl in losses)
        / sum(weight for weight, _ in losses)
        if losses
        else -5.0
    )
    return {
        "n": len(normalized),
        "utility": utility,
        "p": probability,
        "averageWin": average_win,
        "averageLoss": average_loss,
        "weightSum": weight_sum,
    }


def _context_key(values: dict[str, Any]) -> tuple[str, str, str, str] | None:
    elapsed = _finite(values.get("elapsed"))
    move = _finite(values.get("startMoveBps"))
    price = _finite(values.get("entryPrice"))
    path_er = _finite(values.get("pathEr"))
    side = str(values.get("side") or "").upper()
    if (
        elapsed is None
        or move is None
        or price is None
        or path_er is None
        or side not in {"UP", "DOWN"}
    ):
        return None
    phase = "EARLY" if elapsed < 60 else ("MID" if elapsed < 180 else "LATE")
    if abs(move) < 0.75:
        alignment = "FLAT"
    elif (side == "UP" and move > 0) or (side == "DOWN" and move < 0):
        alignment = "ALIGNED"
    else:
        alignment = "OPPOSED"
    price_bucket = (
        "P_LT030"
        if price < 0.30
        else "P_030_055"
        if price < 0.55
        else "P_055_075"
        if price < 0.75
        else "P_GE075"
    )
    er_bucket = "ER_LOW" if path_er < 0.30 else ("ER_MID" if path_er < 0.60 else "ER_HIGH")
    return phase, alignment, price_bucket, er_bucket


def _source_trade(
    self: Any,
    market_id: int,
    source_strategy: str,
) -> dict[str, Any] | None:
    row = self.store.db.execute(
        """SELECT id, strategy, topic_id, market_id, side, entry_price, stake,
                  fee_rate_bps, model_probability, model_edge, opened_at,
                  diagnostics_json
             FROM trades
            WHERE market_id=? AND strategy=?
            ORDER BY id DESC LIMIT 1""",
        (market_id, source_strategy),
    ).fetchone()
    return dict(row) if row is not None else None


def _source_context(self: Any, source: dict[str, Any]) -> dict[str, Any]:
    cache = getattr(self, "_decision_frozen_context_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        self._decision_frozen_context_cache = cache
    trade_id = int(source.get("id") or 0)
    if trade_id in cache:
        return dict(cache[trade_id])

    market_id = int(source.get("market_id") or 0)
    opened_at = str(source.get("opened_at") or "")
    rows = self.store.db.execute(
        """SELECT timestamp, start_price, spot_price, seconds_left
             FROM observations
            WHERE market_id=? AND timestamp<=?
              AND start_price IS NOT NULL AND spot_price IS NOT NULL
            ORDER BY timestamp ASC, id ASC""",
        (market_id, opened_at),
    ).fetchall()
    observations = [dict(row) for row in rows]
    if not observations:
        result = {
            "available": False,
            "side": str(source.get("side") or "").upper(),
            "entryPrice": _finite(source.get("entry_price")),
            "context": None,
        }
        cache[trade_id] = result
        return dict(result)

    latest = observations[-1]
    start_price = _finite(latest.get("start_price"))
    spot_price = _finite(latest.get("spot_price"))
    seconds_left = _finite(latest.get("seconds_left"))
    travel = 0.0
    previous: float | None = None
    for index, observation in enumerate(observations):
        current = _finite(observation.get("spot_price"))
        if current is None:
            continue
        if index == 0 and start_price is not None:
            travel += abs(current - start_price)
        elif previous is not None:
            travel += abs(current - previous)
        previous = current
    if (
        start_price is None
        or start_price <= 0
        or spot_price is None
        or seconds_left is None
    ):
        move_bps = None
        path_er = None
        elapsed = None
    else:
        move = spot_price - start_price
        move_bps = move / start_price * 10_000.0
        path_er = abs(move) / travel if travel > 0 else None
        elapsed = 300.0 - seconds_left
    result = {
        "available": (
            move_bps is not None and path_er is not None and elapsed is not None
        ),
        "side": str(source.get("side") or "").upper(),
        "entryPrice": _finite(source.get("entry_price")),
        "feeRateBps": _finite(source.get("fee_rate_bps")) or 200.0,
        "elapsed": elapsed,
        "startMoveBps": move_bps,
        "pathEr": path_er,
        "observationTimestamp": latest.get("timestamp"),
    }
    result["context"] = _context_key(result)
    cache[trade_id] = result
    return dict(result)


def _closed_history(
    self: Any,
    source_strategy: str,
    as_of: str,
) -> list[dict[str, Any]]:
    version_row = self.store.db.execute(
        """SELECT COALESCE(MAX(id), 0)
             FROM trades
            WHERE strategy=? AND closed_at IS NOT NULL AND pnl IS NOT NULL
              AND closed_at<?""",
        (source_strategy, as_of),
    ).fetchone()
    version = int(version_row[0] or 0) if version_row is not None else 0
    cache = getattr(self, "_decision_frozen_history_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        self._decision_frozen_history_cache = cache
    key = (source_strategy, version)
    if key in cache:
        return [dict(row) for row in cache[key]]
    rows = self.store.db.execute(
        """SELECT id, strategy, topic_id, market_id, side, entry_price,
                  stake, pnl, fee_rate_bps, opened_at, closed_at
             FROM trades
            WHERE strategy=? AND closed_at IS NOT NULL AND pnl IS NOT NULL
              AND closed_at<?
            ORDER BY id DESC LIMIT ?""",
        (source_strategy, as_of, HISTORY_QUERY_LIMIT),
    ).fetchall()
    history = [dict(row) for row in reversed(rows)]
    cache[key] = history
    return [dict(row) for row in history]


def _family_state(
    self: Any,
    snapshot: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    market_id = int(snapshot["market_id"])
    as_of = str(snapshot.get("timestamp") or "9999-12-31T23:59:59+00:00")
    result: dict[str, dict[str, Any]] = {}
    for family, source_strategy in _decision.FAMILY_SOURCES.items():
        source = _source_trade(self, market_id, source_strategy)
        history = _closed_history(self, source_strategy, as_of)
        rank1_history = _exp_stats(
            history,
            limit=RANK1_HISTORY,
            half_life=RANK1_HALF_LIFE,
        )
        source_context = _source_context(self, source) if source is not None else {
            "available": False,
            "context": None,
        }
        context_key = source_context.get("context")
        context_rows: list[dict[str, Any]] = []
        if isinstance(context_key, tuple):
            for row in history:
                if _source_context(self, row).get("context") == context_key:
                    context_rows.append(row)
        rank2_history = _exp_stats(
            context_rows,
            limit=RANK2_HISTORY,
            half_life=RANK2_HALF_LIFE,
        )
        result[family] = {
            "family": family,
            "sourceStrategy": source_strategy,
            "source": source,
            "history": {
                "rank1": rank1_history,
                "rank2": rank2_history,
                "sourceContext": {
                    **source_context,
                    "context": list(context_key) if isinstance(context_key, tuple) else None,
                },
            },
        }
    return result


def _rank1(
    self: Any,
    families: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    scored: list[tuple[str, dict[str, Any], dict[str, Any], float]] = []
    for family, item in families.items():
        source = item.get("source")
        stats = item.get("history", {}).get("rank1", {})
        if source is None or int(stats.get("n") or 0) < RANK1_MIN_HISTORY:
            continue
        utility = float(stats.get("utility") or 0.0)
        probability = float(stats.get("p") or 0.5)
        utility_component = max(0.0, utility / _decision.STAKE_USDT)
        confidence_component = max(0.0, 2.0 * probability - 1.0)
        score = utility_component * confidence_component
        scored.append((family, source, stats, score))
    if len(scored) < RANK1_MIN_SUPPORTERS:
        return {
            "status": "WAITING",
            "reason": "fewer than two source families have ten settled history samples",
        }
    side_weights = {
        side: sum(
            score
            for _, source, _, score in scored
            if str(source.get("side") or "").upper() == side
        )
        for side in ("UP", "DOWN")
    }
    total = sum(side_weights.values())
    if total <= 0:
        return {
            "status": "NO_POSITIVE_WEIGHT",
            "reason": "utility and shrunk hit-rate produce no positive family weight",
            "sideWeights": side_weights,
        }
    winning_side = max(side_weights, key=side_weights.get)
    direction_share = side_weights[winning_side] / total
    supporters = [
        (family, source, stats, score)
        for family, source, stats, score in scored
        if str(source.get("side") or "").upper() == winning_side and score > 0
    ]
    if (
        len(supporters) < RANK1_MIN_SUPPORTERS
        or direction_share + 1e-12 < RANK1_DIRECTION_SHARE
    ):
        return {
            "status": "NO_CONSENSUS",
            "reason": "requires two positive-weight families and at least 67% direction share",
            "agreementWeight": direction_share,
            "sideWeights": side_weights,
        }
    average_utility = sum(
        float(stats.get("utility") or 0.0)
        for _, _, stats, _ in supporters
    ) / len(supporters)
    if average_utility <= 0:
        return {
            "status": "NON_POSITIVE_SUPPORT_UTILITY",
            "reason": "supporting families have non-positive mean utility",
            "agreementWeight": direction_share,
        }
    selected_family, selected_source, selected_stats, selected_score = max(
        supporters,
        key=lambda item: (
            item[3],
            str(item[1].get("opened_at") or ""),
            int(item[1].get("id") or 0),
        ),
    )
    return {
        "status": "CANDIDATE",
        "reason": "frozen utility-weighted family consensus passed",
        "side": winning_side,
        "selectedFamily": selected_family,
        "selectedSource": selected_source,
        "estimatedProbability": float(selected_stats.get("p") or 0.5),
        "agreementWeight": direction_share,
        "selectedScore": selected_score,
        "selectedUtility": float(selected_stats.get("utility") or 0.0),
        "supporters": [family for family, _, _, _ in supporters],
        "sideWeights": side_weights,
    }


def _effective_break_even(entry: float, fee_rate_bps: float) -> float:
    return entry + min(entry, 1.0 - entry) * fee_rate_bps / 10_000.0


def _rank2(
    self: Any,
    families: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    scored: list[tuple[str, dict[str, Any], dict[str, Any], float, float]] = []
    for family, item in families.items():
        source = item.get("source")
        stats = item.get("history", {}).get("rank2", {})
        source_context = item.get("history", {}).get("sourceContext", {})
        if (
            source is None
            or not bool(source_context.get("available"))
            or int(stats.get("n") or 0) < RANK2_MIN_CONTEXT_HISTORY
        ):
            continue
        entry = _finite(source.get("entry_price"))
        if entry is None or not 0 < entry < 1:
            continue
        fee_rate_bps = _finite(source.get("fee_rate_bps")) or 200.0
        break_even = _effective_break_even(entry, fee_rate_bps)
        edge = float(stats.get("p") or 0.5) - break_even
        if edge + 1e-12 < RANK2_EDGE_MARGIN:
            continue
        scored.append((family, source, stats, edge, break_even))
    if len(scored) < RANK2_MIN_SCORED_FAMILIES:
        return {
            "status": "WAITING",
            "reason": "fewer than two context-qualified payoff-positive families",
        }
    raw = {family: edge for family, _, _, edge, _ in scored}
    capped: dict[str, float] = {}
    for family, edge in raw.items():
        others = sum(value for key, value in raw.items() if key != family)
        capped[family] = min(edge, others) if others > 0 else 0.0
    total = sum(capped.values())
    if total <= 0:
        return {
            "status": "FAMILY_CAP",
            "reason": "50% family contribution cap leaves no positive direction weight",
            "rawWeights": raw,
            "cappedWeights": capped,
        }
    side_scores = {
        side: sum(
            capped[family]
            for family, source, _, _, _ in scored
            if str(source.get("side") or "").upper() == side
        )
        for side in ("UP", "DOWN")
    }
    winning_side = max(side_scores, key=side_scores.get)
    support = [
        item
        for item in scored
        if str(item[1].get("side") or "").upper() == winning_side
        and capped[item[0]] > 0
    ]
    opposition = [
        item
        for item in scored
        if str(item[1].get("side") or "").upper() != winning_side
        and capped[item[0]] > 0
    ]
    eligible = len(support) >= 2
    if len(support) == 1:
        _, _, stats, edge, _ = support[0]
        best_opposition = max((item[3] for item in opposition), default=-1.0)
        eligible = (
            float(stats.get("p") or 0.5) >= RANK2_CLEAR_CHAMPION_PROBABILITY
            and edge - best_opposition >= RANK2_CLEAR_CHAMPION_EDGE_LEAD
        )
    if not eligible:
        return {
            "status": "NO_CHAMPION",
            "reason": "requires two same-side families or one >=72% champion with a 5pp edge lead",
            "agreementWeight": side_scores[winning_side] / total,
            "rawWeights": raw,
            "cappedWeights": capped,
            "sideWeights": side_scores,
        }
    selected_family, selected_source, selected_stats, selected_edge, break_even = max(
        support,
        key=lambda item: (
            item[3],
            float(item[2].get("p") or 0.5),
            str(item[1].get("opened_at") or ""),
            int(item[1].get("id") or 0),
        ),
    )
    return {
        "status": "CANDIDATE",
        "reason": "frozen same-context payoff-aware champion passed",
        "side": winning_side,
        "selectedFamily": selected_family,
        "selectedSource": selected_source,
        "estimatedProbability": float(selected_stats.get("p") or 0.5),
        "agreementWeight": side_scores[winning_side] / total,
        "selectedScore": selected_edge,
        "selectedUtility": float(selected_stats.get("utility") or 0.0),
        "sourceBreakEven": break_even,
        "supporters": [item[0] for item in support],
        "rawWeights": raw,
        "cappedWeights": capped,
        "sideWeights": side_scores,
    }


def _patch_dashboard_summary() -> None:
    original = _decision._decision_summary
    if getattr(original, "_frozen_decision_rules_v2", False):
        return

    @wraps(original)
    def frozen_decision_summary(store: Any, tracker: Any) -> dict[str, Any]:
        payload = original(store, tracker)
        payload["version"] = PATCH_VERSION
        common = dict(payload.get("rules") or {})
        common.update(
            {
                "minimumNetEdge": RANK2_EDGE_MARGIN,
                "entryMode": "NATIVE_EVENT_DRIVEN",
                "rank1": {
                    "historyLimit": RANK1_HISTORY,
                    "halfLife": RANK1_HALF_LIFE,
                    "prior": "Beta(2,2)",
                    "minimumHistory": RANK1_MIN_HISTORY,
                    "weightFormula": "max(0, utility/5) * max(0, 2*p-1)",
                    "minimumSupporters": RANK1_MIN_SUPPORTERS,
                    "minimumDirectionShare": RANK1_DIRECTION_SHARE,
                    "positiveMeanSupportUtility": True,
                    "familyCap": None,
                },
                "rank2": {
                    "context": [
                        "phase",
                        "alignmentToStartMove",
                        "entryPriceBucket",
                        "pathErBucket",
                    ],
                    "historyLimit": RANK2_HISTORY,
                    "halfLife": RANK2_HALF_LIFE,
                    "prior": "Beta(2,2)",
                    "minimumContextHistory": RANK2_MIN_CONTEXT_HISTORY,
                    "minimumScoredFamilies": RANK2_MIN_SCORED_FAMILIES,
                    "minimumPosteriorEdge": RANK2_EDGE_MARGIN,
                    "familyContributionCap": RANK2_FAMILY_CAP,
                    "clearChampionProbability": RANK2_CLEAR_CHAMPION_PROBABILITY,
                    "clearChampionEdgeLead": RANK2_CLEAR_CHAMPION_EDGE_LEAD,
                },
            }
        )
        payload["rules"] = common
        for strategy, stats in (payload.get("strategies") or {}).items():
            stats["rules"] = (
                common["rank1"]
                if strategy == _decision.RANK1_STRATEGY
                else common["rank2"]
            )
        return payload

    frozen_decision_summary._frozen_decision_rules_v2 = True  # type: ignore[attr-defined]
    _decision._decision_summary = frozen_decision_summary


def install_decision_strategy_frozen_rules_patch() -> None:
    _decision.DECISION_VERSION = PATCH_VERSION
    _decision.MIN_HISTORY = RANK1_MIN_HISTORY
    _decision.HISTORY_LIMIT = RANK1_HISTORY
    _decision.HISTORY_HALF_LIFE = RANK1_HALF_LIFE
    _decision.RANK2_FAMILY_CAP = RANK2_FAMILY_CAP
    _decision.RANK2_CAP_WINDOW = RANK2_HISTORY
    _decision.MIN_NET_EDGE = RANK2_EDGE_MARGIN

    tracker = _decision.DecisionStrategyTracker
    tracker._source_trade = _source_trade
    tracker._source_context = _source_context
    tracker._closed_history = _closed_history
    tracker._family_state = _family_state
    tracker._rank1 = _rank1
    tracker._rank2 = _rank2
    _patch_dashboard_summary()
