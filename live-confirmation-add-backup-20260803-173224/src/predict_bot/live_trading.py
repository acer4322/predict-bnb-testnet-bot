from __future__ import annotations

import json
import math
import queue
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any, Callable

from .core import (
    ApiError,
    ApiHttpError,
    ApiTransportError,
    BinancePredictionTradingClient,
    taker_fee,
)
from .drawdown_control import MarketRegimeDrawdownController
from .research_forward import (
    CONFIRMATION_ADD_FEE_BPS,
    CONFIRMATION_ADD_SOURCE_STRATEGIES,
    CONFIRMATION_ADD_TRANCHE_USDT,
    FUTURES_LEAD_LIVE_OBSERVER_STRATEGIES,
    FUTURES_LEAD_OBSERVER_VERSIONS,
    confirmation_add_book_event_key,
    confirmation_add_book_is_safe,
    confirmation_add_execution_price,
    confirmation_add_levels,
    futures_lead_observer_decision,
)


LIVE_DEFAULT_MAX_STAKE_USDT = Decimal("1.00")
LIVE_MIN_CONFIGURABLE_STAKE_USDT = Decimal("0.01")
LIVE_MAX_CONFIGURABLE_STAKE_USDT = Decimal("100.00")
LIVE_MAX_SELECTED_STRATEGIES = 4
LIVE_DEFAULT_STRATEGY = "M01O_F1"
LIVE_RESEARCH_STRATEGIES = (
    "R_MICROPRICE",
    "R_FUTURES_LEAD",
    "R_FUTURES_LEAD_REVERSE",
    "R_FUTURES_LEAD_REGIME_REVERSE_3L",
    "R_FUTURES_LEAD_DISTANCE",
    "R_FUTURES_LEAD_SIGNAL_100",
    "R_FUTURES_LEAD_MIN_ENTRY_020",
    "R_OFI",
    "R_CALIBRATED_VALUE",
    "R_OFI_EVENT_CUM",
)
LIVE_OBSERVER_STRATEGIES = (
    *FUTURES_LEAD_LIVE_OBSERVER_STRATEGIES,
    "R_MICROPRICE",
    "R_OFI",
    "R_CALIBRATED_VALUE",
)
FUTURES_LEAD_HEDGE_STRATEGIES = (
    "R_FUTURES_LEAD",
    "R_FUTURES_LEAD_REVERSE",
)
LIVE_RESEARCH_REPRICE_GAPS = {
    "R_MICROPRICE": Decimal("0.05"),
    "R_FUTURES_LEAD": Decimal("0.05"),
    "R_FUTURES_LEAD_REVERSE": Decimal("0.05"),
    "R_FUTURES_LEAD_REGIME_REVERSE_3L": Decimal("0.05"),
    "R_FUTURES_LEAD_DISTANCE": Decimal("0.05"),
    "R_FUTURES_LEAD_SIGNAL_100": Decimal("0.05"),
    "R_FUTURES_LEAD_MIN_ENTRY_020": Decimal("0.05"),
    "R_OFI": Decimal("0.05"),
    "R_CALIBRATED_VALUE": Decimal("0.05"),
    "R_OFI_EVENT_CUM": Decimal("0.05"),
}
LIVE_RESEARCH_PRICE_RANGES = {
    "R_OFI_EVENT_CUM": (Decimal("0.40"), Decimal("0.69")),
    "R_FUTURES_LEAD_MIN_ENTRY_020": (Decimal("0.20"), Decimal("0.55")),
}
LIVE_RELIABILITY_TAGS = {
    "RC_LOW_ENTRY": {
        "strategy": "R_CALIBRATED_VALUE",
        "status": "candidate",
        "title": "低進場價可靠區",
        "condition": "entry_price <= 0.376875",
        "matchDecision": "ALLOW",
    },
    "RC_STALE_QUOTE": {
        "strategy": "R_CALIBRATED_VALUE",
        "status": "warning",
        "title": "報價過舊失準警戒",
        "condition": "book_age_ms > 828.5",
        "matchDecision": "BLOCK",
    },
    "FL_DIRECTION": {
        "strategy": "R_FUTURES_LEAD",
        "status": "limited",
        "title": "方向差異觀測",
        "condition": "side = DOWN",
        "matchDecision": "ALLOW",
    },
    "MP_LATE_WINDOW": {
        "strategy": "R_MICROPRICE",
        "status": "candidate",
        "title": "較晚進場可靠區",
        "condition": "seconds_left <= 178.432",
        "matchDecision": "ALLOW",
    },
    "MP_FRESH_BOOK": {
        "strategy": "R_MICROPRICE",
        "status": "candidate",
        "title": "新鮮訂單簿可靠區",
        "condition": "book_age_ms <= 422.5",
        "matchDecision": "ALLOW",
    },
    "MP_MIDPRICE_WEAK": {
        "strategy": "R_MICROPRICE",
        "status": "warning",
        "title": "中低價開發假象警戒",
        "condition": "0.286425 < entry_price <= 0.39195",
        "matchDecision": "BLOCK",
    },
}
LIVE_RELIABILITY_CANDIDATE_TAGS = (
    "RC_LOW_ENTRY",
    "MP_LATE_WINDOW",
    "MP_FRESH_BOOK",
)
LIVE_SUPPORTED_STRATEGIES = (
    "M01O_F1",
    "PAIR_ARB_010",
    "PAIR_ARB_QC_015",
    "PAIR_ARB_020",
    *LIVE_RESEARCH_STRATEGIES,
)
# Backward-compatible names used by existing scripts/tests and old ledger rows.
LIVE_M0W_MAX_STAKE_USDT = LIVE_DEFAULT_MAX_STAKE_USDT
LIVE_M0W_AMOUNT_WEI = 1_000_000_000_000_000_000
LIVE_M0W_STRATEGY = LIVE_DEFAULT_STRATEGY
LIVE_M0W_GATE_VERSION = "M0W_GATE_V2_ADJACENT_OFFICIAL_WIN"
LIVE_MARKET_DURATION_MS = 300_000
LIVE_MARKET_ADJACENCY_TOLERANCE_MS = 2_000
LIVE_MAX_QUOTE_PRICE_GAP = Decimal("0.10")
LIVE_MAX_PREDICTION_BOOK_AGE_MS = 2_000.0
LIVE_MAX_DRAWDOWN_SPOT_AGE_MS = 2_000.0
LIVE_MAX_DRAWDOWN_SPOT_BOOK_AGE_MS = 500.0
LIVE_MAX_SPOT_DATA_AGE_MS = 4_000.0
LIVE_MIN_DEPTH_COVERAGE_RATIO = Decimal("1.00")
VERIFIED_PREDICTION_ORIENTATIONS = {
    "DIRECT_UP_VERIFIED",
    "INVERTED_TO_UP_VERIFIED",
}
PAIR_ARB_MIN_NET_EDGE = {
    "PAIR_ARB_010": Decimal("0.010"),
    "PAIR_ARB_QC_015": Decimal("0.015"),
    "PAIR_ARB_020": Decimal("0.020"),
    "PAIR_ARB_RISK_020": Decimal("-0.020"),
}
PAIR_ARB_RISK_PRICE_TOLERANCE = Decimal("0.10")
PAIR_ARB_MIN_QUOTE_CAPACITY_RATIO = Decimal("0.70")
PAIR_ARB_QC_MIN_QUOTE_CAPACITY_RATIO = Decimal("0.95")
PAIR_ARB_QC_MIN_LOCKED_PNL = Decimal("0.05")
PAIR_ARB_QC_MIN_LOCKED_ROI = Decimal("0.015")
PAIR_ARB_QC_MAX_NET_SHARE_MISMATCH_RATIO = Decimal("0.0025")
PAIR_ARB_QC_MIN_QUOTE_EXPIRY_MS = 1_500
PAIR_ARB_QC_MAX_QUOTE_RTT_SECONDS = Decimal("0.300")
PAIR_ARB_QC_NETWORK_AND_ROUNDING_BUFFER = Decimal("0.01")
PAIR_ARB_QC_MIN_SHADOW_SAMPLES = 200
PAIR_ARB_FILL_MISMATCH_GRACE_SECONDS = 1.0
PAIR_INSURANCE_WINDOW_SECONDS = Decimal("30")
PAIR_INSURANCE_HIGH_BID_MIN = Decimal("0.80")
PAIR_INSURANCE_DELAY_SECONDS = 3.0
PAIR_INSURANCE_CHECK_SECONDS = 1.0
PAIR_INSURANCE_ROUNDING_GUARD = Decimal("0.00500001")
M01O_F1_MIN_SECONDS_LEFT = Decimal("30")
LIVE_SIGNAL_QUEUE_MAX = 100
LIVE_ORDER_SYNC_SECONDS = 5.0
LIVE_IDLE_SYNC_SECONDS = 30.0
LIVE_ACCOUNT_REFRESH_SECONDS = 30.0
LIVE_SETTLEMENT_SYNC_SECONDS = 15.0
LIVE_HOURLY_GUARD_REFRESH_SECONDS = 30.0
LIVE_AUTO_REDEEM_DELAY_SECONDS = 60.0
LIVE_REDEEM_SCAN_SECONDS = 15.0
LIVE_REDEEM_MAX_PER_SCAN = 20
LIVE_ORDER_SYNC_RESTART_THRESHOLD = 5
M0_HOURLY_MIN_WIN_RATE_PCT = 50.0
M0_HOURLY_MAX_WIN_THEN_LOSS_RATE_PCT = 50.0
REDEEM_PENDING_STATUSES = {
    "ATTEMPTED",
    "AMBIGUOUS",
    "SUBMITTED",
    "PENDING",
    "PROCESSING",
}
REDEEM_SUCCESS_STATUSES = {
    "SUCCESS",
    "SUCCEEDED",
    "COMPLETED",
    "CONFIRMED",
    "CLAIMED",
    "REDEEMED",
    "FINALIZED",
}
REDEEM_FAILURE_STATUSES = {
    "FAILED",
    "REJECTED",
    "ERROR",
    "CANCELED",
    "CANCELLED",
}
ACTIVE_ORDER_STATUSES = {
    "SIGNAL_RECEIVED",
    "QUOTE_REQUESTING",
    "QUOTE_ACCEPTED",
    "PLACE_ATTEMPTED",
    "SUBMITTED",
    "PLACED_PENDING_SYNC",
    "OPENING",
    "OPEN",
    "PARTIAL",
    "PARTIALLY_FILLED",
    "PENDING",
}


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stake_amount_wei(stake: Decimal) -> int:
    scaled = stake * Decimal(10**18)
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ValueError("maxStakeUsdt supports at most 18 decimal places")
    return int(integral)


def estimate_buy_vwap(
    levels: Any, stake_usdt: Decimal | float | str
) -> dict[str, Any]:
    """Estimate a BUY VWAP from immutable local ask levels."""
    stake = _decimal(stake_usdt)
    if stake is None or stake <= 0:
        return {
            "estimated_vwap": None,
            "covered_stake": 0.0,
            "capacity_ratio": 0.0,
            "levels_consumed": 0,
        }
    valid_levels: list[tuple[Decimal, Decimal]] = []
    for raw in list(levels) if isinstance(levels, (list, tuple)) else []:
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            continue
        price = _decimal(raw[0])
        size = _decimal(raw[1])
        if (
            price is None
            or size is None
            or not Decimal("0") < price < Decimal("1")
            or size <= 0
        ):
            continue
        valid_levels.append((price, size))

    remaining = stake
    covered = Decimal("0")
    shares = Decimal("0")
    consumed = 0
    for price, size in sorted(valid_levels, key=lambda level: level[0]):
        level_cost = price * size
        take_cost = min(remaining, level_cost)
        if take_cost <= 0:
            break
        covered += take_cost
        shares += take_cost / price
        remaining -= take_cost
        consumed += 1
        if remaining <= Decimal("0"):
            break
    vwap = covered / shares if shares > 0 else None
    return {
        "estimated_vwap": float(vwap) if vwap is not None else None,
        "covered_stake": float(covered),
        "capacity_ratio": float(min(covered / stake, Decimal("1"))),
        "levels_consumed": consumed,
    }


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError("boolean setting must be true or false")


