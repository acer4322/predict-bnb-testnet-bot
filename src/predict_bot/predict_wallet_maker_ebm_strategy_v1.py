from __future__ import annotations

import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VERSION = "TARGET_MAKER_EBM_V1_FROZEN_HAZARD_LEVEL_FORWARD"
HAZARD_ONLY_COHORT = "MAKER_EBM_HAZARD_V1"
LEVEL_ONLY_COHORT = "MAKER_EBM_LEVEL_V1"
COMBINED_COHORT = "TARGET_MAKER_EBM_V1"
COHORTS = (HAZARD_ONLY_COHORT, LEVEL_ONLY_COHORT, COMBINED_COHORT)

MODEL_DIR = ROOT / "data" / "research" / "target_maker_direct_models_v1"
HAZARD_MODEL_PATH = MODEL_DIR / "hazard_5s_compact.joblib"
LEVEL_MODEL_PATH = MODEL_DIR / "level_2ticks_compact.joblib"
EXPECTED_REPORT_VERSION = "TARGET_MAKER_DIRECT_PLACEMENT_V1_INFERRED_LABEL_INDEPENDENT_WALK_FORWARD"

GRID = 0.01
SHARES_PER_ORDER = 18.0
MIN_PRICE = 0.06
MAX_PRICE = 0.94
MAX_PAIR_PRICE_SUM = 0.99
MIN_REST_MS = 250
REFILL_COOLDOWN_MS = 1_000
STOP_NEW_SECONDS_LEFT = 30.0
MAX_SAMPLE_AGE_MS = 2_000
MAX_PREDICT_RECEIPT_AGE_MS = 2_500
HAZARD_THRESHOLD = 0.60
LEVEL_NEAR_THRESHOLD = 0.55
LEVEL_VERY_NEAR_THRESHOLD = 0.68
FIXED_HAZARD_ONLY_OFFSET_TICKS = 1
DEEP_OFFSET_TICKS = 3

HAZARD_FEATURES = (
    "seconds_left",
    "predict_up_mid",
    "predict_up_spread",
    "predict_down_spread",
    "spot_minus_strike_bps",
    "chainlink_minus_strike_bps",
    "direction_score",
    "spot_queue_imbalance",
    "spot_taker_imbalance_1s",
    "spot_return_1s_bps",
    "spot_return_3s_bps",
    "futures_queue_imbalance",
    "futures_taker_imbalance_1s",
    "futures_return_1s_bps",
    "futures_return_3s_bps",
)
LEVEL_FEATURES = HAZARD_FEATURES + (
    "label_side_up",
    "chosen_predict_bid",
    "chosen_predict_ask",
    "chosen_predict_mid",
    "chosen_predict_spread",
    "opposite_predict_mid",
)

FORBIDDEN_HAZARD_PREFIXES = ("target_", "chosen_", "label_", "side_aligned_")


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _value(snapshot: dict[str, Any], snake: str, camel: str | None = None) -> float | None:
    value = _number(snapshot.get(snake))
    if value is None and camel:
        value = _number(snapshot.get(camel))
    return value


def _spread(bid: float | None, ask: float | None) -> float | None:
    return ask - bid if bid is not None and ask is not None else None


def public_feature_row(snapshot: dict[str, Any]) -> dict[str, float | None]:
    up_bid = _value(snapshot, "predict_up_bid", "predictUpBid")
    up_ask = _value(snapshot, "predict_up_ask", "predictUpAsk")
    down_bid = _value(snapshot, "predict_down_bid", "predictDownBid")
    down_ask = _value(snapshot, "predict_down_ask", "predictDownAsk")
    return {
        "seconds_left": _value(snapshot, "seconds_left", "secondsLeft"),
        "predict_up_mid": _value(snapshot, "predict_up_mid", "predictUpMid"),
        "predict_up_spread": _spread(up_bid, up_ask),
        "predict_down_spread": _spread(down_bid, down_ask),
        "spot_minus_strike_bps": _value(snapshot, "spot_minus_strike_bps", "spotMinusStrikeBps"),
        "chainlink_minus_strike_bps": _value(snapshot, "chainlink_minus_strike_bps", "chainlinkMinusStrikeBps"),
        "direction_score": _value(snapshot, "direction_score", "directionScore"),
        "spot_queue_imbalance": _value(snapshot, "spot_queue_imbalance", "spotQueueImbalance"),
        "spot_taker_imbalance_1s": _value(snapshot, "spot_taker_imbalance_1s", "spotTakerImbalance1s"),
        "spot_return_1s_bps": _value(snapshot, "spot_return_1s_bps", "spotReturn1sBps"),
        "spot_return_3s_bps": _value(snapshot, "spot_return_3s_bps", "spotReturn3sBps"),
        "futures_queue_imbalance": _value(snapshot, "futures_queue_imbalance", "futuresQueueImbalance"),
        "futures_taker_imbalance_1s": _value(snapshot, "futures_taker_imbalance_1s", "futuresTakerImbalance1s"),
        "futures_return_1s_bps": _value(snapshot, "futures_return_1s_bps", "futuresReturn1sBps"),
        "futures_return_3s_bps": _value(snapshot, "futures_return_3s_bps", "futuresReturn3sBps"),
    }


