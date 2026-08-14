from __future__ import annotations

from typing import Any


COHORT = "TAKER_SIGNAL_CONSENSUS_V2"
FEATURES = (
    "direction_score",
    "futures_taker_imbalance_1s",
    "spot_taker_imbalance_250ms",
    "futures_queue_imbalance",
)
QUORUM = 3
STAKE_USDT = 5.0
TAKER_FEE_RATE = 0.02
REPEAT_COOLDOWN_MS = 10_000
FLIP_COOLDOWN_MS = 2_000
MAX_SAMPLE_AGE_MS = 1_000
MAX_PREDICT_AGE_MS = 2_000
MAX_ASK = 0.98
MIN_SECONDS_LEFT = 5.0


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def consensus(snapshot: dict[str, Any]) -> dict[str, Any]:
    votes: dict[str, str | None] = {}
    for feature in FEATURES:
        value = _number(snapshot.get(feature))
        votes[feature] = "UP" if value is not None and value > 0 else "DOWN" if value is not None and value < 0 else None
    up = sum(value == "UP" for value in votes.values())
    down = sum(value == "DOWN" for value in votes.values())
    side = "UP" if up >= QUORUM else "DOWN" if down >= QUORUM else None
    return {
        "side": side,
        "votes": votes,
        "upVotes": up,
        "downVotes": down,
        "availableVotes": up + down,
        "quorum": QUORUM,
    }


def decide(
    snapshot: dict[str, Any],
    *,
    expected_market_id: int,
    now_ms: int,
    last_trade_ms: int | None,
    last_trade_side: str | None,
) -> dict[str, Any]:
    signal_ms = int(_number(snapshot.get("sampled_at_ms")) or 0)
    sample_market_id = int(_number(snapshot.get("market_id")) or 0)
    sample_age_ms = max(0, now_ms - signal_ms) if signal_ms else None
    seconds_left = _number(snapshot.get("seconds_left"))
    predict_age_ms = _number(snapshot.get("predict_receipt_age_ms"))
    result = consensus(snapshot)
    side = result["side"]

    reason = "CONSENSUS"
    if sample_market_id != int(expected_market_id):
        reason = "MARKET_MISMATCH"
    elif sample_age_ms is None or sample_age_ms > MAX_SAMPLE_AGE_MS:
        reason = "STALE_SIGNAL"
    elif predict_age_ms is None or predict_age_ms > MAX_PREDICT_AGE_MS:
        reason = "STALE_PREDICT_BOOK"
    elif seconds_left is None or seconds_left <= MIN_SECONDS_LEFT:
        reason = "TOO_LATE"
    elif side not in {"UP", "DOWN"}:
        reason = "NO_3_OF_4_CONSENSUS"
    else:
        ask = _number(snapshot.get("predict_up_ask" if side == "UP" else "predict_down_ask"))
        if ask is None or not 0 < ask <= MAX_ASK:
            reason = "ASK_UNEXECUTABLE"
        elif last_trade_ms is not None:
            cooldown = REPEAT_COOLDOWN_MS if side == last_trade_side else FLIP_COOLDOWN_MS
            if now_ms - last_trade_ms < cooldown:
                reason = "COOLDOWN"

    return {
        **result,
        "decision": "TRADE" if reason == "CONSENSUS" else "SKIP",
        "reason": reason,
        "signalAtMs": signal_ms or None,
        "sampleAgeMs": sample_age_ms,
        "secondsLeft": seconds_left,
        "predictReceiptAgeMs": predict_age_ms,
    }


def execution(side: str, snapshot: dict[str, Any]) -> dict[str, float] | None:
    ask = _number(snapshot.get("predict_up_ask" if side == "UP" else "predict_down_ask"))
    if ask is None or not 0 < ask <= MAX_ASK:
        return None
    shares = STAKE_USDT / ask
    fee = STAKE_USDT * TAKER_FEE_RATE
    return {
        "ask": ask,
        "principalUsdt": STAKE_USDT,
        "feeUsdt": fee,
        "totalCostUsdt": STAKE_USDT + fee,
        "shares": shares,
    }