def normalize_live_rules(
    values: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    candidate = {
        "strategy": LIVE_DEFAULT_STRATEGY,
        "maxStakeUsdt": float(LIVE_DEFAULT_MAX_STAKE_USDT),
        "minHourlyWinRatePct": M0_HOURLY_MIN_WIN_RATE_PCT,
        "maxHourlyWinThenLossRatePct": M0_HOURLY_MAX_WIN_THEN_LOSS_RATE_PCT,
        "futuresLeadObserverEnabled": False,
        "futuresLeadObserverVersion": "F1",
        "strategyDrawdownControlEnabled": [False],
        "strategyLossCooldownEnabled": [False],
        "reliabilityGateTags": [],
        **(current or {}),
        **values,
    }
    raw_strategies = candidate.get("strategies")
    if "strategies" in values:
        raw_strategies = values["strategies"]
    elif "strategy" in values:
        raw_strategies = [values["strategy"]]
    if isinstance(raw_strategies, str):
        try:
            decoded = json.loads(raw_strategies)
            raw_strategies = decoded if isinstance(decoded, list) else [raw_strategies]
        except json.JSONDecodeError:
            raw_strategies = [raw_strategies]
    if not isinstance(raw_strategies, list):
        raw_strategies = [candidate.get("strategy")]
    strategies = list(dict.fromkeys(
        str(value or "").strip().upper() for value in raw_strategies
        if str(value or "").strip()
    ))
    if not 1 <= len(strategies) <= LIVE_MAX_SELECTED_STRATEGIES:
        raise ValueError(
            "strategies must contain between one and "
            f"{LIVE_MAX_SELECTED_STRATEGIES} unique strategies"
        )
    if (
        "R_FUTURES_LEAD_REVERSE" in strategies
        and tuple(strategies[:2]) != FUTURES_LEAD_HEDGE_STRATEGIES
    ):
        raise ValueError(
            "R_FUTURES_LEAD_REVERSE is dependent and must be strategy 2 "
            "after R_FUTURES_LEAD"
        )
    if any(strategy not in LIVE_SUPPORTED_STRATEGIES for strategy in strategies):
        raise ValueError(
            "strategy must be one of: " + ", ".join(LIVE_SUPPORTED_STRATEGIES)
        )
    raw_stakes = candidate.get("strategyStakesUsdt")
    if "strategyStakesUsdt" in values:
        raw_stakes = values["strategyStakesUsdt"]
    elif "maxStakeUsdt" in values:
        raw_stakes = [values["maxStakeUsdt"]]
    if isinstance(raw_stakes, str):
        try:
            decoded_stakes = json.loads(raw_stakes)
            raw_stakes = decoded_stakes if isinstance(decoded_stakes, list) else [raw_stakes]
        except json.JSONDecodeError:
            raw_stakes = [raw_stakes]
    if not isinstance(raw_stakes, list):
        raw_stakes = [candidate.get("maxStakeUsdt")]
    fallback_stake = raw_stakes[0] if raw_stakes else candidate.get("maxStakeUsdt")
    raw_stakes = (raw_stakes + [fallback_stake] * len(strategies))[:len(strategies)]
    stakes: list[Decimal] = []
    for value in raw_stakes:
        stake = _decimal(value)
        if stake is None or not (
            LIVE_MIN_CONFIGURABLE_STAKE_USDT
            <= stake
            <= LIVE_MAX_CONFIGURABLE_STAKE_USDT
        ):
            raise ValueError(
                "each strategy stake must be between "
                f"{LIVE_MIN_CONFIGURABLE_STAKE_USDT} and "
                f"{LIVE_MAX_CONFIGURABLE_STAKE_USDT}"
            )
        _stake_amount_wei(stake)
        stakes.append(stake)
    min_win_rate = _float(candidate.get("minHourlyWinRatePct"))
    max_win_then_loss = _float(
        candidate.get("maxHourlyWinThenLossRatePct")
    )
    if min_win_rate is None or not 0 <= min_win_rate <= 100:
        raise ValueError("minHourlyWinRatePct must be between 0 and 100")
    if max_win_then_loss is None or not 0 <= max_win_then_loss <= 100:
        raise ValueError(
            "maxHourlyWinThenLossRatePct must be between 0 and 100"
        )
    observer_enabled = _boolean(candidate.get("futuresLeadObserverEnabled"))
    observer_version = str(
        candidate.get("futuresLeadObserverVersion") or ""
    ).strip().upper()
    if observer_version not in FUTURES_LEAD_OBSERVER_VERSIONS:
        raise ValueError(
            "futuresLeadObserverVersion must be one of: "
            + ", ".join(FUTURES_LEAD_OBSERVER_VERSIONS)
        )
    raw_observer_enabled = candidate.get("strategyObserverEnabled")
    if "strategyObserverEnabled" in values:
        raw_observer_enabled = values["strategyObserverEnabled"]
    elif "futuresLeadObserverEnabled" in values:
        raw_observer_enabled = [observer_enabled] * len(strategies)
    if isinstance(raw_observer_enabled, str):
        try:
            decoded_enabled = json.loads(raw_observer_enabled)
            raw_observer_enabled = decoded_enabled
        except json.JSONDecodeError:
            raw_observer_enabled = None
    if not isinstance(raw_observer_enabled, list):
        raw_observer_enabled = [observer_enabled] * len(strategies)
    strategy_observer_enabled = [
        _boolean(value)
        for value in (
            raw_observer_enabled
            + [False] * len(strategies)
        )[:len(strategies)]
    ]
    if len(strategy_observer_enabled) >= LIVE_MAX_SELECTED_STRATEGIES:
        # Slot 4 is execution-only and never applies an Observer gate.
        strategy_observer_enabled[LIVE_MAX_SELECTED_STRATEGIES - 1] = False

    raw_observer_versions = candidate.get("strategyObserverVersions")
    if "strategyObserverVersions" in values:
        raw_observer_versions = values["strategyObserverVersions"]
    elif "futuresLeadObserverVersion" in values:
        raw_observer_versions = [observer_version] * len(strategies)
    if isinstance(raw_observer_versions, str):
        try:
            decoded_versions = json.loads(raw_observer_versions)
            raw_observer_versions = decoded_versions
        except json.JSONDecodeError:
            raw_observer_versions = None
    if not isinstance(raw_observer_versions, list):
        raw_observer_versions = [observer_version] * len(strategies)
    strategy_observer_versions = [
        str(value or "F1").strip().upper()
        for value in (
            raw_observer_versions
            + ["F1"] * len(strategies)
        )[:len(strategies)]
    ]
    invalid_observer_versions = sorted(
        set(strategy_observer_versions) - set(FUTURES_LEAD_OBSERVER_VERSIONS)
    )
    if invalid_observer_versions:
        raise ValueError(
            "each strategy observer version must be one of: "
            + ", ".join(FUTURES_LEAD_OBSERVER_VERSIONS)
        )
    raw_drawdown_enabled = candidate.get("strategyDrawdownControlEnabled")
    if "strategyDrawdownControlEnabled" in values:
        raw_drawdown_enabled = values["strategyDrawdownControlEnabled"]
    if isinstance(raw_drawdown_enabled, str):
        try:
            raw_drawdown_enabled = json.loads(raw_drawdown_enabled)
        except json.JSONDecodeError:
            raw_drawdown_enabled = None
    if not isinstance(raw_drawdown_enabled, list):
        raw_drawdown_enabled = [False] * len(strategies)
    strategy_drawdown_control_enabled = [
        _boolean(value)
        for value in (
            raw_drawdown_enabled + [False] * len(strategies)
        )[:len(strategies)]
    ]
    raw_loss_cooldown_enabled = candidate.get("strategyLossCooldownEnabled")
    if "strategyLossCooldownEnabled" in values:
        raw_loss_cooldown_enabled = values["strategyLossCooldownEnabled"]
    if isinstance(raw_loss_cooldown_enabled, str):
        try:
            raw_loss_cooldown_enabled = json.loads(raw_loss_cooldown_enabled)
        except json.JSONDecodeError:
            raw_loss_cooldown_enabled = None
    if not isinstance(raw_loss_cooldown_enabled, list):
        raw_loss_cooldown_enabled = [False] * len(strategies)
    strategy_loss_cooldown_enabled = [
        _boolean(value)
        for value in (
            raw_loss_cooldown_enabled + [False] * len(strategies)
        )[:len(strategies)]
    ]
    raw_reliability_tags = candidate.get("reliabilityGateTags")
    if isinstance(raw_reliability_tags, str):
        try:
            raw_reliability_tags = json.loads(raw_reliability_tags)
        except json.JSONDecodeError:
            raw_reliability_tags = None
    if not isinstance(raw_reliability_tags, list):
        raw_reliability_tags = []
    reliability_gate_tags = list(dict.fromkeys(
        str(value or "").strip().upper()
        for value in raw_reliability_tags
        if str(value or "").strip()
    ))
    invalid_reliability_tags = sorted(
        set(reliability_gate_tags) - set(LIVE_RELIABILITY_CANDIDATE_TAGS)
    )
    if invalid_reliability_tags:
        raise ValueError(
            "reliabilityGateTags may only contain: "
            + ", ".join(LIVE_RELIABILITY_CANDIDATE_TAGS)
        )
    return {
        "strategy": strategies[0],
        "strategies": strategies,
        "maxStakeUsdt": float(stakes[0]),
        "strategyStakesUsdt": [float(stake) for stake in stakes],
        "minHourlyWinRatePct": float(min_win_rate),
        "maxHourlyWinThenLossRatePct": float(max_win_then_loss),
        # The scalar fields remain aliases for strategy slot 1 so old clients
        # and persisted ledgers continue to load without changing behavior.
        "futuresLeadObserverEnabled": strategy_observer_enabled[0],
        "futuresLeadObserverVersion": strategy_observer_versions[0],
        "strategyObserverEnabled": strategy_observer_enabled,
        "strategyObserverVersions": strategy_observer_versions,
        "strategyDrawdownControlEnabled": strategy_drawdown_control_enabled,
        "strategyLossCooldownEnabled": strategy_loss_cooldown_enabled,
        "reliabilityGateTags": reliability_gate_tags,
    }


def evaluate_m0_hourly_guard(
    performance: dict[str, Any],
    at: str | datetime | None = None,
    *,
    min_win_rate_pct: float = M0_HOURLY_MIN_WIN_RATE_PCT,
    max_win_then_loss_rate_pct: float = M0_HOURLY_MAX_WIN_THEN_LOSS_RATE_PCT,
) -> dict[str, Any]:
    """Evaluate the real-order guard against the matching Taipei hour bucket."""
    if at is None:
        instant = datetime.now(timezone.utc)
    elif isinstance(at, datetime):
        instant = at
    else:
        instant = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local = instant.astimezone(timezone(timedelta(hours=8)))
    hour = int(local.hour)
    buckets = performance.get("hours") if isinstance(performance, dict) else None
    bucket = next(
        (
            item
            for item in (buckets if isinstance(buckets, list) else [])
            if isinstance(item, dict) and int(item.get("hour", -1)) == hour
        ),
        {},
    )
    win_rate = _float(bucket.get("winRatePct"))
    win_then_loss_rate = _float(bucket.get("winThenLossRatePct"))
    reasons: list[str] = []
    if win_then_loss_rate is not None and (
        win_then_loss_rate > max_win_then_loss_rate_pct
    ):
        reasons.append(
            f"一勝一敗率 {win_then_loss_rate:.2f}% > "
            f"{max_win_then_loss_rate_pct:.2f}%"
        )
    if win_rate is not None and win_rate < min_win_rate_pct:
        reasons.append(
            f"M0 勝率 {win_rate:.2f}% < {min_win_rate_pct:.2f}%"
        )
    blocked = bool(reasons)
    return {
        "status": "BLOCKED" if blocked else "ALLOW",
        "blocked": blocked,
        "timezone": "Asia/Taipei",
        "utcOffset": "+08:00",
        "hour": hour,
        "label": str(bucket.get("label") or f"{hour:02d}:00–{hour:02d}:59"),
        "evaluatedAt": local.isoformat(),
        "settledTrades": int(bucket.get("settledTrades") or 0),
        "wins": int(bucket.get("wins") or 0),
        "losses": int(bucket.get("losses") or 0),
        "winRatePct": win_rate,
        "winThenLossCount": int(bucket.get("winThenLossCount") or 0),
        "winThenLossOpportunities": int(
            bucket.get("winThenLossOpportunities") or 0
        ),
        "winThenLossRatePct": win_then_loss_rate,
        "minWinRatePct": float(min_win_rate_pct),
        "maxWinThenLossRatePct": float(max_win_then_loss_rate_pct),
        "reasons": reasons,
        "resetAt": performance.get("resetAt") if isinstance(performance, dict) else None,
    }


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _float(value: Any) -> float | None:
    result = _decimal(value)
    return float(result) if result is not None else None


def evaluate_live_reliability_tags(
    *,
    strategy: Any,
    side: Any,
    entry_price: Any,
    seconds_left: Any,
    book_age_ms: Any,
) -> list[dict[str, Any]]:
    """Evaluate frozen research tags against one real filled-order snapshot."""
    normalized_strategy = str(strategy or "").strip().upper()
    normalized_side = str(side or "").strip().upper()
    entry = _float(entry_price)
    seconds = _float(seconds_left)
    book_age = _float(book_age_ms)
    decisions: list[dict[str, Any]] = []
    for tag_id, definition in LIVE_RELIABILITY_TAGS.items():
        if definition["strategy"] != normalized_strategy:
            continue
        matched: bool | None
        if tag_id == "RC_LOW_ENTRY":
            matched = entry <= 0.376875 if entry is not None else None
        elif tag_id == "RC_STALE_QUOTE":
            matched = book_age > 828.5 if book_age is not None else None
        elif tag_id == "FL_DIRECTION":
            matched = normalized_side == "DOWN" if normalized_side else None
        elif tag_id == "MP_LATE_WINDOW":
            matched = seconds <= 178.432 if seconds is not None else None
        elif tag_id == "MP_FRESH_BOOK":
            matched = book_age <= 422.5 if book_age is not None else None
        elif tag_id == "MP_MIDPRICE_WEAK":
            matched = (
                0.286425 < entry <= 0.39195 if entry is not None else None
            )
        else:  # pragma: no cover - definitions and evaluator stay paired.
            matched = None
        match_decision = str(definition["matchDecision"])
        decision = (
            "UNAVAILABLE"
            if matched is None
            else match_decision
            if matched
            else "BLOCK" if match_decision == "ALLOW" else "ALLOW"
        )
        decisions.append(
            {
                "id": tag_id,
                "conditionMatched": matched,
                "decision": decision,
            }
        )
    return decisions


def _mask_wallet(value: str | None) -> str | None:
    if not value:
        return None
    return f"{value[:8]}…{value[-4:]}" if len(value) > 14 else "configured"


def _safe_payload(value: dict[str, Any]) -> str:
    """Persist only documented response fields; never wallet or quote IDs."""
    allowed = {
        "orderId",
        "status",
        "orderType",
        "side",
        "marketId",
        "marketTopicId",
        "marketTitle",
        "makerUsdtAmount",
        "makerShareQty",
        "filledUsdtAmount",
        "filledShareQty",
        "fillPercentage",
        "price",
        "marketProviderFee",
        "networkFee",
        "costBasis",
        "realizedPnl",
        "errorMessage",
        "createTime",
        "modifyTime",
        "terminalTime",
        "signalPrice",
        "signalBookAgeMs",
        "latestLocalAsk",
        "latestLocalAskSize",
        "latestLocalBookAgeMs",
        "latestMarketId",
        "orientation",
        "maximumExecutionPrice",
        "eventToLocalCheckMs",
        "topLevelCapacityUsdt",
        "topLevelCapacityRatio",
        "depthCoverageRatio",
        "depthCoveredStakeUsdt",
        "depthLevelsConsumed",
        "minimumDepthCoverageRatio",
        "configuredStake",
        "estimatedVwap",
        "estimatedVwapCapacityRatio",
        "estimatedVwapCoveredStake",
        "estimatedVwapLevelsConsumed",
        "vwapAvailable",
        "pairDynamicTargetShares",
        "pairDynamicLegStakeUsdt",
        "pairDynamicTotalStakeUsdt",
        "pairDynamicMaximumStakeUsdt",
        "pairDynamicLockedPnlUsdt",
    }
    return json.dumps(
        {key: value[key] for key in allowed if key in value},
        ensure_ascii=False,
        sort_keys=True,
    )


def _safe_redeem_payload(value: dict[str, Any]) -> str:
    """Persist redeem identifiers/statuses without credentials or wallet data."""
    results: list[dict[str, Any]] = []
    for raw in value.get("results") or []:
        if not isinstance(raw, dict):
            continue
        results.append(
            {
                key: raw[key]
                for key in ("requestId", "txHash", "status", "error")
                if key in raw
            }
        )
    return json.dumps(
        {
            **({"batchId": value["batchId"]} if value.get("batchId") else {}),
            "results": results,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


class LiveLedger:
    """A physically separate SQLite ledger for real-money M-series activity."""

    ORDER_COLUMNS = {
        "status",
        "quote_average_price",
        "quote_amount_in_wei",
        "quote_amount_out_wei",
        "quote_expires_at",
        "order_id",
        "maker_usdt_amount",
        "filled_usdt_amount",
        "filled_share_qty",
        "fill_percentage",
        "market_provider_fee",
        "network_fee",
        "realized_pnl",
        "attempted_at",
        "submitted_at",
        "error_kind",
        "error_message",
        "response_json",
    }
    REDEEM_COLUMNS = {
        "status",
        "shares",
        "claimable_value",
        "attempted_at",
        "attempt_count",
        "batch_id",
        "request_id",
        "tx_hash",
        "exchange_status",
        "completed_at",
        "error_kind",
        "error_message",
        "response_json",
    }

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA busy_timeout=5000")
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS live_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL,
                    topic_id INTEGER NOT NULL,
                    market_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    signal_price REAL NOT NULL,
                    max_stake_usdt REAL NOT NULL,
                    requested_amount_wei TEXT NOT NULL,
                    status TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    time_in_force TEXT NOT NULL,
                    account_type TEXT NOT NULL,
                    quote_average_price REAL,
                    quote_amount_in_wei TEXT,
                    quote_amount_out_wei TEXT,
                    quote_expires_at INTEGER,
                    order_id TEXT,
                    maker_usdt_amount REAL,
                    filled_usdt_amount REAL,
                    filled_share_qty REAL,
                    fill_percentage REAL,
                    market_provider_fee REAL,
                    network_fee REAL,
                    realized_pnl REAL,
                    signal_at TEXT NOT NULL,
                    attempted_at TEXT,
                    submitted_at TEXT,
                    updated_at TEXT NOT NULL,
                    error_kind TEXT,
                    error_message TEXT,
                    response_json TEXT,
                    UNIQUE(strategy, market_id)
                );
                CREATE INDEX IF NOT EXISTS live_orders_order_id_idx
                    ON live_orders(order_id);
                CREATE TABLE IF NOT EXISTS live_manual_exits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_local_id INTEGER NOT NULL UNIQUE,
                    strategy TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    time_in_force TEXT NOT NULL,
                    status TEXT NOT NULL,
                    sell_shares REAL NOT NULL,
                    price_limit REAL,
                    quote_average_price REAL,
                    quote_amount_in_wei TEXT,
                    quote_amount_out_wei TEXT,
                    quote_expires_at INTEGER,
                    order_id TEXT,
                    filled_usdt_amount REAL,
                    filled_share_qty REAL,
                    fill_percentage REAL,
                    realized_pnl REAL,
                    requested_at TEXT NOT NULL,
                    attempted_at TEXT,
                    submitted_at TEXT,
                    updated_at TEXT NOT NULL,
                    error_kind TEXT,
                    error_message TEXT,
                    response_json TEXT,
                    FOREIGN KEY(order_local_id) REFERENCES live_orders(id)
                );
                CREATE INDEX IF NOT EXISTS live_manual_exits_order_id_idx
                    ON live_manual_exits(order_id);
                CREATE TABLE IF NOT EXISTS live_strategy_settlements (
                    order_local_id INTEGER PRIMARY KEY,
                    market_id INTEGER NOT NULL,
                    position_status TEXT NOT NULL,
                    result TEXT NOT NULL,
                    cost_usdt REAL NOT NULL,
                    payout_usdt REAL NOT NULL,
                    pnl_usdt REAL NOT NULL,
                    roi_pct REAL NOT NULL,
                    settled_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(order_local_id) REFERENCES live_orders(id)
                );
                CREATE INDEX IF NOT EXISTS live_strategy_settlements_market_idx
                    ON live_strategy_settlements(market_id);
                CREATE TABLE IF NOT EXISTS live_reliability_samples (
                    order_local_id INTEGER PRIMARY KEY,
                    strategy TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    signal_price REAL,
                    executed_entry_price REAL,
                    seconds_left REAL,
                    book_age_ms REAL,
                    exchange_status TEXT,
                    filled_cost_usdt REAL,
                    captured_at TEXT,
                    settlement_result TEXT,
                    settlement_cost_usdt REAL,
                    settlement_pnl_usdt REAL,
                    settlement_roi_pct REAL,
                    settled_at TEXT,
                    tag_decisions_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(order_local_id) REFERENCES live_orders(id)
                );
                CREATE INDEX IF NOT EXISTS live_reliability_samples_strategy_idx
                    ON live_reliability_samples(strategy, order_local_id DESC);
                CREATE TABLE IF NOT EXISTS live_confirmation_add_mirrors (
                    order_local_id INTEGER PRIMARY KEY,
                    strategy TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    base_price REAL NOT NULL,
                    levels_json TEXT NOT NULL,
                    fills_json TEXT NOT NULL,
                    last_event_key TEXT,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    settlement_result TEXT,
                    hypothetical_stake_usdt REAL NOT NULL DEFAULT 0,
                    hypothetical_fees_usdt REAL NOT NULL DEFAULT 0,
                    hypothetical_shares REAL NOT NULL DEFAULT 0,
                    hypothetical_payout_usdt REAL,
                    hypothetical_pnl_usdt REAL,
                    hypothetical_roi_pct REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    settled_at TEXT,
                    FOREIGN KEY(order_local_id) REFERENCES live_orders(id)
                );
                CREATE INDEX IF NOT EXISTS live_confirmation_add_market_idx
                    ON live_confirmation_add_mirrors(market_id, status);
                CREATE TABLE IF NOT EXISTS live_strategy_loss_cooldown_results (
                    order_local_id INTEGER PRIMARY KEY,
                    strategy TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    result TEXT NOT NULL CHECK (result IN ('WIN', 'LOSS')),
                    processed_at TEXT NOT NULL,
                    FOREIGN KEY(order_local_id) REFERENCES live_orders(id)
                );
                CREATE INDEX IF NOT EXISTS live_strategy_loss_cooldown_results_strategy_idx
                    ON live_strategy_loss_cooldown_results(strategy, order_local_id);
                CREATE TABLE IF NOT EXISTS live_strategy_loss_cooldown_state (
                    strategy TEXT PRIMARY KEY,
                    consecutive_losses INTEGER NOT NULL DEFAULT 0,
                    last_result TEXT,
                    last_settlement_order_local_id INTEGER,
                    last_settlement_market_id INTEGER,
                    last_skipped_market_id INTEGER,
                    last_skipped_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    market_id INTEGER,
                    message TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_attempt_telemetry (
                    order_local_id INTEGER PRIMARY KEY,
                    captured_at TEXT NOT NULL,
                    final_outcome TEXT NOT NULL,
                    telemetry_json TEXT NOT NULL,
                    FOREIGN KEY(order_local_id) REFERENCES live_orders(id)
                );
                CREATE INDEX IF NOT EXISTS live_attempt_telemetry_captured_idx
                    ON live_attempt_telemetry(order_local_id DESC);
                CREATE TABLE IF NOT EXISTS live_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_redeems (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id TEXT NOT NULL UNIQUE,
                    market_id INTEGER,
                    topic_id INTEGER,
                    chain_id TEXT NOT NULL,
                    outcome_name TEXT,
                    shares REAL NOT NULL DEFAULT 0,
                    claimable_value REAL NOT NULL DEFAULT 0,
                    end_date_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    eligible_at TEXT NOT NULL,
                    attempted_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    batch_id TEXT,
                    request_id TEXT,
                    tx_hash TEXT,
                    exchange_status TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    error_kind TEXT,
                    error_message TEXT,
                    response_json TEXT
                );
                CREATE INDEX IF NOT EXISTS live_redeems_status_idx
                    ON live_redeems(status, id);
                CREATE INDEX IF NOT EXISTS live_redeems_tx_hash_idx
                    ON live_redeems(tx_hash);
                CREATE TABLE IF NOT EXISTS live_pair_insurance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    low_side TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    armed_at TEXT NOT NULL,
                    attempted_at TEXT,
                    submitted_at TEXT,
                    order_id TEXT,
                    sell_shares REAL NOT NULL,
                    price_limit REAL NOT NULL,
                    high_bid REAL NOT NULL,
                    guaranteed_cost_lower REAL NOT NULL,
                    up_payout_upper REAL NOT NULL,
                    down_payout_upper REAL NOT NULL,
                    error_kind TEXT,
                    error_message TEXT,
                    response_json TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(strategy, market_id)
                );
                CREATE INDEX IF NOT EXISTS live_pair_insurance_order_id_idx
                    ON live_pair_insurance(order_id);
                CREATE TABLE IF NOT EXISTS live_pair_quote_audits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    up_quote_price REAL,
                    down_quote_price REAL,
                    up_net_shares REAL,
                    down_net_shares REAL,
                    total_cost_usdt REAL,
                    guaranteed_payout_usdt REAL,
                    locked_pnl_usdt REAL,
                    locked_roi REAL,
                    minimum_capacity_ratio REAL,
                    net_share_mismatch_ratio REAL,
                    minimum_expiry_ms INTEGER,
                    maximum_quote_rtt_ms REAL,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(strategy, market_id)
                );
                CREATE INDEX IF NOT EXISTS live_pair_quote_audits_strategy_idx
                    ON live_pair_quote_audits(strategy, id DESC);
                CREATE TABLE IF NOT EXISTS live_pair_incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    filled_side TEXT,
                    missing_side TEXT,
                    reason TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    resolved_at TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(strategy, market_id)
                );
                CREATE INDEX IF NOT EXISTS live_pair_incidents_status_idx
                    ON live_pair_incidents(status, id DESC);
                """
            )
            self.db.commit()

    def sqlite_settings(self) -> dict[str, Any]:
        with self.lock:
            journal_mode = str(
                self.db.execute("PRAGMA journal_mode").fetchone()[0]
            ).upper()
            synchronous_code = int(
                self.db.execute("PRAGMA synchronous").fetchone()[0]
            )
            busy_timeout_ms = int(
                self.db.execute("PRAGMA busy_timeout").fetchone()[0]
            )
        synchronous = {
            0: "OFF",
            1: "NORMAL",
            2: "FULL",
            3: "EXTRA",
        }.get(synchronous_code, str(synchronous_code))
        resolved_path = str(self.path.resolve())
        lowered = resolved_path.lower()
        warning = None
        if resolved_path.startswith("\\\\"):
            warning = "live DB is on a network/UNC path"
        elif any(marker in lowered for marker in ("onedrive", "dropbox", "google drive")):
            warning = "live DB appears to be inside a synchronized folder"
        return {
            "sqliteJournalMode": journal_mode,
            "sqliteSynchronous": synchronous,
            "sqliteBusyTimeoutMs": busy_timeout_ms,
            "liveDbPath": resolved_path,
            "sqlitePathWarning": warning,
        }

    def runtime_override(self) -> bool | None:
        with self.lock:
            row = self.db.execute(
                "SELECT value FROM live_settings WHERE key='runtime_enabled'"
            ).fetchone()
        if row is None:
            return None
        return str(row["value"]).lower() in {"1", "true", "yes", "on"}

    def set_runtime_enabled(self, enabled: bool) -> None:
        with self.lock:
            self.db.execute(
                """INSERT INTO live_settings(key, value, updated_at)
                   VALUES ('runtime_enabled', ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       value=excluded.value, updated_at=excluded.updated_at""",
                ("1" if enabled else "0", utc_iso()),
            )
            self.db.commit()

    def live_rule_overrides(self) -> dict[str, str]:
        keys = (
            "strategy",
            "strategies",
            "strategyStakesUsdt",
            "maxStakeUsdt",
            "minHourlyWinRatePct",
            "maxHourlyWinThenLossRatePct",
            "futuresLeadObserverEnabled",
            "futuresLeadObserverVersion",
            "strategyObserverEnabled",
            "strategyObserverVersions",
            "strategyDrawdownControlEnabled",
            "strategyLossCooldownEnabled",
            "reliabilityGateTags",
        )
        placeholders = ",".join("?" for _ in keys)
        with self.lock:
            rows = self.db.execute(
                f"SELECT key, value FROM live_settings WHERE key IN ({placeholders})",
                keys,
            ).fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def set_live_rules(self, rules: dict[str, Any]) -> None:
        now = utc_iso()
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                for key in (
                    "strategy",
                    "strategies",
                    "strategyStakesUsdt",
                    "maxStakeUsdt",
                    "minHourlyWinRatePct",
                    "maxHourlyWinThenLossRatePct",
                    "futuresLeadObserverEnabled",
                    "futuresLeadObserverVersion",
                    "strategyObserverEnabled",
                    "strategyObserverVersions",
                    "strategyDrawdownControlEnabled",
                    "strategyLossCooldownEnabled",
                    "reliabilityGateTags",
                ):
                    self.db.execute(
                        """INSERT INTO live_settings(key, value, updated_at)
                           VALUES (?, ?, ?)
                           ON CONFLICT(key) DO UPDATE SET
                               value=excluded.value,
                               updated_at=excluded.updated_at""",
                        (
                            key,
                            json.dumps(rules[key])
                            if key in {
                                "strategies",
                                "strategyStakesUsdt",
                                "strategyObserverEnabled",
                                "strategyObserverVersions",
                                "strategyDrawdownControlEnabled",
                                "strategyLossCooldownEnabled",
                                "reliabilityGateTags",
                            }
                            else str(rules[key]),
                            now,
                        ),
                    )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def record_event(
        self,
        level: str,
        event_type: str,
        message: str,
        market_id: int | None = None,
    ) -> None:
        # Errors are intentionally redacted by the API layer before arrival.
        with self.lock:
            self.db.execute(
                """INSERT INTO live_events(timestamp, level, event_type, market_id, message)
                   VALUES (?, ?, ?, ?, ?)""",
                (utc_iso(), level[:16], event_type[:48], market_id, message[:500]),
            )
            self.db.commit()

    def record_attempt_telemetry(
        self, order_local_id: int, payload: dict[str, Any]
    ) -> None:
        allowed = {
            "strategy", "marketId", "side", "signalPrice",
            "signalBookAgeMs", "signalAsk", "signalAskSize",
            "latestLocalAsk", "latestLocalAskSize", "latestLocalBookAgeMs",
            "maximumExecutionPrice", "topLevelCapacityUsdt",
            "topLevelCapacityRatio", "depthCoverageRatio",
            "depthCoveredStakeUsdt", "depthLevelsConsumed",
            "minimumDepthCoverageRatio", "configuredStake", "estimatedVwap",
            "estimatedVwapCapacityRatio", "estimatedVwapCoveredStake",
            "estimatedVwapLevelsConsumed", "vwapAvailable", "queueMs",
            "preQuoteMs", "quotePhaseMs", "quoteNetworkMs", "quoteToPlaceMs",
            "placeNetworkMs", "totalMs", "marketEventToDecisionStartMs",
            "decisionAndStoreMs", "storeMs", "candidateToLiveQueueMs",
            "marketEventToLiveQueueMs", "marketEventToQuoteStartMs",
            "marketEventToPlaceStartMs", "eventToPlaceResponseMs",
            "preLedgerMs", "acceptedLedgerMs", "quoteAttempts",
            "requoteTriggered", "firstQuoteAveragePrice",
            "secondQuoteAveragePrice", "firstQuoteNetworkMs",
            "secondQuoteNetworkMs", "finalQuoteAveragePrice", "finalOutcome",
            "eventToLocalCheckMs", "orientation", "latestMarketId",
            "measuredAt",
        }
        sanitized = {
            key: payload.get(key)
            for key in allowed
            if key in payload
        }
        outcome = str(sanitized.get("finalOutcome") or "UNKNOWN")
        with self.lock:
            self.db.execute(
                """INSERT INTO live_attempt_telemetry(
                       order_local_id, captured_at, final_outcome, telemetry_json
                   ) VALUES (?, ?, ?, ?)
                   ON CONFLICT(order_local_id) DO UPDATE SET
                       captured_at=excluded.captured_at,
                       final_outcome=excluded.final_outcome,
                       telemetry_json=excluded.telemetry_json""",
                (
                    int(order_local_id),
                    utc_iso(),
                    outcome,
                    json.dumps(sanitized, ensure_ascii=False, sort_keys=True),
                ),
            )
            self.db.commit()

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        if len(ordered) == 1:
            return ordered[0]
        position = (len(ordered) - 1) * percentile
        lower = int(math.floor(position))
        upper = int(math.ceil(position))
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight

    def attempt_summary(self, limit: int = 100) -> dict[str, Any]:
        with self.lock:
            rows = self.db.execute(
                """SELECT final_outcome, telemetry_json
                     FROM live_attempt_telemetry
                    ORDER BY order_local_id DESC LIMIT ?""",
                (max(1, min(100, int(limit))),),
            ).fetchall()
        attempts: list[dict[str, Any]] = []
        for row in rows:
            try:
                item = json.loads(str(row["telemetry_json"]))
            except (TypeError, json.JSONDecodeError):
                item = {}
            item["finalOutcome"] = str(row["final_outcome"])
            attempts.append(item)
        outcome_keys = {
            "SUBMITTED": "submitted",
            "BLOCKED_STALE_PREDICTION_BOOK": "blockedStaleBook",
            "BLOCKED_LOCAL_PRICE_MOVED": "blockedLocalPriceMoved",
            "BLOCKED_INSUFFICIENT_TOP_LEVEL_CAPACITY": (
                "blockedInsufficientCapacity"
            ),
            "BLOCKED_INSUFFICIENT_DEPTH_COVERAGE": (
                "blockedInsufficientCapacity"
            ),
            "BLOCKED_ESTIMATED_VWAP_TOO_HIGH": "blockedEstimatedVwapTooHigh",
            "BLOCKED_STALE_SPOT_DATA": "blockedStaleSpotData",
            "QUOTE_REJECTED": "quoteRejected",
            "PLACEMENT_REJECTED": "placementRejected",
            "PLACEMENT_AMBIGUOUS": "placementAmbiguous",
        }
        outcomes = {value: 0 for value in outcome_keys.values()}
        for attempt in attempts:
            key = outcome_keys.get(str(attempt.get("finalOutcome") or ""))
            if key is not None:
                outcomes[key] += 1
        latency_fields = (
            "eventToPlaceResponseMs",
            "queueMs",
            "preQuoteMs",
            "quoteNetworkMs",
            "quoteToPlaceMs",
            "placeNetworkMs",
        )
        latency: dict[str, dict[str, float | None]] = {}
        for field in latency_fields:
            values = [
                float(attempt[field])
                for attempt in attempts
                if isinstance(attempt.get(field), (int, float))
                and math.isfinite(float(attempt[field]))
                and float(attempt[field]) >= 0
            ]
            latency[field] = {
                "p50": self._percentile(values, 0.50),
                "p90": self._percentile(values, 0.90),
                "p95": self._percentile(values, 0.95),
                "max": max(values) if values else None,
            }
        return {
            "window": min(100, max(1, int(limit))),
            "sampleSize": len(attempts),
            "outcomes": outcomes,
            "latency": latency,
        }

    def record_signal(
        self,
        *,
        topic_id: int,
        market_id: int,
        side: str,
        token_id: str,
        signal_price: float,
        account_type: str,
        signal_at: str,
        strategy: str = LIVE_DEFAULT_STRATEGY,
        max_stake_usdt: float = float(LIVE_DEFAULT_MAX_STAKE_USDT),
        requested_amount_wei: str = str(LIVE_M0W_AMOUNT_WEI),
        initial_status: str = "SIGNAL_RECEIVED",
        reliability_context: dict[str, Any] | None = None,
    ) -> int | None:
        now = utc_iso()
        with self.lock:
            existing = self.db.execute(
                "SELECT id FROM live_orders WHERE strategy=? AND market_id=? LIMIT 1",
                (str(strategy), int(market_id)),
            ).fetchone()
            if existing is not None:
                return None
            cursor = self.db.execute(
                """INSERT OR IGNORE INTO live_orders(
                       strategy, topic_id, market_id, side, token_id,
                       signal_price, max_stake_usdt, requested_amount_wei,
                       status, order_type, time_in_force, account_type,
                       signal_at, updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(strategy),
                    int(topic_id),
                    int(market_id),
                    side,
                    token_id,
                    float(signal_price),
                    float(max_stake_usdt),
                    str(requested_amount_wei),
                    str(initial_status),
                    "LIMIT",
                    "GTC",
                    account_type,
                    signal_at,
                    now,
                ),
            )
            local_id = int(cursor.lastrowid) if cursor.rowcount else None
            if local_id is not None and isinstance(reliability_context, dict):
                self._record_reliability_context_locked(
                    order_local_id=local_id,
                    strategy=str(strategy),
                    market_id=int(market_id),
                    side=side,
                    signal_price=float(signal_price),
                    context=reliability_context,
                )
            self.db.commit()
            return local_id

    def record_accepted_signal(
        self,
        *,
        topic_id: int,
        market_id: int,
        side: str,
        token_id: str,
        signal_price: float,
        account_type: str,
        signal_at: str,
        strategy: str,
        max_stake_usdt: float,
        requested_amount_wei: str,
        reliability_context: dict[str, Any] | None,
        event_message: str,
    ) -> int | None:
        """Atomically deduplicate and persist the accepted signal plus event."""
        now = utc_iso()
        with self.lock:
            try:
                existing = self.db.execute(
                    "SELECT id FROM live_orders WHERE strategy=? AND market_id=? LIMIT 1",
                    (str(strategy), int(market_id)),
                ).fetchone()
                if existing is not None:
                    return None
                cursor = self.db.execute(
                    """INSERT INTO live_orders(
                           strategy, topic_id, market_id, side, token_id,
                           signal_price, max_stake_usdt, requested_amount_wei,
                           status, order_type, time_in_force, account_type,
                           signal_at, updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        str(strategy),
                        int(topic_id),
                        int(market_id),
                        side,
                        token_id,
                        float(signal_price),
                        float(max_stake_usdt),
                        str(requested_amount_wei),
                        "QUOTE_REQUESTING",
                        "LIMIT",
                        "GTC",
                        account_type,
                        signal_at,
                        now,
                    ),
                )
                local_id = int(cursor.lastrowid)
                if isinstance(reliability_context, dict):
                    self._record_reliability_context_locked(
                        order_local_id=local_id,
                        strategy=str(strategy),
                        market_id=int(market_id),
                        side=side,
                        signal_price=float(signal_price),
                        context=reliability_context,
                    )
                self.db.execute(
                    """INSERT INTO live_events(
                           timestamp, level, event_type, market_id, message
                       ) VALUES (?, 'INFO', 'SIGNAL_ACCEPTED', ?, ?)""",
                    (now, int(market_id), str(event_message)[:500]),
                )
                self.db.commit()
                return local_id
            except Exception:
                self.db.rollback()
                raise

    def _record_reliability_context_locked(
        self,
        *,
        order_local_id: int,
        strategy: str,
        market_id: int,
        side: str,
        signal_price: float,
        context: dict[str, Any],
    ) -> None:
        normalized_strategy = str(strategy).split(":", 1)[0].upper()
        reliability_strategies = {
            definition["strategy"] for definition in LIVE_RELIABILITY_TAGS.values()
        }
        if normalized_strategy not in {
            *reliability_strategies,
            *CONFIRMATION_ADD_SOURCE_STRATEGIES,
        }:
            return
        now = utc_iso()
        self.db.execute(
            """INSERT OR IGNORE INTO live_reliability_samples(
                   order_local_id, strategy, market_id, side, signal_price,
                   seconds_left, book_age_ms, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(order_local_id),
                normalized_strategy,
                int(market_id),
                str(side).upper(),
                float(signal_price),
                _float(context.get("seconds_left")),
                _float(context.get("book_age_ms")),
                now,
                now,
            ),
        )

    def update_order(self, local_id: int, **values: Any) -> None:
        updates = {
            key: value for key, value in values.items() if key in self.ORDER_COLUMNS
        }
        if not updates:
            return
        updates["updated_at"] = utc_iso()
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self.lock:
            self.db.execute(
                f"UPDATE live_orders SET {assignments} WHERE id=?",
                (*updates.values(), int(local_id)),
            )
            self.db.commit()

    def sync_exchange_order(self, local_id: int, order: dict[str, Any]) -> None:
        self.update_order(
            local_id,
            status=str(order.get("status") or "UNKNOWN").upper(),
            maker_usdt_amount=_float(order.get("makerUsdtAmount")),
            filled_usdt_amount=_float(order.get("filledUsdtAmount")),
            filled_share_qty=_float(order.get("filledShareQty")),
            fill_percentage=_float(order.get("fillPercentage")),
            market_provider_fee=_float(order.get("marketProviderFee")),
            network_fee=_float(order.get("networkFee")),
            realized_pnl=_float(order.get("realizedPnl")),
            error_message=(
                str(order.get("errorMessage"))[:500]
                if order.get("errorMessage") else None
            ),
            response_json=_safe_payload(order),
        )
        self._capture_filled_reliability_sample(local_id)

    def _capture_filled_reliability_sample(self, local_id: int) -> None:
        with self.lock:
            row = self.db.execute(
                """SELECT o.*, r.seconds_left, r.book_age_ms,
                          r.order_local_id AS reliability_order_local_id
                     FROM live_orders o
                     LEFT JOIN live_reliability_samples r
                       ON r.order_local_id=o.id
                    WHERE o.id=?""",
                (int(local_id),),
            ).fetchone()
            if (
                row is None
                or row["reliability_order_local_id"] is None
                or float(_float(row["filled_usdt_amount"]) or 0.0) <= 0
            ):
                return
            quote_cost = self._wei_amount(row["quote_amount_in_wei"])
            filled_cost = _float(row["filled_usdt_amount"])
            positive_costs = [
                value for value in (quote_cost, filled_cost)
                if value is not None and value > 0
            ]
            if not positive_costs:
                return
            actual_entry = _float(row["quote_average_price"])
            if actual_entry is None:
                shares = _float(row["filled_share_qty"])
                actual_entry = (
                    min(positive_costs) / shares
                    if shares is not None and shares > 0 else _float(row["signal_price"])
                )
            decisions = evaluate_live_reliability_tags(
                strategy=row["strategy"],
                side=row["side"],
                entry_price=actual_entry,
                seconds_left=row["seconds_left"],
                book_age_ms=row["book_age_ms"],
            )
            now = utc_iso()
            self.db.execute(
                """UPDATE live_reliability_samples
                      SET executed_entry_price=?, exchange_status=?,
                          filled_cost_usdt=?,
                          captured_at=COALESCE(captured_at, ?),
                          tag_decisions_json=?, updated_at=?
                    WHERE order_local_id=?""",
                (
                    actual_entry,
                    str(row["status"]),
                    min(positive_costs),
                    now,
                    json.dumps(decisions, ensure_ascii=False, sort_keys=True),
                    now,
                    int(local_id),
                ),
            )
            self._create_confirmation_add_mirror_locked(
                row=row,
                base_price=float(actual_entry),
                initial_stake_usdt=min(
                    CONFIRMATION_ADD_TRANCHE_USDT,
                    min(positive_costs),
                ),
                captured_at=now,
            )
            self.db.commit()

    def _create_confirmation_add_mirror_locked(
        self,
        *,
        row: sqlite3.Row,
        base_price: float,
        initial_stake_usdt: float,
        captured_at: str,
    ) -> None:
        strategy = str(row["strategy"] or "").split(":", 1)[0].upper()
        if strategy not in CONFIRMATION_ADD_SOURCE_STRATEGIES:
            return
        if not 0 < base_price < 1 or initial_stake_usdt <= 0:
            return
        levels = confirmation_add_levels(base_price)
        shares = initial_stake_usdt / base_price
        fee = taker_fee(shares, base_price, CONFIRMATION_ADD_FEE_BPS)
        fills = [
            {
                "index": index,
                "targetPrice": float(level),
                "triggered": index == 0,
                "stakeUsdt": initial_stake_usdt if index == 0 else 0.0,
                "shares": shares if index == 0 else 0.0,
                "fees": fee if index == 0 else 0.0,
                "events": [
                    {
                        "eventKey": f"actual-fill:{int(row['id'])}",
                        "timestamp": captured_at,
                        "executionPrice": base_price,
                        "stakeUsdt": initial_stake_usdt,
                        "shares": shares,
                        "fee": fee,
                        "source": "actual_live_fill",
                    }
                ] if index == 0 else [],
            }
            for index, level in enumerate(levels)
        ]
        self.db.execute(
            """INSERT OR IGNORE INTO live_confirmation_add_mirrors(
                   order_local_id, strategy, market_id, side, base_price,
                   levels_json, fills_json, status,
                   hypothetical_stake_usdt, hypothetical_fees_usdt,
                   hypothetical_shares, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?)""",
            (
                int(row["id"]),
                strategy,
                int(row["market_id"]),
                str(row["side"]).upper(),
                base_price,
                json.dumps(list(levels)),
                json.dumps(fills, sort_keys=True),
                initial_stake_usdt,
                fee,
                shares,
                captured_at,
                captured_at,
            ),
        )

    def record_confirmation_add_snapshot(
        self, snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        """Advance real-fill mirrors without submitting or quoting an order."""
        try:
            market_id = int(snapshot["market_id"])
        except (KeyError, TypeError, ValueError):
            return {"updatedMirrors": 0, "filledStakeUsdt": 0.0}
        with self.lock:
            rows = self.db.execute(
                """SELECT * FROM live_confirmation_add_mirrors
                    WHERE market_id=? AND status='ACTIVE'
                    ORDER BY order_local_id ASC""",
                (market_id,),
            ).fetchall()
            updated = 0
            filled_stake = 0.0
            for side in ("UP", "DOWN"):
                side_rows = [row for row in rows if str(row["side"]) == side]
                if not side_rows:
                    continue
                safe, _ = confirmation_add_book_is_safe(snapshot, side)
                if not safe:
                    continue
                event_key = confirmation_add_book_event_key(snapshot, side)
                actionable = [
                    row for row in side_rows
                    if str(row["last_event_key"] or "") != event_key
                ]
                if not actionable:
                    continue
                prefix = side.lower()
                ask = float(snapshot[f"{prefix}_ask"])
                execution_price = confirmation_add_execution_price(ask)
                if execution_price is None:
                    continue
                available_shares = float(snapshot[f"{prefix}_ask_size"])
                for row in actionable:
                    levels = [float(value) for value in json.loads(row["levels_json"])]
                    fills = json.loads(row["fills_json"])
                    for index in range(1, len(fills)):
                        if ask + 1e-12 >= levels[index]:
                            fills[index]["triggered"] = True
                    for index in range(1, len(fills)):
                        item = fills[index]
                        if not item.get("triggered") or available_shares <= 1e-12:
                            continue
                        remaining_stake = max(
                            0.0,
                            CONFIRMATION_ADD_TRANCHE_USDT
                            - float(item.get("stakeUsdt") or 0.0),
                        )
                        if remaining_stake <= 1e-12:
                            continue
                        shares = min(
                            remaining_stake / execution_price,
                            available_shares,
                        )
                        if shares <= 1e-12:
                            continue
                        stake = shares * execution_price
                        fee = taker_fee(
                            shares,
                            execution_price,
                            CONFIRMATION_ADD_FEE_BPS,
                        )
                        item["stakeUsdt"] = float(item.get("stakeUsdt") or 0.0) + stake
                        item["shares"] = float(item.get("shares") or 0.0) + shares
                        item["fees"] = float(item.get("fees") or 0.0) + fee
                        item.setdefault("events", []).append(
                            {
                                "eventKey": event_key,
                                "timestamp": snapshot.get("timestamp"),
                                "secondsLeft": float(snapshot["seconds_left"]),
                                "observedAsk": ask,
                                "executionPrice": execution_price,
                                "stakeUsdt": stake,
                                "shares": shares,
                                "fee": fee,
                                "bookAgeMs": float(snapshot["book_age_ms"]),
                                "bookSkewMs": float(snapshot["book_skew_ms"]),
                                "source": "recorded_prediction_book",
                            }
                        )
                        available_shares -= shares
                        filled_stake += stake
                    total_stake = sum(float(item.get("stakeUsdt") or 0.0) for item in fills)
                    total_fees = sum(float(item.get("fees") or 0.0) for item in fills)
                    total_shares = sum(float(item.get("shares") or 0.0) for item in fills)
                    now = str(snapshot.get("timestamp") or utc_iso())
                    self.db.execute(
                        """UPDATE live_confirmation_add_mirrors
                              SET fills_json=?, last_event_key=?,
                                  hypothetical_stake_usdt=?,
                                  hypothetical_fees_usdt=?,
                                  hypothetical_shares=?, updated_at=?
                            WHERE order_local_id=?""",
                        (
                            json.dumps(fills, sort_keys=True),
                            event_key,
                            total_stake,
                            total_fees,
                            total_shares,
                            now,
                            int(row["order_local_id"]),
                        ),
                    )
                    updated += 1
            self.db.commit()
        return {
            "updatedMirrors": updated,
            "filledStakeUsdt": filled_stake,
            "paperOnly": True,
            "liveOrdersAffected": False,
        }

    def _finalize_confirmation_add_mirror_locked(
        self,
        *,
        order_local_id: int,
        result: str,
        settled_at: str,
    ) -> None:
        row = self.db.execute(
            """SELECT * FROM live_confirmation_add_mirrors
                WHERE order_local_id=? LIMIT 1""",
            (int(order_local_id),),
        ).fetchone()
        if row is None:
            return
        normalized = str(result).upper()
        if normalized not in {"WIN", "LOSS"}:
            return
        stake = float(row["hypothetical_stake_usdt"] or 0.0)
        fees = float(row["hypothetical_fees_usdt"] or 0.0)
        shares = float(row["hypothetical_shares"] or 0.0)
        payout = shares if normalized == "WIN" else 0.0
        pnl = payout - stake - fees
        cost = stake + fees
        self.db.execute(
            """UPDATE live_confirmation_add_mirrors
                  SET status='SETTLED', settlement_result=?,
                      hypothetical_payout_usdt=?, hypothetical_pnl_usdt=?,
                      hypothetical_roi_pct=?, settled_at=?, updated_at=?
                WHERE order_local_id=?""",
            (
                normalized,
                payout,
                pnl,
                pnl / cost * 100.0 if cost > 0 else None,
                settled_at,
                utc_iso(),
                int(order_local_id),
            ),
        )

    def pending_orders(self) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in ACTIVE_ORDER_STATUSES)
        with self.lock:
            rows = self.db.execute(
                f"""SELECT * FROM live_orders
                    WHERE status IN ({placeholders}) OR status='AMBIGUOUS'
                    ORDER BY id ASC""",
                tuple(ACTIVE_ORDER_STATUSES),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_orders(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                """SELECT o.*,
                          s.position_status AS settlement_status,
                          s.result AS settlement_result,
                          s.cost_usdt AS settlement_cost_usdt,
                          s.payout_usdt AS settlement_payout_usdt,
                          s.pnl_usdt AS settlement_pnl_usdt,
                          s.roi_pct AS settlement_roi_pct,
                          s.settled_at
                     FROM live_orders o
                     LEFT JOIN live_strategy_settlements s
                       ON s.order_local_id=o.id
                    ORDER BY o.id DESC LIMIT ?""",
                (max(1, min(500, int(limit))),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            # Token IDs are public market metadata but add no monitoring value.
            item.pop("token_id", None)
            item.pop("response_json", None)
            result.append(item)
        return result

    def confirmation_add_research_summary(
        self, recent_limit: int = 50
    ) -> dict[str, Any]:
        with self.lock:
            rows = [dict(row) for row in self.db.execute(
                """SELECT m.*, s.cost_usdt AS original_cost_usdt,
                          s.pnl_usdt AS original_pnl_usdt,
                          s.result AS original_result
                     FROM live_confirmation_add_mirrors AS m
                     LEFT JOIN live_strategy_settlements AS s
                       ON s.order_local_id=m.order_local_id
                    ORDER BY m.order_local_id DESC"""
            ).fetchall()]

        def cohort(items: list[dict[str, Any]]) -> dict[str, Any]:
            settled = [item for item in items if item["status"] == "SETTLED"]
            original_cost = sum(float(item["original_cost_usdt"] or 0.0) for item in settled)
            original_pnl = sum(float(item["original_pnl_usdt"] or 0.0) for item in settled)
            hypothetical_stake = sum(
                float(item["hypothetical_stake_usdt"] or 0.0) for item in settled
            )
            hypothetical_fees = sum(
                float(item["hypothetical_fees_usdt"] or 0.0) for item in settled
            )
            hypothetical_pnl = sum(
                float(item["hypothetical_pnl_usdt"] or 0.0) for item in settled
            )
            wins = sum(item["settlement_result"] == "WIN" for item in settled)
            return {
                "samples": len(items),
                "settledSamples": len(settled),
                "pendingSamples": len(items) - len(settled),
                "wins": int(wins),
                "losses": len(settled) - int(wins),
                "originalCostUsdt": original_cost,
                "originalPnlUsdt": original_pnl,
                "hypotheticalStakeUsdt": hypothetical_stake,
                "hypotheticalFeesUsdt": hypothetical_fees,
                "hypotheticalCostUsdt": hypothetical_stake + hypothetical_fees,
                "hypotheticalPnlUsdt": hypothetical_pnl,
                "hypotheticalReturnOnCostPct": (
                    hypothetical_pnl / (hypothetical_stake + hypothetical_fees) * 100.0
                    if hypothetical_stake + hypothetical_fees > 0 else None
                ),
                "deltaVsOriginalPnlUsdt": hypothetical_pnl - original_pnl,
            }

        recent = []
        for row in rows[:max(1, min(200, int(recent_limit)))]:
            fills = json.loads(str(row["fills_json"] or "[]"))
            recent.append(
                {
                    "orderLocalId": int(row["order_local_id"]),
                    "strategy": row["strategy"],
                    "marketId": int(row["market_id"]),
                    "side": row["side"],
                    "basePrice": row["base_price"],
                    "filledTranches": sum(
                        float(item.get("stakeUsdt") or 0.0) >= 1.0 - 1e-9
                        for item in fills
                    ),
                    "hypotheticalStakeUsdt": row["hypothetical_stake_usdt"],
                    "hypotheticalFeesUsdt": row["hypothetical_fees_usdt"],
                    "settlementResult": row["settlement_result"],
                    "originalPnlUsdt": row["original_pnl_usdt"],
                    "hypotheticalPnlUsdt": row["hypothetical_pnl_usdt"],
                    "deltaVsOriginalPnlUsdt": (
                        float(row["hypothetical_pnl_usdt"] or 0.0)
                        - float(row["original_pnl_usdt"] or 0.0)
                        if row["status"] == "SETTLED" else None
                    ),
                    "status": row["status"],
                    "createdAt": row["created_at"],
                    "settledAt": row["settled_at"],
                    "fills": fills,
                }
            )
        return {
            "status": "FORWARD_ONLY",
            "source": "real_filled_orders_only",
            "paperOnly": True,
            "liveOrdersAffected": False,
            "historicalBackfill": False,
            "rule": {
                "initialStakeUsdt": 1.0,
                "addStakeUsdt": 1.0,
                "multipliers": [1.0, 1.1, 1.2, 1.3, 1.4],
                "minimumSecondsLeftExclusive": 30.0,
                "maximumStakeUsdt": 5.0,
                "slippageBps": 50.0,
                "feeBps": 200,
                "maxSpread": 0.03,
                "maxBookAgeMs": 2000.0,
                "maxBookSkewMs": 500.0,
            },
            "overall": cohort(rows),
            "byStrategy": {
                strategy: cohort([row for row in rows if row["strategy"] == strategy])
                for strategy in CONFIRMATION_ADD_SOURCE_STRATEGIES
            },
            "recent": recent,
        }

    def reliability_research_summary(
        self,
        *,
        enabled_tags: list[str] | tuple[str, ...] = (),
        recent_limit: int = 50,
    ) -> dict[str, Any]:
        """Compare real filled orders with each tag's allow/block counterfactual."""
        with self.lock:
            rows = [dict(row) for row in self.db.execute(
                """SELECT * FROM live_reliability_samples
                    WHERE captured_at IS NOT NULL
                    ORDER BY order_local_id DESC"""
            ).fetchall()]

        for row in rows:
            try:
                decoded = json.loads(str(row.get("tag_decisions_json") or "[]"))
            except json.JSONDecodeError:
                decoded = []
            row["tag_decisions"] = decoded if isinstance(decoded, list) else []
            row["decision_by_tag"] = {
                str(item.get("id")): str(item.get("decision"))
                for item in row["tag_decisions"]
                if isinstance(item, dict) and item.get("id")
            }

        def cohort(items: list[dict[str, Any]]) -> dict[str, Any]:
            settled = [
                item for item in items
                if item.get("settlement_result") in {"WIN", "LOSS"}
            ]
            pnl = sum(float(item.get("settlement_pnl_usdt") or 0.0) for item in settled)
            cost = sum(float(item.get("settlement_cost_usdt") or 0.0) for item in settled)
            wins = sum(item.get("settlement_result") == "WIN" for item in settled)
            return {
                "samples": len(items),
                "settledSamples": len(settled),
                "pendingSamples": len(items) - len(settled),
                "wins": int(wins),
                "losses": len(settled) - int(wins),
                "winRatePct": (
                    wins / len(settled) * 100.0 if settled else None
                ),
                "costUsdt": cost,
                "pnlUsdt": pnl,
                "returnOnCostPct": pnl / cost * 100.0 if cost > 0 else None,
            }

        enabled = {str(tag).upper() for tag in enabled_tags}
        tag_summaries: list[dict[str, Any]] = []
        for tag_id, definition in LIVE_RELIABILITY_TAGS.items():
            applicable = [
                row for row in rows
                if row.get("strategy") == definition["strategy"]
            ]
            allowed = [
                row for row in applicable
                if row["decision_by_tag"].get(tag_id) == "ALLOW"
            ]
            blocked = [
                row for row in applicable
                if row["decision_by_tag"].get(tag_id) == "BLOCK"
            ]
            unavailable = [
                row for row in applicable
                if row["decision_by_tag"].get(tag_id) == "UNAVAILABLE"
            ]
            original_metrics = cohort(applicable)
            allowed_metrics = cohort(allowed)
            blocked_metrics = cohort(blocked)
            tag_summaries.append(
                {
                    "id": tag_id,
                    **definition,
                    "activationAvailable": tag_id in LIVE_RELIABILITY_CANDIDATE_TAGS,
                    "enabledForLive": tag_id in enabled,
                    "original": original_metrics,
                    "allowed": allowed_metrics,
                    "blocked": blocked_metrics,
                    "unavailable": cohort(unavailable),
                    "policyPnlUsdt": allowed_metrics["pnlUsdt"],
                    "deltaVsOriginalPnlUsdt": (
                        allowed_metrics["pnlUsdt"] - original_metrics["pnlUsdt"]
                    ),
                    "avoidedOriginalPnlUsdt": -blocked_metrics["pnlUsdt"],
                }
            )

        recent_samples = []
        for row in rows[:max(1, min(200, int(recent_limit)))]:
            recent_samples.append(
                {
                    "orderLocalId": int(row["order_local_id"]),
                    "strategy": row["strategy"],
                    "marketId": int(row["market_id"]),
                    "side": row["side"],
                    "executedEntryPrice": row["executed_entry_price"],
                    "secondsLeft": row["seconds_left"],
                    "bookAgeMs": row["book_age_ms"],
                    "capturedAt": row["captured_at"],
                    "settlementResult": row["settlement_result"],
                    "settlementCostUsdt": row["settlement_cost_usdt"],
                    "settlementPnlUsdt": row["settlement_pnl_usdt"],
                    "settledAt": row["settled_at"],
                    "tagDecisions": row["tag_decisions"],
                }
            )
        return {
            "source": "real_filled_orders_only",
            "paperOrdersIncluded": False,
            "blockedOrRejectedOrdersIncluded": False,
            "copiedSamples": len(rows),
            "settledSamples": sum(
                row.get("settlement_result") in {"WIN", "LOSS"} for row in rows
            ),
            "pendingSamples": sum(
                row.get("settlement_result") not in {"WIN", "LOSS"} for row in rows
            ),
            "enabledLiveTags": sorted(enabled),
            "tags": tag_summaries,
            "confirmationAdd": self.confirmation_add_research_summary(
                recent_limit=recent_limit
            ),
            "recentSamples": recent_samples,
        }

    def order_for_manual_exit(self, local_id: int) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                """SELECT o.*
                     FROM live_orders o
                     LEFT JOIN live_strategy_settlements s
                       ON s.order_local_id=o.id
                    WHERE o.id=? AND s.order_local_id IS NULL
                    LIMIT 1""",
                (int(local_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def manual_exit_for_order(self, local_id: int) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM live_manual_exits WHERE order_local_id=? LIMIT 1",
                (int(local_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def begin_manual_exit(
        self,
        *,
        order: dict[str, Any],
        order_type: str,
        sell_shares: Decimal,
        price_limit: Decimal | None,
    ) -> dict[str, Any]:
        now = utc_iso()
        local_id = int(order["id"])
        with self.lock:
            existing = self.db.execute(
                "SELECT * FROM live_manual_exits WHERE order_local_id=? LIMIT 1",
                (local_id,),
            ).fetchone()
            if existing is not None and str(existing["status"]).upper() not in {
                "REJECTED", "FAILED", "CANCELED", "CANCELLED",
            }:
                raise ValueError(
                    "this position already has a manual sell request; reconcile it before retrying"
                )
            values = (
                str(order["strategy"]),
                int(order["market_id"]),
                str(order["side"]),
                str(order["token_id"]),
                str(order_type),
                "GTC" if str(order_type).upper() == "LIMIT" else "FOK",
                "QUOTE_REQUESTING",
                float(sell_shares),
                float(price_limit) if price_limit is not None else None,
                now,
                now,
            )
            self.db.execute(
                """INSERT INTO live_manual_exits(
                       order_local_id, strategy, market_id, side, token_id,
                       order_type, time_in_force, status, sell_shares,
                       price_limit, requested_at, updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(order_local_id) DO UPDATE SET
                       strategy=excluded.strategy,
                       market_id=excluded.market_id,
                       side=excluded.side,
                       token_id=excluded.token_id,
                       order_type=excluded.order_type,
                       time_in_force=excluded.time_in_force,
                       status=excluded.status,
                       sell_shares=excluded.sell_shares,
                       price_limit=excluded.price_limit,
                       quote_average_price=NULL,
                       quote_amount_in_wei=NULL,
                       quote_amount_out_wei=NULL,
                       quote_expires_at=NULL,
                       order_id=NULL,
                       filled_usdt_amount=NULL,
                       filled_share_qty=NULL,
                       fill_percentage=NULL,
                       realized_pnl=NULL,
                       requested_at=excluded.requested_at,
                       attempted_at=NULL,
                       submitted_at=NULL,
                       updated_at=excluded.updated_at,
                       error_kind=NULL,
                       error_message=NULL,
                       response_json=NULL""",
                (local_id, *values),
            )
            self.db.commit()
            row = self.db.execute(
                "SELECT * FROM live_manual_exits WHERE order_local_id=? LIMIT 1",
                (local_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("failed to persist manual exit request")
        return dict(row)

    def update_manual_exit(self, exit_id: int, **values: Any) -> None:
        allowed = {
            "status", "sell_shares", "price_limit", "quote_average_price",
            "quote_amount_in_wei", "quote_amount_out_wei", "quote_expires_at",
            "order_id", "filled_usdt_amount", "filled_share_qty",
            "fill_percentage", "realized_pnl", "attempted_at", "submitted_at",
            "error_kind", "error_message", "response_json",
        }
        updates = {key: value for key, value in values.items() if key in allowed}
        if not updates:
            return
        updates["updated_at"] = utc_iso()
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self.lock:
            self.db.execute(
                f"UPDATE live_manual_exits SET {assignments} WHERE id=?",
                (*updates.values(), int(exit_id)),
            )
            self.db.commit()

    def sync_manual_exit(
        self, exit_id: int, order: dict[str, Any]
    ) -> dict[str, Any] | None:
        self.update_manual_exit(
            exit_id,
            status=str(order.get("status") or "UNKNOWN").upper(),
            filled_usdt_amount=_float(order.get("filledUsdtAmount")),
            filled_share_qty=_float(order.get("filledShareQty")),
            fill_percentage=_float(order.get("fillPercentage")),
            realized_pnl=_float(order.get("realizedPnl")),
            error_message=(
                str(order.get("errorMessage"))[:500]
                if order.get("errorMessage") else None
            ),
            response_json=_safe_payload(order),
        )
        return self.record_manual_exit_settlement(exit_id)

    def record_manual_exit_settlement(
        self, exit_id: int
    ) -> dict[str, Any] | None:
        """Finalize strategy PnL when a full manual exit is confirmed filled."""
        now = utc_iso()
        with self.lock:
            row = self.db.execute(
                """SELECT x.*, o.strategy AS entry_strategy,
                          o.market_id AS entry_market_id,
                          o.quote_amount_in_wei AS entry_quote_amount_in_wei,
                          o.filled_usdt_amount AS entry_filled_usdt_amount,
                          o.network_fee AS entry_network_fee,
                          s.order_local_id AS existing_settlement_id
                     FROM live_manual_exits x
                     JOIN live_orders o ON o.id=x.order_local_id
                     LEFT JOIN live_strategy_settlements s
                       ON s.order_local_id=o.id
                    WHERE x.id=? LIMIT 1""",
                (int(exit_id),),
            ).fetchone()
            if (
                row is None
                or str(row["status"] or "").upper() != "FILLED"
                or row["existing_settlement_id"] is not None
            ):
                return None

            sell_shares = _float(row["sell_shares"])
            filled_shares = _float(row["filled_share_qty"])
            if (
                sell_shares is None
                or filled_shares is None
                or sell_shares <= 0
                or filled_shares + 0.02 < sell_shares
            ):
                return None

            quote_cost = self._wei_amount(row["entry_quote_amount_in_wei"])
            filled_cost = _float(row["entry_filled_usdt_amount"])
            positive_costs = [
                value
                for value in (quote_cost, filled_cost)
                if value is not None and value > 0
            ]
            if not positive_costs:
                return None
            cost_usdt = min(positive_costs)

            precise_payout = self._wei_amount(row["quote_amount_out_wei"])
            filled_payout = _float(row["filled_usdt_amount"])
            payout_usdt = (
                precise_payout
                if precise_payout is not None and precise_payout > 0
                else filled_payout
            )
            if payout_usdt is None or payout_usdt < 0:
                return None

            network_fee = max(
                0.0, float(_float(row["entry_network_fee"]) or 0.0)
            )
            pnl_usdt = float(payout_usdt) - cost_usdt - network_fee
            result = "WIN" if pnl_usdt >= 0 else "LOSS"
            roi_pct = pnl_usdt / cost_usdt * 100.0
            settled_at = str(row["updated_at"] or now)
            order_local_id = int(row["order_local_id"])
            market_id = int(row["entry_market_id"])
            strategy = str(row["entry_strategy"] or "")

            cursor = self.db.execute(
                """INSERT OR IGNORE INTO live_strategy_settlements(
                       order_local_id, market_id, position_status, result,
                       cost_usdt, payout_usdt, pnl_usdt, roi_pct,
                       settled_at, updated_at
                   ) VALUES (?, ?, 'MANUAL_EXIT_FILLED', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_local_id,
                    market_id,
                    result,
                    cost_usdt,
                    float(payout_usdt),
                    pnl_usdt,
                    roi_pct,
                    settled_at,
                    now,
                ),
            )
            if not cursor.rowcount:
                self.db.rollback()
                return None
            self._record_loss_cooldown_result_locked(
                order_local_id=order_local_id,
                strategy=strategy,
                market_id=market_id,
                result=result,
                processed_at=now,
            )
            self.db.execute(
                """UPDATE live_reliability_samples
                      SET settlement_result=?, settlement_cost_usdt=?,
                          settlement_pnl_usdt=?, settlement_roi_pct=?,
                          settled_at=?, updated_at=?
                    WHERE order_local_id=? AND captured_at IS NOT NULL""",
                (
                    result,
                    cost_usdt,
                    pnl_usdt,
                    roi_pct,
                    settled_at,
                    now,
                    order_local_id,
                ),
            )
            self._finalize_confirmation_add_mirror_locked(
                order_local_id=order_local_id,
                result=result,
                settled_at=settled_at,
            )
            self.db.commit()
            settlement = self.db.execute(
                """SELECT * FROM live_strategy_settlements
                    WHERE order_local_id=? LIMIT 1""",
                (order_local_id,),
            ).fetchone()
        if settlement is None:
            return None
        result_row = dict(settlement)
        result_row["strategy"] = strategy
        result_row["manual_exit_id"] = int(exit_id)
        return result_row

    def reconcile_filled_manual_exit_settlements(
        self,
    ) -> list[dict[str, Any]]:
        """Backfill confirmed manual exits that predate immediate finalization."""
        with self.lock:
            rows = self.db.execute(
                """SELECT x.id
                     FROM live_manual_exits x
                     LEFT JOIN live_strategy_settlements s
                       ON s.order_local_id=x.order_local_id
                    WHERE UPPER(x.status)='FILLED'
                      AND s.order_local_id IS NULL
                    ORDER BY x.id ASC"""
            ).fetchall()
        settlements: list[dict[str, Any]] = []
        for row in rows:
            settlement = self.record_manual_exit_settlement(int(row["id"]))
            if settlement is not None:
                settlements.append(settlement)
        return settlements

    def pending_manual_exits(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                """SELECT * FROM live_manual_exits
                   WHERE status IN ('SUBMITTED','OPEN','PARTIAL','PARTIALLY_FILLED','PENDING')
                   ORDER BY id ASC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_manual_exits(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM live_manual_exits ORDER BY id DESC LIMIT ?",
                (max(1, min(200, int(limit))),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item.pop("token_id", None)
            item.pop("response_json", None)
            result.append(item)
        return result

    def active_strategy_positions(
        self,
        strategy: str | None = None,
        market_id: int | None = None,
    ) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                """SELECT o.id AS order_local_id, o.strategy, o.topic_id,
                          o.market_id, o.side, o.status AS entry_status,
                          o.quote_average_price, o.quote_amount_in_wei,
                          o.filled_usdt_amount, o.filled_share_qty,
                          o.market_provider_fee, o.network_fee,
                          o.signal_at, o.updated_at,
                          x.id AS exit_id, x.status AS exit_status,
                          x.order_type AS exit_order_type,
                          x.time_in_force AS exit_time_in_force,
                          x.sell_shares AS exit_sell_shares,
                          x.price_limit AS exit_price_limit,
                          x.order_id AS exit_order_id,
                          x.error_kind AS exit_error_kind,
                          x.error_message AS exit_error_message,
                          x.submitted_at AS exit_submitted_at,
                          x.updated_at AS exit_updated_at
                     FROM live_orders o
                     LEFT JOIN live_strategy_settlements s
                       ON s.order_local_id=o.id
                     LEFT JOIN live_manual_exits x
                       ON x.order_local_id=o.id
                    WHERE (? IS NULL OR o.strategy=?)
                      AND (? IS NULL OR o.market_id=?)
                      AND COALESCE(o.filled_usdt_amount,0)>0
                      AND COALESCE(o.filled_share_qty,0)>0
                      AND s.order_local_id IS NULL
                      AND COALESCE(x.status,'') NOT IN ('FILLED','COMPLETED')
                    ORDER BY o.id DESC""",
                (strategy, strategy, market_id, market_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_pair_orders(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                """SELECT * FROM live_orders
                   WHERE strategy GLOB 'PAIR_ARB_*:*'
                   ORDER BY id DESC LIMIT ?""",
                (max(2, min(500, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_pair_quote_audit(
        self,
        *,
        strategy: str,
        market_id: int,
        status: str,
        reason: str,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        values = dict(metrics or {})
        with self.lock:
            self.db.execute(
                """INSERT INTO live_pair_quote_audits(
                       strategy, market_id, status, up_quote_price,
                       down_quote_price, up_net_shares, down_net_shares,
                       total_cost_usdt, guaranteed_payout_usdt,
                       locked_pnl_usdt, locked_roi, minimum_capacity_ratio,
                       net_share_mismatch_ratio, minimum_expiry_ms,
                       maximum_quote_rtt_ms, reason, created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(strategy, market_id) DO UPDATE SET
                       status=excluded.status,
                       up_quote_price=excluded.up_quote_price,
                       down_quote_price=excluded.down_quote_price,
                       up_net_shares=excluded.up_net_shares,
                       down_net_shares=excluded.down_net_shares,
                       total_cost_usdt=excluded.total_cost_usdt,
                       guaranteed_payout_usdt=excluded.guaranteed_payout_usdt,
                       locked_pnl_usdt=excluded.locked_pnl_usdt,
                       locked_roi=excluded.locked_roi,
                       minimum_capacity_ratio=excluded.minimum_capacity_ratio,
                       net_share_mismatch_ratio=excluded.net_share_mismatch_ratio,
                       minimum_expiry_ms=excluded.minimum_expiry_ms,
                       maximum_quote_rtt_ms=excluded.maximum_quote_rtt_ms,
                       reason=excluded.reason,
                       created_at=excluded.created_at""",
                (
                    str(strategy), int(market_id), str(status),
                    values.get("up_quote_price"),
                    values.get("down_quote_price"),
                    values.get("up_net_shares"),
                    values.get("down_net_shares"),
                    values.get("total_cost_usdt"),
                    values.get("guaranteed_payout_usdt"),
                    values.get("locked_pnl_usdt"),
                    values.get("locked_roi"),
                    values.get("minimum_capacity_ratio"),
                    values.get("net_share_mismatch_ratio"),
                    values.get("minimum_expiry_ms"),
                    values.get("maximum_quote_rtt_ms"),
                    str(reason)[:500], utc_iso(),
                ),
            )
            self.db.commit()

    def recent_pair_quote_audits(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                """SELECT * FROM live_pair_quote_audits
                   ORDER BY id DESC LIMIT ?""",
                (max(1, min(500, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def pair_qc_shadow_sample_count(self, strategy: str) -> int:
        with self.lock:
            row = self.db.execute(
                """SELECT COUNT(*) AS count
                     FROM live_pair_quote_audits
                    WHERE strategy=?
                      AND status IN ('SHADOW_ACCEPTED', 'SHADOW_REJECTED')""",
                (str(strategy),),
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    def pair_market_has_settlement(self, strategy: str, market_id: int) -> bool:
        with self.lock:
            row = self.db.execute(
                """SELECT 1
                     FROM live_strategy_settlements AS settlements
                     JOIN live_orders AS orders
                       ON orders.id=settlements.order_local_id
                    WHERE orders.market_id=?
                      AND orders.strategy LIKE ?
                    LIMIT 1""",
                (int(market_id), f"{str(strategy)}:%"),
            ).fetchone()
        return row is not None

    def pair_incident(
        self, strategy: str, market_id: int
    ) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                """SELECT * FROM live_pair_incidents
                   WHERE strategy=? AND market_id=? LIMIT 1""",
                (str(strategy), int(market_id)),
            ).fetchone()
        return dict(row) if row is not None else None

    def record_pair_incident(
        self,
        *,
        strategy: str,
        market_id: int,
        status: str,
        reason: str,
        filled_side: str | None = None,
        missing_side: str | None = None,
    ) -> dict[str, Any]:
        normalized_status = str(status).upper()
        if normalized_status not in {"ACTIVE", "HEDGED", "SETTLED", "RESOLVED"}:
            raise ValueError("unsupported pair incident status")
        now = utc_iso()
        resolved_at = now if normalized_status != "ACTIVE" else None
        with self.lock:
            self.db.execute(
                """INSERT INTO live_pair_incidents(
                       strategy, market_id, status, filled_side, missing_side,
                       reason, detected_at, resolved_at, updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(strategy, market_id) DO UPDATE SET
                       status=excluded.status,
                       filled_side=COALESCE(excluded.filled_side, live_pair_incidents.filled_side),
                       missing_side=COALESCE(excluded.missing_side, live_pair_incidents.missing_side),
                       reason=excluded.reason,
                       resolved_at=excluded.resolved_at,
                       updated_at=excluded.updated_at""",
                (
                    str(strategy), int(market_id), normalized_status,
                    filled_side, missing_side, str(reason)[:500], now,
                    resolved_at, now,
                ),
            )
            self.db.commit()
            row = self.db.execute(
                """SELECT * FROM live_pair_incidents
                   WHERE strategy=? AND market_id=? LIMIT 1""",
                (str(strategy), int(market_id)),
            ).fetchone()
        assert row is not None
        return dict(row)

    def recent_pair_incidents(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                """SELECT * FROM live_pair_incidents
                   ORDER BY id DESC LIMIT ?""",
                (max(1, min(500, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def pair_insurance(
        self, strategy: str, market_id: int
    ) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                """SELECT * FROM live_pair_insurance
                   WHERE strategy=? AND market_id=? LIMIT 1""",
                (str(strategy), int(market_id)),
            ).fetchone()
        return dict(row) if row is not None else None

    def arm_pair_insurance(self, **values: Any) -> dict[str, Any]:
        now = utc_iso()
        with self.lock:
            self.db.execute(
                """INSERT OR IGNORE INTO live_pair_insurance(
                       strategy, market_id, low_side, token_id, status,
                       armed_at, sell_shares, price_limit, high_bid,
                       guaranteed_cost_lower, up_payout_upper,
                       down_payout_upper, updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(values["strategy"]),
                    int(values["market_id"]),
                    str(values["low_side"]),
                    str(values["token_id"]),
                    "ARMED",
                    now,
                    float(values["sell_shares"]),
                    float(values["price_limit"]),
                    float(values["high_bid"]),
                    float(values["guaranteed_cost_lower"]),
                    float(values["up_payout_upper"]),
                    float(values["down_payout_upper"]),
                    now,
                ),
            )
            self.db.commit()
            row = self.db.execute(
                """SELECT * FROM live_pair_insurance
                   WHERE strategy=? AND market_id=? LIMIT 1""",
                (str(values["strategy"]), int(values["market_id"])),
            ).fetchone()
        if row is None:
            raise RuntimeError("failed to persist pair insurance arm")
        return dict(row)

    def update_pair_insurance(
        self, insurance_id: int, **values: Any
    ) -> None:
        allowed = {
            "status", "attempted_at", "submitted_at", "order_id",
            "sell_shares", "price_limit", "high_bid",
            "guaranteed_cost_lower", "up_payout_upper",
            "down_payout_upper", "error_kind", "error_message",
            "response_json",
        }
        updates = {key: value for key, value in values.items() if key in allowed}
        if not updates:
            return
        updates["updated_at"] = utc_iso()
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self.lock:
            self.db.execute(
                f"UPDATE live_pair_insurance SET {assignments} WHERE id=?",
                (*updates.values(), int(insurance_id)),
            )
            self.db.commit()

    def pending_pair_insurance(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                """SELECT * FROM live_pair_insurance
                   WHERE status IN ('SUBMITTED','OPEN','PARTIAL','PENDING')
                   ORDER BY id ASC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_pair_insurance(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                """SELECT * FROM live_pair_insurance
                   ORDER BY id DESC LIMIT ?""",
                (max(1, min(200, int(limit))),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item.pop("token_id", None)
            item.pop("response_json", None)
            result.append(item)
        return result

    def unsettled_filled_orders(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                """SELECT o.*
                     FROM live_orders o
                     LEFT JOIN live_strategy_settlements s
                       ON s.order_local_id=o.id
                    WHERE COALESCE(o.filled_usdt_amount, 0)>0
                      AND s.order_local_id IS NULL
                    ORDER BY o.id ASC"""
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _wei_amount(value: Any) -> float | None:
        try:
            amount = Decimal(str(value)) / Decimal(10**18)
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not amount.is_finite() or amount < 0:
            return None
        return float(amount)

    @staticmethod
    def _loss_cooldown_strategy(strategy: Any) -> str:
        return str(strategy or "").split(":", 1)[0].strip().upper()

    def _record_loss_cooldown_result_locked(
        self,
        *,
        order_local_id: int,
        strategy: str,
        market_id: int,
        result: str,
        processed_at: str,
    ) -> None:
        normalized_strategy = self._loss_cooldown_strategy(strategy)
        normalized_result = str(result).upper()
        if not normalized_strategy or normalized_result not in {"WIN", "LOSS"}:
            return
        inserted = self.db.execute(
            """INSERT OR IGNORE INTO live_strategy_loss_cooldown_results(
                   order_local_id, strategy, market_id, result, processed_at
               ) VALUES (?, ?, ?, ?, ?)""",
            (
                int(order_local_id),
                normalized_strategy,
                int(market_id),
                normalized_result,
                processed_at,
            ),
        )
        if inserted.rowcount != 1:
            return
        loss_value = 1 if normalized_result == "LOSS" else 0
        self.db.execute(
            """INSERT INTO live_strategy_loss_cooldown_state(
                   strategy, consecutive_losses, last_result,
                   last_settlement_order_local_id, last_settlement_market_id,
                   updated_at
               ) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(strategy) DO UPDATE SET
                   consecutive_losses=CASE
                       WHEN excluded.last_result='LOSS'
                       THEN MIN(1000, live_strategy_loss_cooldown_state.consecutive_losses + 1)
                       ELSE 0
                   END,
                   last_result=excluded.last_result,
                   last_settlement_order_local_id=excluded.last_settlement_order_local_id,
                   last_settlement_market_id=excluded.last_settlement_market_id,
                   updated_at=excluded.updated_at""",
            (
                normalized_strategy,
                loss_value,
                normalized_result,
                int(order_local_id),
                int(market_id),
                processed_at,
            ),
        )

    def loss_cooldown_state(self, strategy: str) -> dict[str, Any]:
        normalized_strategy = self._loss_cooldown_strategy(strategy)
        with self.lock:
            row = self.db.execute(
                """SELECT * FROM live_strategy_loss_cooldown_state
                    WHERE strategy=?""",
                (normalized_strategy,),
            ).fetchone()
        item = dict(row) if row is not None else {}
        consecutive_losses = int(item.get("consecutive_losses") or 0)
        return {
            "strategy": normalized_strategy,
            "consecutiveLosses": consecutive_losses,
            "cooldownPending": consecutive_losses >= 2,
            "lastResult": item.get("last_result"),
            "lastSettlementOrderLocalId": item.get(
                "last_settlement_order_local_id"
            ),
            "lastSettlementMarketId": item.get("last_settlement_market_id"),
            "lastSkippedMarketId": item.get("last_skipped_market_id"),
            "lastSkippedAt": item.get("last_skipped_at"),
            "updatedAt": item.get("updated_at"),
        }

    def consume_loss_cooldown(
        self, strategy: str, market_id: int
    ) -> tuple[bool, dict[str, Any]]:
        """Atomically consume one market after two discovered official losses."""
        normalized_strategy = self._loss_cooldown_strategy(strategy)
        normalized_market_id = int(market_id)
        now = utc_iso()
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                row = self.db.execute(
                    """SELECT * FROM live_strategy_loss_cooldown_state
                        WHERE strategy=?""",
                    (normalized_strategy,),
                ).fetchone()
                if row is None:
                    self.db.commit()
                    return False, self.loss_cooldown_state(normalized_strategy)
                if int(row["last_skipped_market_id"] or -1) == normalized_market_id:
                    self.db.commit()
                    return True, self.loss_cooldown_state(normalized_strategy)
                if int(row["consecutive_losses"] or 0) < 2:
                    self.db.commit()
                    return False, self.loss_cooldown_state(normalized_strategy)
                self.db.execute(
                    """UPDATE live_strategy_loss_cooldown_state
                          SET consecutive_losses=0,
                              last_skipped_market_id=?,
                              last_skipped_at=?,
                              updated_at=?
                        WHERE strategy=?""",
                    (
                        normalized_market_id,
                        now,
                        now,
                        normalized_strategy,
                    ),
                )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return True, self.loss_cooldown_state(normalized_strategy)

    def record_strategy_settlement(
        self,
        order: dict[str, Any],
        position: dict[str, Any],
    ) -> dict[str, Any] | None:
        position_status = str(position.get("positionStatus") or "").upper()
        is_winner = position.get("isWinner")
        if (
            position_status
            not in {"CLAIMED", "PENDING_CLAIM", "ENDED", "SETTLED", "REDEEMED"}
            or not isinstance(is_winner, bool)
        ):
            return None

        quote_cost = self._wei_amount(order.get("quote_amount_in_wei"))
        filled_cost = _float(order.get("filled_usdt_amount"))
        positive_costs = [
            value
            for value in (quote_cost, filled_cost)
            if value is not None and value > 0
        ]
        if not positive_costs:
            return None
        # Binance display fields are rounded to two decimals.  For a complete
        # fill the signed quote amount is the precise debited cost; for a
        # partial fill, filledUsdtAmount is the safe upper bound for this order.
        cost_usdt = min(positive_costs)
        network_fee = max(0.0, float(_float(order.get("network_fee")) or 0.0))

        payout_usdt = 0.0
        if is_winner:
            quoted_gross_shares = self._wei_amount(
                order.get("quote_amount_out_wei")
            )
            if quoted_gross_shares is not None:
                provider_fee = max(
                    0.0,
                    float(_float(order.get("market_provider_fee")) or 0.0),
                )
                payout_usdt = max(0.0, quoted_gross_shares - provider_fee)
            else:
                # filledShareQty is already net when no precise quote survives.
                payout_usdt = max(
                    0.0,
                    float(_float(order.get("filled_share_qty")) or 0.0),
                )

        pnl_usdt = payout_usdt - cost_usdt - network_fee
        result = "WIN" if is_winner else "LOSS"
        roi_pct = (pnl_usdt / cost_usdt) * 100.0
        end_date_ms = int(position.get("endDate") or 0)
        settled_at = (
            datetime.fromtimestamp(end_date_ms / 1_000, tz=timezone.utc).isoformat()
            if end_date_ms > 0
            else utc_iso()
        )
        now = utc_iso()
        with self.lock:
            self.db.execute(
                """INSERT INTO live_strategy_settlements(
                       order_local_id, market_id, position_status, result,
                       cost_usdt, payout_usdt, pnl_usdt, roi_pct,
                       settled_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(order_local_id) DO UPDATE SET
                       position_status=excluded.position_status,
                       result=excluded.result,
                       cost_usdt=excluded.cost_usdt,
                       payout_usdt=excluded.payout_usdt,
                       pnl_usdt=excluded.pnl_usdt,
                       roi_pct=excluded.roi_pct,
                       settled_at=excluded.settled_at,
                       updated_at=excluded.updated_at""",
                (
                    int(order["id"]),
                    int(order["market_id"]),
                    position_status,
                    result,
                    cost_usdt,
                    payout_usdt,
                    pnl_usdt,
                    roi_pct,
                    settled_at,
                    now,
                ),
            )
            self._record_loss_cooldown_result_locked(
                order_local_id=int(order["id"]),
                strategy=str(order.get("strategy") or ""),
                market_id=int(order["market_id"]),
                result=result,
                processed_at=now,
            )
            self.db.execute(
                """UPDATE live_reliability_samples
                      SET settlement_result=?, settlement_cost_usdt=?,
                          settlement_pnl_usdt=?, settlement_roi_pct=?,
                          settled_at=?, updated_at=?
                    WHERE order_local_id=? AND captured_at IS NOT NULL""",
                (
                    result,
                    cost_usdt,
                    pnl_usdt,
                    roi_pct,
                    settled_at,
                    now,
                    int(order["id"]),
                ),
            )
            self._finalize_confirmation_add_mirror_locked(
                order_local_id=int(order["id"]),
                result=result,
                settled_at=settled_at,
            )
            self.db.commit()
            row = self.db.execute(
                "SELECT * FROM live_strategy_settlements WHERE order_local_id=?",
                (int(order["id"]),),
            ).fetchone()
        return dict(row) if row is not None else None

    def strategy_performance(self, strategy: str | None = None) -> dict[str, Any]:
        pair_strategy = bool(strategy and str(strategy).startswith("PAIR_ARB_"))
        order_filter = (
            " AND strategy LIKE ?" if pair_strategy
            else " AND strategy=?" if strategy
            else ""
        )
        settlement_filter = (
            " WHERE order_local_id IN (SELECT id FROM live_orders WHERE strategy LIKE ?)"
            if pair_strategy
            else " WHERE order_local_id IN (SELECT id FROM live_orders WHERE strategy=?)"
            if strategy
            else ""
        )
        parameters: tuple[Any, ...] = (
            (f"{strategy}:%",) if pair_strategy
            else (str(strategy),) if strategy
            else ()
        )
        with self.lock:
            executed_row = self.db.execute(
                f"""SELECT COUNT(*) executed
                     FROM live_orders
                    WHERE COALESCE(filled_usdt_amount, 0)>0{order_filter}""",
                parameters,
            ).fetchone()
            row = self.db.execute(
                f"""SELECT
                       COUNT(*) settled,
                       COALESCE(SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END),0) wins,
                       COALESCE(SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END),0) losses,
                       COALESCE(SUM(cost_usdt),0) cost,
                       COALESCE(SUM(payout_usdt),0) payout,
                       COALESCE(SUM(pnl_usdt),0) pnl
                     FROM live_strategy_settlements{settlement_filter}""",
                parameters,
            ).fetchone()
        executed = int(executed_row["executed"])
        settled = int(row["settled"])
        wins = int(row["wins"])
        losses = int(row["losses"])
        cost = float(row["cost"])
        payout = float(row["payout"])
        pnl = float(row["pnl"])
        return {
            "executedTrades": executed,
            "settledTrades": settled,
            "unsettledTrades": max(0, executed - settled),
            "wins": wins,
            "losses": losses,
            "winRatePct": ((wins / settled) * 100.0 if settled else None),
            "settledCostUsdt": cost,
            "settledPayoutUsdt": payout,
            "profitUsdt": pnl,
            "roiPct": ((pnl / cost) * 100.0 if cost > 0 else None),
        }

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM live_events ORDER BY id DESC LIMIT ?",
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_claimable_redeem(self, position: dict[str, Any]) -> dict[str, Any]:
        token_id = str(position.get("tokenId") or "")
        end_date_ms = int(position.get("endDate") or 0)
        if not token_id or end_date_ms <= 0:
            raise ValueError("claimable position is missing tokenId or endDate")
        now = utc_iso()
        eligible_at = datetime.fromtimestamp(
            (end_date_ms / 1_000) + LIVE_AUTO_REDEEM_DELAY_SECONDS,
            tz=timezone.utc,
        ).isoformat()
        with self.lock:
            self.db.execute(
                """INSERT INTO live_redeems(
                       token_id, market_id, topic_id, chain_id, outcome_name,
                       shares, claimable_value, end_date_ms, status,
                       discovered_at, eligible_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'DISCOVERED', ?, ?, ?)
                   ON CONFLICT(token_id) DO UPDATE SET
                       market_id=excluded.market_id,
                       topic_id=excluded.topic_id,
                       chain_id=excluded.chain_id,
                       outcome_name=excluded.outcome_name,
                       shares=excluded.shares,
                       claimable_value=excluded.claimable_value,
                       end_date_ms=excluded.end_date_ms,
                       eligible_at=excluded.eligible_at,
                       updated_at=excluded.updated_at""",
                (
                    token_id,
                    int(position["marketId"])
                    if position.get("marketId") is not None else None,
                    int(position["marketTopicId"])
                    if position.get("marketTopicId") is not None else None,
                    str(position.get("chainId") or "56"),
                    str(position.get("outcomeName") or position.get("outcome") or ""),
                    float(_float(position.get("shares")) or 0.0),
                    float(_float(position.get("value")) or 0.0),
                    end_date_ms,
                    now,
                    eligible_at,
                    now,
                ),
            )
            self.db.commit()
            row = self.db.execute(
                "SELECT * FROM live_redeems WHERE token_id=?", (token_id,)
            ).fetchone()
        assert row is not None
        return dict(row)

    def begin_redeem_attempt(self, token_id: str) -> dict[str, Any] | None:
        now = utc_iso()
        with self.lock:
            cursor = self.db.execute(
                """UPDATE live_redeems
                   SET status='ATTEMPTED', attempted_at=?,
                       attempt_count=attempt_count+1, updated_at=?,
                       error_kind=NULL, error_message=NULL
                   WHERE token_id=? AND status='DISCOVERED'""",
                (now, now, str(token_id)),
            )
            self.db.commit()
            if not cursor.rowcount:
                return None
            row = self.db.execute(
                "SELECT * FROM live_redeems WHERE token_id=?", (str(token_id),)
            ).fetchone()
        return dict(row) if row is not None else None

    def update_redeem(self, token_id: str, **values: Any) -> None:
        updates = {
            key: value for key, value in values.items() if key in self.REDEEM_COLUMNS
        }
        if not updates:
            return
        updates["updated_at"] = utc_iso()
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self.lock:
            self.db.execute(
                f"UPDATE live_redeems SET {assignments} WHERE token_id=?",
                (*updates.values(), str(token_id)),
            )
            self.db.commit()

    def redeem_candidates(self, limit: int = LIVE_REDEEM_MAX_PER_SCAN) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                """SELECT * FROM live_redeems WHERE status='DISCOVERED'
                   ORDER BY end_date_ms ASC, id ASC LIMIT ?""",
                (max(1, min(100, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def redeem_by_token(self, token_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM live_redeems WHERE token_id=?", (str(token_id),)
            ).fetchone()
        return dict(row) if row is not None else None

    def pending_redeems(self) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in REDEEM_PENDING_STATUSES)
        with self.lock:
            rows = self.db.execute(
                f"""SELECT * FROM live_redeems
                    WHERE status IN ({placeholders}) ORDER BY id ASC""",
                tuple(REDEEM_PENDING_STATUSES),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_redeems(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM live_redeems ORDER BY id DESC LIMIT ?",
                (max(1, min(500, int(limit))),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            token_id = str(item.pop("token_id", ""))
            item["token"] = (
                f"{token_id[:8]}…{token_id[-5:]}" if len(token_id) > 16 else token_id
            )
            item.pop("response_json", None)
            result.append(item)
        return result

    def redeem_summary(self) -> dict[str, Any]:
        pending = tuple(REDEEM_PENDING_STATUSES)
        placeholders = ",".join("?" for _ in pending)
        with self.lock:
            row = self.db.execute(
                f"""SELECT
                       COUNT(*) discovered_total,
                       COALESCE(SUM(CASE WHEN status='DISCOVERED' THEN 1 ELSE 0 END),0) ready,
                       COALESCE(SUM(CASE WHEN status IN ({placeholders}) THEN 1 ELSE 0 END),0) pending,
                       COALESCE(SUM(CASE WHEN status='COMPLETED' THEN 1 ELSE 0 END),0) completed,
                       COALESCE(SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END),0) failed,
                       COALESCE(SUM(CASE WHEN status='AMBIGUOUS' THEN 1 ELSE 0 END),0) ambiguous,
                       COALESCE(SUM(CASE WHEN status='COMPLETED' THEN claimable_value ELSE 0 END),0) redeemed_value
                   FROM live_redeems""",
                pending,
            ).fetchone()
        return {
            "discovered": int(row["discovered_total"]),
            "ready": int(row["ready"]),
            "pending": int(row["pending"]),
            "completed": int(row["completed"]),
            "failed": int(row["failed"]),
            "ambiguous": int(row["ambiguous"]),
            "redeemedValue": float(row["redeemed_value"]),
        }

    def summary(self) -> dict[str, Any]:
        with self.lock:
            row = self.db.execute(
                """SELECT
                       COUNT(*) signals,
                       COALESCE(SUM(CASE WHEN order_id IS NOT NULL THEN 1 ELSE 0 END),0) submitted,
                       COALESCE(SUM(CASE WHEN filled_usdt_amount>0 THEN 1 ELSE 0 END),0) filled_orders,
                       COALESCE(SUM(CASE WHEN fill_percentage>0 AND fill_percentage<1 THEN 1 ELSE 0 END),0) partial_orders,
                       COALESCE(SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END),0) rejected,
                       COALESCE(SUM(CASE WHEN status='AMBIGUOUS' THEN 1 ELSE 0 END),0) ambiguous,
                       COALESCE(SUM(filled_usdt_amount),0) filled_usdt,
                       COALESCE(SUM(realized_pnl),0) realized_pnl,
                       COALESCE(SUM(COALESCE(market_provider_fee,0)+COALESCE(network_fee,0)),0) fees
                   FROM live_orders"""
            ).fetchone()
        return {
            "signals": int(row["signals"]),
            "submitted": int(row["submitted"]),
            "filledOrders": int(row["filled_orders"]),
            "partialOrders": int(row["partial_orders"]),
            "rejected": int(row["rejected"]),
            "ambiguous": int(row["ambiguous"]),
            "filledUsdt": float(row["filled_usdt"]),
            "realizedPnl": float(row["realized_pnl"]),
            "fees": float(row["fees"]),
        }


class LiveM0WEngine:
    """Fail-closed, one-attempt-per-market executor for selected M-series signals."""

    def __init__(
        self,
        *,
        api_key: str | None,
        api_secret: str | None,
        configured_enabled: bool,
        credential_source: str,
        current_market: Callable[[], dict[str, Any] | None],
        db_path: Path,
        account_type: str = "SPOT",
        auto_redeem_enabled: bool = True,
        m0_hourly_performance: Callable[[], dict[str, Any]] | None = None,
        client_factory: Callable[[str, str], BinancePredictionTradingClient]
        | None = None,
        order_sync_restart_threshold: int = LIVE_ORDER_SYNC_RESTART_THRESHOLD,
        restart_request: Callable[[str], None] | None = None,
        drawdown_market_history: Callable[
            [int, int], list[dict[str, Any]]
        ] | None = None,
        current_verified_prediction_book: Callable[
            [], dict[str, Any] | None
        ] | None = None,
        current_spot_reference: Callable[
            [], dict[str, Any] | None
        ] | None = None,
        current_direct_rest_prediction_book: Callable[
            [], dict[str, Any] | None
        ] | None = None,
        max_prediction_book_age_ms: float = LIVE_MAX_PREDICTION_BOOK_AGE_MS,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.configured_enabled = bool(configured_enabled)
        self.credential_source = credential_source
        self.current_market = current_market
        self.account_type = account_type.upper()
        self.auto_redeem_enabled = bool(auto_redeem_enabled)
        self.m0_hourly_performance = m0_hourly_performance
        if self.account_type not in {"SPOT", "FUNDING"}:
            self.account_type = "SPOT"
        self.client_factory = client_factory or BinancePredictionTradingClient
        self.order_sync_restart_threshold = max(
            1, int(order_sync_restart_threshold)
        )
        self.restart_request = restart_request
        self.drawdown_market_history = drawdown_market_history
        self.current_verified_prediction_book = current_verified_prediction_book
        self.current_spot_reference = current_spot_reference
        self.current_direct_rest_prediction_book = (
            current_direct_rest_prediction_book
            or current_verified_prediction_book
        )
        self.max_prediction_book_age_ms = max(
            1.0, float(max_prediction_book_age_ms)
        )
        self.ledger = LiveLedger(db_path)
        self.sqlite_state = self.ledger.sqlite_settings()
        self.attempt_summary_state = self.ledger.attempt_summary(100)
        self.live_rules = normalize_live_rules(self.ledger.live_rule_overrides())
        override = self.ledger.runtime_override()
        self.runtime_enabled = (
            self.configured_enabled if override is None else bool(override)
        )
        self.events: queue.Queue[dict[str, Any]] = queue.Queue(
            maxsize=LIVE_SIGNAL_QUEUE_MAX
        )
        self.stop_event = threading.Event()
        self.preflight_ready = threading.Event()
        self.order_in_flight = threading.Event()
        self.thread: threading.Thread | None = None
        self.maintenance_thread: threading.Thread | None = None
        self.lock = threading.RLock()
        self.client: BinancePredictionTradingClient | None = None
        self.wallet_address: str | None = None
        self.wallet_id: str | None = None
        self.status = "STARTING" if self.runtime_enabled else "DISABLED"
        self.armed = False
        self.sas_status = "UNVERIFIED"
        self.quote_access = "UNVERIFIED"
        self.last_error: str | None = None
        self.last_preflight_at: str | None = None
        self.last_order_sync_at: str | None = None
        self.consecutive_order_sync_transport_errors = 0
        self.restart_requested = False
        self.last_settlement_sync_at: str | None = None
        self.last_signal_at: str | None = None
        self.last_signal_market_id: int | None = None
        self.last_order_latency: dict[str, Any] | None = None
        self.last_local_price_check: dict[str, Any] | None = None
        self.last_depth_check: dict[str, Any] | None = None
        self.last_quote_attempt: dict[str, Any] | None = None
        self.last_drawdown_reference: dict[str, Any] | None = None
        self.dropped_signals = 0
        self.balances: list[dict[str, Any]] = []
        self.quota: dict[str, Any] = {}
        self.portfolio_state: dict[str, Any] = {}
        self.next_account_refresh = 0.0
        self.next_order_sync = 0.0
        self.next_settlement_sync = 0.0
        self.next_hourly_guard_refresh = 0.0
        self.next_quote_preflight = 0.0
        self.next_pair_insurance_check = 0.0
        self.auto_redeem_status = (
            "STARTING" if self.auto_redeem_enabled else "DISABLED"
        )
        self.last_redeem_scan_at: str | None = None
        self.last_redeem_success_at: str | None = None
        self.last_redeem_error: str | None = None
        self.claimable_count = 0
        self.claimable_amount = 0.0
        self.next_redeem_scan = 0.0
        self.redeem_cycle_lock = threading.Lock()
        self.manual_exit_lock = threading.Lock()
        self.pending_pair_signals: dict[
            tuple[str, int], dict[str, dict[str, Any]]
        ] = {}
        self.pending_futures_lead_hedges: dict[
            int, dict[str, dict[str, Any]]
        ] = {}
        self.hourly_performance_snapshot: dict[str, Any] | None = None
        self.hourly_guard_state: dict[str, Any] = {
            "status": "WAITING" if self.m0_hourly_performance else "DISABLED",
            "blocked": False,
            "timezone": "Asia/Taipei",
            "utcOffset": "+08:00",
            "minWinRatePct": self.live_rules["minHourlyWinRatePct"],
            "maxWinThenLossRatePct": self.live_rules[
                "maxHourlyWinThenLossRatePct"
            ],
            "reasons": [],
        }

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(
            target=self._run, name="live-m0w-executor", daemon=True
        )
        self.maintenance_thread = threading.Thread(
            target=self._run_maintenance,
            name="live-m0w-maintenance",
            daemon=True,
        )
        self.thread.start()
        self.maintenance_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3.0)
        if self.maintenance_thread:
            self.maintenance_thread.join(timeout=3.0)
        with self.lock:
            client = self.client
        close = getattr(client, "close", None)
        if callable(close):
            close()

    def set_runtime_enabled(self, enabled: bool) -> dict[str, Any]:
        self.ledger.set_runtime_enabled(bool(enabled))
        with self.lock:
            self.runtime_enabled = bool(enabled)
            if not enabled:
                self.armed = False
                self.status = "PAUSED"
                self.ledger.record_event("WARN", "RUNTIME_PAUSED", "實單已由本地監視頁暫停")
            else:
                self.status = "PREFLIGHT"
                self.last_error = None
                self.ledger.record_event("INFO", "RUNTIME_RESUMED", "實單已由本地監視頁恢復，重新執行預檢")
        if enabled:
            self._preflight()
        return self.state()

    def selected_strategy(self) -> str:
        with self.lock:
            return str(self.live_rules["strategy"])

    def selected_strategies(self) -> tuple[str, ...]:
        with self.lock:
            return tuple(str(value) for value in self.live_rules["strategies"])

    def _pair_qc_live_ready(self) -> bool:
        return self.ledger.pair_qc_shadow_sample_count(
            "PAIR_ARB_QC_015"
        ) >= PAIR_ARB_QC_MIN_SHADOW_SAMPLES

    def update_live_rules(self, values: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise ValueError("live rules must be a JSON object")
        allowed = {
            "strategy",
            "strategies",
            "maxStakeUsdt",
            "strategyStakesUsdt",
            "minHourlyWinRatePct",
            "maxHourlyWinThenLossRatePct",
            "futuresLeadObserverEnabled",
            "futuresLeadObserverVersion",
            "strategyObserverEnabled",
            "strategyObserverVersions",
            "strategyDrawdownControlEnabled",
            "strategyLossCooldownEnabled",
            "reliabilityGateTags",
        }
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError("unsupported live rule fields: " + ", ".join(unknown))
        with self.lock:
            previous = dict(self.live_rules)
        updated = normalize_live_rules(values, previous)
        self.ledger.set_live_rules(updated)
        with self.lock:
            self.live_rules = dict(updated)
            self.quote_access = "UNVERIFIED"
            self.next_quote_preflight = 0.0
            self.hourly_guard_state = {
                **self.hourly_guard_state,
                "status": "WAITING",
                "blocked": False,
                "minWinRatePct": updated["minHourlyWinRatePct"],
                "maxWinThenLossRatePct": updated[
                    "maxHourlyWinThenLossRatePct"
                ],
                "reasons": [],
            }
            runtime_enabled = self.runtime_enabled
        self.ledger.record_event(
            "WARN",
            "LIVE_RULES_UPDATED",
            (
                f"實單規則已更新：策略 {','.join(previous['strategies'])}→"
                f"{','.join(updated['strategies'])}；"
                f"各策略上限 {previous['strategyStakesUsdt']}→"
                f"{updated['strategyStakesUsdt']} USDT；M0 每小時最低勝率 "
                f"{previous['minHourlyWinRatePct']:.8g}%→"
                f"{updated['minHourlyWinRatePct']:.8g}%；一勝一敗率上限 "
                f"{previous['maxHourlyWinThenLossRatePct']:.8g}%→"
                f"{updated['maxHourlyWinThenLossRatePct']:.8g}%；"
                f"各策略 Observer {updated['strategyObserverEnabled']} / "
                f"{updated['strategyObserverVersions']}"
                f"; drawdown control "
                f"{updated['strategyDrawdownControlEnabled']}"
                f"; two-loss cooldown "
                f"{updated['strategyLossCooldownEnabled']}"
                f"; reliability gates {updated['reliabilityGateTags']}"
            ),
        )
        if runtime_enabled:
            self._preflight()
        return self.state()

    def submit_signal(self, signal: dict[str, Any]) -> None:
        with self.lock:
            selected_strategies = set(self.live_rules["strategies"])
            restart_requested = self.restart_requested
        if restart_requested:
            return
        if str(signal.get("strategy")) not in selected_strategies:
            return
        try:
            queued = dict(signal)
            queued.setdefault("_live_enqueued_monotonic_ns", time.monotonic_ns())
            self.events.put_nowait(queued)
        except queue.Full:
            with self.lock:
                self.dropped_signals += 1
                self.status = "DEGRADED"
                self.last_error = "live signal queue overflow; no order was placed"

    def record_confirmation_add_snapshot(
        self, snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        """Record counterfactual add-on fills; never reaches the order queue."""
        return self.ledger.record_confirmation_add_snapshot(snapshot)

    def _preflight(self) -> None:
        with self.lock:
            if not self.runtime_enabled and not self.auto_redeem_enabled:
                self.status = "PAUSED" if self.configured_enabled else "DISABLED"
                self.armed = False
                self.auto_redeem_status = "DISABLED"
                return
            runtime_enabled = self.runtime_enabled
            self.status = "PREFLIGHT" if runtime_enabled else (
                "PAUSED" if self.configured_enabled else "DISABLED"
            )
            if self.auto_redeem_enabled:
                self.auto_redeem_status = "PREFLIGHT"
            self.armed = False
            qc_live_ready = self._pair_qc_live_ready()
            max_stake_usdt = sum(
                float(stake)
                for strategy, stake in zip(
                    self.live_rules["strategies"],
                    self.live_rules["strategyStakesUsdt"],
                )
                if strategy != "PAIR_ARB_QC_015" or qc_live_ready
            )
        if not self.api_key or not self.api_secret:
            with self.lock:
                self.status = "CONFIG_REQUIRED"
                self.last_error = "live Binance credentials are unavailable"
                if self.auto_redeem_enabled:
                    self.auto_redeem_status = "BLOCKED_PREFLIGHT"
                    self.last_redeem_error = self.last_error
            return
        try:
            client = self.client_factory(self.api_key, self.api_secret)
            wallets = client.wallets().get("wallets") or []
            if len(wallets) != 1:
                raise RuntimeError(
                    f"expected exactly one Prediction wallet, received {len(wallets)}"
                )
            wallet = wallets[0]
            wallet_address = str(wallet.get("walletAddress") or "")
            wallet_id = str(wallet.get("walletId") or "")
            if not wallet_address or not wallet_id:
                raise RuntimeError("Prediction wallet response is incomplete")
            quota = client.quota_status()
            balance_payload = client.payment_option_balances()
            balances = [
                {
                    "accountType": str(item.get("accountType") or "UNKNOWN"),
                    "availableBalance": _float(item.get("availableBalanceDisplay")),
                    "enabled": bool(item.get("enabled")),
                }
                for item in (balance_payload.get("items") or [])
            ]
            enabled_balance = sum(
                float(item["availableBalance"] or 0.0)
                for item in balances
                if item["enabled"]
            )
            balance_is_sufficient = (
                enabled_balance + 1e-12 >= max_stake_usdt
            )
            with self.lock:
                self.client = client
                self.wallet_address = wallet_address
                self.wallet_id = wallet_id
                self.quota = {
                    "dailyLimit": _float(quota.get("dailyLimit")),
                    "remainingDailyLimit": _float(
                        quota.get("remainingDailyLimit")
                    ),
                }
                self.balances = balances
                if self.auto_redeem_enabled:
                    self.auto_redeem_status = "READY"
                    self.last_redeem_error = None
                if not runtime_enabled:
                    self.status = "PAUSED" if self.configured_enabled else "DISABLED"
                    self.armed = False
                    self.last_error = None
                elif balance_is_sufficient:
                    self.status = "ARMED_SAS_UNVERIFIED"
                    self.sas_status = "UNVERIFIED_UNTIL_FIRST_ORDER"
                    self.armed = True
                    self.last_error = None
                else:
                    self.status = "BLOCKED_BALANCE"
                    self.armed = False
                    self.last_error = (
                        "enabled Prediction payment balances are below the configured "
                        f"{max_stake_usdt:.8g} USDT cap"
                    )
                self.last_preflight_at = utc_iso()
                self.next_account_refresh = time.monotonic()
            self.ledger.record_event(
                "WARN",
                "PREFLIGHT_OK",
                (
                    "Prediction wallet connection ready; trading remains paused and "
                    "auto-redeem may continue"
                    if not runtime_enabled
                    else "Prediction wallet、配額與餘額預檢通過；SAS 會在首筆正式送單驗證"
                ),
            )
        except Exception as exc:
            with self.lock:
                self.status = "BLOCKED_PREFLIGHT"
                self.armed = False
                self.last_error = str(exc)[:400]
                self.last_preflight_at = utc_iso()
                if self.auto_redeem_enabled:
                    self.auto_redeem_status = "BLOCKED_PREFLIGHT"
                    self.last_redeem_error = str(exc)[:400]
            self.ledger.record_event(
                "ERROR", "PREFLIGHT_FAILED", str(exc)[:400]
            )

    @staticmethod
    def _price_limit(value: Any) -> str:
        price = _decimal(value)
        if price is None or not Decimal("0") < price < Decimal("1"):
            raise ValueError("live signal price must be between 0 and 1")
        # str(Decimal) preserves the exchange tick received from the book and
        # avoids binary-float artifacts such as 0.9300000000000001.
        return format(price.normalize(), "f")

    def _latest_prediction_book_check(
        self, *, market_id: int, side: str
    ) -> tuple[
        dict[str, Any] | None,
        str | None,
        str | None,
        str | None,
        dict[str, Any],
    ]:
        callback = self.current_verified_prediction_book
        try:
            raw = callback() if callback is not None else None
        except Exception as exc:
            return (
                None,
                "BLOCKED_PREDICTION_ORIENTATION_UNVERIFIED",
                "LOCAL_UNVERIFIED_BOOK",
                f"verified Prediction book callback failed: {str(exc)[:200]}",
                {"latestMarketId": None, "orientation": "UNAVAILABLE"},
            )
        if not isinstance(raw, dict):
            return (
                None,
                "BLOCKED_PREDICTION_ORIENTATION_UNVERIFIED",
                "LOCAL_UNVERIFIED_BOOK",
                "no verified local Prediction book is available",
                {"latestMarketId": None, "orientation": "UNAVAILABLE"},
            )
        try:
            latest_market_id = int(raw.get("market_id") or 0)
        except (TypeError, ValueError):
            latest_market_id = 0
        orientation = str(raw.get("orientation") or "UNVERIFIED")
        diagnostics: dict[str, Any] = {
            "latestMarketId": latest_market_id or None,
            "orientation": orientation,
        }
        if latest_market_id != market_id:
            return (
                None,
                "BLOCKED_PREDICTION_MARKET_MISMATCH",
                "LOCAL_MARKET_MISMATCH",
                (
                    f"latest Prediction book market {latest_market_id or 'unknown'} "
                    f"does not match signal market {market_id}"
                ),
                diagnostics,
            )
        if orientation not in VERIFIED_PREDICTION_ORIENTATIONS:
            return (
                None,
                "BLOCKED_PREDICTION_ORIENTATION_UNVERIFIED",
                "LOCAL_UNVERIFIED_BOOK",
                f"latest Prediction book orientation is not verified: {orientation}",
                diagnostics,
            )
        try:
            received_ns = int(raw.get("received_monotonic_ns") or 0)
        except (TypeError, ValueError):
            received_ns = 0
        now_ns = time.monotonic_ns()
        if received_ns <= 0 or received_ns > now_ns:
            age_ms = None
        else:
            receipt_age_ms = max(0.0, (now_ns - received_ns) / 1_000_000)
            reported_age_ms = _float(raw.get("book_age_ms"))
            age_ms = max(
                receipt_age_ms,
                reported_age_ms if reported_age_ms is not None else 0.0,
            )
        diagnostics["latestLocalBookAgeMs"] = age_ms
        if age_ms is None or age_ms > self.max_prediction_book_age_ms:
            return (
                None,
                "BLOCKED_STALE_PREDICTION_BOOK",
                "LOCAL_STALE_BOOK",
                (
                    "latest verified Prediction book has no valid local receipt age"
                    if age_ms is None
                    else (
                        f"latest verified Prediction book age {age_ms:.3f}ms exceeds "
                        f"{self.max_prediction_book_age_ms:.3f}ms"
                    )
                ),
                diagnostics,
            )
        ask_key = "up_ask" if side == "UP" else "down_ask"
        ask_size_key = "up_ask_size" if side == "UP" else "down_ask_size"
        ask = _decimal(raw.get(ask_key))
        ask_size = _decimal(raw.get(ask_size_key))
        if ask is None or not Decimal("0") < ask < Decimal("1"):
            return (
                None,
                "BLOCKED_PREDICTION_ORIENTATION_UNVERIFIED",
                "LOCAL_UNVERIFIED_BOOK",
                f"latest verified Prediction book is missing valid {side} top level",
                diagnostics,
            )
        checked = dict(raw)
        checked["book_age_ms"] = age_ms
        checked["latest_ask"] = ask
        checked["latest_ask_size"] = ask_size
        diagnostics.update(
            {
                "latestLocalAsk": float(ask),
                "latestLocalAskSize": (
                    float(ask_size) if ask_size is not None else None
                ),
            }
        )
        return checked, None, None, None, diagnostics

    def _latest_pair_rest_book_check(
        self,
        *,
        signal: dict[str, Any],
        market_id: int,
        side: str,
    ) -> tuple[
        dict[str, Any] | None,
        str | None,
        str | None,
        str | None,
        dict[str, Any],
    ]:
        raw = signal.get("_pair_preflight_book")
        if not isinstance(raw, dict):
            callback = self.current_direct_rest_prediction_book
            try:
                raw = callback() if callback is not None else None
            except Exception as exc:
                return (
                    None,
                    "BLOCKED_PREDICTION_ORIENTATION_UNVERIFIED",
                    "PAIR_REST_BOOK_UNAVAILABLE",
                    f"direct Prediction REST book callback failed: {str(exc)[:200]}",
                    {"latestMarketId": None, "bookSource": "UNAVAILABLE"},
                )
        if not isinstance(raw, dict):
            return (
                None,
                "BLOCKED_PREDICTION_ORIENTATION_UNVERIFIED",
                "PAIR_REST_BOOK_UNAVAILABLE",
                "no current direct Prediction REST pair book is available",
                {"latestMarketId": None, "bookSource": "UNAVAILABLE"},
            )
        try:
            latest_market_id = int(raw.get("market_id") or 0)
        except (TypeError, ValueError):
            latest_market_id = 0
        book_source = str(raw.get("data_source") or "dual_token_rest")
        diagnostics: dict[str, Any] = {
            "latestMarketId": latest_market_id or None,
            "bookSource": book_source,
            "freshnessBasis": "REST_RECEIPT_AGE",
        }
        if latest_market_id != market_id:
            return (
                None,
                "BLOCKED_PREDICTION_MARKET_MISMATCH",
                "PAIR_REST_MARKET_MISMATCH",
                (
                    f"latest direct REST pair book market "
                    f"{latest_market_id or 'unknown'} does not match signal market "
                    f"{market_id}"
                ),
                diagnostics,
            )
        if book_source != "dual_token_rest":
            return (
                None,
                "BLOCKED_PREDICTION_ORIENTATION_UNVERIFIED",
                "PAIR_REST_BOOK_UNAVAILABLE",
                f"latest pair book source is not dual_token_rest: {book_source}",
                diagnostics,
            )
        try:
            received_ns = int(raw.get("received_monotonic_ns") or 0)
        except (TypeError, ValueError):
            received_ns = 0
        now_ns = time.monotonic_ns()
        receipt_age_ms = (
            max(0.0, (now_ns - received_ns) / 1_000_000)
            if 0 < received_ns <= now_ns
            else None
        )
        content_age_ms = _float(raw.get("book_age_ms"))
        book_skew_ms = _float(raw.get("book_skew_ms"))
        max_content_age_ms = _float(signal.get("pair_max_book_age_ms"))
        max_book_skew_ms = _float(signal.get("pair_max_book_skew_ms"))
        diagnostics.update(
            {
                "latestRestReceiptAgeMs": receipt_age_ms,
                "latestExchangeContentAgeMs": content_age_ms,
                "latestPairBookSkewMs": book_skew_ms,
                "maximumRestReceiptAgeMs": self.max_prediction_book_age_ms,
                "maximumExchangeContentAgeMs": max_content_age_ms,
                "maximumPairBookSkewMs": max_book_skew_ms,
            }
        )
        if (
            receipt_age_ms is None
            or receipt_age_ms > self.max_prediction_book_age_ms
        ):
            return (
                None,
                "BLOCKED_STALE_PREDICTION_BOOK",
                "PAIR_REST_RECEIPT_STALE",
                (
                    "latest direct Prediction REST pair book has no valid receipt age"
                    if receipt_age_ms is None
                    else (
                        f"latest direct Prediction REST receipt age "
                        f"{receipt_age_ms:.3f}ms exceeds "
                        f"{self.max_prediction_book_age_ms:.3f}ms"
                    )
                ),
                diagnostics,
            )
        if (
            content_age_ms is None
            or max_content_age_ms is None
            or max_content_age_ms <= 0
            or content_age_ms > max_content_age_ms
        ):
            return (
                None,
                "BLOCKED_STALE_PREDICTION_BOOK",
                "PAIR_EXCHANGE_CONTENT_STALE",
                (
                    "latest direct Prediction REST pair book has no valid content age limit"
                    if content_age_ms is None or max_content_age_ms is None
                    else (
                        f"latest direct Prediction REST content age "
                        f"{content_age_ms:.3f}ms exceeds "
                        f"{max_content_age_ms:.3f}ms"
                    )
                ),
                diagnostics,
            )
        if (
            book_skew_ms is None
            or max_book_skew_ms is None
            or max_book_skew_ms < 0
            or book_skew_ms > max_book_skew_ms
        ):
            return (
                None,
                "BLOCKED_PREDICTION_ORIENTATION_UNVERIFIED",
                "PAIR_BOOK_SKEW_EXCEEDED",
                (
                    "latest direct Prediction REST pair book has no valid skew limit"
                    if book_skew_ms is None or max_book_skew_ms is None
                    else (
                        f"latest direct Prediction REST book skew "
                        f"{book_skew_ms:.3f}ms exceeds "
                        f"{max_book_skew_ms:.3f}ms"
                    )
                ),
                diagnostics,
            )
        ask_key = "up_ask" if side == "UP" else "down_ask"
        ask_size_key = "up_ask_size" if side == "UP" else "down_ask_size"
        ask = _decimal(raw.get(ask_key))
        ask_size = _decimal(raw.get(ask_size_key))
        if ask is None or not Decimal("0") < ask < Decimal("1"):
            return (
                None,
                "BLOCKED_PREDICTION_ORIENTATION_UNVERIFIED",
                "PAIR_REST_BOOK_UNAVAILABLE",
                f"latest direct Prediction REST pair book is missing valid {side} top level",
                diagnostics,
            )
        checked = dict(raw)
        checked["book_age_ms"] = receipt_age_ms
        checked["latest_ask"] = ask
        checked["latest_ask_size"] = ask_size
        diagnostics.update(
            {
                "latestLocalBookAgeMs": receipt_age_ms,
                "latestLocalAsk": float(ask),
                "latestLocalAskSize": (
                    float(ask_size) if ask_size is not None else None
                ),
            }
        )
        return checked, None, None, None, diagnostics

    def _record_prediction_book_block(
        self,
        signal: dict[str, Any],
        *,
        status: str,
        error_kind: str,
        message: str,
        diagnostics: dict[str, Any],
        enqueued_monotonic: float,
        processing_started_monotonic: float,
    ) -> None:
        finished = time.monotonic()
        event_received = self._signal_monotonic_seconds(
            signal, "market_event_received_monotonic_ns"
        )
        enriched = {
            "signalPrice": _float(signal.get("entry_price")),
            "signalBookAgeMs": _float(
                signal.get("signal_prediction_book_age_ms")
            ),
            **diagnostics,
            "eventToLocalCheckMs": self._elapsed_ms(event_received, finished),
        }
        local_id = self._record_blocked_signal(
            signal,
            status,
            message,
            error_kind=error_kind,
            diagnostics=enriched,
        )
        latency = self._order_latency_payload(
            {
                "market_id": int(signal.get("market_id") or 0),
                "selected_strategy": str(signal.get("strategy") or ""),
                "side": str(signal.get("side") or ""),
                "live_enqueued_monotonic": enqueued_monotonic,
                "processing_started_monotonic": processing_started_monotonic,
                "quote_started_monotonic": finished,
                "quote_finished_monotonic": finished,
                "quote_network_seconds": 0.0,
                "market_event_received_monotonic": event_received,
                "strategy_decision_started_monotonic": (
                    self._signal_monotonic_seconds(
                        signal, "strategy_decision_started_monotonic_ns"
                    )
                ),
                "strategy_store_started_monotonic": (
                    self._signal_monotonic_seconds(
                        signal, "strategy_store_started_monotonic_ns"
                    )
                ),
                "strategy_store_finished_monotonic": (
                    self._signal_monotonic_seconds(
                        signal, "strategy_store_finished_monotonic_ns"
                    )
                ),
                "live_candidate_created_monotonic": (
                    self._signal_monotonic_seconds(
                        signal, "live_candidate_created_monotonic_ns"
                    )
                ),
                "attempt_context": {
                    **enriched,
                    "strategy": str(signal.get("strategy") or ""),
                    "marketId": int(signal.get("market_id") or 0),
                    "side": str(signal.get("side") or ""),
                    "signalAsk": _float(signal.get("signal_prediction_ask")),
                    "signalAskSize": _float(
                        signal.get("signal_prediction_ask_size")
                    ),
                },
            },
            outcome=status,
            placement_started_monotonic=None,
            placement_finished_monotonic=finished,
        )
        with self.lock:
            check_payload = {
                **enriched,
                "status": status,
                "errorKind": error_kind,
                "checkedAt": utc_iso(),
            }
            if error_kind in {
                "LOCAL_INSUFFICIENT_DEPTH",
                "LOCAL_ESTIMATED_VWAP_TOO_HIGH",
            }:
                self.last_depth_check = check_payload
            else:
                self.last_local_price_check = check_payload
            self.last_order_latency = latency
        if local_id is not None:
            self._persist_attempt_telemetry(local_id, latency)

    def _persist_attempt_telemetry(
        self, local_id: int, latency: dict[str, Any]
    ) -> None:
        self.ledger.record_attempt_telemetry(local_id, latency)
        summary = self.ledger.attempt_summary(100)
        with self.lock:
            self.attempt_summary_state = summary

    @staticmethod
    def _maximum_reprice_limit(
        signal: dict[str, Any], strategy: str, signal_price: Decimal
    ) -> Decimal:
        """Bound one refreshed quote to 0.10 above the original signal price."""
        if strategy in LIVE_RESEARCH_STRATEGIES:
            # Research live strategies receive one narrowly bounded refresh,
            # separate from the generic +0.10 allowance used by M strategies.
            maximum = min(
                signal_price + LIVE_RESEARCH_REPRICE_GAPS[strategy],
                Decimal("0.99"),
            )
            price_range = LIVE_RESEARCH_PRICE_RANGES.get(strategy)
            return min(maximum, price_range[1]) if price_range else maximum
        maximum = min(signal_price + LIVE_MAX_QUOTE_PRICE_GAP, Decimal("0.99"))
        if strategy.startswith("PAIR_ARB_"):
            return (
                maximum
                if strategy in {"PAIR_ARB_010", "PAIR_ARB_RISK_020"}
                else signal_price
            )
        if maximum < signal_price:
            maximum = signal_price
        if strategy in {"M01", "M01T180", "M01O_F1", "M01W"}:
            ceiling = _decimal(signal.get("maximum_entry_price"))
            if ceiling is None or not Decimal("0") < ceiling < Decimal("1"):
                raise ValueError(
                    f"{strategy} live signal is missing its maximum entry price"
                )
            if signal_price > ceiling + Decimal("0.00000001"):
                raise ValueError(
                    f"{strategy} signal price exceeds its maximum entry price"
                )
        return maximum

    @staticmethod
    def _research_signal_price_is_allowed(
        signal: dict[str, Any], strategy: str
    ) -> tuple[bool, str]:
        price_range = LIVE_RESEARCH_PRICE_RANGES.get(strategy)
        if price_range is None:
            return True, ""
        price = _decimal(signal.get("entry_price"))
        minimum, maximum = price_range
        if price is None or not minimum <= price <= maximum:
            return False, (
                f"{strategy} live signal price must be between "
                f"{minimum} and {maximum}"
            )
        return True, ""

    @staticmethod
    def _research_quote_range_is_safe(
        quote: dict[str, Any], strategy: str
    ) -> tuple[bool, str]:
        price_range = LIVE_RESEARCH_PRICE_RANGES.get(strategy)
        if price_range is None:
            return True, ""
        average = _decimal(quote.get("averagePrice"))
        minimum, maximum = price_range
        if average is None or not minimum <= average <= maximum:
            return False, (
                f"quote average price must be between {minimum} and {maximum} "
                f"for {strategy}"
            )
        return True, ""

    @staticmethod
    def _reliability_gate_is_safe(
        signal: dict[str, Any], rules: dict[str, Any]
    ) -> tuple[bool, str]:
        strategy = str(signal.get("strategy") or "").upper()
        enabled = {
            str(value).upper() for value in rules.get("reliabilityGateTags", [])
        }
        applicable = [
            tag_id for tag_id in enabled
            if LIVE_RELIABILITY_TAGS[tag_id]["strategy"] == strategy
        ]
        if not applicable:
            return True, ""
        decisions = {
            str(item["id"]): str(item["decision"])
            for item in evaluate_live_reliability_tags(
                strategy=strategy,
                side=signal.get("side"),
                entry_price=signal.get("entry_price"),
                seconds_left=signal.get("seconds_left"),
                book_age_ms=signal.get("book_age_ms"),
            )
        }
        blocked = [tag_id for tag_id in applicable if decisions.get(tag_id) != "ALLOW"]
        if blocked:
            details = ", ".join(
                f"{tag_id}={decisions.get(tag_id, 'UNAVAILABLE')}" for tag_id in blocked
            )
            return False, f"候選可靠條件未放行：{details}"
        return True, ""

    @staticmethod
    def _reliability_quote_is_safe(
        quote: dict[str, Any], strategy: str, rules: dict[str, Any]
    ) -> tuple[bool, str]:
        enabled = {
            str(value).upper() for value in rules.get("reliabilityGateTags", [])
        }
        if strategy == "R_CALIBRATED_VALUE" and "RC_LOW_ENTRY" in enabled:
            average = _decimal(quote.get("averagePrice"))
            if average is None or average > Decimal("0.376875"):
                return False, "RC_LOW_ENTRY signed quote average exceeds 0.376875"
        return True, ""

    @staticmethod
    def _observer_gate_is_safe(
        signal: dict[str, Any], reference: dict[str, Any]
    ) -> tuple[bool, str]:
        """Fail closed unless the exact current-market F1 gate allows entry."""
        if signal.get("observer_gate_required") is not True:
            return False, "F1 實單訊號未標記為強制 Observer 驗證"
        gate = signal.get("market_observer_gate")
        if not isinstance(gate, dict):
            return False, "F1 實單訊號缺少 Market Observer 判斷"
        if str(gate.get("profile") or "").upper() != "F1":
            return False, "F1 實單訊號的 Observer profile 不正確"
        if gate.get("paperOnly") is not False or gate.get(
            "liveOrdersAffected"
        ) is not True:
            return False, "F1 Observer 判斷尚未經實單轉接器驗證"
        if gate.get("allowed") is not True or str(
            gate.get("status") or ""
        ).upper() != "ALLOW":
            return False, str(gate.get("reason") or "F1 Observer 未放行")
        if str(gate.get("dataQualityStatus") or "").upper() != "READY":
            return False, "F1 Observer 行情資料尚未就緒"
        try:
            gate_market_id = int(gate["currentMarketId"])
            signal_market_id = int(signal["market_id"])
            reference_market_id = int(reference["market_id"])
            sample_count = int(gate["historicalSampleCount"])
            min_samples = int(gate["minSettledSamples"])
            range_score = int(gate["currentRangeScore"])
        except (KeyError, TypeError, ValueError):
            return False, "F1 Observer 判斷缺少必要欄位"
        if not gate_market_id == signal_market_id == reference_market_id:
            return False, "F1 Observer 判斷不屬於目前市場"
        if sample_count < min_samples:
            return False, "F1 Observer 歷史樣本尚未成熟"
        if str(gate.get("historicalState") or "").upper() == "TREND":
            return False, "F1 Observer 判定歷史狀態為 TREND"
        if gate.get("currentTrendVeto") is True:
            return False, "F1 Observer 偵測到當輪接近單邊市場"
        if range_score < 1:
            return False, "F1 Observer 當輪震盪證據不足"
        return True, ""

    @staticmethod
    def _strategy_observer_gate_is_safe(
        signal: dict[str, Any],
        reference: dict[str, Any],
        version: str,
    ) -> tuple[bool, str]:
        """Apply one strategy slot's Observer version to live-safe context."""
        required = signal.get("strategy_observer_gate_required")
        gate = signal.get("strategy_observer_gate")
        if required is None and gate is None:
            # Accept the legacy Lead-specific envelope during rolling upgrades.
            required = signal.get("futures_lead_observer_gate_required")
            gate = signal.get("futures_lead_observer_gate")
        if required is not True:
            return False, "strategy signal is missing required Observer context"
        if not isinstance(gate, dict):
            return False, "strategy Observer gate is unavailable"
        if gate.get("paperOnly") is not False or gate.get(
            "liveOrdersAffected"
        ) is not True:
            return False, "strategy Observer gate is not authorized for live use"
        try:
            market_id = int(reference["market_id"])
            if int(signal["market_id"]) != market_id:
                return False, "strategy signal belongs to a different market"
        except (KeyError, TypeError, ValueError):
            return False, "strategy market metadata is incomplete"
        decision = futures_lead_observer_decision(
            version,
            gate,
            expected_market_id=market_id,
        )
        if decision["allowed"] is not True:
            return False, str(decision.get("reason") or "Observer rule blocked")
        return True, ""

    @staticmethod
    def _strategy_observer_config(
        rules: dict[str, Any], strategy: str
    ) -> tuple[bool, str]:
        try:
            index = list(rules["strategies"]).index(strategy)
            enabled = bool(rules["strategyObserverEnabled"][index])
            version = str(rules["strategyObserverVersions"][index])
        except (KeyError, IndexError, TypeError, ValueError):
            return False, "F1"
        return enabled, version

    @staticmethod
    def _strategy_drawdown_control_enabled(
        rules: dict[str, Any], strategy: str
    ) -> bool:
        try:
            index = list(rules["strategies"]).index(strategy)
            return bool(rules["strategyDrawdownControlEnabled"][index])
        except (KeyError, IndexError, TypeError, ValueError):
            return False

    @staticmethod
    def _strategy_loss_cooldown_enabled(
        rules: dict[str, Any], strategy: str
    ) -> bool:
        if strategy.startswith("PAIR_ARB_"):
            return False
        if (
            tuple(str(value) for value in rules.get("strategies", ())[:2])
            == FUTURES_LEAD_HEDGE_STRATEGIES
            and strategy in FUTURES_LEAD_HEDGE_STRATEGIES
        ):
            return False
        try:
            index = list(rules["strategies"]).index(strategy)
            return bool(rules["strategyLossCooldownEnabled"][index])
        except (KeyError, IndexError, TypeError, ValueError):
            return False

    def _loss_cooldown_is_safe(
        self, strategy: str, market_id: int
    ) -> tuple[bool, str]:
        try:
            blocked, state = self.ledger.consume_loss_cooldown(
                strategy, market_id
            )
        except Exception as exc:
            return False, f"two-loss cooldown state lookup failed: {str(exc)[:160]}"
        if not blocked:
            return True, ""
        return (
            False,
            (
                f"{strategy} skipped market {market_id} after two consecutive "
                "official losses; only this strategy is cooling down for one market"
                + (
                    f" (last settlement market {state['lastSettlementMarketId']})"
                    if state.get("lastSettlementMarketId") is not None
                    else ""
                )
            ),
        )

    def _drawdown_control_is_safe(
        self,
        signal: dict[str, Any],
        reference: dict[str, Any],
    ) -> tuple[bool, str]:
        """Evaluate the causal completed-market guard immediately before quote.

        The strategy's frozen signal Spot value remains provenance only.  The
        price used for this order-time safety recheck is selected independently:
        a fresh Spot trade first, then a fresh Spot book microprice (or midpoint
        when sizes are unavailable).  If both live references are stale, fail
        closed without widening the trade threshold.
        """
        if self.drawdown_market_history is None:
            return False, "drawdown-control completed-market history is unavailable"
        try:
            market_id = int(reference["market_id"])
            if int(signal["market_id"]) != market_id:
                return False, "drawdown-control signal belongs to a different market"
            side = str(signal["side"]).strip().upper()
            start_price = float(signal["drawdown_control_start_price"])
            signal_spot_price = float(signal["drawdown_control_spot_price"])
            signal_spot_age_ms = float(signal["drawdown_control_spot_age_ms"])
            signal_received_monotonic_ns = int(
                signal["market_event_received_monotonic_ns"]
            )
        except (KeyError, TypeError, ValueError):
            return False, "drawdown-control signal snapshot is incomplete"
        if side not in {"UP", "DOWN"}:
            return False, "drawdown-control signal side is invalid"
        if (
            not math.isfinite(signal_spot_price)
            or signal_spot_price <= 0
            or not math.isfinite(signal_spot_age_ms)
            or signal_spot_age_ms < 0
        ):
            return False, "drawdown-control signal Spot snapshot is invalid"
        now_monotonic_ns = time.monotonic_ns()
        if (
            signal_received_monotonic_ns <= 0
            or signal_received_monotonic_ns > now_monotonic_ns
        ):
            return False, "drawdown-control signal receipt time is invalid"

        raw_signal_source = str(
            signal.get("drawdown_signal_spot_source")
            or signal.get("drawdown_control_spot_source")
            or signal.get("signal_spot_price_source")
            or "SPOT_TRADE"
        ).strip()
        signal_source = {
            "trade": "SPOT_TRADE",
            "bookTicker_midpoint": "SPOT_BOOK_MIDPOINT",
        }.get(raw_signal_source, raw_signal_source.upper())
        signal["drawdown_signal_spot_price"] = signal_spot_price
        signal["drawdown_signal_spot_age_ms"] = signal_spot_age_ms
        signal["drawdown_signal_spot_source"] = signal_source

        spot_price: float
        spot_age_ms: float
        reference_source: str
        trade_age_ms: float | None = None
        book_age_ms: float | None = None
        callback = self.current_spot_reference
        if callback is None:
            # Compatibility for older callers and archived tests.  Production
            # wiring always supplies the independent current-reference callback.
            spot_price = signal_spot_price
            spot_age_ms = signal_spot_age_ms + (
                now_monotonic_ns - signal_received_monotonic_ns
            ) / 1_000_000
            reference_source = signal_source
            if spot_age_ms > LIVE_MAX_DRAWDOWN_SPOT_AGE_MS:
                return False, (
                    f"drawdown-control Spot age {spot_age_ms:.3f}ms exceeds "
                    f"{LIVE_MAX_DRAWDOWN_SPOT_AGE_MS:.3f}ms"
                )
        else:
            try:
                current_spot = callback()
            except Exception as exc:
                return False, (
                    "drawdown-control current Spot reference lookup failed: "
                    f"{str(exc)[:160]}"
                )
            if not isinstance(current_spot, dict):
                return False, "drawdown-control current Spot reference is unavailable"
            trade_price = _float(current_spot.get("trade_price"))
            trade_age_ms = _float(current_spot.get("trade_age_ms"))
            book_microprice = _float(current_spot.get("book_microprice"))
            book_midpoint = _float(current_spot.get("book_midpoint"))
            book_age_ms = _float(current_spot.get("book_age_ms"))
            trade_is_fresh = bool(
                trade_price is not None
                and trade_price > 0
                and trade_age_ms is not None
                and 0 <= trade_age_ms <= LIVE_MAX_DRAWDOWN_SPOT_AGE_MS
            )
            book_is_fresh = bool(
                book_age_ms is not None
                and 0 <= book_age_ms <= LIVE_MAX_DRAWDOWN_SPOT_BOOK_AGE_MS
            )
            if trade_is_fresh:
                assert trade_price is not None and trade_age_ms is not None
                spot_price = trade_price
                spot_age_ms = trade_age_ms
                reference_source = "SPOT_TRADE"
            elif book_is_fresh and book_microprice is not None and book_microprice > 0:
                spot_price = book_microprice
                spot_age_ms = book_age_ms
                reference_source = "SPOT_BOOK_MICROPRICE"
            elif book_is_fresh and book_midpoint is not None and book_midpoint > 0:
                spot_price = book_midpoint
                spot_age_ms = book_age_ms
                reference_source = "SPOT_BOOK_MIDPOINT"
            else:
                trade_text = (
                    f"{trade_age_ms:.3f}ms"
                    if trade_age_ms is not None and trade_age_ms >= 0
                    else "unavailable"
                )
                book_text = (
                    f"{book_age_ms:.3f}ms"
                    if book_age_ms is not None and book_age_ms >= 0
                    else "unavailable"
                )
                signal["drawdown_recheck_spot_price"] = None
                signal["drawdown_recheck_spot_age_ms"] = None
                signal["drawdown_recheck_spot_source"] = "UNAVAILABLE"
                signal["drawdown_recheck_trade_age_ms"] = trade_age_ms
                signal["drawdown_recheck_book_age_ms"] = book_age_ms
                with self.lock:
                    self.last_drawdown_reference = {
                        "source": "UNAVAILABLE",
                        "ageMs": None,
                        "price": None,
                        "tradeAgeMs": trade_age_ms,
                        "bookAgeMs": book_age_ms,
                        "checkedAt": utc_iso(),
                        "marketId": market_id,
                    }
                return False, (
                    "drawdown-control Spot references are unavailable or stale "
                    f"(trade {trade_text}, max "
                    f"{LIVE_MAX_DRAWDOWN_SPOT_AGE_MS:.3f}ms; book {book_text}, "
                    f"max {LIVE_MAX_DRAWDOWN_SPOT_BOOK_AGE_MS:.3f}ms)"
                )

        signal["drawdown_recheck_spot_price"] = spot_price
        signal["drawdown_recheck_spot_age_ms"] = spot_age_ms
        signal["drawdown_recheck_spot_source"] = reference_source
        signal["drawdown_recheck_trade_age_ms"] = trade_age_ms
        signal["drawdown_recheck_book_age_ms"] = book_age_ms
        with self.lock:
            self.last_drawdown_reference = {
                "source": reference_source,
                "ageMs": spot_age_ms,
                "price": spot_price,
                "tradeAgeMs": trade_age_ms,
                "bookAgeMs": book_age_ms,
                "checkedAt": utc_iso(),
                "marketId": market_id,
            }
        reference_start = _float(reference.get("start_price"))
        if (
            reference_start is None
            or not math.isclose(
                start_price,
                reference_start,
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
        ):
            return False, "drawdown-control start price does not match current market"

        controller = MarketRegimeDrawdownController()
        try:
            rows = self.drawdown_market_history(
                market_id, controller.lookback_markets
            )
        except Exception as exc:
            return False, f"drawdown-control history lookup failed: {str(exc)[:160]}"
        if not isinstance(rows, list):
            return False, "drawdown-control history lookup returned invalid data"
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                controller.record_completed_market(
                    int(row["market_id"]),
                    start_price=float(row["start_price"]),
                    end_price=float(row["end_price"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
        try:
            decision = controller.evaluate(
                side=side,
                start_price=start_price,
                spot_price=spot_price,
            )
        except (TypeError, ValueError) as exc:
            return False, f"drawdown-control signal snapshot is invalid: {exc}"
        return decision.allowed, decision.reason

    @staticmethod
    def _spot_data_is_safe_for_live(
        signal: dict[str, Any],
    ) -> tuple[bool, str]:
        """Require a fresh effective Spot price immediately before quoting."""
        age_value = signal.get("signal_spot_age_ms")
        if age_value is None:
            # Older candidates used the drawdown snapshot field.  Keep the
            # compatibility path explicit, but still fail closed when neither
            # field exists.
            age_value = signal.get("drawdown_control_spot_age_ms")
        try:
            age_ms = float(age_value)
            received_ns = int(signal["market_event_received_monotonic_ns"])
        except (KeyError, TypeError, ValueError):
            return False, "live Spot freshness metadata is incomplete"
        now_ns = time.monotonic_ns()
        if received_ns <= 0 or received_ns > now_ns:
            return False, "live Spot freshness receipt time is invalid"
        age_ms += (now_ns - received_ns) / 1_000_000
        if not math.isfinite(age_ms) or age_ms < 0:
            return False, "live Spot freshness age is invalid"
        if age_ms > LIVE_MAX_SPOT_DATA_AGE_MS:
            source = str(signal.get("signal_spot_price_source") or "spot")
            return False, (
                f"live Spot data age {age_ms:.3f}ms exceeds "
                f"{LIVE_MAX_SPOT_DATA_AGE_MS:.0f}ms ({source})"
            )
        return True, ""

    @staticmethod
    def _f1_entry_time_is_safe(
        signal: dict[str, Any],
        reference: dict[str, Any],
        client: BinancePredictionTradingClient,
    ) -> tuple[bool, str]:
        signal_seconds = _decimal(signal.get("seconds_left"))
        if signal_seconds is None:
            return False, "F1 live signal is missing seconds_left"
        try:
            market_end_ms = int(reference["end_ms"])
            server_now_ms = int(client.server_timestamp_ms())
        except (KeyError, TypeError, ValueError) as exc:
            return False, f"F1 live cutoff time is unavailable: {exc}"
        current_seconds = (
            Decimal(market_end_ms - server_now_ms) / Decimal(1000)
        )
        if (
            signal_seconds <= M01O_F1_MIN_SECONDS_LEFT
            or current_seconds <= M01O_F1_MIN_SECONDS_LEFT
        ):
            return False, (
                "F1 does not enter during the final 30 seconds "
                f"(signal={signal_seconds:.3f}, current={current_seconds:.3f})"
            )
        return True, ""

    @staticmethod
    def _strategy_gate_is_safe(
        signal: dict[str, Any], reference: dict[str, Any]
    ) -> tuple[bool, str]:
        """Independently fail closed unless the exact prior M0 officially won."""
        gate = signal.get("m0w_gate")
        if not isinstance(gate, dict):
            return False, "win-gated live signal is missing the adjacent-market gate"
        if str(gate.get("version") or "") != LIVE_M0W_GATE_VERSION:
            return False, "live signal gate version is unsupported"
        if gate.get("previous_market_is_adjacent") is not True:
            return False, "M0W predecessor was not verified as the adjacent round"
        if str(gate.get("previous_settlement_status") or "").upper() != "OFFICIAL":
            return False, "the adjacent M0 round is not officially settled"
        if str(gate.get("previous_m0_status") or "").upper() != "SETTLED_WIN":
            return False, "the adjacent M0 round did not win"
        previous_pnl = _float(gate.get("previous_m0_pnl"))
        if previous_pnl is None or not math.isfinite(previous_pnl) or previous_pnl <= 0:
            return False, "the adjacent M0 round has no positive settled PnL"
        try:
            current_market_id = int(signal["market_id"])
            previous_market_id = int(gate["previous_market_id"])
            reference_start_ms = int(reference["start_ms"])
            gate_start_ms = int(gate["current_market_start_ms"])
            previous_start_ms = int(gate["previous_market_start_ms"])
            previous_end_ms = int(gate["previous_market_end_ms"])
        except (KeyError, TypeError, ValueError):
            return False, "live signal gate has incomplete market-boundary metadata"
        if previous_market_id <= 0 or previous_market_id == current_market_id:
            return False, "live signal gate has an invalid predecessor market"
        if abs(gate_start_ms - reference_start_ms) > LIVE_MARKET_ADJACENCY_TOLERANCE_MS:
            return False, "live signal gate does not match the current market boundary"
        if abs(previous_end_ms - gate_start_ms) > LIVE_MARKET_ADJACENCY_TOLERANCE_MS:
            return False, "M0W predecessor does not end at the current market boundary"
        if (
            abs(
                previous_start_ms
                + LIVE_MARKET_DURATION_MS
                - gate_start_ms
            )
            > LIVE_MARKET_ADJACENCY_TOLERANCE_MS
        ):
            return False, "M0W predecessor is not exactly one five-minute round earlier"
        return True, ""

    @staticmethod
    def _quote_is_safe(
        quote: dict[str, Any], *, token_id: str, price_limit: Decimal | None,
        amount_in_wei: int, side: str = "BUY", order_type: str = "LIMIT",
    ) -> tuple[bool, str]:
        normalized_side = str(side).upper()
        normalized_order_type = str(order_type).upper()
        if not quote.get("quoteId"):
            return False, "quote response did not contain quoteId"
        if (
            str(quote.get("orderType") or normalized_order_type).upper()
            != normalized_order_type
        ):
            return False, "quote changed the requested order type"
        if str(quote.get("side") or normalized_side).upper() != normalized_side:
            return False, "quote changed the requested order side"
        if quote.get("tokenId") is not None and str(quote["tokenId"]) != token_id:
            return False, "quote token does not match the M0W side"
        amount_in = _decimal(quote.get("amountIn"))
        if amount_in is None or amount_in <= 0:
            return False, "quote amountIn is invalid"
        if amount_in > Decimal(amount_in_wei):
            return False, "quote exceeds the configured per-order hard cap"
        amount_out = _decimal(quote.get("amountOut"))
        if amount_out is None or amount_out <= 0:
            return False, "quote amountOut is invalid"
        average_price = _decimal(quote.get("averagePrice"))
        if average_price is not None and price_limit is not None:
            tolerance = Decimal("0.00000001")
            unsafe_price = (
                average_price > price_limit + tolerance
                if normalized_side == "BUY"
                else average_price < price_limit - tolerance
            )
            if unsafe_price:
                relation = "exceeds" if normalized_side == "BUY" else "is below"
                return False, (
                    f"quote average price {format(average_price.normalize(), 'f')} "
                    f"{relation} the submitted limit price "
                    f"{format(price_limit.normalize(), 'f')}"
                )
        return True, ""

    def manual_sell(
        self, order_local_id: int, order_type: str
    ) -> dict[str, Any]:
        """Submit one operator-confirmed exit for an active live-order fill."""
        normalized_type = str(order_type).strip().upper()
        if normalized_type not in {"LIMIT", "MARKET"}:
            raise ValueError("orderType must be LIMIT or MARKET")
        if not self.manual_exit_lock.acquire(blocking=False):
            raise ValueError("another manual sell request is already being processed")
        try:
            with self.lock:
                client = self.client
                wallet_address = self.wallet_address
                wallet_id = self.wallet_id
            if client is None or not wallet_address or not wallet_id:
                raise ValueError("live trading wallet is not ready for a manual sell")

            order = self.ledger.order_for_manual_exit(int(order_local_id))
            if order is None:
                raise ValueError("active live position was not found")
            strategy = str(order.get("strategy") or "UNKNOWN")
            if str(order.get("status") or "").upper() != "FILLED":
                raise ValueError(
                    "manual sell requires a fully filled entry; reconcile or cancel the entry first"
                )

            reference = self.current_market() or {}
            try:
                market_id = int(order["market_id"])
                if int(reference["market_id"]) != market_id:
                    raise ValueError("the selected position is not in the current market")
                if client.server_timestamp_ms() >= int(reference["end_ms"]):
                    raise ValueError("the selected market has already ended")
                fee_bps = int(reference.get("fee_bps") or 200)
            except KeyError as exc:
                raise ValueError("current market metadata is incomplete") from exc

            token_id = str(order.get("token_id") or "")
            ledger_shares = _decimal(order.get("filled_share_qty"))
            if not token_id or ledger_shares is None or ledger_shares <= 0:
                raise ValueError("the selected entry has no sellable filled shares")
            position_payload = client.position_by_token(wallet_address, token_id)
            wallet_shares = self._position_shares(
                self._position_record(position_payload)
            )
            if wallet_shares is None or wallet_shares <= 0:
                raise ValueError("Binance reports no available shares for this position")
            if wallet_shares + Decimal("0.02") < ledger_shares:
                raise ValueError(
                    "wallet shares are below the strategy fill; refusing to sell an uncertain allocation"
                )
            sell_shares = min(wallet_shares, ledger_shares)
            amount_in_wei = int(
                (sell_shares * Decimal(10**18)).to_integral_value(
                    rounding=ROUND_DOWN
                )
            )
            if amount_in_wei <= 0:
                raise ValueError("the selected position is too small to sell")

            best_bid = self._best_bid(client.orderbook(market_id, token_id))
            if best_bid is None or best_bid <= 0:
                raise ValueError("the current outcome order book has no executable bid")
            price_limit = best_bid if normalized_type == "LIMIT" else None
            price_limit_text = (
                format(best_bid.normalize(), "f")
                if normalized_type == "LIMIT" else None
            )
            exit_row = self.ledger.begin_manual_exit(
                order=order,
                order_type=normalized_type,
                sell_shares=sell_shares,
                price_limit=price_limit,
            )
            exit_id = int(exit_row["id"])

            try:
                quote = client.get_quote(
                    wallet_address=wallet_address,
                    token_id=token_id,
                    amount_in_wei=str(amount_in_wei),
                    price_limit=price_limit_text,
                    slippage_bps=100,
                    fee_rate_bps=fee_bps,
                    funding_source="MPC",
                    side="SELL",
                    order_type=normalized_type,
                )
                safe, reason = self._quote_is_safe(
                    quote,
                    token_id=token_id,
                    price_limit=price_limit,
                    amount_in_wei=amount_in_wei,
                    side="SELL",
                    order_type=normalized_type,
                )
                if not safe:
                    raise ValueError(reason)
                quoted_input = _decimal(quote.get("amountIn"))
                if quoted_input is None or abs(
                    quoted_input - Decimal(amount_in_wei)
                ) >= Decimal(1):
                    raise ValueError(
                        "manual sell quote does not cover the full strategy position"
                    )
                expiry = int(quote.get("expireAt") or 0)
                if expiry and expiry <= client.server_timestamp_ms() + 250:
                    raise ValueError("manual sell quote expired before placement")
                self.ledger.update_manual_exit(
                    exit_id,
                    status="QUOTE_ACCEPTED",
                    quote_average_price=_float(quote.get("averagePrice")),
                    quote_amount_in_wei=str(quote.get("amountIn")),
                    quote_amount_out_wei=str(quote.get("amountOut")),
                    quote_expires_at=expiry or None,
                )
            except Exception as exc:
                detail = str(exc)[:500]
                self.ledger.update_manual_exit(
                    exit_id,
                    status="REJECTED",
                    error_kind="MANUAL_SELL_QUOTE_REJECTED",
                    error_message=detail,
                )
                self.ledger.record_event(
                    "ERROR", "MANUAL_SELL_QUOTE_REJECTED", detail, market_id
                )
                raise ValueError(detail) from exc

            self.ledger.update_manual_exit(
                exit_id, status="PLACE_ATTEMPTED", attempted_at=utc_iso()
            )
            try:
                if normalized_type == "LIMIT":
                    placed = client.place_limit_order(
                        wallet_address=wallet_address,
                        wallet_id=wallet_id,
                        quote_id=str(quote["quoteId"]),
                        price_limit=str(price_limit_text),
                        slippage_bps=100,
                        account_type=self.account_type,
                        funding_source="MPC",
                    )
                else:
                    placed = client.place_market_order(
                        wallet_address=wallet_address,
                        wallet_id=wallet_id,
                        quote_id=str(quote["quoteId"]),
                        slippage_bps=100,
                        account_type=self.account_type,
                        funding_source="MPC",
                    )
                exchange_order_id = str(placed.get("orderId") or "")
                if not exchange_order_id:
                    raise ApiTransportError(
                        "manual sell response did not contain orderId"
                    )
            except Exception as exc:
                ambiguous = isinstance(exc, ApiTransportError) or (
                    isinstance(exc, ApiHttpError) and exc.status_code >= 500
                )
                detail = str(exc)[:500]
                self.ledger.update_manual_exit(
                    exit_id,
                    status="AMBIGUOUS" if ambiguous else "REJECTED",
                    error_kind=(
                        "MANUAL_SELL_PLACEMENT_AMBIGUOUS"
                        if ambiguous else "MANUAL_SELL_PLACEMENT_REJECTED"
                    ),
                    error_message=detail,
                )
                self.ledger.record_event(
                    "ERROR",
                    (
                        "MANUAL_SELL_PLACEMENT_AMBIGUOUS"
                        if ambiguous else "MANUAL_SELL_PLACEMENT_REJECTED"
                    ),
                    detail,
                    market_id,
                )
                raise ValueError(detail) from exc

            self.ledger.update_manual_exit(
                exit_id,
                status="SUBMITTED",
                submitted_at=utc_iso(),
                order_id=exchange_order_id,
                response_json=_safe_payload(placed),
                error_kind=None,
                error_message=None,
            )
            with self.lock:
                self.next_order_sync = 0.0
            self.ledger.record_event(
                "WARN",
                "MANUAL_SELL_SUBMITTED",
                (
                    f"{strategy} market {market_id}: operator submitted "
                    f"SELL {normalized_type} {sell_shares} {order['side']} shares"
                    + (
                        f" at LIMIT {price_limit_text}"
                        if normalized_type == "LIMIT" else " with FOK"
                    )
                ),
                market_id,
            )
            return self.state()
        finally:
            self.manual_exit_lock.release()

    def _record_blocked_signal(
        self,
        signal: dict[str, Any],
        status: str,
        message: str,
        *,
        error_kind: str = "LOCAL_BLOCK",
        diagnostics: dict[str, Any] | None = None,
    ) -> int | None:
        if diagnostics is None and status == "BLOCKED_DRAWDOWN_CONTROL":
            diagnostics = {
                "drawdownSignalSpotPrice": _float(
                    signal.get("drawdown_signal_spot_price")
                ),
                "drawdownSignalSpotAgeMs": _float(
                    signal.get("drawdown_signal_spot_age_ms")
                ),
                "drawdownSignalSpotSource": signal.get(
                    "drawdown_signal_spot_source"
                ),
                "drawdownReferencePrice": _float(
                    signal.get("drawdown_recheck_spot_price")
                ),
                "drawdownReferenceAgeMs": _float(
                    signal.get("drawdown_recheck_spot_age_ms")
                ),
                "drawdownReferenceSource": signal.get(
                    "drawdown_recheck_spot_source"
                ),
                "drawdownTradeAgeMs": _float(
                    signal.get("drawdown_recheck_trade_age_ms")
                ),
                "drawdownBookAgeMs": _float(
                    signal.get("drawdown_recheck_book_age_ms")
                ),
            }
        market_id = int(signal.get("market_id") or 0)
        reference = self.current_market() or {}
        side = str(signal.get("side") or "UNKNOWN")
        token_id = str(reference.get(f"{side.lower()}_token_id") or "unknown")
        try:
            signal_price = float(signal.get("entry_price"))
        except (TypeError, ValueError):
            signal_price = 0.0
        if not math.isfinite(signal_price):
            signal_price = 0.0
        with self.lock:
            rules = dict(self.live_rules)
        strategy = str(signal.get("strategy") or rules["strategy"])
        try:
            strategy_index = list(rules["strategies"]).index(strategy)
            configured_stake = rules["strategyStakesUsdt"][strategy_index]
        except (KeyError, IndexError, TypeError, ValueError):
            configured_stake = rules["maxStakeUsdt"]
        stake = Decimal(str(configured_stake))
        dynamic_leg_stake = _decimal(
            signal.get("_pair_dynamic_leg_stake_usdt")
        )
        if dynamic_leg_stake is not None and dynamic_leg_stake > 0:
            stake = dynamic_leg_stake
        local_id = self.ledger.record_signal(
            topic_id=int(signal.get("topic_id") or reference.get("topic_id") or 0),
            market_id=market_id,
            side=side,
            token_id=token_id,
            signal_price=signal_price,
            account_type=self.account_type,
            signal_at=str(signal.get("signal_timestamp") or utc_iso()),
            strategy=strategy,
            max_stake_usdt=float(stake),
            requested_amount_wei=str(_stake_amount_wei(stake)),
        )
        if local_id is not None:
            self.ledger.update_order(
                local_id,
                status=status,
                error_kind=error_kind,
                error_message=message,
                **(
                    {"response_json": _safe_payload(diagnostics)}
                    if isinstance(diagnostics, dict)
                    else {}
                ),
            )
            self.ledger.record_event("WARN", status, message, market_id)
        return local_id

    def _evaluate_hourly_guard(
        self,
        *,
        at: str | datetime | None = None,
        performance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if performance is None and self.m0_hourly_performance is None:
            with self.lock:
                return dict(self.hourly_guard_state)
        try:
            with self.lock:
                rules = dict(self.live_rules)
            snapshot = (
                performance
                if performance is not None
                else self.m0_hourly_performance()
            )
            if not isinstance(snapshot, dict):
                raise ValueError("M0 每小時統計快照格式無效")
            result = evaluate_m0_hourly_guard(
                snapshot,
                at,
                min_win_rate_pct=float(rules["minHourlyWinRatePct"]),
                max_win_then_loss_rate_pct=float(
                    rules["maxHourlyWinThenLossRatePct"]
                ),
            )
            cached_snapshot: dict[str, Any] | None = dict(snapshot)
        except Exception as exc:
            # This guard controls real money, so an unavailable/corrupt
            # statistics snapshot must not silently fall through to an order.
            result = {
                "status": "ERROR",
                "blocked": True,
                "timezone": "Asia/Taipei",
                "utcOffset": "+08:00",
                "minWinRatePct": float(
                    rules.get(
                        "minHourlyWinRatePct", M0_HOURLY_MIN_WIN_RATE_PCT
                    )
                ),
                "maxWinThenLossRatePct": float(
                    rules.get(
                        "maxHourlyWinThenLossRatePct",
                        M0_HOURLY_MAX_WIN_THEN_LOSS_RATE_PCT,
                    )
                ),
                "reasons": [f"M0 每小時統計無法讀取：{str(exc)[:240]}"],
            }
            cached_snapshot = None
        with self.lock:
            self.hourly_guard_state = dict(result)
            self.hourly_performance_snapshot = cached_snapshot
        return result

    def _evaluate_cached_hourly_guard(
        self, *, at: str | datetime | None = None
    ) -> dict[str, Any]:
        """Evaluate the latest safe snapshot without a database read in the hot path."""
        if self.m0_hourly_performance is None:
            with self.lock:
                return dict(self.hourly_guard_state)
        with self.lock:
            snapshot = (
                dict(self.hourly_performance_snapshot)
                if self.hourly_performance_snapshot is not None
                else None
            )
            rules = dict(self.live_rules)
            prior = dict(self.hourly_guard_state)
        if snapshot is None:
            return {
                **prior,
                "status": "ERROR",
                "blocked": True,
                "reasons": prior.get("reasons")
                or ["M0 每小時統計快照尚未就緒"],
            }
        result = evaluate_m0_hourly_guard(
            snapshot,
            at,
            min_win_rate_pct=float(rules["minHourlyWinRatePct"]),
            max_win_then_loss_rate_pct=float(
                rules["maxHourlyWinThenLossRatePct"]
            ),
        )
        with self.lock:
            self.hourly_guard_state = dict(result)
        return result

    def process_signal(self, signal: dict[str, Any]) -> None:
        strategy = str(signal.get("strategy") or "").upper()
        with self.lock:
            selected = tuple(
                str(value) for value in self.live_rules["strategies"]
            )
        if (
            selected[:2] == FUTURES_LEAD_HEDGE_STRATEGIES
            and strategy in FUTURES_LEAD_HEDGE_STRATEGIES
        ):
            self._process_futures_lead_hedge_signal(signal)
            return
        if strategy.startswith("PAIR_ARB_") and signal.get("pair_arb_leg") is True:
            self._process_pair_signal(signal)
            return
        self._process_single_signal(signal)

    def _process_futures_lead_hedge_signal(
        self, signal: dict[str, Any]
    ) -> None:
        """Quote both dependent Lead legs before allowing either placement."""
        strategy = str(signal.get("strategy") or "").upper()
        market_id = int(signal.get("market_id") or 0)
        with self.lock:
            # A missing dependent signal must fail closed without retaining
            # stale primary signals into a later market.
            for stale_market_id in tuple(self.pending_futures_lead_hedges):
                if stale_market_id != market_id:
                    self.pending_futures_lead_hedges.pop(stale_market_id, None)
            legs = self.pending_futures_lead_hedges.setdefault(market_id, {})
            legs.setdefault(strategy, dict(signal))
            if set(legs) != set(FUTURES_LEAD_HEDGE_STRATEGIES):
                return
            pair = self.pending_futures_lead_hedges.pop(market_id)

        lead = pair["R_FUTURES_LEAD"]
        reverse = pair["R_FUTURES_LEAD_REVERSE"]
        lead_side = str(lead.get("side") or "").upper()
        reverse_side = str(reverse.get("side") or "").upper()
        dependency_is_valid = (
            {lead_side, reverse_side} == {"UP", "DOWN"}
            and reverse_side != lead_side
            and str(reverse.get("source_strategy") or "").upper()
            == "R_FUTURES_LEAD"
            and str(reverse.get("source_side") or "").upper() == lead_side
        )
        if not dependency_is_valid:
            self.ledger.record_event(
                "ERROR",
                "FUTURES_LEAD_HEDGE_INVALID",
                "Lead/Reverse dependency or opposite-side validation failed; no order was placed",
                market_id,
            )
            return

        with self.lock:
            observer_rules = dict(self.live_rules)
        drawdown_enabled = {
            leg_strategy: self._strategy_drawdown_control_enabled(
                observer_rules, leg_strategy
            )
            for leg_strategy in FUTURES_LEAD_HEDGE_STRATEGIES
        }
        if any(drawdown_enabled.values()):
            reference = self.current_market() or {}
            decisions = [
                self._drawdown_control_is_safe(pair[leg_strategy], reference)
                for leg_strategy in FUTURES_LEAD_HEDGE_STRATEGIES
                if drawdown_enabled[leg_strategy]
            ]
            blocked_reason = next(
                (reason for allowed, reason in decisions if not allowed), ""
            )
            if blocked_reason:
                for leg_strategy in FUTURES_LEAD_HEDGE_STRATEGIES:
                    self._record_blocked_signal(
                        pair[leg_strategy],
                        "BLOCKED_DRAWDOWN_CONTROL",
                        blocked_reason,
                    )
                return
            for leg_strategy in FUTURES_LEAD_HEDGE_STRATEGIES:
                if drawdown_enabled[leg_strategy]:
                    pair[leg_strategy]["_drawdown_control_validated"] = True
        observer_configs = {
            leg_strategy: self._strategy_observer_config(
                observer_rules, leg_strategy
            )
            for leg_strategy in FUTURES_LEAD_HEDGE_STRATEGIES
        }
        if any(enabled for enabled, _ in observer_configs.values()):
            reference = self.current_market() or {}
            decisions = [
                self._strategy_observer_gate_is_safe(
                    pair[leg_strategy],
                    reference,
                    observer_configs[leg_strategy][1],
                )
                for leg_strategy in FUTURES_LEAD_HEDGE_STRATEGIES
                if observer_configs[leg_strategy][0]
            ]
            blocked_reason = next(
                (reason for allowed, reason in decisions if not allowed),
                "",
            )
            if blocked_reason:
                for leg_strategy in FUTURES_LEAD_HEDGE_STRATEGIES:
                    self._record_blocked_signal(
                        pair[leg_strategy],
                        "BLOCKED_FUTURES_LEAD_OBSERVER",
                        blocked_reason,
                    )
                return

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                leg_strategy: executor.submit(
                    self._process_single_signal,
                    pair[leg_strategy],
                    defer_placement=True,
                )
                for leg_strategy in FUTURES_LEAD_HEDGE_STRATEGIES
            }
            prepared = {
                leg_strategy: future.result()
                for leg_strategy, future in futures.items()
            }

        accepted = [item for item in prepared.values() if item is not None]
        if len(accepted) != 2:
            for item in accepted:
                self.ledger.update_order(
                    int(item["local_id"]),
                    status="REJECTED",
                    error_kind="FUTURES_LEAD_HEDGE_QUOTE_ABORTED",
                    error_message="另一腿 signed quote 未通過；兩腿皆不送單",
                )
            self.ledger.record_event(
                "ERROR",
                "FUTURES_LEAD_HEDGE_QUOTE_ABORTED",
                "Lead/Reverse 任一腿 signed quote 未通過；兩腿皆未送單",
                market_id,
            )
            return

        with self.lock:
            placement_allowed = self.runtime_enabled and self.armed
        if not placement_allowed:
            for item in accepted:
                self.ledger.update_order(
                    int(item["local_id"]),
                    status="ABORTED_RUNTIME_PAUSED",
                )
            self.ledger.record_event(
                "WARN",
                "FUTURES_LEAD_HEDGE_ABORTED_RUNTIME_PAUSED",
                "runtime was paused before Lead/Reverse placement; no order was placed",
                market_id,
            )
            return

        with ThreadPoolExecutor(max_workers=2) as executor:
            placement_results = list(
                executor.map(
                    lambda item: (
                        str(item["selected_strategy"]),
                        self._place_accepted_quote(item),
                    ),
                    accepted,
                )
            )
        if all(result for _, result in placement_results):
            self.ledger.record_event(
                "INFO",
                "FUTURES_LEAD_HEDGE_SUBMITTED",
                "Lead 2 USDT / Reverse 1 USDT dependent hedge pair submitted",
                market_id,
            )
            return

        accepted_strategies = [
            leg_strategy
            for leg_strategy, result in placement_results
            if result
        ]
        missing_strategies = [
            leg_strategy
            for leg_strategy, result in placement_results
            if not result
        ]
        self.set_runtime_enabled(False)
        with self.lock:
            self.status = "PAUSED_FUTURES_LEAD_HEDGE_INCOMPLETE"
            self.armed = False
            self.last_error = (
                "Lead/Reverse 送單未能確認兩腿皆成功；實單已自動暫停，"
                "請人工核對持倉"
            )
        self.ledger.record_event(
            "ERROR",
            "FUTURES_LEAD_HEDGE_PLACEMENT_INCOMPLETE",
            f"Lead/Reverse placement incomplete; accepted={accepted_strategies}, "
            f"missing={missing_strategies}; runtime paused",
            market_id,
        )

    def _requote_qc_pair(
        self,
        accepted: list[dict[str, Any]],
        *,
        minimum_capacity: Decimal = PAIR_ARB_QC_MIN_QUOTE_CAPACITY_RATIO,
    ) -> tuple[list[dict[str, Any]] | None, str]:
        """Requote both legs for the same conservative gross-share target."""
        try:
            initial_outputs = [
                Decimal(int(item["quote"]["amountOut"])) for item in accepted
            ]
            initial_inputs = [
                Decimal(int(item["quote"]["amountIn"])) for item in accepted
            ]
            expected_outputs = [
                Decimal(int(item["expected_amount_out_wei"])) for item in accepted
            ]
            requested_inputs = [
                Decimal(int(item["requested_amount_wei"])) for item in accepted
            ]
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return None, "initial pair quote amounts are invalid"

        initial_capacity = min(
            *(
                quoted / requested
                for quoted, requested in zip(initial_inputs, requested_inputs)
                if requested > 0
            ),
            *(
                quoted / expected
                for quoted, expected in zip(initial_outputs, expected_outputs)
                if expected > 0
            ),
        )
        if initial_capacity < minimum_capacity:
            return None, (
                f"initial signed quote capacity {initial_capacity:.1%} is below "
                f"{minimum_capacity:.0%}"
            )

        target_output = min(initial_outputs)
        if target_output <= 0:
            return None, "pair quote target output is invalid"

        def requote(item: dict[str, Any]) -> dict[str, Any]:
            quote = item["quote"]
            current_input = Decimal(int(quote["amountIn"]))
            current_output = Decimal(int(quote["amountOut"]))
            revised_input = int(
                (current_input * target_output / current_output).to_integral_value(
                    rounding=ROUND_DOWN
                )
            )
            if revised_input < _stake_amount_wei(
                LIVE_MIN_CONFIGURABLE_STAKE_USDT
            ):
                raise ValueError("equal-share requote falls below the minimum stake")
            self.ledger.update_order(
                int(item["local_id"]), status="PAIR_REQUOTE_REQUESTING"
            )
            started = time.monotonic()
            refreshed = item["client"].get_quote(
                wallet_address=item["wallet_address"],
                token_id=item["token_id"],
                amount_in_wei=str(revised_input),
                price_limit=item["price_limit_text"],
                slippage_bps=100,
                fee_rate_bps=int(item["fee_bps"]),
                funding_source="MPC",
            )
            rtt = max(0.0, time.monotonic() - started)
            safe, reason = self._quote_is_safe(
                refreshed,
                token_id=str(item["token_id"]),
                price_limit=Decimal(str(item["price_limit_text"])),
                amount_in_wei=revised_input,
            )
            if not safe:
                raise ValueError(reason)
            result = dict(item)
            result["quote"] = refreshed
            result["requested_amount_wei"] = revised_input
            result["expected_amount_out_wei"] = int(target_output)
            # The final gate is about freshness of the signed quote that will
            # actually be placed.  The initial discovery quote is intentionally
            # excluded from this RTT; it has already been superseded.
            result["quote_rtt_seconds"] = rtt
            self.ledger.update_order(
                int(item["local_id"]),
                status="QUOTE_ACCEPTED",
                quote_average_price=_float(refreshed.get("averagePrice")),
                quote_amount_in_wei=str(refreshed.get("amountIn")),
                quote_amount_out_wei=str(refreshed.get("amountOut")),
                quote_expires_at=int(refreshed.get("expireAt") or 0) or None,
            )
            return result

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                requoted = list(executor.map(requote, accepted))
        except Exception as exc:
            return None, f"equal-share requote failed: {str(exc)[:300]}"
        return requoted, ""

    @staticmethod
    def _pair_quote_share_mismatch_ratio(
        accepted: list[dict[str, Any]],
    ) -> Decimal | None:
        try:
            outputs = [
                Decimal(int(item["quote"]["amountOut"]))
                for item in accepted
            ]
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return None
        if len(outputs) != 2 or min(outputs) <= 0:
            return None
        return (max(outputs) - min(outputs)) / min(outputs)

    @staticmethod
    def _pair_010_profitable_depth_plan(
        book: dict[str, Any],
        *,
        maximum_total_stake: Decimal,
        fee_bps: int,
    ) -> tuple[dict[str, Any] | None, str]:
        """Size equal pair legs only through currently profitable ask depth."""

        def levels_for(side: str) -> list[tuple[Decimal, Decimal]]:
            key = f"{side.lower()}_asks"
            result: list[tuple[Decimal, Decimal]] = []
            raw_levels = book.get(key)
            for raw_level in (
                raw_levels
                if isinstance(raw_levels, (list, tuple))
                else []
            ):
                try:
                    if isinstance(raw_level, dict):
                        price = _decimal(raw_level.get("price"))
                        size = _decimal(
                            raw_level.get("size", raw_level.get("quantity"))
                        )
                    else:
                        price = _decimal(raw_level[0])
                        size = _decimal(raw_level[1])
                except (IndexError, TypeError):
                    continue
                if (
                    price is not None
                    and size is not None
                    and Decimal("0") < price < Decimal("1")
                    and size > 0
                ):
                    result.append((price, size))
            if not result:
                price = _decimal(book.get(f"{side.lower()}_ask"))
                size = _decimal(book.get(f"{side.lower()}_ask_size"))
                if (
                    price is not None
                    and size is not None
                    and Decimal("0") < price < Decimal("1")
                    and size > 0
                ):
                    result.append((price, size))
            return sorted(result, key=lambda level: level[0])

        up_levels = levels_for("UP")
        down_levels = levels_for("DOWN")
        if not up_levels or not down_levels or maximum_total_stake <= 0:
            return None, "current pair book has no usable two-sided ask depth"

        top_pair_price = up_levels[0][0] + down_levels[0][0]
        if top_pair_price <= 0:
            return None, "current pair top-level price is invalid"
        requested_shares = maximum_total_stake / top_pair_price
        remaining_shares = requested_shares
        remaining_stake = maximum_total_stake
        up_index = down_index = 0
        up_remaining = up_levels[0][1]
        down_remaining = down_levels[0][1]
        filled_shares = Decimal("0")
        up_cost = down_cost = Decimal("0")
        up_fee = down_fee = Decimal("0")
        fee_rate = Decimal(int(fee_bps)) / Decimal(10_000)
        minimum_edge = PAIR_ARB_MIN_NET_EDGE["PAIR_ARB_010"]
        up_levels_consumed: set[int] = set()
        down_levels_consumed: set[int] = set()

        while (
            remaining_shares > 0
            and remaining_stake > 0
            and up_index < len(up_levels)
            and down_index < len(down_levels)
        ):
            up_price = up_levels[up_index][0]
            down_price = down_levels[down_index][0]
            unit_up_fee = min(up_price, Decimal("1") - up_price) * fee_rate
            unit_down_fee = min(down_price, Decimal("1") - down_price) * fee_rate
            marginal_edge = (
                Decimal("1")
                - up_price
                - down_price
                - unit_up_fee
                - unit_down_fee
            )
            if marginal_edge < minimum_edge:
                break
            pair_notional = up_price + down_price
            quantity = min(
                remaining_shares,
                up_remaining,
                down_remaining,
                remaining_stake / pair_notional,
            )
            if quantity <= 0:
                break
            filled_shares += quantity
            up_cost += quantity * up_price
            down_cost += quantity * down_price
            up_fee += quantity * unit_up_fee
            down_fee += quantity * unit_down_fee
            remaining_shares -= quantity
            remaining_stake -= quantity * pair_notional
            up_remaining -= quantity
            down_remaining -= quantity
            up_levels_consumed.add(up_index)
            down_levels_consumed.add(down_index)
            if up_remaining <= 0:
                up_index += 1
                if up_index < len(up_levels):
                    up_remaining = up_levels[up_index][1]
            if down_remaining <= 0:
                down_index += 1
                if down_index < len(down_levels):
                    down_remaining = down_levels[down_index][1]

        if filled_shares <= 0:
            return None, "no current equal-share depth meets the 1% net edge"
        total_cost = (
            up_cost
            + down_cost
            + up_fee
            + down_fee
            + PAIR_ARB_QC_NETWORK_AND_ROUNDING_BUFFER
        )
        locked_pnl = filled_shares - total_cost
        minimum_locked_pnl = filled_shares * minimum_edge
        if locked_pnl < minimum_locked_pnl:
            return None, (
                f"profitable shared depth locks {locked_pnl:.6f} USDT, below "
                f"required {minimum_locked_pnl:.6f} USDT after buffer"
            )
        if min(up_cost, down_cost) < LIVE_MIN_CONFIGURABLE_STAKE_USDT:
            return None, "profitable shared depth leaves one leg below minimum stake"

        quantize = Decimal("0.000000000000000001")
        up_stake = up_cost.quantize(quantize, rounding=ROUND_DOWN)
        down_stake = down_cost.quantize(quantize, rounding=ROUND_DOWN)
        target_shares = filled_shares.quantize(quantize, rounding=ROUND_DOWN)
        return {
            "target_shares": target_shares,
            "up_stake": up_stake,
            "down_stake": down_stake,
            "total_order_stake": up_stake + down_stake,
            "maximum_total_stake": maximum_total_stake,
            "up_vwap": up_cost / filled_shares,
            "down_vwap": down_cost / filled_shares,
            "up_fee": up_fee,
            "down_fee": down_fee,
            "locked_pnl_after_buffer": locked_pnl,
            "minimum_locked_pnl": minimum_locked_pnl,
            "up_levels_consumed": len(up_levels_consumed),
            "down_levels_consumed": len(down_levels_consumed),
        }, ""

    @staticmethod
    def _pair_010_locked_quote_metrics(
        accepted: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str | None]:
        """Conservatively value two signed quotes as one matched-share pair."""
        by_side = {str(item.get("side")): item for item in accepted}
        if set(by_side) != {"UP", "DOWN"}:
            return {}, "both PAIR_ARB_010 sides are required"
        try:
            fee_rate = Decimal(int(accepted[0]["fee_bps"])) / Decimal(10_000)
            outputs: dict[str, Decimal] = {}
            prices: dict[str, Decimal] = {}
            conservative_leg_costs: dict[str, Decimal] = {}
            for side, item in by_side.items():
                quote = item["quote"]
                quote_input = Decimal(int(quote["amountIn"])) / Decimal(10**18)
                quote_output = Decimal(int(quote["amountOut"])) / Decimal(10**18)
                price = Decimal(str(quote["averagePrice"]))
                if (
                    quote_input <= 0
                    or quote_output <= 0
                    or not Decimal("0") < price < Decimal("1")
                ):
                    return {}, "PAIR_ARB_010 signed quote amounts are invalid"
                modeled_cost = quote_output * price
                provider_fee = (
                    quote_output
                    * min(price, Decimal("1") - price)
                    * fee_rate
                )
                outputs[side] = quote_output
                prices[side] = price
                conservative_leg_costs[side] = (
                    max(quote_input, modeled_cost) + provider_fee
                )
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return {}, "PAIR_ARB_010 signed quote fields are invalid"

        matched_shares = min(outputs.values())
        mismatch_ratio = (
            (max(outputs.values()) - matched_shares) / matched_shares
            if matched_shares > 0
            else Decimal("Infinity")
        )
        total_cost = (
            sum(conservative_leg_costs.values(), Decimal(0))
            + PAIR_ARB_QC_NETWORK_AND_ROUNDING_BUFFER
        )
        locked_pnl = matched_shares - total_cost
        minimum_locked_pnl = (
            matched_shares * PAIR_ARB_MIN_NET_EDGE["PAIR_ARB_010"]
        )
        locked_roi = locked_pnl / total_cost if total_cost > 0 else Decimal("-1")
        metrics = {
            "up_quote_price": float(prices["UP"]),
            "down_quote_price": float(prices["DOWN"]),
            "up_gross_shares": float(outputs["UP"]),
            "down_gross_shares": float(outputs["DOWN"]),
            "matched_shares": float(matched_shares),
            "total_cost_usdt": float(total_cost),
            "locked_pnl_usdt": float(locked_pnl),
            "minimum_locked_pnl_usdt": float(minimum_locked_pnl),
            "locked_roi": float(locked_roi),
            "share_mismatch_ratio": float(mismatch_ratio),
            "network_and_rounding_buffer_usdt": float(
                PAIR_ARB_QC_NETWORK_AND_ROUNDING_BUFFER
            ),
        }
        if mismatch_ratio > PAIR_ARB_QC_MAX_NET_SHARE_MISMATCH_RATIO:
            return metrics, (
                f"gross share mismatch {mismatch_ratio:.3%} exceeds "
                f"{PAIR_ARB_QC_MAX_NET_SHARE_MISMATCH_RATIO:.2%}"
            )
        if locked_pnl < minimum_locked_pnl:
            return metrics, (
                f"PAIR_ARB_010 locked PnL {locked_pnl:.6f} USDT is below "
                f"required {minimum_locked_pnl:.6f} USDT"
            )
        return metrics, None

    @staticmethod
    def _qc_pair_metrics(
        accepted: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], str | None]:
        by_side = {str(item["side"]): item for item in accepted}
        if set(by_side) != {"UP", "DOWN"}:
            return {}, "both QC pair sides are required"
        try:
            fee_rate = Decimal(int(accepted[0]["fee_bps"])) / Decimal(10_000)
            server_now = int(accepted[0]["client"].server_timestamp_ms())
            quote_inputs: dict[str, Decimal] = {}
            gross_outputs: dict[str, Decimal] = {}
            net_outputs: dict[str, Decimal] = {}
            capacities: list[Decimal] = []
            expiries: list[int] = []
            quote_prices: dict[str, Decimal] = {}
            quote_rtts: list[Decimal] = []
            for side, item in by_side.items():
                quote = item["quote"]
                quote_input_wei = Decimal(int(quote["amountIn"]))
                quote_output_wei = Decimal(int(quote["amountOut"]))
                requested_input_wei = Decimal(int(item["requested_amount_wei"]))
                expected_output_wei = Decimal(int(item["expected_amount_out_wei"]))
                quote_inputs[side] = quote_input_wei / Decimal(10**18)
                gross_outputs[side] = quote_output_wei / Decimal(10**18)
                net_outputs[side] = gross_outputs[side] * (Decimal(1) - fee_rate)
                capacities.extend((
                    quote_input_wei / requested_input_wei,
                    quote_output_wei / expected_output_wei,
                ))
                expiries.append(int(quote.get("expireAt") or 0) - server_now)
                quote_prices[side] = Decimal(str(quote["averagePrice"]))
                quote_rtts.append(Decimal(str(item.get("quote_rtt_seconds") or 0)))
        except (KeyError, TypeError, ValueError, InvalidOperation, ZeroDivisionError):
            return {}, "final signed pair quote fields are invalid"

        minimum_capacity = min(capacities)
        minimum_net_shares = min(net_outputs.values())
        maximum_net_shares = max(net_outputs.values())
        mismatch_ratio = (
            (maximum_net_shares - minimum_net_shares) / minimum_net_shares
            if minimum_net_shares > 0 else Decimal("Infinity")
        )
        total_cost = (
            sum(quote_inputs.values(), Decimal(0))
            + PAIR_ARB_QC_NETWORK_AND_ROUNDING_BUFFER
        )
        locked_pnl = minimum_net_shares - total_cost
        locked_roi = locked_pnl / total_cost if total_cost > 0 else Decimal("-1")
        minimum_expiry = min(expiries)
        maximum_rtt = max(quote_rtts)
        metrics = {
            "up_quote_price": float(quote_prices["UP"]),
            "down_quote_price": float(quote_prices["DOWN"]),
            "up_net_shares": float(net_outputs["UP"]),
            "down_net_shares": float(net_outputs["DOWN"]),
            "total_cost_usdt": float(total_cost),
            "guaranteed_payout_usdt": float(minimum_net_shares),
            "locked_pnl_usdt": float(locked_pnl),
            "locked_roi": float(locked_roi),
            "minimum_capacity_ratio": float(minimum_capacity),
            "net_share_mismatch_ratio": float(mismatch_ratio),
            "minimum_expiry_ms": int(minimum_expiry),
            "maximum_quote_rtt_ms": float(maximum_rtt * Decimal(1000)),
        }
        if minimum_capacity < PAIR_ARB_QC_MIN_QUOTE_CAPACITY_RATIO:
            return metrics, (
                f"final quote capacity {minimum_capacity:.1%} is below "
                f"{PAIR_ARB_QC_MIN_QUOTE_CAPACITY_RATIO:.0%}"
            )
        if mismatch_ratio > PAIR_ARB_QC_MAX_NET_SHARE_MISMATCH_RATIO:
            return metrics, (
                f"net share mismatch {mismatch_ratio:.3%} exceeds "
                f"{PAIR_ARB_QC_MAX_NET_SHARE_MISMATCH_RATIO:.2%}"
            )
        if minimum_expiry < PAIR_ARB_QC_MIN_QUOTE_EXPIRY_MS:
            return metrics, (
                f"minimum quote expiry {minimum_expiry}ms is below "
                f"{PAIR_ARB_QC_MIN_QUOTE_EXPIRY_MS}ms"
            )
        if maximum_rtt > PAIR_ARB_QC_MAX_QUOTE_RTT_SECONDS:
            return metrics, (
                f"quote round trip {maximum_rtt * Decimal(1000):.1f}ms exceeds "
                f"{PAIR_ARB_QC_MAX_QUOTE_RTT_SECONDS * Decimal(1000):.0f}ms"
            )
        if locked_pnl < PAIR_ARB_QC_MIN_LOCKED_PNL:
            return metrics, (
                f"locked PnL {locked_pnl:.6f} is below "
                f"{PAIR_ARB_QC_MIN_LOCKED_PNL} USDT"
            )
        if locked_roi < PAIR_ARB_QC_MIN_LOCKED_ROI:
            return metrics, (
                f"locked ROI {locked_roi:.3%} is below "
                f"{PAIR_ARB_QC_MIN_LOCKED_ROI:.1%}"
            )
        return metrics, None

    def _process_pair_signal(self, signal: dict[str, Any]) -> None:
        strategy = str(signal.get("strategy") or "").upper()
        market_id = int(signal.get("market_id") or 0)
        side = str(signal.get("side") or "").upper()
        if side not in {"UP", "DOWN"}:
            self._record_blocked_signal(
                signal, "BLOCKED_INVALID_SIGNAL", "互補實單訊號方向無效"
            )
            return
        key = (strategy, market_id)
        with self.lock:
            legs = self.pending_pair_signals.setdefault(key, {})
            legs.setdefault(side, dict(signal))
            if set(legs) != {"UP", "DOWN"}:
                return
            pair = self.pending_pair_signals.pop(key)
            runtime_enabled = self.runtime_enabled
        shadow_only = strategy == "PAIR_ARB_QC_015" and (
            not runtime_enabled or not self._pair_qc_live_ready()
        )

        with self.lock:
            rules = dict(self.live_rules)
        if self._strategy_drawdown_control_enabled(rules, strategy):
            reference = self.current_market() or {}
            decisions = [
                self._drawdown_control_is_safe(pair[leg_side], reference)
                for leg_side in ("UP", "DOWN")
            ]
            blocked_reason = next(
                (reason for allowed, reason in decisions if not allowed), ""
            )
            if blocked_reason:
                for leg_side in ("UP", "DOWN"):
                    self._record_blocked_signal(
                        pair[leg_side],
                        "BLOCKED_DRAWDOWN_CONTROL",
                        blocked_reason,
                    )
                return
            for leg_side in ("UP", "DOWN"):
                pair[leg_side]["_drawdown_control_validated"] = True

        if strategy == "PAIR_ARB_010":
            callback = self.current_direct_rest_prediction_book
            try:
                shared_book = callback() if callback is not None else None
            except Exception:
                shared_book = None
            if isinstance(shared_book, dict):
                for leg_side in ("UP", "DOWN"):
                    pair[leg_side]["_pair_preflight_book"] = shared_book
                checked_books = [
                    self._latest_pair_rest_book_check(
                        signal=pair[leg_side],
                        market_id=market_id,
                        side=leg_side,
                    )[0]
                    for leg_side in ("UP", "DOWN")
                ]
                if all(book is not None for book in checked_books):
                    try:
                        strategy_index = list(rules["strategies"]).index(strategy)
                        maximum_total_stake = Decimal(
                            str(rules["strategyStakesUsdt"][strategy_index])
                        )
                    except (KeyError, IndexError, TypeError, ValueError):
                        maximum_total_stake = Decimal(
                            str(rules.get("maxStakeUsdt") or 0)
                        )
                    reference = self.current_market() or {}
                    depth_plan, depth_error = self._pair_010_profitable_depth_plan(
                        shared_book,
                        maximum_total_stake=maximum_total_stake,
                        fee_bps=int(reference.get("fee_bps") or 200),
                    )
                    if depth_plan is None:
                        diagnostics = {
                            "maximumTotalStakeUsdt": float(maximum_total_stake),
                            "reason": depth_error,
                            "bookAgeMs": _float(shared_book.get("book_age_ms")),
                            "bookSkewMs": _float(shared_book.get("book_skew_ms")),
                        }
                        self._record_blocked_signal(
                            pair["UP"],
                            "BLOCKED_PAIR_PROFITABLE_DEPTH",
                            depth_error,
                            error_kind="PAIR_NO_PROFITABLE_SHARED_DEPTH",
                            diagnostics=diagnostics,
                        )
                        return
                    plan_diagnostics = {
                        key: float(value) if isinstance(value, Decimal) else value
                        for key, value in depth_plan.items()
                    }
                    for leg_side in ("UP", "DOWN"):
                        pair[leg_side]["_pair_dynamic_leg_stake_usdt"] = format(
                            depth_plan[f"{leg_side.lower()}_stake"], "f"
                        )
                        pair[leg_side]["_pair_dynamic_target_shares"] = format(
                            depth_plan["target_shares"], "f"
                        )
                        pair[leg_side]["_pair_dynamic_depth_plan"] = (
                            plan_diagnostics
                        )
                        pair[leg_side]["pairDynamicTargetShares"] = float(
                            depth_plan["target_shares"]
                        )
                        pair[leg_side]["pairDynamicLegStakeUsdt"] = float(
                            depth_plan[f"{leg_side.lower()}_stake"]
                        )
                        pair[leg_side]["pairDynamicTotalStakeUsdt"] = float(
                            depth_plan["total_order_stake"]
                        )
                        pair[leg_side]["pairDynamicMaximumStakeUsdt"] = float(
                            depth_plan["maximum_total_stake"]
                        )
                        pair[leg_side]["pairDynamicLockedPnlUsdt"] = float(
                            depth_plan["locked_pnl_after_buffer"]
                        )
                    self.ledger.record_event(
                        "INFO",
                        "PAIR_DYNAMIC_DEPTH_SIZED",
                        (
                            f"PAIR_ARB_010 resized from "
                            f"{maximum_total_stake:.6f} to "
                            f"{depth_plan['total_order_stake']:.6f} USDT for "
                            f"{depth_plan['target_shares']:.6f} equal shares"
                        ),
                        market_id,
                    )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                leg_side: executor.submit(
                    self._process_single_signal,
                    pair[leg_side],
                    defer_placement=True,
                    allow_paused_quote_only=shadow_only,
                )
                for leg_side in ("UP", "DOWN")
            }
            prepared = {
                leg_side: future.result()
                for leg_side, future in futures.items()
            }

        accepted = [item for item in prepared.values() if item is not None]
        rejected_audit_status = "SHADOW_REJECTED" if shadow_only else "REJECTED"
        if len(accepted) != 2:
            for item in accepted:
                self.ledger.update_order(
                    int(item["local_id"]),
                    status="REJECTED",
                    error_kind="PAIR_QUOTE_ABORTED",
                    error_message="互補另一腿報價未通過；兩腿皆不送單",
                )
            self.ledger.record_event(
                "ERROR",
                "PAIR_QUOTE_ABORTED",
                f"{strategy} 任一腿報價未通過；兩腿皆未送單",
                market_id,
            )
            if shadow_only:
                self.ledger.record_pair_quote_audit(
                    strategy=strategy,
                    market_id=market_id,
                    status="SHADOW_UNAVAILABLE",
                    reason="signed pair quote preparation failed while paused",
                )
            return

        if strategy == "PAIR_ARB_010":
            mismatch_ratio = self._pair_quote_share_mismatch_ratio(accepted)
            if mismatch_ratio is None:
                requoted = None
                requote_error = "initial pair quote share amounts are invalid"
            elif mismatch_ratio > PAIR_ARB_QC_MAX_NET_SHARE_MISMATCH_RATIO:
                requoted, requote_error = self._requote_qc_pair(
                    accepted,
                    minimum_capacity=PAIR_ARB_MIN_QUOTE_CAPACITY_RATIO,
                )
            else:
                requoted, requote_error = accepted, ""
            if requoted is None:
                for item in accepted:
                    self.ledger.update_order(
                        int(item["local_id"]),
                        status="REJECTED",
                        error_kind="PAIR_EQUAL_SHARE_REQUOTE_REJECTED",
                        error_message=requote_error,
                    )
                self.ledger.record_event(
                    "ERROR",
                    "PAIR_EQUAL_SHARE_REQUOTE_REJECTED",
                    requote_error,
                    market_id,
                )
                return
            accepted = requoted

        if strategy == "PAIR_ARB_QC_015":
            requoted, requote_error = self._requote_qc_pair(accepted)
            if requoted is None:
                for item in accepted:
                    self.ledger.update_order(
                        int(item["local_id"]),
                        status="REJECTED",
                        error_kind="PAIR_QC_REQUOTE_REJECTED",
                        error_message=requote_error,
                    )
                self.ledger.record_pair_quote_audit(
                    strategy=strategy,
                    market_id=market_id,
                    status=rejected_audit_status,
                    reason=requote_error,
                )
                self.ledger.record_event(
                    "ERROR", "PAIR_QC_REQUOTE_REJECTED", requote_error, market_id
                )
                return
            accepted = requoted

        capacity_failures: list[str] = []
        minimum_capacity = (
            PAIR_ARB_QC_MIN_QUOTE_CAPACITY_RATIO
            if strategy == "PAIR_ARB_QC_015"
            else PAIR_ARB_MIN_QUOTE_CAPACITY_RATIO
        )
        for item in accepted:
            requested_input = Decimal(int(item["requested_amount_wei"]))
            expected_output = Decimal(int(item["expected_amount_out_wei"]))
            quoted_input = _decimal(item["quote"].get("amountIn"))
            quoted_output = _decimal(item["quote"].get("amountOut"))
            input_ratio = (
                quoted_input / requested_input
                if quoted_input is not None and requested_input > 0
                else Decimal(0)
            )
            output_ratio = (
                quoted_output / expected_output
                if quoted_output is not None and expected_output > 0
                else Decimal(0)
            )
            if (
                input_ratio < minimum_capacity
                or output_ratio < minimum_capacity
            ):
                capacity_failures.append(
                    f"{item['side']} quote capacity too low "
                    f"(amountIn={input_ratio:.1%}, amountOut={output_ratio:.1%}, "
                    f"minimum={minimum_capacity:.0%})"
                )
        if capacity_failures:
            detail = "; ".join(capacity_failures)
            for item in accepted:
                self.ledger.update_order(
                    int(item["local_id"]),
                    status="REJECTED",
                    error_kind="PAIR_QUOTE_CAPACITY_LOW",
                    error_message=detail,
                )
            self.ledger.record_event(
                "ERROR", "PAIR_QUOTE_CAPACITY_LOW", detail, market_id
            )
            if strategy == "PAIR_ARB_QC_015":
                self.ledger.record_pair_quote_audit(
                    strategy=strategy,
                    market_id=market_id,
                    status=rejected_audit_status,
                    reason=detail,
                )
            return

        server_now = accepted[0]["client"].server_timestamp_ms()
        expiries = [int(item["quote"].get("expireAt") or 0) for item in accepted]
        if any(expiry and expiry <= server_now + 500 for expiry in expiries):
            pair_safe = False
            net_edge = None
            unsafe_detail = "互補任一腿報價距離到期不足 500ms"
        elif strategy == "PAIR_ARB_010":
            pair_metrics, pair_metrics_error = self._pair_010_locked_quote_metrics(
                accepted
            )
            pair_safe = pair_metrics_error is None
            matched_shares = _decimal(pair_metrics.get("matched_shares"))
            locked_pnl = _decimal(pair_metrics.get("locked_pnl_usdt"))
            net_edge = (
                locked_pnl / matched_shares
                if locked_pnl is not None
                and matched_shares is not None
                and matched_shares > 0
                else None
            )
            unsafe_detail = pair_metrics_error or (
                f"PAIR_ARB_010 locked PnL "
                f"{pair_metrics['locked_pnl_usdt']:.6f} USDT passed"
            )
        else:
            averages = [
                _decimal(item["quote"].get("averagePrice"))
                for item in accepted
            ]
            fee_rate = Decimal(int(accepted[0]["fee_bps"])) / Decimal(10_000)
            if any(value is None for value in averages):
                pair_safe = False
                net_edge = None
                unsafe_detail = "互補雙腿實際報價無法計算"
            else:
                up_price, down_price = averages
                unit_fees = (
                    min(up_price, Decimal(1) - up_price)
                    + min(down_price, Decimal(1) - down_price)
                ) * fee_rate
                net_edge = Decimal(1) - up_price - down_price - unit_fees
                minimum_edge = PAIR_ARB_MIN_NET_EDGE[strategy]
                pair_safe = net_edge >= minimum_edge
                unsafe_detail = (
                    f"互補雙腿實際含費淨邊際 {net_edge:.6f} 低於策略門檻"
                )
        if not pair_safe:
            detail = unsafe_detail
            for item in accepted:
                self.ledger.update_order(
                    int(item["local_id"]),
                    status="REJECTED",
                    error_kind="PAIR_QUOTE_ABORTED",
                    error_message=detail,
                )
            self.ledger.record_event(
                "ERROR", "PAIR_QUOTE_ABORTED", detail, market_id
            )
            if strategy == "PAIR_ARB_QC_015":
                self.ledger.record_pair_quote_audit(
                    strategy=strategy,
                    market_id=market_id,
                    status=rejected_audit_status,
                    reason=detail,
                )
            return

        if strategy == "PAIR_ARB_QC_015":
            metrics, qc_error = self._qc_pair_metrics(accepted)
            if qc_error is not None:
                for item in accepted:
                    self.ledger.update_order(
                        int(item["local_id"]),
                        status="REJECTED",
                        error_kind="PAIR_QC_FINAL_GATE_REJECTED",
                        error_message=qc_error,
                    )
                self.ledger.record_pair_quote_audit(
                    strategy=strategy,
                    market_id=market_id,
                    status=rejected_audit_status,
                    reason=qc_error,
                    metrics=metrics,
                )
                self.ledger.record_event(
                    "WARN", "PAIR_QC_FINAL_GATE_REJECTED", qc_error, market_id
                )
                return
            detail = (
                f"QC pair accepted: locked PnL "
                f"{metrics['locked_pnl_usdt']:.6f} USDT, "
                f"ROI {metrics['locked_roi']:.3%}"
            )
            self.ledger.record_pair_quote_audit(
                strategy=strategy,
                market_id=market_id,
                status="SHADOW_ACCEPTED" if shadow_only else "ACCEPTED",
                reason=detail,
                metrics=metrics,
            )
            self.ledger.record_event(
                "INFO", "PAIR_QC_FINAL_GATE_ACCEPTED", detail, market_id
            )
            if shadow_only:
                for item in accepted:
                    self.ledger.update_order(
                        int(item["local_id"]),
                        status="SHADOW_ACCEPTED_NO_PLACEMENT",
                    )
                self.ledger.record_event(
                    "INFO",
                    "PAIR_QC_SHADOW_ACCEPTED",
                    "signed pair quote passed all QC gates; no order was placed",
                    market_id,
                )
                return

        # A pause may arrive while the two network quotes are in flight.
        # Recheck immediately before placement so pause is a hard boundary.
        with self.lock:
            placement_allowed = self.runtime_enabled and self.armed
        if not placement_allowed:
            for item in accepted:
                self.ledger.update_order(
                    int(item["local_id"]),
                    status="ABORTED_RUNTIME_PAUSED",
                )
            self.ledger.record_event(
                "WARN",
                "PAIR_PLACEMENT_ABORTED_RUNTIME_PAUSED",
                "runtime was paused before pair placement; no order was placed",
                market_id,
            )
            return

        with ThreadPoolExecutor(max_workers=2) as executor:
            placement_results = list(executor.map(
                lambda item: (str(item["side"]), self._place_accepted_quote(item)),
                accepted,
            ))
        if not all(result for _, result in placement_results):
            accepted_sides = [side for side, result in placement_results if result]
            missing_sides = [side for side, result in placement_results if not result]
            incident_reason = (
                f"pair placement incomplete; accepted={accepted_sides}, "
                f"missing={missing_sides}"
            )
            self.ledger.record_pair_incident(
                strategy=strategy,
                market_id=market_id,
                status="ACTIVE",
                reason=incident_reason,
                # A successful place response is not proof of a fill.  The
                # order sync guard records filled_side only after Binance
                # reports an actual filled quantity.
                filled_side=None,
                missing_side=missing_sides[0] if len(missing_sides) == 1 else None,
            )
            self.set_runtime_enabled(False)
            with self.lock:
                self.status = "PAUSED_PAIR_INCOMPLETE"
                self.armed = False
                self.last_error = (
                    "互補送單未能確認兩腿皆成功；實單已自動暫停，請人工核對持倉"
                )
            self.ledger.record_event(
                "ERROR",
                "PAIR_PLACEMENT_INCOMPLETE",
                "互補送單未能確認兩腿皆成功；實單已自動暫停",
                market_id,
            )

    def _process_single_signal(
        self,
        signal: dict[str, Any],
        *,
        defer_placement: bool = False,
        allow_paused_quote_only: bool = False,
    ) -> dict[str, Any] | None:
        """Process one signal synchronously; exposed for deterministic tests."""
        processing_started_monotonic = time.monotonic()
        try:
            enqueued_monotonic = (
                int(signal.get("_live_enqueued_monotonic_ns")) / 1_000_000_000
            )
        except (TypeError, ValueError):
            enqueued_monotonic = processing_started_monotonic
        queue_delay_seconds = max(
            0.0, processing_started_monotonic - enqueued_monotonic
        )
        market_id = int(signal.get("market_id") or 0)
        with self.lock:
            self.last_signal_at = utc_iso()
            self.last_signal_market_id = market_id or None
            runtime_enabled = self.runtime_enabled
            armed = self.armed
            client = self.client
            wallet_address = self.wallet_address
            wallet_id = self.wallet_id
            rules = dict(self.live_rules)
        signal_strategy = str(signal.get("strategy") or "").upper()
        if signal_strategy not in set(str(value) for value in rules["strategies"]):
            return
        selected_strategy = signal_strategy
        strategy_index = list(rules["strategies"]).index(selected_strategy)
        max_stake = Decimal(str(rules["strategyStakesUsdt"][strategy_index]))
        if selected_strategy in LIVE_RESEARCH_STRATEGIES:
            price_allowed, price_reason = self._research_signal_price_is_allowed(
                signal, selected_strategy
            )
            if not price_allowed:
                self._record_blocked_signal(
                    signal, "BLOCKED_RESEARCH_PRICE_RANGE", price_reason
                )
                return
        if selected_strategy.startswith("PAIR_ARB_"):
            pair_total_price = _decimal(signal.get("pair_total_price"))
            leg_price = _decimal(signal.get("entry_price"))
            signal_up_price = _decimal(signal.get("pair_up_price"))
            signal_down_price = _decimal(signal.get("pair_down_price"))
            if (
                signal.get("pair_arb_leg") is not True
                or pair_total_price is None
                or leg_price is None
                or pair_total_price <= 0
                or leg_price <= 0
            ):
                self._record_blocked_signal(
                    signal, "BLOCKED_INVALID_SIGNAL", "互補實單訊號缺少雙邊價格"
                )
                return
            dynamic_leg_stake = _decimal(
                signal.get("_pair_dynamic_leg_stake_usdt")
            )
            if (
                selected_strategy == "PAIR_ARB_010"
                and dynamic_leg_stake is not None
                and dynamic_leg_stake > 0
            ):
                max_stake = dynamic_leg_stake
            else:
                allocation_total = pair_total_price
                if (
                    selected_strategy == "PAIR_ARB_RISK_020"
                    and signal_up_price is not None
                    and signal_down_price is not None
                    and signal_up_price > 0
                    and signal_down_price > 0
                ):
                    allocation_total = signal_up_price + signal_down_price
                max_stake = (max_stake * leg_price / allocation_total).quantize(
                    Decimal("0.000000000000000001"), rounding=ROUND_DOWN
                )
            if max_stake < LIVE_MIN_CONFIGURABLE_STAKE_USDT:
                self._record_blocked_signal(
                    signal, "BLOCKED_INVALID_SIGNAL", "互補單腿金額低於可下單下限"
                )
                return
        amount_in_wei = _stake_amount_wei(max_stake)
        paused_quote_only = (
            allow_paused_quote_only
            and defer_placement
            and selected_strategy == "PAIR_ARB_QC_015"
            and not runtime_enabled
        )
        if not runtime_enabled and not paused_quote_only:
            self._record_blocked_signal(
                signal, "SKIPPED_PAUSED", "實單執行目前已暫停"
            )
            return
        if (
            (not armed and not paused_quote_only)
            or client is None
            or not wallet_address
            or not wallet_id
        ):
            self._record_blocked_signal(
                signal, "BLOCKED_PREFLIGHT", "實單預檢尚未通過，未送出訂單"
            )
            return
        if signal.get("market_data_integrity_ok") is False:
            self._record_blocked_signal(
                signal, "BLOCKED_DATA_INTEGRITY", "行情事件完整性失敗，未送出訂單"
            )
            return
        hourly_guard = (
            {"blocked": False}
            if selected_strategy.startswith("PAIR_ARB_")
            else self._evaluate_cached_hourly_guard(
                at=str(signal.get("signal_timestamp") or utc_iso())
            )
        )
        if hourly_guard.get("blocked") is True:
            reasons = hourly_guard.get("reasons") or ["時段條件未通過"]
            message = (
                f"M0 台北時段閘門 {hourly_guard.get('label') or ''} 暫停新單："
                + "；".join(str(reason) for reason in reasons)
            )
            self._record_blocked_signal(
                signal, "SKIPPED_M0_HOURLY_GUARD", message
            )
            return

        reference = self.current_market()
        if not reference or int(reference.get("market_id") or -1) != market_id:
            self._record_blocked_signal(
                signal, "BLOCKED_MARKET_MISMATCH", "實單訊號市場已不是目前市場"
            )
            return
        if (
            not selected_strategy.startswith("PAIR_ARB_")
            and (
                "signal_spot_age_ms" in signal
                or "signal_spot_trade_processed_age_ms" in signal
            )
        ):
            spot_is_safe, spot_reason = self._spot_data_is_safe_for_live(signal)
            if not spot_is_safe:
                self._record_blocked_signal(
                    signal,
                    "BLOCKED_STALE_SPOT_DATA",
                    spot_reason,
                    diagnostics={
                        "spotAgeMs": _float(signal.get("signal_spot_age_ms")),
                        "spotPriceSource": signal.get("signal_spot_price_source"),
                        "spotTradeIngressAgeMs": _float(
                            signal.get("signal_spot_trade_ingress_age_ms")
                        ),
                        "spotTradeProcessedAgeMs": _float(
                            signal.get("signal_spot_trade_processed_age_ms")
                        ),
                        "spotDataMaxAgeMs": LIVE_MAX_SPOT_DATA_AGE_MS,
                    },
                )
                return
        reliability_is_safe, reliability_reason = self._reliability_gate_is_safe(
            signal, rules
        )
        if not reliability_is_safe:
            self._record_blocked_signal(
                signal, "BLOCKED_RELIABILITY_GATE", reliability_reason
            )
            return
        drawdown_enabled = self._strategy_drawdown_control_enabled(
            rules, selected_strategy
        )
        if (
            drawdown_enabled
            and signal.get("_drawdown_control_validated") is not True
        ):
            drawdown_is_safe, drawdown_reason = self._drawdown_control_is_safe(
                signal, reference
            )
            if not drawdown_is_safe:
                self._record_blocked_signal(
                    signal,
                    "BLOCKED_DRAWDOWN_CONTROL",
                    drawdown_reason,
                )
                return
        observer_enabled, observer_version = self._strategy_observer_config(
            rules, selected_strategy
        )
        if selected_strategy in LIVE_OBSERVER_STRATEGIES and observer_enabled:
            gate_is_safe, gate_reason = self._strategy_observer_gate_is_safe(
                signal,
                reference,
                observer_version,
            )
            if not gate_is_safe:
                self._record_blocked_signal(
                    signal,
                    (
                        "BLOCKED_FUTURES_LEAD_OBSERVER"
                        if selected_strategy
                        in FUTURES_LEAD_LIVE_OBSERVER_STRATEGIES
                        else "BLOCKED_STRATEGY_OBSERVER"
                    ),
                    gate_reason,
                )
                return
        if selected_strategy in {"M0W", "M01W"}:
            gate_is_safe, gate_reason = self._strategy_gate_is_safe(
                signal, reference
            )
            if not gate_is_safe:
                self._record_blocked_signal(
                    signal, "BLOCKED_STRATEGY_GATE", gate_reason
                )
                return
        if selected_strategy == "M01O_F1":
            time_is_safe, time_reason = self._f1_entry_time_is_safe(
                signal, reference, client
            )
            if not time_is_safe:
                self._record_blocked_signal(
                    signal, "SKIPPED_F1_LAST_30_SECONDS", time_reason
                )
                return
            gate_is_safe, gate_reason = self._observer_gate_is_safe(
                signal, reference
            )
            if not gate_is_safe:
                self._record_blocked_signal(
                    signal, "BLOCKED_OBSERVER_GATE", gate_reason
                )
                return
        side = str(signal.get("side") or "").upper()
        if side not in {"UP", "DOWN"}:
            self._record_blocked_signal(
                signal, "BLOCKED_INVALID_SIGNAL", "實單訊號方向無效"
            )
            return
        token_id = str(reference.get(f"{side.lower()}_token_id") or "")
        if not token_id:
            self._record_blocked_signal(
                signal, "BLOCKED_MARKET_METADATA", "目前市場缺少 outcome token ID"
            )
            return
        if selected_strategy.startswith("PAIR_ARB_"):
            up_price = _decimal(signal.get("pair_up_price"))
            down_price = _decimal(signal.get("pair_down_price"))
            leg_price = _decimal(signal.get("entry_price"))
            expected_leg_price = up_price if side == "UP" else down_price
            price_tolerance = (
                PAIR_ARB_RISK_PRICE_TOLERANCE
                if selected_strategy == "PAIR_ARB_RISK_020"
                else Decimal("0.00000001")
            )
            if (
                up_price is None
                or down_price is None
                or leg_price is None
                or pair_total_price is None
                or abs(up_price + down_price - pair_total_price) > price_tolerance
                or abs(leg_price - expected_leg_price) > price_tolerance
            ):
                self._record_blocked_signal(
                    signal, "BLOCKED_INVALID_SIGNAL", "互補雙邊價格不一致"
                )
                return
            fee_rate = Decimal(int(reference.get("fee_bps") or 200)) / Decimal(10_000)
            unit_fees = (
                min(up_price, Decimal(1) - up_price)
                + min(down_price, Decimal(1) - down_price)
            ) * fee_rate
            net_edge = Decimal(1) - up_price - down_price - unit_fees
            minimum_edge = PAIR_ARB_MIN_NET_EDGE[selected_strategy]
            if net_edge < minimum_edge:
                self._record_blocked_signal(
                    signal, "BLOCKED_STRATEGY_GATE", "互補含費淨邊際已低於策略門檻"
                )
                return
        try:
            signal_price_text = self._price_limit(signal.get("entry_price"))
            signal_price = Decimal(signal_price_text)
            maximum_reprice_limit = self._maximum_reprice_limit(
                signal, selected_strategy, signal_price
            )
            if (
                selected_strategy == "R_CALIBRATED_VALUE"
                and "RC_LOW_ENTRY" in rules["reliabilityGateTags"]
            ):
                maximum_reprice_limit = min(
                    maximum_reprice_limit, Decimal("0.376875")
                )
        except ValueError as exc:
            self._record_blocked_signal(
                signal, "BLOCKED_INVALID_PRICE", str(exc)
            )
            return

        if self._strategy_loss_cooldown_enabled(rules, selected_strategy):
            cooldown_is_safe, cooldown_reason = self._loss_cooldown_is_safe(
                selected_strategy, market_id
            )
            if not cooldown_is_safe:
                self._record_blocked_signal(
                    signal,
                    "SKIPPED_TWO_LOSS_COOLDOWN",
                    cooldown_reason,
                )
                return

        if selected_strategy.startswith("PAIR_ARB_"):
            (
                latest_prediction_book,
                book_block_status,
                book_error_kind,
                book_block_message,
                book_diagnostics,
            ) = self._latest_pair_rest_book_check(
                signal=signal,
                market_id=market_id,
                side=side,
            )
        else:
            (
                latest_prediction_book,
                book_block_status,
                book_error_kind,
                book_block_message,
                book_diagnostics,
            ) = self._latest_prediction_book_check(
                market_id=market_id,
                side=side,
            )
        if latest_prediction_book is None:
            assert book_block_status is not None
            assert book_error_kind is not None
            assert book_block_message is not None
            self._record_prediction_book_block(
                signal,
                status=book_block_status,
                error_kind=book_error_kind,
                message=book_block_message,
                diagnostics=book_diagnostics,
                enqueued_monotonic=enqueued_monotonic,
                processing_started_monotonic=processing_started_monotonic,
            )
            return
        latest_verified_ask = Decimal(
            str(latest_prediction_book["latest_ask"])
        )
        book_diagnostics["signalPrice"] = float(signal_price)
        book_diagnostics["signalBookAgeMs"] = _float(
            signal.get("signal_prediction_book_age_ms")
        )
        book_diagnostics["maximumExecutionPrice"] = float(
            maximum_reprice_limit
        )
        event_received_monotonic = self._signal_monotonic_seconds(
            signal, "market_event_received_monotonic_ns"
        )
        book_diagnostics["eventToLocalCheckMs"] = self._elapsed_ms(
            event_received_monotonic, time.monotonic()
        )
        if (
            latest_verified_ask
            > maximum_reprice_limit + Decimal("0.00000001")
        ):
            self._record_prediction_book_block(
                signal,
                status="BLOCKED_LOCAL_PRICE_MOVED",
                error_kind="LOCAL_PRICE_MOVED",
                message=(
                    f"latest verified {side} ask "
                    f"{format(latest_verified_ask.normalize(), 'f')} exceeds "
                    "the permitted execution limit "
                    f"{format(maximum_reprice_limit.normalize(), 'f')}"
                ),
                diagnostics=book_diagnostics,
                enqueued_monotonic=enqueued_monotonic,
                processing_started_monotonic=processing_started_monotonic,
            )
            return
        with self.lock:
            self.last_local_price_check = {
                **book_diagnostics,
                "status": "PASS",
                "errorKind": None,
                "checkedAt": utc_iso(),
            }

        latest_ask_size = _decimal(
            latest_prediction_book.get("latest_ask_size")
        )
        top_level_capacity = (
            latest_verified_ask * latest_ask_size
            if latest_ask_size is not None and latest_ask_size > 0
            else Decimal("0")
        )
        capacity_ratio = (
            top_level_capacity / max_stake
            if max_stake > 0
            else Decimal("0")
        )
        level_key = "up_asks" if side == "UP" else "down_asks"
        local_levels = latest_prediction_book.get(level_key)
        estimate = estimate_buy_vwap(local_levels, max_stake)
        estimated_vwap = _decimal(estimate["estimated_vwap"])
        depth_coverage_ratio = _decimal(estimate["capacity_ratio"]) or Decimal("0")
        depth_diagnostics = {
            **book_diagnostics,
            "configuredStake": float(max_stake),
            "topLevelCapacityUsdt": float(top_level_capacity),
            "topLevelCapacityRatio": float(capacity_ratio),
            "depthCoverageRatio": estimate["capacity_ratio"],
            "depthCoveredStakeUsdt": estimate["covered_stake"],
            "depthLevelsConsumed": estimate["levels_consumed"],
            "minimumDepthCoverageRatio": float(
                LIVE_MIN_DEPTH_COVERAGE_RATIO
            ),
            "vwapAvailable": estimated_vwap is not None,
            "estimatedVwap": estimate["estimated_vwap"],
            "estimatedVwapCapacityRatio": estimate["capacity_ratio"],
            "estimatedVwapCoveredStake": estimate["covered_stake"],
            "estimatedVwapLevelsConsumed": estimate["levels_consumed"],
        }
        if depth_coverage_ratio < LIVE_MIN_DEPTH_COVERAGE_RATIO:
            self._record_prediction_book_block(
                signal,
                status="BLOCKED_INSUFFICIENT_DEPTH_COVERAGE",
                error_kind="LOCAL_INSUFFICIENT_DEPTH",
                message=(
                    f"latest {side} multi-level depth coverage ratio "
                    f"{float(depth_coverage_ratio):.6f} is below required "
                    f"{float(LIVE_MIN_DEPTH_COVERAGE_RATIO):.6f}"
                ),
                diagnostics=depth_diagnostics,
                enqueued_monotonic=enqueued_monotonic,
                processing_started_monotonic=processing_started_monotonic,
            )
            return

        if (
            estimated_vwap is not None
            and estimated_vwap
            > maximum_reprice_limit + Decimal("0.00000001")
        ):
            self._record_prediction_book_block(
                signal,
                status="BLOCKED_ESTIMATED_VWAP_TOO_HIGH",
                error_kind="LOCAL_ESTIMATED_VWAP_TOO_HIGH",
                message=(
                    f"estimated local {side} VWAP "
                    f"{format(estimated_vwap.normalize(), 'f')} exceeds "
                    "the permitted execution limit "
                    f"{format(maximum_reprice_limit.normalize(), 'f')}"
                ),
                diagnostics=depth_diagnostics,
                enqueued_monotonic=enqueued_monotonic,
                processing_started_monotonic=processing_started_monotonic,
            )
            return
        with self.lock:
            self.last_depth_check = {
                **depth_diagnostics,
                "status": "PASS",
                "errorKind": None,
                "checkedAt": utc_iso(),
            }

        ledger_strategy = (
            f"{selected_strategy}:{side}"
            if selected_strategy.startswith("PAIR_ARB_")
            else selected_strategy
        )
        accepted_event_message = (
            f"{selected_strategy} {side} signal accepted for "
            f"{float(max_stake):.8g} USDT LIMIT quote"
        )
        accepted_ledger_started_monotonic = time.monotonic()
        local_id = self.ledger.record_accepted_signal(
            topic_id=int(reference["topic_id"]),
            market_id=market_id,
            side=side,
            token_id=token_id,
            signal_price=float(signal_price),
            account_type=self.account_type,
            signal_at=str(signal.get("signal_timestamp") or utc_iso()),
            strategy=ledger_strategy,
            max_stake_usdt=float(max_stake),
            requested_amount_wei=str(amount_in_wei),
            reliability_context=signal,
            event_message=accepted_event_message,
        )
        if local_id is None:
            self.ledger.record_event(
                "WARN",
                "DUPLICATE_MARKET_SIGNAL",
                "同一市場已有實單紀錄；不重送",
                market_id,
            )
            return

        accepted_ledger_finished_monotonic = time.monotonic()
        quote: dict[str, Any] | None = None
        quote_started_monotonic = time.monotonic()
        quote_network_seconds = 0.0
        quote_attempts = 0
        requote_triggered = False
        first_quote_network_seconds: float | None = None
        second_quote_network_seconds: float | None = None
        first_quote_average_price: float | None = None
        second_quote_average_price: float | None = None
        reprice_event_message: str | None = None
        if selected_strategy in {"PAIR_ARB_010", "PAIR_ARB_RISK_020"}:
            price_limit = maximum_reprice_limit
        elif selected_strategy.startswith("PAIR_ARB_"):
            # Pair strategies retain their exact leg-price consistency rules.
            price_limit = signal_price
        else:
            price_limit = min(
                max(signal_price, latest_verified_ask),
                maximum_reprice_limit,
            )
        price_limit_text = format(price_limit.normalize(), "f")
        latency_context = {
            "market_id": market_id,
            "selected_strategy": selected_strategy,
            "side": side,
            "live_enqueued_monotonic": enqueued_monotonic,
            "processing_started_monotonic": processing_started_monotonic,
            "accepted_ledger_started_monotonic": (
                accepted_ledger_started_monotonic
            ),
            "accepted_ledger_finished_monotonic": (
                accepted_ledger_finished_monotonic
            ),
            "quote_started_monotonic": quote_started_monotonic,
            "market_event_received_monotonic": self._signal_monotonic_seconds(
                signal, "market_event_received_monotonic_ns"
            ),
            "strategy_decision_started_monotonic": self._signal_monotonic_seconds(
                signal, "strategy_decision_started_monotonic_ns"
            ),
            "strategy_store_started_monotonic": self._signal_monotonic_seconds(
                signal, "strategy_store_started_monotonic_ns"
            ),
            "strategy_store_finished_monotonic": self._signal_monotonic_seconds(
                signal, "strategy_store_finished_monotonic_ns"
            ),
            "live_candidate_created_monotonic": self._signal_monotonic_seconds(
                signal, "live_candidate_created_monotonic_ns"
            ),
            "attempt_context": {
                "strategy": selected_strategy,
                "marketId": market_id,
                "side": side,
                "signalPrice": float(signal_price),
                "drawdownSignalSpotPrice": _float(
                    signal.get("drawdown_signal_spot_price")
                ),
                "drawdownSignalSpotAgeMs": _float(
                    signal.get("drawdown_signal_spot_age_ms")
                ),
                "drawdownSignalSpotSource": signal.get(
                    "drawdown_signal_spot_source"
                ),
                "drawdownReferencePrice": _float(
                    signal.get("drawdown_recheck_spot_price")
                ),
                "drawdownReferenceAgeMs": _float(
                    signal.get("drawdown_recheck_spot_age_ms")
                ),
                "drawdownReferenceSource": signal.get(
                    "drawdown_recheck_spot_source"
                ),
                "signalBookAgeMs": _float(
                    signal.get("signal_prediction_book_age_ms")
                ),
                "signalAsk": _float(signal.get("signal_prediction_ask")),
                "signalAskSize": _float(
                    signal.get("signal_prediction_ask_size")
                ),
                **depth_diagnostics,
            },
        }

        def request_quote(limit_text: str) -> dict[str, Any]:
            nonlocal quote_attempts
            nonlocal quote_network_seconds
            nonlocal first_quote_network_seconds
            nonlocal second_quote_network_seconds
            quote_attempts += 1
            attempt_number = quote_attempts
            network_started = time.monotonic()
            try:
                return client.get_quote(
                    wallet_address=wallet_address,
                    token_id=token_id,
                    amount_in_wei=str(amount_in_wei),
                    price_limit=limit_text,
                    slippage_bps=100,
                    fee_rate_bps=int(reference.get("fee_bps") or 200),
                    funding_source="MPC",
                )
            finally:
                elapsed_seconds = max(0.0, time.monotonic() - network_started)
                quote_network_seconds += elapsed_seconds
                if attempt_number == 1:
                    first_quote_network_seconds = elapsed_seconds
                elif attempt_number == 2:
                    second_quote_network_seconds = elapsed_seconds

        try:
            quote = request_quote(price_limit_text)
            first_quote_average_price = _float(quote.get("averagePrice"))
            safe, reason = self._quote_is_safe(
                quote,
                token_id=token_id,
                price_limit=price_limit,
                amount_in_wei=amount_in_wei,
            )
            if safe:
                safe, reason = self._research_quote_range_is_safe(
                    quote, selected_strategy
                )
            if safe:
                safe, reason = self._reliability_quote_is_safe(
                    quote, selected_strategy, rules
                )
            quote_average = _decimal(quote.get("averagePrice"))
            if (
                not safe
                and quote_average is not None
                and quote_average > price_limit + Decimal("0.00000001")
            ):
                if (
                    maximum_reprice_limit <= price_limit
                    or quote_average
                    > maximum_reprice_limit + Decimal("0.00000001")
                ):
                    raise ValueError(
                        f"quote average price {format(quote_average.normalize(), 'f')} "
                        "exceeds the permitted execution limit "
                        f"{format(maximum_reprice_limit.normalize(), 'f')}"
                    )
                # Refresh once at the complete user-authorized ceiling.  Using
                # the first quote's exact average here can immediately become
                # stale again before the second quote reaches us.
                refreshed_limit = maximum_reprice_limit
                old_limit_text = price_limit_text
                price_limit = refreshed_limit
                price_limit_text = format(refreshed_limit.normalize(), "f")
                reprice_event_message = (
                    f"quote average {format(quote_average.normalize(), 'f')} exceeded "
                    f"limit {old_limit_text}; refreshed once at {price_limit_text}"
                )
                requote_triggered = True
                quote = request_quote(price_limit_text)
                second_quote_average_price = _float(quote.get("averagePrice"))
                safe, reason = self._quote_is_safe(
                    quote,
                    token_id=token_id,
                    price_limit=price_limit,
                    amount_in_wei=amount_in_wei,
                )
                if safe:
                    safe, reason = self._research_quote_range_is_safe(
                        quote, selected_strategy
                    )
                if safe:
                    safe, reason = self._reliability_quote_is_safe(
                        quote, selected_strategy, rules
                    )
            if not safe:
                raise ValueError(reason)
            expire_at = int(quote.get("expireAt") or 0)
            if expire_at and expire_at <= client.server_timestamp_ms() + 250:
                raise ValueError("quote expired before placement")
            with self.lock:
                self.quote_access = "VERIFIED"
        except Exception as exc:
            failed_at_monotonic = time.monotonic()
            quote_fields: dict[str, Any] = {}
            if isinstance(quote, dict):
                quote_fields = {
                    "quote_average_price": _float(quote.get("averagePrice")),
                    "quote_amount_in_wei": (
                        str(quote.get("amountIn"))
                        if quote.get("amountIn") is not None
                        else None
                    ),
                    "quote_amount_out_wei": (
                        str(quote.get("amountOut"))
                        if quote.get("amountOut") is not None
                        else None
                    ),
                    "quote_expires_at": int(quote.get("expireAt") or 0) or None,
                }
            self.ledger.update_order(
                local_id,
                status="REJECTED",
                error_kind="QUOTE_REJECTED",
                error_message=str(exc)[:500],
                **quote_fields,
            )
            with self.lock:
                self.last_error = str(exc)[:400]
                latency = self._order_latency_payload(
                    {
                        **latency_context,
                        "quote_finished_monotonic": failed_at_monotonic,
                        "quote_network_seconds": quote_network_seconds,
                        "quote_attempts": quote_attempts,
                        "requote_triggered": requote_triggered,
                        "first_quote_average_price": first_quote_average_price,
                        "second_quote_average_price": second_quote_average_price,
                        "first_quote_network_seconds": (
                            first_quote_network_seconds
                        ),
                        "second_quote_network_seconds": (
                            second_quote_network_seconds
                        ),
                        "final_quote_average_price": (
                            _float(quote.get("averagePrice"))
                            if isinstance(quote, dict)
                            else None
                        ),
                    },
                    outcome="QUOTE_REJECTED",
                    placement_started_monotonic=None,
                    placement_finished_monotonic=failed_at_monotonic,
                )
                self.last_order_latency = latency
                self.last_quote_attempt = self._quote_attempt_payload(latency)
            self._persist_attempt_telemetry(local_id, latency)
            self.ledger.record_event(
                "ERROR", "QUOTE_REJECTED", str(exc)[:400], market_id
            )
            if reprice_event_message is not None:
                self.ledger.record_event(
                    "WARN",
                    "QUOTE_REPRICE_REQUESTED",
                    reprice_event_message,
                    market_id,
                )
            return None

        prepared = {
            **latency_context,
            "local_id": local_id,
            "client": client,
            "wallet_address": wallet_address,
            "wallet_id": wallet_id,
            "quote": quote,
            "price_limit_text": price_limit_text,
            "requested_amount_wei": amount_in_wei,
            "expected_amount_out_wei": int(
                (
                    (
                        _decimal(signal.get("_pair_dynamic_target_shares"))
                        if selected_strategy == "PAIR_ARB_010"
                        and _decimal(
                            signal.get("_pair_dynamic_target_shares")
                        ) is not None
                        else max_stake / signal_price
                    )
                    * Decimal(10**18)
                ).to_integral_value(rounding=ROUND_DOWN)
            ),
            "side": side,
            "market_id": market_id,
            "fee_bps": int(reference.get("fee_bps") or 200),
            "selected_strategy": selected_strategy,
            "token_id": token_id,
            "quote_rtt_seconds": max(
                0.0, time.monotonic() - quote_started_monotonic
            ),
            "quote_finished_monotonic": time.monotonic(),
            "quote_network_seconds": quote_network_seconds,
            "quote_attempts": quote_attempts,
            "requote_triggered": requote_triggered,
            "first_quote_average_price": first_quote_average_price,
            "second_quote_average_price": second_quote_average_price,
            "first_quote_network_seconds": first_quote_network_seconds,
            "second_quote_network_seconds": second_quote_network_seconds,
            "final_quote_average_price": _float(quote.get("averagePrice")),
            "queue_delay_seconds": queue_delay_seconds,
            "reprice_event_message": reprice_event_message,
        }
        if defer_placement:
            self.ledger.update_order(
                local_id,
                status="QUOTE_ACCEPTED",
                quote_average_price=_float(quote.get("averagePrice")),
                quote_amount_in_wei=str(quote.get("amountIn")),
                quote_amount_out_wei=str(quote.get("amountOut")),
                quote_expires_at=int(quote.get("expireAt") or 0) or None,
            )
            if reprice_event_message is not None:
                self.ledger.record_event(
                    "WARN",
                    "QUOTE_REPRICE_REQUESTED",
                    reprice_event_message,
                    market_id,
                )
            return prepared
        self._place_accepted_quote(prepared)
        return prepared

    def _place_accepted_quote(self, prepared: dict[str, Any]) -> bool:
        local_id = int(prepared["local_id"])
        client = prepared["client"]
        wallet_address = str(prepared["wallet_address"])
        wallet_id = str(prepared["wallet_id"])
        quote = prepared["quote"]
        price_limit_text = str(prepared["price_limit_text"])
        side = str(prepared["side"])
        market_id = int(prepared["market_id"])
        selected_strategy = str(prepared["selected_strategy"])

        # Persist PLACE_ATTEMPTED before the network call.  From this point on
        # the market is permanently deduplicated even if the response is lost.
        attempted_at = utc_iso()
        self.ledger.update_order(
            local_id,
            status="PLACE_ATTEMPTED",
            attempted_at=attempted_at,
            quote_average_price=_float(quote.get("averagePrice")),
            quote_amount_in_wei=str(quote.get("amountIn")),
            quote_amount_out_wei=str(quote.get("amountOut")),
            quote_expires_at=int(quote.get("expireAt") or 0) or None,
        )
        placement_started_monotonic = time.monotonic()
        try:
            placed = client.place_limit_order(
                wallet_address=wallet_address,
                wallet_id=wallet_id,
                quote_id=str(quote["quoteId"]),
                price_limit=price_limit_text,
                slippage_bps=100,
                account_type=self.account_type,
                funding_source="MPC",
            )
            order_id = str(placed.get("orderId") or "")
            if not order_id:
                raise ApiTransportError(
                    "place-order response did not contain orderId"
                )
        except Exception as exc:
            placement_finished_monotonic = time.monotonic()
            ambiguous = isinstance(exc, ApiTransportError) or (
                isinstance(exc, ApiHttpError) and exc.status_code >= 500
            )
            status = "AMBIGUOUS" if ambiguous else "REJECTED"
            detail = str(exc)[:500]
            self.ledger.update_order(
                local_id,
                status=status,
                error_kind=("PLACEMENT_AMBIGUOUS" if ambiguous else "PLACEMENT_REJECTED"),
                error_message=detail,
            )
            with self.lock:
                self.last_error = detail[:400]
                latency_outcome = (
                    "PLACEMENT_AMBIGUOUS"
                    if ambiguous
                    else "PLACEMENT_REJECTED"
                )
                latency = self._order_latency_payload(
                    prepared,
                    outcome=latency_outcome,
                    placement_started_monotonic=placement_started_monotonic,
                    placement_finished_monotonic=placement_finished_monotonic,
                )
                self.last_order_latency = latency
                self.last_quote_attempt = self._quote_attempt_payload(latency)
                if "-31003" in detail or "SAS authorization required" in detail:
                    self.sas_status = "BLOCKED_SAS_REQUIRED"
                    self.status = "BLOCKED_SAS_REQUIRED"
                    self.armed = False
            self._persist_attempt_telemetry(local_id, latency)
            self.ledger.record_event(
                "ERROR",
                "PLACEMENT_AMBIGUOUS" if ambiguous else "PLACEMENT_REJECTED",
                (
                    "送單結果不確定，基於防重複原則不會重送：" + detail
                    if ambiguous else detail
                ),
                market_id,
            )
            if prepared.get("reprice_event_message"):
                self.ledger.record_event(
                    "WARN",
                    "QUOTE_REPRICE_REQUESTED",
                    str(prepared["reprice_event_message"]),
                    market_id,
                )
            return False

        placement_finished_monotonic = time.monotonic()
        submitted_at = utc_iso()
        self.ledger.update_order(
            local_id,
            status="SUBMITTED",
            order_id=order_id,
            submitted_at=submitted_at,
            response_json=_safe_payload(placed),
        )
        with self.lock:
            self.sas_status = "VERIFIED"
            self.status = "LIVE"
            self.last_error = None
            self.next_order_sync = 0.0
            latency = self._order_latency_payload(
                prepared,
                outcome="SUBMITTED",
                placement_started_monotonic=placement_started_monotonic,
                placement_finished_monotonic=placement_finished_monotonic,
            )
            self.last_order_latency = latency
            self.last_quote_attempt = self._quote_attempt_payload(latency)
        self._persist_attempt_telemetry(local_id, latency)
        self.ledger.record_event(
            "INFO",
            "ORDER_SUBMITTED",
            f"Binance 已接受 {selected_strategy} {side} LIMIT GTC 訂單 #{order_id}",
            market_id,
        )
        if prepared.get("reprice_event_message"):
            self.ledger.record_event(
                "WARN",
                "QUOTE_REPRICE_REQUESTED",
                str(prepared["reprice_event_message"]),
                market_id,
            )
        return True

    @staticmethod
    def _signal_monotonic_seconds(
        signal: dict[str, Any], key: str
    ) -> float | None:
        try:
            value = int(signal.get(key))
        except (TypeError, ValueError):
            return None
        return value / 1_000_000_000 if value > 0 else None

    @staticmethod
    def _elapsed_ms(start: Any, finish: Any) -> float | None:
        try:
            start_value = float(start)
            finish_value = float(finish)
        except (TypeError, ValueError):
            return None
        return max(0.0, finish_value - start_value) * 1000

    @staticmethod
    def _quote_attempt_payload(latency: dict[str, Any]) -> dict[str, Any]:
        return {
            key: latency.get(key)
            for key in (
                "marketId",
                "strategy",
                "side",
                "quoteAttempts",
                "requoteTriggered",
                "firstQuoteAveragePrice",
                "secondQuoteAveragePrice",
                "firstQuoteNetworkMs",
                "secondQuoteNetworkMs",
                "finalQuoteAveragePrice",
                "finalOutcome",
                "measuredAt",
            )
        }

    @staticmethod
    def _order_latency_payload(
        prepared: dict[str, Any],
        *,
        outcome: str,
        placement_started_monotonic: float | None,
        placement_finished_monotonic: float,
    ) -> dict[str, Any]:
        enqueued = float(
            prepared.get("live_enqueued_monotonic")
            or prepared.get("processing_started_monotonic")
            or placement_started_monotonic
        )
        processing_started = float(
            prepared.get("processing_started_monotonic") or enqueued
        )
        quote_started = float(
            prepared.get("quote_started_monotonic") or processing_started
        )
        quote_finished = float(
            prepared.get("quote_finished_monotonic")
            or placement_started_monotonic
            or placement_finished_monotonic
        )
        event_received = prepared.get("market_event_received_monotonic")
        decision_started = prepared.get("strategy_decision_started_monotonic")
        store_started = prepared.get("strategy_store_started_monotonic")
        store_finished = prepared.get("strategy_store_finished_monotonic")
        candidate_created = prepared.get("live_candidate_created_monotonic")
        ledger_started = prepared.get("accepted_ledger_started_monotonic")
        ledger_finished = prepared.get("accepted_ledger_finished_monotonic")
        attempt_context = prepared.get("attempt_context")
        if not isinstance(attempt_context, dict):
            attempt_context = {}
        return {
            **attempt_context,
            "marketId": int(prepared["market_id"]),
            "strategy": str(prepared["selected_strategy"]),
            "side": str(prepared["side"]),
            "outcome": str(outcome),
            "queueMs": max(0.0, processing_started - enqueued) * 1000,
            "preQuoteMs": max(0.0, quote_started - processing_started) * 1000,
            "quotePhaseMs": max(0.0, quote_finished - quote_started) * 1000,
            "quoteNetworkMs": max(
                0.0, float(prepared.get("quote_network_seconds") or 0.0)
            ) * 1000,
            "quoteToPlaceMs": LiveM0WEngine._elapsed_ms(
                quote_finished, placement_started_monotonic
            ),
            "placeNetworkMs": LiveM0WEngine._elapsed_ms(
                placement_started_monotonic, placement_finished_monotonic
            ),
            "totalMs": max(
                0.0, placement_finished_monotonic - enqueued
            ) * 1000,
            "marketEventToDecisionStartMs": LiveM0WEngine._elapsed_ms(
                event_received, decision_started
            ),
            "decisionAndStoreMs": LiveM0WEngine._elapsed_ms(
                decision_started, store_finished
            ),
            "storeMs": LiveM0WEngine._elapsed_ms(store_started, store_finished),
            "candidateToLiveQueueMs": LiveM0WEngine._elapsed_ms(
                candidate_created, enqueued
            ),
            "marketEventToLiveQueueMs": LiveM0WEngine._elapsed_ms(
                event_received, enqueued
            ),
            "marketEventToQuoteStartMs": LiveM0WEngine._elapsed_ms(
                event_received, quote_started
            ),
            "marketEventToPlaceStartMs": LiveM0WEngine._elapsed_ms(
                event_received, placement_started_monotonic
            ),
            # For pre-placement failures this is event-to-final-failure; the
            # outcome field makes that distinction explicit.
            "eventToPlaceResponseMs": LiveM0WEngine._elapsed_ms(
                event_received, placement_finished_monotonic
            ),
            "preLedgerMs": LiveM0WEngine._elapsed_ms(
                processing_started, ledger_started
            ),
            "acceptedLedgerMs": LiveM0WEngine._elapsed_ms(
                ledger_started, ledger_finished
            ),
            "quoteAttempts": int(prepared.get("quote_attempts") or 0),
            "requoteTriggered": bool(prepared.get("requote_triggered")),
            "firstQuoteAveragePrice": prepared.get(
                "first_quote_average_price"
            ),
            "secondQuoteAveragePrice": prepared.get(
                "second_quote_average_price"
            ),
            "firstQuoteNetworkMs": (
                max(
                    0.0,
                    float(prepared.get("first_quote_network_seconds")),
                )
                * 1000
                if prepared.get("first_quote_network_seconds") is not None
                else None
            ),
            "secondQuoteNetworkMs": (
                max(
                    0.0,
                    float(prepared.get("second_quote_network_seconds")),
                )
                * 1000
                if prepared.get("second_quote_network_seconds") is not None
                else None
            ),
            "finalQuoteAveragePrice": prepared.get(
                "final_quote_average_price"
            ),
            "finalOutcome": str(outcome),
            "measuredAt": utc_iso(),
        }

    def _enforce_pair_execution_safety(self) -> None:
        """Persistently stop live trading after a one-sided pair execution."""
        with self.lock:
            if not self.runtime_enabled:
                return
            selected = {
                str(value) for value in self.live_rules["strategies"]
                if str(value).startswith("PAIR_ARB_")
            }
        if not selected:
            return

        grouped: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
        for row in self.ledger.recent_pair_orders():
            strategy_text = str(row.get("strategy") or "")
            try:
                base_strategy, ledger_side = strategy_text.rsplit(":", 1)
            except ValueError:
                continue
            side = str(row.get("side") or ledger_side).upper()
            if base_strategy not in selected or side not in {"UP", "DOWN"}:
                continue
            grouped.setdefault(
                (base_strategy, int(row["market_id"])), {}
            ).setdefault(side, row)

        now = datetime.now(timezone.utc)
        for (strategy, market_id), legs in grouped.items():
            if set(legs) != {"UP", "DOWN"}:
                continue
            incident = self.ledger.pair_incident(strategy, market_id)
            if incident is not None and str(incident["status"]).upper() in {
                "HEDGED", "SETTLED", "RESOLVED",
            }:
                continue
            if self.ledger.pair_market_has_settlement(strategy, market_id):
                self.ledger.record_pair_incident(
                    strategy=strategy,
                    market_id=market_id,
                    status="SETTLED",
                    reason="official strategy settlement closed the historical pair incident",
                )
                continue
            timestamps: list[datetime] = []
            for row in legs.values():
                raw = row.get("submitted_at") or row.get("attempted_at")
                if not raw:
                    continue
                try:
                    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    timestamps.append(
                        parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                    )
                except ValueError:
                    continue
            if not timestamps:
                continue
            age_seconds = (now - max(timestamps)).total_seconds()
            if age_seconds < PAIR_ARB_FILL_MISMATCH_GRACE_SECONDS:
                continue

            def has_fill(row: dict[str, Any]) -> bool:
                return (
                    float(_float(row.get("filled_usdt_amount")) or 0.0) > 0
                    or float(_float(row.get("filled_share_qty")) or 0.0) > 0
                )

            up_filled = has_fill(legs["UP"])
            down_filled = has_fill(legs["DOWN"])
            if up_filled == down_filled:
                if up_filled and incident is not None:
                    self.ledger.record_pair_incident(
                        strategy=strategy,
                        market_id=market_id,
                        status="HEDGED",
                        reason="both pair legs are now filled",
                    )
                continue

            filled_side = "UP" if up_filled else "DOWN"
            missing_side = "DOWN" if up_filled else "UP"
            missing_status = str(
                legs[missing_side].get("status") or "UNKNOWN"
            ).upper()
            reason = (
                f"{strategy} market {market_id} has one-sided execution: "
                f"{filled_side} filled while {missing_side} is {missing_status}; "
                "live trading was paused before another market could open"
            )
            self.ledger.record_pair_incident(
                strategy=strategy,
                market_id=market_id,
                status="ACTIVE",
                reason=reason,
                filled_side=filled_side,
                missing_side=missing_side,
            )
            self.ledger.set_runtime_enabled(False)
            with self.lock:
                self.runtime_enabled = False
                self.armed = False
                self.status = "PAUSED_PAIR_FILL_MISMATCH"
                self.last_error = reason[:400]
            self.ledger.record_event(
                "ERROR", "PAIR_FILL_MISMATCH_PAUSED", reason, market_id
            )
            return

    @staticmethod
    def _best_bid(book: dict[str, Any]) -> Decimal | None:
        prices: list[Decimal] = []
        for level in book.get("bids") or []:
            price = _decimal(level.get("price")) if isinstance(level, dict) else None
            size = _decimal(level.get("size")) if isinstance(level, dict) else None
            if price is not None and size is not None and price > 0 and size > 0:
                prices.append(price)
        return max(prices) if prices else None

    @staticmethod
    def _position_shares(position: dict[str, Any]) -> Decimal | None:
        for key in ("shares", "availableShares", "quantity"):
            shares = _decimal(position.get(key))
            if shares is not None and shares >= 0:
                return shares
        return None

    @staticmethod
    def _insurance_loss_bounds(
        legs: dict[str, dict[str, Any]],
    ) -> tuple[Decimal, dict[str, Decimal]] | None:
        costs: list[Decimal] = []
        payouts: dict[str, Decimal] = {}
        for side in ("UP", "DOWN"):
            row = legs[side]
            if str(row.get("status") or "").upper() != "FILLED":
                return None
            cost = _decimal(row.get("filled_usdt_amount"))
            shares = _decimal(row.get("filled_share_qty"))
            if cost is None or shares is None or cost <= 0 or shares <= 0:
                return None
            # Exchange history display values are rounded. Subtract from cost
            # and add to payout so this test can only understate a locked loss.
            costs.append(max(Decimal(0), cost - PAIR_INSURANCE_ROUNDING_GUARD))
            payouts[side] = shares + PAIR_INSURANCE_ROUNDING_GUARD
        return sum(costs, Decimal(0)), payouts

    def _submit_pair_insurance(
        self,
        insurance: dict[str, Any],
        *,
        client: BinancePredictionTradingClient,
        wallet_address: str,
        wallet_id: str,
        fee_bps: int,
        sell_shares: Decimal,
        price_limit: Decimal,
        high_bid: Decimal,
        cost_lower: Decimal,
        payouts: dict[str, Decimal],
    ) -> None:
        insurance_id = int(insurance["id"])
        market_id = int(insurance["market_id"])
        amount_in_wei = int(
            (sell_shares * Decimal(10**18)).to_integral_value(
                rounding=ROUND_DOWN
            )
        )
        if amount_in_wei <= 0:
            return
        price_limit_text = format(price_limit.normalize(), "f")
        try:
            quote = client.get_quote(
                wallet_address=wallet_address,
                token_id=str(insurance["token_id"]),
                amount_in_wei=str(amount_in_wei),
                price_limit=price_limit_text,
                slippage_bps=100,
                fee_rate_bps=int(fee_bps),
                funding_source="MPC",
                side="SELL",
                order_type="LIMIT",
            )
            safe, reason = self._quote_is_safe(
                quote,
                token_id=str(insurance["token_id"]),
                price_limit=price_limit,
                amount_in_wei=amount_in_wei,
                side="SELL",
            )
            quoted_input = _decimal(quote.get("amountIn"))
            capacity = (
                quoted_input / Decimal(amount_in_wei)
                if quoted_input is not None else Decimal(0)
            )
            if not safe:
                raise ValueError(reason)
            if capacity < PAIR_ARB_MIN_QUOTE_CAPACITY_RATIO:
                raise ValueError(
                    f"insurance SELL quote capacity {capacity:.1%} is below "
                    f"{PAIR_ARB_MIN_QUOTE_CAPACITY_RATIO:.0%}"
                )
            expiry = int(quote.get("expireAt") or 0)
            if expiry and expiry <= client.server_timestamp_ms() + 250:
                raise ValueError("insurance SELL quote expired before placement")
        except Exception as exc:
            detail = str(exc)[:500]
            self.ledger.update_pair_insurance(
                insurance_id,
                status="REJECTED",
                error_kind="SELL_QUOTE_REJECTED",
                error_message=detail,
            )
            self.ledger.record_event(
                "ERROR", "PAIR_INSURANCE_QUOTE_REJECTED", detail, market_id
            )
            return

        attempted_at = utc_iso()
        self.ledger.update_pair_insurance(
            insurance_id,
            status="PLACE_ATTEMPTED",
            attempted_at=attempted_at,
            sell_shares=float(sell_shares),
            price_limit=float(price_limit),
            high_bid=float(high_bid),
            guaranteed_cost_lower=float(cost_lower),
            up_payout_upper=float(payouts["UP"]),
            down_payout_upper=float(payouts["DOWN"]),
        )
        try:
            placed = client.place_limit_order(
                wallet_address=wallet_address,
                wallet_id=wallet_id,
                quote_id=str(quote["quoteId"]),
                price_limit=price_limit_text,
                slippage_bps=100,
                account_type=self.account_type,
                funding_source="MPC",
            )
            order_id = str(placed.get("orderId") or "")
            if not order_id:
                raise ApiTransportError(
                    "insurance place-order response did not contain orderId"
                )
        except Exception as exc:
            ambiguous = isinstance(exc, ApiTransportError) or (
                isinstance(exc, ApiHttpError) and exc.status_code >= 500
            )
            detail = str(exc)[:500]
            self.ledger.update_pair_insurance(
                insurance_id,
                status="AMBIGUOUS" if ambiguous else "REJECTED",
                error_kind=(
                    "SELL_PLACEMENT_AMBIGUOUS"
                    if ambiguous else "SELL_PLACEMENT_REJECTED"
                ),
                error_message=detail,
            )
            self.ledger.record_event(
                "ERROR",
                (
                    "PAIR_INSURANCE_PLACEMENT_AMBIGUOUS"
                    if ambiguous else "PAIR_INSURANCE_PLACEMENT_REJECTED"
                ),
                detail,
                market_id,
            )
            return

        self.ledger.update_pair_insurance(
            insurance_id,
            status="SUBMITTED",
            submitted_at=utc_iso(),
            order_id=order_id,
            response_json=_safe_payload(placed),
            error_kind=None,
            error_message=None,
        )
        with self.lock:
            self.next_order_sync = 0.0
        self.ledger.record_event(
            "WARN",
            "PAIR_INSURANCE_SELL_SUBMITTED",
            (
                f"{insurance['strategy']} market {market_id}: guaranteed loss "
                f"confirmed in both outcomes; sold {sell_shares} "
                f"{insurance['low_side']} shares at LIMIT {price_limit_text} "
                f"after {PAIR_INSURANCE_DELAY_SECONDS:.0f}s confirmation"
            ),
            market_id,
        )

    def _run_pair_loss_insurance(self) -> None:
        """Sell only a verified low leg whose pair loses in both outcomes."""
        with self.lock:
            runtime_enabled = self.runtime_enabled
            client = self.client
            wallet_address = self.wallet_address
            wallet_id = self.wallet_id
        if not runtime_enabled or client is None or not wallet_address or not wallet_id:
            return
        reference = self.current_market() or {}
        try:
            market_id = int(reference["market_id"])
            end_ms = int(reference["end_ms"])
            fee_bps = int(reference.get("fee_bps") or 200)
            seconds_left = (
                Decimal(end_ms - client.server_timestamp_ms()) / Decimal(1000)
            )
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return
        if seconds_left <= 0 or seconds_left > PAIR_INSURANCE_WINDOW_SECONDS:
            return

        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        for row in self.ledger.recent_pair_orders():
            if int(row.get("market_id") or 0) != market_id:
                continue
            strategy_text = str(row.get("strategy") or "")
            try:
                strategy, ledger_side = strategy_text.rsplit(":", 1)
            except ValueError:
                continue
            side = str(row.get("side") or ledger_side).upper()
            if side in {"UP", "DOWN"}:
                grouped.setdefault(strategy, {})[side] = row

        for strategy, legs in grouped.items():
            if set(legs) != {"UP", "DOWN"}:
                continue
            bounds = self._insurance_loss_bounds(legs)
            if bounds is None:
                continue
            cost_lower, payouts = bounds
            if cost_lower <= max(payouts.values()):
                continue
            try:
                bids = {
                    side: self._best_bid(
                        client.orderbook(market_id, str(legs[side]["token_id"]))
                    )
                    for side in ("UP", "DOWN")
                }
                if bids["UP"] is None or bids["DOWN"] is None:
                    continue
                high_side = "UP" if bids["UP"] > bids["DOWN"] else "DOWN"
                low_side = "DOWN" if high_side == "UP" else "UP"
                if bids[high_side] < PAIR_INSURANCE_HIGH_BID_MIN:
                    continue
                positions: dict[str, Decimal] = {}
                for side in ("UP", "DOWN"):
                    payload = client.position_by_token(
                        wallet_address, str(legs[side]["token_id"])
                    )
                    position = self._position_record(payload)
                    shares = self._position_shares(position)
                    ledger_shares = _decimal(legs[side].get("filled_share_qty"))
                    if (
                        shares is None or shares <= 0 or ledger_shares is None
                        or abs(shares - ledger_shares) > Decimal("0.02")
                    ):
                        positions = {}
                        break
                    positions[side] = shares
                if set(positions) != {"UP", "DOWN"}:
                    continue
                # Re-prove the loss from the live wallet quantities immediately
                # before arming or placing. Equality is deliberately not enough.
                live_payouts = {
                    side: positions[side] + Decimal("0.00000001")
                    for side in ("UP", "DOWN")
                }
                if cost_lower <= max(live_payouts.values()):
                    continue

                insurance = self.ledger.pair_insurance(strategy, market_id)
                if insurance is None:
                    insurance = self.ledger.arm_pair_insurance(
                        strategy=strategy,
                        market_id=market_id,
                        low_side=low_side,
                        token_id=str(legs[low_side]["token_id"]),
                        sell_shares=positions[low_side],
                        price_limit=bids[low_side],
                        high_bid=bids[high_side],
                        guaranteed_cost_lower=cost_lower,
                        up_payout_upper=live_payouts["UP"],
                        down_payout_upper=live_payouts["DOWN"],
                    )
                    self.ledger.record_event(
                        "WARN",
                        "PAIR_INSURANCE_ARMED",
                        (
                            f"{strategy} market {market_id}: both settlement "
                            f"outcomes are guaranteed below cost; confirming "
                            f"{low_side} exit for {PAIR_INSURANCE_DELAY_SECONDS:.0f}s"
                        ),
                        market_id,
                    )
                    continue
                if str(insurance.get("status") or "").upper() != "ARMED":
                    continue
                if str(insurance.get("low_side") or "").upper() != low_side:
                    continue
                armed_at = datetime.fromisoformat(
                    str(insurance["armed_at"]).replace("Z", "+00:00")
                )
                if armed_at.tzinfo is None:
                    armed_at = armed_at.replace(tzinfo=timezone.utc)
                if (
                    datetime.now(timezone.utc) - armed_at
                ).total_seconds() < PAIR_INSURANCE_DELAY_SECONDS:
                    continue
                self._submit_pair_insurance(
                    insurance,
                    client=client,
                    wallet_address=wallet_address,
                    wallet_id=wallet_id,
                    fee_bps=fee_bps,
                    sell_shares=positions[low_side],
                    price_limit=bids[low_side],
                    high_bid=bids[high_side],
                    cost_lower=cost_lower,
                    payouts=live_payouts,
                )
            except Exception as exc:
                self.ledger.record_event(
                    "ERROR",
                    "PAIR_INSURANCE_CHECK_FAILED",
                    str(exc)[:400],
                    market_id,
                )

    def _sync_orders(self) -> None:
        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
        pending = self.ledger.pending_orders()
        pending_insurance = self.ledger.pending_pair_insurance()
        pending_manual_exits = self.ledger.pending_manual_exits()
        if client is None or not wallet_address:
            return
        try:
            manual_settlements = (
                self.ledger.reconcile_filled_manual_exit_settlements()
            )
            for settlement in manual_settlements:
                self.ledger.record_event(
                    "INFO",
                    "MANUAL_EXIT_SETTLED",
                    (
                        f"{settlement['strategy']} manual exit "
                        f"{settlement['result']} confirmed; strategy PnL "
                        f"{float(settlement['pnl_usdt']):+.8f} USDT"
                    ),
                    int(settlement["market_id"]),
                )
            if pending or pending_insurance or pending_manual_exits:
                payload = client.order_history(wallet_address, limit=100)
                with self.lock:
                    self.consecutive_order_sync_transport_errors = 0
                exchange_orders = {
                    str(order.get("orderId")): order
                    for order in (payload.get("orders") or [])
                    if order.get("orderId") is not None
                }
                for local in pending:
                    order_id = str(local.get("order_id") or "")
                    if order_id and order_id in exchange_orders:
                        self.ledger.sync_exchange_order(
                            int(local["id"]), exchange_orders[order_id]
                        )
                for insurance in pending_insurance:
                    order_id = str(insurance.get("order_id") or "")
                    if order_id and order_id in exchange_orders:
                        exchange_order = exchange_orders[order_id]
                        self.ledger.update_pair_insurance(
                            int(insurance["id"]),
                            status=str(
                                exchange_order.get("status") or "UNKNOWN"
                            ).upper(),
                            error_message=(
                                str(exchange_order.get("errorMessage"))[:500]
                                if exchange_order.get("errorMessage") else None
                            ),
                            response_json=_safe_payload(exchange_order),
                        )
                for manual_exit in pending_manual_exits:
                    order_id = str(manual_exit.get("order_id") or "")
                    if order_id and order_id in exchange_orders:
                        settlement = self.ledger.sync_manual_exit(
                            int(manual_exit["id"]), exchange_orders[order_id]
                        )
                        if settlement is not None:
                            self.ledger.record_event(
                                "INFO",
                                "MANUAL_EXIT_SETTLED",
                                (
                                    f"{settlement['strategy']} manual exit "
                                    f"{settlement['result']} confirmed; strategy PnL "
                                    f"{float(settlement['pnl_usdt']):+.8f} USDT"
                                ),
                                int(settlement["market_id"]),
                            )
            self._enforce_pair_execution_safety()
            with self.lock:
                self.last_order_sync_at = utc_iso()
                self.last_error = None
        except Exception as exc:
            request_restart = False
            with self.lock:
                self.last_error = f"order sync: {str(exc)[:300]}"
                if isinstance(exc, ApiTransportError):
                    self.consecutive_order_sync_transport_errors += 1
                    if (
                        self.consecutive_order_sync_transport_errors
                        >= self.order_sync_restart_threshold
                        and not self.restart_requested
                    ):
                        self.restart_requested = True
                        self.status = "RESTARTING"
                        self.armed = False
                        request_restart = True
                else:
                    self.consecutive_order_sync_transport_errors = 0
            self.ledger.record_event(
                "ERROR", "ORDER_SYNC_FAILED", str(exc)[:400]
            )
            if request_restart:
                reason = (
                    "order history transport failed "
                    f"{self.consecutive_order_sync_transport_errors} consecutive times: "
                    f"{str(exc)[:240]}"
                )
                self.ledger.record_event(
                    "ERROR", "API_RESTART_REQUESTED", reason[:400]
                )
                if self.restart_request is not None:
                    self.restart_request(reason)

    def _refresh_account(self) -> None:
        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
        if client is None or not wallet_address:
            return
        try:
            quota = client.quota_status()
            balance_payload = client.payment_option_balances()
            portfolio = client.portfolio(wallet_address, activeOnly=True)
            balances = [
                {
                    "accountType": str(item.get("accountType") or "UNKNOWN"),
                    "availableBalance": _float(item.get("availableBalanceDisplay")),
                    "enabled": bool(item.get("enabled")),
                }
                for item in (balance_payload.get("items") or [])
            ]
            with self.lock:
                self.quota = {
                    "dailyLimit": _float(quota.get("dailyLimit")),
                    "remainingDailyLimit": _float(
                        quota.get("remainingDailyLimit")
                    ),
                }
                self.balances = balances
                self.portfolio_state = {
                    "activePositionsCount": int(
                        portfolio.get("activePositionsCount") or 0
                    ),
                    "totalRealizedPnl": _float(portfolio.get("totalRealizedPnl")),
                    "totalUnrealizedPnl": _float(
                        portfolio.get("totalUnrealizedPnl")
                    ),
                    "totalPnl": _float(portfolio.get("totalPnl")),
                    "totalCostBasis": _float(portfolio.get("totalCostBasis")),
                    "totalCurrentValue": _float(
                        portfolio.get("totalCurrentValue")
                    ),
                }
        except Exception as exc:
            with self.lock:
                self.last_error = f"account refresh: {str(exc)[:300]}"

    def _sync_strategy_settlements(self) -> None:
        """Reconcile only this executor's fills against official token outcomes."""
        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
        if client is None or not wallet_address:
            return
        pending = self.ledger.unsettled_filled_orders()
        errors: list[str] = []
        for order in pending:
            try:
                payload = client.position_by_token(
                    wallet_address, str(order["token_id"])
                )
                position = self._position_record(payload)
                settlement = self.ledger.record_strategy_settlement(
                    order, position
                )
                if settlement is not None:
                    self.ledger.record_event(
                        "INFO",
                        "STRATEGY_SETTLED",
                        (
                            f"{order['strategy']} {settlement['result']} confirmed; "
                            f"strategy PnL {float(settlement['pnl_usdt']):+.8f} USDT"
                        ),
                        int(order["market_id"]),
                    )
            except Exception as exc:
                errors.append(
                    f"market {order.get('market_id')}: {str(exc)[:180]}"
                )
        with self.lock:
            self.last_settlement_sync_at = utc_iso()
            if errors:
                self.last_error = "settlement sync: " + "; ".join(errors[:3])

    @staticmethod
    def _position_record(payload: dict[str, Any]) -> dict[str, Any]:
        position = payload.get("position")
        return position if isinstance(position, dict) else payload

    @staticmethod
    def _position_confirms_claimed(position: dict[str, Any]) -> bool:
        status = str(position.get("positionStatus") or "").upper()
        return (
            status in {"CLAIMED", "REDEEMED"}
            or position.get("isClaimed") is True
        )

    def _claimable_positions(
        self,
        client: BinancePredictionTradingClient,
        wallet_address: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return only exact winning positions advertised as claimable."""
        offset = 0
        positions: list[dict[str, Any]] = []
        response_summary: dict[str, Any] = {}
        seen_tokens: set[str] = set()
        for _ in range(5):
            payload = client.positions(
                wallet_address,
                tab="PENDING_CLAIM",
                offset=offset,
                limit=100,
            )
            if not response_summary and isinstance(payload.get("summary"), dict):
                response_summary = dict(payload["summary"])
            page = payload.get("positions") or []
            for raw in page:
                if not isinstance(raw, dict):
                    continue
                token_id = str(raw.get("tokenId") or "")
                shares = _float(raw.get("shares")) or 0.0
                value = _float(raw.get("value")) or 0.0
                status = str(raw.get("positionStatus") or "").upper()
                if (
                    token_id
                    and token_id not in seen_tokens
                    and raw.get("canClaim") is True
                    and status == "PENDING_CLAIM"
                    and shares > 0
                    and value > 0
                    and int(raw.get("endDate") or 0) > 0
                ):
                    seen_tokens.add(token_id)
                    positions.append(dict(raw))
            if not payload.get("hasMore"):
                break
            step = int(payload.get("limit") or len(page) or 100)
            offset += max(1, step)
        return positions, response_summary

    def _complete_redeem(
        self,
        row: dict[str, Any],
        *,
        exchange_status: str,
        response_json: str | None = None,
    ) -> None:
        token_id = str(row["token_id"])
        completed_at = utc_iso()
        values: dict[str, Any] = {
            "status": "COMPLETED",
            "exchange_status": exchange_status or "CONFIRMED",
            "completed_at": completed_at,
            "error_kind": None,
            "error_message": None,
        }
        if response_json is not None:
            values["response_json"] = response_json
        self.ledger.update_redeem(token_id, **values)
        with self.lock:
            self.last_redeem_success_at = completed_at
            self.last_redeem_error = None
            self.auto_redeem_status = "READY"
            self.next_account_refresh = 0.0
        self.ledger.record_event(
            "INFO",
            "AUTO_REDEEM_COMPLETED",
            (
                f"Auto-redeem confirmed for market {row.get('market_id')}; "
                f"claimable value {float(row.get('claimable_value') or 0):.8f} USDT"
            ),
            int(row["market_id"]) if row.get("market_id") is not None else None,
        )

    def _reconcile_redeem(
        self,
        row: dict[str, Any],
        claimable_token_ids: set[str],
    ) -> None:
        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
        if client is None or not wallet_address:
            return
        token_id = str(row["token_id"])
        tx_hash = str(row.get("tx_hash") or "")
        if tx_hash:
            try:
                payload = client.redeem_status(wallet_address, tx_hash)
                exchange_status = str(payload.get("status") or "").upper()
                status_json = json.dumps(
                    {
                        key: payload[key]
                        for key in ("txHash", "status")
                        if key in payload
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if exchange_status in REDEEM_SUCCESS_STATUSES:
                    self._complete_redeem(
                        row,
                        exchange_status=exchange_status,
                        response_json=status_json,
                    )
                    return
                if exchange_status in REDEEM_FAILURE_STATUSES:
                    detail = f"Binance redeem status: {exchange_status}"
                    self.ledger.update_redeem(
                        token_id,
                        status="FAILED",
                        exchange_status=exchange_status,
                        error_kind="REDEEM_FAILED",
                        error_message=detail,
                        response_json=status_json,
                    )
                    with self.lock:
                        self.auto_redeem_status = "ATTENTION"
                        self.last_redeem_error = detail
                    return
                self.ledger.update_redeem(
                    token_id,
                    status="PENDING",
                    exchange_status=exchange_status or "PENDING",
                    response_json=status_json,
                )
            except Exception as exc:
                # A status-read failure cannot justify another redeem POST.
                self.ledger.update_redeem(
                    token_id,
                    error_kind="STATUS_CHECK_FAILED",
                    error_message=str(exc)[:500],
                )
                with self.lock:
                    self.auto_redeem_status = "RECONCILING"
                    self.last_redeem_error = f"redeem status: {str(exc)[:300]}"

        try:
            payload = client.position_by_token(wallet_address, token_id)
            position = self._position_record(payload)
            if self._position_confirms_claimed(position):
                self._complete_redeem(
                    row,
                    exchange_status=(
                        str(position.get("positionStatus") or "CLAIMED").upper()
                    ),
                )
                return
        except Exception as exc:
            self.ledger.update_redeem(
                token_id,
                error_kind="POSITION_RECONCILE_FAILED",
                error_message=str(exc)[:500],
            )
            return

        if not tx_hash and str(row.get("status") or "") == "ATTEMPTED":
            detail = (
                "redeem attempt was persisted without a trustworthy transaction "
                "identifier; automatic retry is disabled"
            )
            self.ledger.update_redeem(
                token_id,
                status="AMBIGUOUS",
                error_kind="REDEEM_AMBIGUOUS",
                error_message=detail,
            )
            with self.lock:
                self.auto_redeem_status = "ATTENTION"
                self.last_redeem_error = detail
        elif token_id not in claimable_token_ids and not tx_hash:
            # The list endpoint may be briefly stale or omit an item.  Absence
            # alone is not proof of settlement, so retain the no-retry state.
            self.ledger.update_redeem(
                token_id,
                error_kind="AWAITING_EXACT_CONFIRMATION",
                error_message="claim disappeared from list; exact position is not yet CLAIMED",
            )

    def _submit_redeem(
        self,
        row: dict[str, Any],
        claimable_token_ids: set[str],
    ) -> None:
        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
            wallet_id = self.wallet_id
        if client is None or not wallet_address or not wallet_id:
            return
        token_id = str(row["token_id"])
        attempted = self.ledger.begin_redeem_attempt(token_id)
        if attempted is None:
            return
        with self.lock:
            self.auto_redeem_status = "SUBMITTING"
        self.ledger.record_event(
            "INFO",
            "AUTO_REDEEM_ATTEMPTED",
            (
                f"Submitting one-token auto-redeem for market {row.get('market_id')} "
                f"after the {int(LIVE_AUTO_REDEEM_DELAY_SECONDS)}s delay"
            ),
            int(row["market_id"]) if row.get("market_id") is not None else None,
        )
        try:
            payload = client.batch_redeem(
                wallet_address=wallet_address,
                wallet_id=wallet_id,
                token_ids=[token_id],
                chain_id=str(row.get("chain_id") or "56"),
            )
        except Exception as exc:
            ambiguous = isinstance(exc, ApiTransportError) or (
                isinstance(exc, ApiHttpError) and exc.status_code >= 500
            )
            local_status = "AMBIGUOUS" if ambiguous else "FAILED"
            detail = str(exc)[:500]
            self.ledger.update_redeem(
                token_id,
                status=local_status,
                error_kind=(
                    "REDEEM_AMBIGUOUS" if ambiguous else "REDEEM_REJECTED"
                ),
                error_message=detail,
            )
            with self.lock:
                self.auto_redeem_status = "ATTENTION"
                self.last_redeem_error = detail[:400]
            self.ledger.record_event(
                "ERROR",
                "AUTO_REDEEM_AMBIGUOUS" if ambiguous else "AUTO_REDEEM_REJECTED",
                (
                    "Redeem result is uncertain and will not be retried: " + detail
                    if ambiguous
                    else detail
                ),
                int(row["market_id"]) if row.get("market_id") is not None else None,
            )
            return

        results = payload.get("results") or []
        result = results[0] if results and isinstance(results[0], dict) else {}
        batch_id = str(payload.get("batchId") or "")
        request_id = str(result.get("requestId") or "")
        tx_hash = str(result.get("txHash") or "")
        exchange_status = str(result.get("status") or "").upper()
        result_error = str(result.get("error") or "")
        safe_payload = _safe_redeem_payload(payload)
        if result_error or exchange_status in REDEEM_FAILURE_STATUSES:
            detail = result_error or f"Binance redeem status: {exchange_status}"
            self.ledger.update_redeem(
                token_id,
                status="FAILED",
                batch_id=batch_id or None,
                request_id=request_id or None,
                tx_hash=tx_hash or None,
                exchange_status=exchange_status or "FAILED",
                error_kind="REDEEM_REJECTED",
                error_message=detail[:500],
                response_json=safe_payload,
            )
            with self.lock:
                self.auto_redeem_status = "ATTENTION"
                self.last_redeem_error = detail[:400]
            return
        if not any((batch_id, request_id, tx_hash, exchange_status)):
            detail = "batch-redeem returned no trustworthy identifier or status"
            self.ledger.update_redeem(
                token_id,
                status="AMBIGUOUS",
                error_kind="REDEEM_AMBIGUOUS",
                error_message=detail,
                response_json=safe_payload,
            )
            with self.lock:
                self.auto_redeem_status = "ATTENTION"
                self.last_redeem_error = detail
            return

        self.ledger.update_redeem(
            token_id,
            status=(
                "COMPLETED"
                if exchange_status in REDEEM_SUCCESS_STATUSES
                else "PENDING"
            ),
            batch_id=batch_id or None,
            request_id=request_id or None,
            tx_hash=tx_hash or None,
            exchange_status=exchange_status or "SUBMITTED",
            completed_at=(
                utc_iso() if exchange_status in REDEEM_SUCCESS_STATUSES else None
            ),
            response_json=safe_payload,
        )
        refreshed = self.ledger.redeem_by_token(token_id)
        if refreshed is None:
            return
        if exchange_status in REDEEM_SUCCESS_STATUSES:
            self._complete_redeem(
                refreshed,
                exchange_status=exchange_status,
                response_json=safe_payload,
            )
        else:
            self._reconcile_redeem(refreshed, claimable_token_ids)

    def run_redeem_cycle(self) -> bool:
        """Scan, submit, and reconcile claims; safe to call while trading is paused."""
        if not self.auto_redeem_enabled:
            return False
        if not self.redeem_cycle_lock.acquire(blocking=False):
            return False
        try:
            with self.lock:
                client = self.client
                wallet_address = self.wallet_address
                wallet_id = self.wallet_id
                self.auto_redeem_status = "SCANNING"
            if client is None or not wallet_address or not wallet_id:
                with self.lock:
                    self.auto_redeem_status = "BLOCKED_PREFLIGHT"
                    self.last_redeem_error = "Prediction wallet connection is unavailable"
                return False

            positions, response_summary = self._claimable_positions(
                client, wallet_address
            )
            claimable_tokens = {str(item["tokenId"]) for item in positions}
            summary_amount = _float(response_summary.get("totalClaimableAmount"))
            with self.lock:
                self.claimable_count = len(positions)
                self.claimable_amount = (
                    float(summary_amount)
                    if summary_amount is not None
                    else sum(float(_float(item.get("value")) or 0.0) for item in positions)
                )
                self.last_redeem_scan_at = utc_iso()
            for position in positions:
                self.ledger.upsert_claimable_redeem(position)

            for pending in self.ledger.pending_redeems():
                self._reconcile_redeem(pending, claimable_tokens)

            now_ms = int(client.server_timestamp_ms())
            for candidate in self.ledger.redeem_candidates():
                if str(candidate["token_id"]) not in claimable_tokens:
                    continue
                eligible_ms = int(candidate["end_date_ms"]) + int(
                    LIVE_AUTO_REDEEM_DELAY_SECONDS * 1_000
                )
                if now_ms < eligible_ms:
                    continue
                self._submit_redeem(candidate, claimable_tokens)

            summary = self.ledger.redeem_summary()
            with self.lock:
                if summary["ambiguous"] or summary["failed"]:
                    self.auto_redeem_status = "ATTENTION"
                elif summary["pending"]:
                    self.auto_redeem_status = "PENDING"
                else:
                    self.auto_redeem_status = "READY"
                    self.last_redeem_error = None
            return True
        except Exception as exc:
            detail = str(exc)[:400]
            with self.lock:
                self.auto_redeem_status = "ERROR"
                self.last_redeem_error = detail
                self.last_redeem_scan_at = utc_iso()
            self.ledger.record_event("ERROR", "AUTO_REDEEM_SCAN_FAILED", detail)
            return False
        finally:
            self.redeem_cycle_lock.release()

    def _verify_quote_access(self) -> None:
        """Obtain one expiring LIMIT quote without ever placing it."""
        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
            already_verified = self.quote_access == "VERIFIED"
            runtime_enabled = self.runtime_enabled
            armed = self.armed
            max_stake = Decimal(str(self.live_rules["maxStakeUsdt"]))
            amount_in_wei = _stake_amount_wei(max_stake)
        if (
            client is None
            or not wallet_address
            or already_verified
            or not runtime_enabled
            or not armed
        ):
            return
        reference = self.current_market()
        if not reference:
            return
        market_id = int(reference.get("market_id") or 0)
        try:
            chosen_token = ""
            chosen_price = ""
            for side in ("up", "down"):
                token_id = str(reference.get(f"{side}_token_id") or "")
                if not token_id:
                    continue
                book = client.orderbook(market_id, token_id)
                asks = book.get("asks") or []
                if not asks:
                    continue
                level = asks[0]
                raw_price = (
                    level.get("price")
                    if isinstance(level, dict)
                    else level[0]
                )
                chosen_price = self._price_limit(raw_price)
                chosen_token = token_id
                break
            if not chosen_token:
                return
            quote = client.get_quote(
                wallet_address=wallet_address,
                token_id=chosen_token,
                amount_in_wei=str(amount_in_wei),
                price_limit=chosen_price,
                slippage_bps=100,
                fee_rate_bps=int(reference.get("fee_bps") or 200),
                funding_source="MPC",
            )
            safe, reason = self._quote_is_safe(
                quote,
                token_id=chosen_token,
                price_limit=Decimal(chosen_price),
                amount_in_wei=amount_in_wei,
            )
            if not safe:
                raise ValueError(reason)
            with self.lock:
                self.quote_access = "VERIFIED"
                self.last_error = None
            self.ledger.record_event(
                "INFO",
                "QUOTE_PREFLIGHT_OK",
                f"{float(max_stake):.8g} USDT LIMIT quote 權限已驗證；"
                "quote 已任其到期，沒有送單",
                market_id,
            )
        except Exception as exc:
            with self.lock:
                self.quote_access = "RETRYING"
                self.last_error = f"quote preflight: {str(exc)[:300]}"

    def _run(self) -> None:
        self._preflight()
        if self.m0_hourly_performance is not None:
            self._evaluate_hourly_guard()
            self.next_hourly_guard_refresh = (
                time.monotonic() + LIVE_HOURLY_GUARD_REFRESH_SECONDS
            )
        self.preflight_ready.set()
        while not self.stop_event.is_set():
            try:
                signal = self.events.get(timeout=0.20)
            except queue.Empty:
                signal = None
            if signal is not None:
                self.order_in_flight.set()
                try:
                    self.process_signal(signal)
                except Exception as exc:
                    # A coding or schema error must fail closed and remain visible.
                    with self.lock:
                        self.status = "ERROR"
                        self.armed = False
                        self.last_error = str(exc)[:400]
                    self.ledger.record_event(
                        "ERROR", "EXECUTOR_ERROR", str(exc)[:400]
                    )
                finally:
                    self.order_in_flight.clear()
        with self.lock:
            self.status = "STOPPED"
            self.armed = False

    def _run_maintenance(self) -> None:
        """Keep slow reconciliation traffic off the order-submission worker."""
        while not self.stop_event.is_set() and not self.preflight_ready.wait(0.20):
            pass
        while not self.stop_event.is_set():
            if self.order_in_flight.is_set() or not self.events.empty():
                self.stop_event.wait(0.02)
                continue
            now = time.monotonic()
            pending = (
                self.ledger.pending_orders()
                or self.ledger.pending_pair_insurance()
                or self.ledger.pending_manual_exits()
            )
            sync_interval = (
                LIVE_ORDER_SYNC_SECONDS if pending else LIVE_IDLE_SYNC_SECONDS
            )
            if now >= self.next_order_sync:
                self.next_order_sync = now + sync_interval
                self._sync_orders()
            if now >= self.next_pair_insurance_check:
                self.next_pair_insurance_check = (
                    now + PAIR_INSURANCE_CHECK_SECONDS
                )
                self._run_pair_loss_insurance()
            if now >= self.next_settlement_sync:
                self.next_settlement_sync = now + LIVE_SETTLEMENT_SYNC_SECONDS
                self._sync_strategy_settlements()
            if now >= self.next_hourly_guard_refresh:
                self.next_hourly_guard_refresh = (
                    now + LIVE_HOURLY_GUARD_REFRESH_SECONDS
                )
                self._evaluate_hourly_guard()
            if now >= self.next_account_refresh:
                self.next_account_refresh = now + LIVE_ACCOUNT_REFRESH_SECONDS
                self._refresh_account()
            if now >= self.next_redeem_scan:
                self.next_redeem_scan = now + LIVE_REDEEM_SCAN_SECONDS
                self.run_redeem_cycle()
            if now >= self.next_quote_preflight:
                self.next_quote_preflight = now + LIVE_IDLE_SYNC_SECONDS
                self._verify_quote_access()
            self.stop_event.wait(0.20)

    def state(
        self,
        m0_hourly_performance: dict[str, Any] | None = None,
        *,
        include_ledger: bool = True,
    ) -> dict[str, Any]:
        if m0_hourly_performance is not None:
            self._evaluate_hourly_guard(performance=m0_hourly_performance)
        elif self.m0_hourly_performance is not None:
            with self.lock:
                guard_waiting = self.hourly_guard_state.get("status") == "WAITING"
            if guard_waiting:
                self._evaluate_hourly_guard()
        with self.lock:
            rules = dict(self.live_rules)
            internal = {
                "status": self.status,
                "configuredEnabled": self.configured_enabled,
                "runtimeEnabled": self.runtime_enabled,
                "armed": self.armed,
                "realMoney": True,
                "strategy": str(rules["strategy"]),
                "strategies": list(rules["strategies"]),
                "maxStakeUsdt": float(rules["maxStakeUsdt"]),
                "strategyStakesUsdt": list(rules["strategyStakesUsdt"]),
                "rules": rules,
                "strategyLossCooldownStates": [
                    {
                        **self.ledger.loss_cooldown_state(strategy),
                        "enabled": self._strategy_loss_cooldown_enabled(
                            rules, strategy
                        ),
                    }
                    for strategy in rules["strategies"]
                ],
                "supportedStrategies": list(LIVE_SUPPORTED_STRATEGIES),
                "maxSelectableStrategies": LIVE_MAX_SELECTED_STRATEGIES,
                "configurableStakeRangeUsdt": {
                    "min": float(LIVE_MIN_CONFIGURABLE_STAKE_USDT),
                    "max": float(LIVE_MAX_CONFIGURABLE_STAKE_USDT),
                },
                "orderType": "LIMIT",
                "timeInForce": "GTC",
                "accountType": self.account_type,
                "fundingSource": "MPC",
                "credentialSource": self.credential_source,
                "wallet": _mask_wallet(self.wallet_address),
                "sasStatus": self.sas_status,
                "quoteAccess": self.quote_access,
                "lastError": self.last_error,
                "lastPreflightAt": self.last_preflight_at,
                "lastOrderSyncAt": self.last_order_sync_at,
                "orderSyncWatchdog": {
                    "consecutiveTransportErrors": (
                        self.consecutive_order_sync_transport_errors
                    ),
                    "restartThreshold": self.order_sync_restart_threshold,
                    "restartRequested": self.restart_requested,
                },
                "lastSettlementSyncAt": self.last_settlement_sync_at,
                "lastSignalAt": self.last_signal_at,
                "lastSignalMarketId": self.last_signal_market_id,
                "orderLatency": (
                    dict(self.last_order_latency)
                    if self.last_order_latency is not None
                    else None
                ),
                "lastOrderLatency": (
                    dict(self.last_order_latency)
                    if self.last_order_latency is not None
                    else None
                ),
                "lastLocalPriceCheck": (
                    dict(self.last_local_price_check)
                    if self.last_local_price_check is not None
                    else None
                ),
                "lastDepthCheck": (
                    dict(self.last_depth_check)
                    if self.last_depth_check is not None
                    else None
                ),
                "lastQuoteAttempt": (
                    dict(self.last_quote_attempt)
                    if self.last_quote_attempt is not None
                    else None
                ),
                "attemptSummary": dict(self.attempt_summary_state),
                "maxPredictionBookAgeMs": self.max_prediction_book_age_ms,
                "maxDrawdownSpotAgeMs": LIVE_MAX_DRAWDOWN_SPOT_AGE_MS,
                "maxDrawdownSpotBookAgeMs": (
                    LIVE_MAX_DRAWDOWN_SPOT_BOOK_AGE_MS
                ),
                "drawdownReferenceSource": (
                    self.last_drawdown_reference.get("source")
                    if self.last_drawdown_reference is not None
                    else None
                ),
                "drawdownReferenceAgeMs": (
                    self.last_drawdown_reference.get("ageMs")
                    if self.last_drawdown_reference is not None
                    else None
                ),
                "lastDrawdownReference": (
                    dict(self.last_drawdown_reference)
                    if self.last_drawdown_reference is not None
                    else None
                ),
                "minDepthCoverageRatio": float(
                    LIVE_MIN_DEPTH_COVERAGE_RATIO
                ),
                **self.sqlite_state,
                "droppedSignals": self.dropped_signals,
                "queueDepth": self.events.qsize(),
                "balances": list(self.balances),
                "quota": dict(self.quota),
                "portfolio": dict(self.portfolio_state),
                "hourlyGuard": dict(self.hourly_guard_state),
                "autoRedeem": {
                    "enabled": self.auto_redeem_enabled,
                    "status": self.auto_redeem_status,
                    "delaySeconds": LIVE_AUTO_REDEEM_DELAY_SECONDS,
                    "scanIntervalSeconds": LIVE_REDEEM_SCAN_SECONDS,
                    "claimableCount": self.claimable_count,
                    "claimableAmount": self.claimable_amount,
                    "lastScanAt": self.last_redeem_scan_at,
                    "lastSuccessAt": self.last_redeem_success_at,
                    "lastError": self.last_redeem_error,
                },
            }
        if not include_ledger:
            return {
                **internal,
                "updatedAt": utc_iso(),
            }
        auto_redeem = {
            **internal.pop("autoRedeem"),
            "summary": self.ledger.redeem_summary(),
            "redeems": self.ledger.recent_redeems(),
        }
        current_reference = self.current_market() or {}
        try:
            current_market_id = int(current_reference["market_id"])
        except (KeyError, TypeError, ValueError):
            current_market_id = None
        return {
            **internal,
            "autoRedeem": auto_redeem,
            "summary": self.ledger.summary(),
            "performance": self.ledger.strategy_performance(
                str(rules["strategy"])
            ),
            "performances": {
                strategy: self.ledger.strategy_performance(strategy)
                for strategy in rules["strategies"]
            },
            "overallPerformance": self.ledger.strategy_performance(),
            "orders": self.ledger.recent_orders(),
            "activePositions": (
                self.ledger.active_strategy_positions(market_id=current_market_id)
                if current_market_id is not None
                else []
            ),
            "manualExits": self.ledger.recent_manual_exits(),
            "pairInsurance": self.ledger.recent_pair_insurance(),
            "pairQuoteAudits": self.ledger.recent_pair_quote_audits(),
            "pairIncidents": self.ledger.recent_pair_incidents(),
            "reliabilityResearch": self.ledger.reliability_research_summary(
                enabled_tags=rules["reliabilityGateTags"]
            ),
            "events": self.ledger.recent_events(),
            "updatedAt": utc_iso(),
            "policy": {
                "oneAttemptPerMarket": True,
                "oneAttemptPerStrategyPerMarket": True,
                "retryAmbiguousPlacement": False,
                "marketOrderDisabled": True,
                "m01oF1MinSecondsLeftExclusive": float(
                    M01O_F1_MIN_SECONDS_LEFT
                ),
                "pairMinQuoteCapacityPct": float(
                    PAIR_ARB_MIN_QUOTE_CAPACITY_RATIO * Decimal(100)
                ),
                "pairQc015": {
                    "minimumLockedPnlUsdt": float(PAIR_ARB_QC_MIN_LOCKED_PNL),
                    "minimumLockedRoiPct": float(
                        PAIR_ARB_QC_MIN_LOCKED_ROI * Decimal(100)
                    ),
                    "minimumQuoteCapacityPct": float(
                        PAIR_ARB_QC_MIN_QUOTE_CAPACITY_RATIO * Decimal(100)
                    ),
                    "maximumNetShareMismatchPct": float(
                        PAIR_ARB_QC_MAX_NET_SHARE_MISMATCH_RATIO * Decimal(100)
                    ),
                    "minimumQuoteExpiryMs": PAIR_ARB_QC_MIN_QUOTE_EXPIRY_MS,
                    "maximumQuoteRttMs": float(
                        PAIR_ARB_QC_MAX_QUOTE_RTT_SECONDS * Decimal(1000)
                    ),
                    "networkAndRoundingBufferUsdt": float(
                        PAIR_ARB_QC_NETWORK_AND_ROUNDING_BUFFER
                    ),
                    "equalShareRequote": True,
                    "shadowOnlyWhilePaused": True,
                    "minimumShadowSamples": PAIR_ARB_QC_MIN_SHADOW_SAMPLES,
                    "shadowSamples": self.ledger.pair_qc_shadow_sample_count(
                        "PAIR_ARB_QC_015"
                    ),
                    "liveReady": self._pair_qc_live_ready(),
                    "placementBlockedUntilShadowReady": True,
                },
                "pairFillMismatchGraceSeconds": (
                    PAIR_ARB_FILL_MISMATCH_GRACE_SECONDS
                ),
                "pairInsurance": {
                    "enabled": True,
                    "windowSeconds": float(PAIR_INSURANCE_WINDOW_SECONDS),
                    "confirmationSeconds": PAIR_INSURANCE_DELAY_SECONDS,
                    "retainedLegMinBid": float(PAIR_INSURANCE_HIGH_BID_MIN),
                    "exitOrderType": "LIMIT",
                    "exitPrice": "current low-leg best bid",
                    "requiresGuaranteedLossInBothOutcomes": True,
                },
                "autoRedeemDelaySeconds": LIVE_AUTO_REDEEM_DELAY_SECONDS,
                "retryAmbiguousRedeem": False,
                "hourlyGuard": {
                    "timezone": "Asia/Taipei",
                    "statisticsStrategy": "M0",
                    "minWinRatePct": float(
                        rules["minHourlyWinRatePct"]
                    ),
                    "maxWinThenLossRatePct": float(
                        rules["maxHourlyWinThenLossRatePct"]
                    ),
                    "exactBoundaryAllowed": True,
                },
                "performanceBasis": "official settled token outcomes; unsettled and unfilled orders are excluded",
                "reason": "Binance MARKET minimum is approximately 1.5 USDT; configurable stakes use LIMIT GTC only.",
            },
        }