def level_feature_row(snapshot: dict[str, Any], side: str) -> dict[str, float | None]:
    row = public_feature_row(snapshot)
    chosen = "up" if side == "UP" else "down"
    opposite = "down" if side == "UP" else "up"
    bid = _value(snapshot, f"predict_{chosen}_bid", f"predict{chosen.title()}Bid")
    ask = _value(snapshot, f"predict_{chosen}_ask", f"predict{chosen.title()}Ask")
    row.update({
        "label_side_up": 1.0 if side == "UP" else 0.0,
        "chosen_predict_bid": bid,
        "chosen_predict_ask": ask,
        "chosen_predict_mid": _value(snapshot, f"predict_{chosen}_mid", f"predict{chosen.title()}Mid"),
        "chosen_predict_spread": _spread(bid, ask),
        "opposite_predict_mid": _value(snapshot, f"predict_{opposite}_mid", f"predict{opposite.title()}Mid"),
    })
    return row


def _load_bundle(path: Path, *, task: str, features: tuple[str, ...]) -> dict[str, Any]:
    try:
        import joblib
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError('Maker EBM dependency missing. Run: pip install -e ".[research]"') from exc
    model_path = path.expanduser().resolve()
    if not model_path.exists():
        raise FileNotFoundError(
            f"Maker EBM artifact missing: {model_path}. Run: python tools/train_target_maker_direct_runtime_v1.py"
        )
    bundle = joblib.load(model_path)
    if not isinstance(bundle, dict):
        raise RuntimeError(f"Maker EBM artifact is not a bundle: {model_path}")
    if bundle.get("reportVersion") != EXPECTED_REPORT_VERSION:
        raise RuntimeError(f"Maker EBM report version mismatch: {bundle.get('reportVersion')}")
    if bundle.get("task") != task:
        raise RuntimeError(f"Maker EBM task mismatch: expected={task} actual={bundle.get('task')}")
    if bundle.get("researchOnly") is not True or bundle.get("automaticStrategyPromotion") is not False:
        raise RuntimeError("Maker EBM artifact lacks research-only safety metadata")
    actual = tuple(str(value) for value in bundle.get("features") or ())
    if actual != features:
        raise RuntimeError(f"Maker EBM feature contract mismatch: expected={features} actual={actual}")
    if task.startswith("hazard"):
        for feature in actual:
            if feature.startswith(FORBIDDEN_HAZARD_PREFIXES):
                raise RuntimeError(f"Forbidden hazard runtime feature: {feature}")
    model = bundle.get("model")
    if model is None or not hasattr(model, "predict_proba"):
        raise RuntimeError("Maker EBM artifact has no classifier")
    return {**bundle, "path": str(model_path)}


def load_models(
    hazard_path: Path = HAZARD_MODEL_PATH,
    level_path: Path = LEVEL_MODEL_PATH,
) -> dict[str, dict[str, Any]]:
    return {
        "hazard": _load_bundle(hazard_path, task="hazard_5s_compact", features=HAZARD_FEATURES),
        "level": _load_bundle(level_path, task="level_2ticks_compact", features=LEVEL_FEATURES),
    }


def _probability(bundle: dict[str, Any] | None, row: dict[str, float | None]) -> dict[str, Any]:
    if not isinstance(bundle, dict) or bundle.get("model") is None:
        return {"status": "MODEL_UNAVAILABLE", "probability": None, "missingFeatures": list(row)}
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError('Maker EBM dependency missing. Run: pip install -e ".[research]"') from exc
    features = [str(value) for value in bundle["features"]]
    missing = [name for name in features if row.get(name) is None]
    frame = pd.DataFrame([{name: row.get(name) for name in features}], columns=features)
    model = bundle["model"]
    probabilities = model.predict_proba(frame)[0]
    classes = [int(value) for value in model.classes_]
    if 1 not in classes:
        raise RuntimeError(f"Maker EBM has no positive class: {classes}")
    probability = float(probabilities[classes.index(1)])
    return {
        "status": "OK",
        "probability": max(0.0, min(1.0, probability)),
        "missingFeatures": missing,
        "modelPath": bundle.get("path"),
        "reportVersion": bundle.get("reportVersion"),
        "probabilityCalibrationClaim": False,
    }


def inventory(up_shares: float, down_shares: float, up_cost: float = 0.0, down_cost: float = 0.0) -> dict[str, Any]:
    up = float(up_shares)
    down = float(down_shares)
    total = up + down
    delta = up - down
    return {
        "upShares": up,
        "downShares": down,
        "upCostUsdt": float(up_cost),
        "downCostUsdt": float(down_cost),
        "deltaShares": delta,
        "residualSide": "UP" if delta > 1e-9 else "DOWN" if delta < -1e-9 else None,
        "imbalanceRatio": abs(delta) / total if total > 1e-9 else 0.0,
        "pairedCoverage": (2.0 * min(up, down) / total) if total > 1e-9 else 1.0,
    }


def desired_sides(inventory_state: dict[str, Any]) -> list[str]:
    delta = float(inventory_state.get("deltaShares") or 0.0)
    if delta >= SHARES_PER_ORDER - 1e-9:
        return ["DOWN"]
    if delta <= -SHARES_PER_ORDER + 1e-9:
        return ["UP"]
    return ["UP", "DOWN"]


def _quote_tick(snapshot: dict[str, Any], side: str, offset_ticks: int) -> int | None:
    bid = _value(snapshot, f"predict_{side.lower()}_bid", f"predict{side.title()}Bid")
    if bid is None:
        return None
    best_tick = int(math.floor((bid + 1e-9) / GRID))
    minimum_tick = int(round(MIN_PRICE / GRID))
    maximum_tick = int(round(MAX_PRICE / GRID))
    tick = max(minimum_tick, min(maximum_tick, best_tick - max(0, int(offset_ticks))))
    return tick if tick * GRID * SHARES_PER_ORDER + 1e-9 >= 1.0 else None


def _level_offset(probability: float | None) -> int:
    if probability is None:
        return DEEP_OFFSET_TICKS
    if probability >= LEVEL_VERY_NEAR_THRESHOLD:
        return 0
    if probability >= LEVEL_NEAR_THRESHOLD:
        return 1
    return DEEP_OFFSET_TICKS


def decide(
    snapshot: dict[str, Any],
    models: dict[str, dict[str, Any]] | None,
    inventory_state: dict[str, Any],
    *,
    cohort: str,
    expected_market_id: int,
    now_ms: int,
) -> dict[str, Any]:
    if cohort not in COHORTS:
        raise ValueError(f"unknown Maker EBM cohort: {cohort}")
    sampled_at_ms = int(_value(snapshot, "sampled_at_ms", "sampledAtMs") or 0)
    market_id = int(_value(snapshot, "market_id", "marketId") or 0)
    sample_age_ms = max(0, now_ms - sampled_at_ms) if sampled_at_ms else None
    predict_age_ms = _value(snapshot, "predict_receipt_age_ms", "predictReceiptAgeMs")
    seconds_left = _value(snapshot, "seconds_left", "secondsLeft")
    bundles = models if isinstance(models, dict) else {}

    hazard = _probability(bundles.get("hazard"), public_feature_row(snapshot))
    needs_hazard = cohort in {HAZARD_ONLY_COHORT, COMBINED_COHORT}
    hazard_ok = not needs_hazard or (
        hazard.get("status") == "OK" and float(hazard.get("probability") or 0.0) >= HAZARD_THRESHOLD
    )

    reason = "MAKER_EBM_ACTIVE"
    if market_id != int(expected_market_id):
        reason = "MARKET_MISMATCH"
    elif sample_age_ms is None or sample_age_ms > MAX_SAMPLE_AGE_MS:
        reason = "STALE_PUBLIC_SNAPSHOT"
    elif predict_age_ms is None or predict_age_ms > MAX_PREDICT_RECEIPT_AGE_MS:
        reason = "STALE_PREDICT_BOOK"
    elif seconds_left is None or seconds_left <= STOP_NEW_SECONDS_LEFT:
        reason = "TAIL_FREEZE"
    elif needs_hazard and hazard.get("status") != "OK":
        reason = "HAZARD_MODEL_UNAVAILABLE"
    elif not hazard_ok:
        reason = "HAZARD_EBM_INACTIVE"

    orders: list[dict[str, Any]] = []
    level_scores: dict[str, Any] = {}
    if reason == "MAKER_EBM_ACTIVE":
        for side in desired_sides(inventory_state):
            if cohort == HAZARD_ONLY_COHORT:
                level = {"status": "BYPASS_FIXED", "probability": None}
                offset = FIXED_HAZARD_ONLY_OFFSET_TICKS
            else:
                level = _probability(bundles.get("level"), level_feature_row(snapshot, side))
                if level.get("status") != "OK":
                    reason = "LEVEL_MODEL_UNAVAILABLE"
                    orders = []
                    break
                offset = _level_offset(level.get("probability"))
            level_scores[side] = {**level, "offsetTicks": offset}
            tick = _quote_tick(snapshot, side, offset)
            if tick is None:
                continue
            orders.append({
                "side": side,
                "priceTick": tick,
                "price": round(tick * GRID, 2),
                "shares": SHARES_PER_ORDER,
                "offsetTicks": offset,
                "origin": cohort,
            })

    if len(orders) == 2:
        # Keep the complementary pair economically bounded. Back off the more
        # expensive leg one tick at a time rather than crossing the pair cap.
        while sum(float(order["price"]) for order in orders) > MAX_PAIR_PRICE_SUM + 1e-9:
            expensive = max(orders, key=lambda order: float(order["price"]))
            next_tick = int(expensive["priceTick"]) - 1
            if next_tick < int(round(MIN_PRICE / GRID)):
                orders = []
                reason = "PAIR_PRICE_CAP_UNSATISFIABLE"
                break
            expensive["priceTick"] = next_tick
            expensive["price"] = round(next_tick * GRID, 2)
            expensive["offsetTicks"] = int(expensive["offsetTicks"]) + 1

    return {
        "decision": "QUOTE" if reason == "MAKER_EBM_ACTIVE" and orders else "IDLE",
        "reason": reason if orders or reason != "MAKER_EBM_ACTIVE" else "NO_EXECUTABLE_QUOTE",
        "orders": orders,
        "secondsLeft": seconds_left,
        "sampledAtMs": sampled_at_ms or None,
        "sampleAgeMs": sample_age_ms,
        "predictReceiptAgeMs": predict_age_ms,
        "hazard": {**hazard, "threshold": HAZARD_THRESHOLD, "used": needs_hazard},
        "levels": level_scores,
        "inventory": inventory_state,
        "paperOnly": True,
        "targetEventsUsed": False,
    }


def ask_touch_fill(order: dict[str, Any], snapshot: dict[str, Any], *, snapshot_ns: int, now_ms: int) -> bool:
    placed_at = int(order.get("placedAtMs") or 0)
    placed_snapshot_ns = int(order.get("placedSnapshotNs") or 0)
    if now_ms - placed_at < MIN_REST_MS or int(snapshot_ns) <= placed_snapshot_ns:
        return False
    side = str(order.get("side") or "").upper()
    ask = _value(snapshot, f"predict_{side.lower()}_ask", f"predict{side.title()}Ask")
    price = _number(order.get("price"))
    return bool(ask is not None and price is not None and ask <= price + 1e-12)


def policy() -> dict[str, Any]:
    return {
        "version": VERSION,
        "cohorts": list(COHORTS),
        "paperOnly": True,
        "forwardOnly": True,
        "historicalBackfill": False,
        "targetEventsDriveStrategy": False,
        "liveOrdersAffected": False,
        "sharesPerOrder": SHARES_PER_ORDER,
        "grid": GRID,
        "maximumPairPriceSum": MAX_PAIR_PRICE_SUM,
        "stopNewQuotesSecondsLeft": STOP_NEW_SECONDS_LEFT,
        "fillProxy": "strict later-snapshot ask-touch after >=250ms rest; no queue priority or rebate credit",
        "sidePolicy": "own cohort inventory only: quote deficient side after >=18-share residual, otherwise quote both sides",
        "hazardModel": {
            "artifact": str(HAZARD_MODEL_PATH),
            "task": "P(next inferred placement within 5s)",
            "featureFamily": "compact_public",
            "threshold": HAZARD_THRESHOLD,
            "evidence": "quick walk-forward mean AUC about 0.76; latest-window AUC about 0.71",
        },
        "levelModel": {
            "artifact": str(LEVEL_MODEL_PATH),
            "task": "P(inferred placement within 2 ticks of strict-pre best bid)",
            "featureFamily": "compact_level",
            "nearThreshold": LEVEL_NEAR_THRESHOLD,
            "veryNearThreshold": LEVEL_VERY_NEAR_THRESHOLD,
            "mapping": ">=veryNear => best bid; >=near => best bid-1 tick; else best bid-3 ticks",
            "evidence": "quick walk-forward mean AUC about 0.697; latest-window AUC about 0.689",
        },
        "abTest": {
            HAZARD_ONLY_COHORT: "EBM controls WHEN; quote level fixed at one tick behind best bid",
            LEVEL_ONLY_COHORT: "always-active public window; EBM controls WHERE",
            COMBINED_COHORT: "EBM controls WHEN + WHERE",
        },
        "makerSideEbm": "not promoted: direct Maker Side quick AUC was only about 0.58; side remains own-inventory/lifecycle driven",
        "labelBoundary": "models were trained on high-confidence inferred placements reconstructed from anonymous public book plus known Target Maker fills; not private-order ground truth",
    }
