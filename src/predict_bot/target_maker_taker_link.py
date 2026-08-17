from __future__ import annotations

import bisect
import csv
import json
import math
import random
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from .target_maker_ebm_v3_dataset import DEFAULT_OUTPUT as DEFAULT_MAKER_DATASET

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SHADOW_DB = ROOT / "data" / "predict_wallet_shadow.db"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_taker_link_report_v1.json"
REPORT_VERSION = "TARGET_MAKER_TAKER_LINK_DISCOVERY_V1"
TAKER_COHORT = "TARGET_TAKER_MIRROR_AUDIT_V1"
WINDOWS_MS = (250, 500, 1000, 2000, 5000)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _connect_readonly(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    db = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    return db


def _has_table(db: sqlite3.Connection, table: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone() is not None


def _load_makers(path: Path) -> list[dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    with resolved.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _load_takers(db: sqlite3.Connection, deployed_at_ms: int, excluded_market_id: int | None) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    query = """
        SELECT parent_id,market_id,order_hash,side,target_event_ms,target_last_event_ms,
               target_shares_at_detection,target_latest_shares,target_fill_legs,detection_lag_ms
          FROM wallet_target_taker_mirror_parents
         WHERE cohort=? AND target_event_ms>=?
         ORDER BY market_id,target_event_ms,parent_id
    """
    for raw in db.execute(query, (TAKER_COHORT, int(deployed_at_ms))):
        row = dict(raw)
        market_id = int(row["market_id"])
        if excluded_market_id is not None and market_id == excluded_market_id:
            continue
        grouped[market_id].append(row)
    return grouped


def _events_between(events: list[dict[str, Any]], times: list[int], low: int, high: int, *, include_low: bool, include_high: bool) -> list[dict[str, Any]]:
    left = bisect.bisect_left(times, low) if include_low else bisect.bisect_right(times, low)
    right = bisect.bisect_right(times, high) if include_high else bisect.bisect_left(times, high)
    return events[left:right]


def _inventory_balancing(prior_delta: float | None, taker_side: str) -> bool | None:
    if prior_delta is None or abs(prior_delta) <= 1e-9:
        return None
    heavy_side = "UP" if prior_delta > 0 else "DOWN"
    return taker_side != heavy_side


def _record_for_maker(row: dict[str, Any], takers: list[dict[str, Any]]) -> dict[str, Any]:
    anchor = int(float(row["last_target_ms"]))
    times = [int(event["target_event_ms"]) for event in takers]
    side = str(row.get("target_side") or "")
    prior_delta = _number(row.get("prior_maker_delta_shares"))
    output: dict[str, Any] = {
        "market_id": int(float(row["market_id"])),
        "anchor_ms": anchor,
        "maker_side": side,
        "post_action": str(row.get("post_action") or ""),
        "filled_near_18": int(float(row.get("observed_filled_near_18") or 0)),
        "prior_imbalance_ratio": _number(row.get("prior_maker_imbalance_ratio")),
        "prior_delta": prior_delta,
        "seconds_left": _number(row.get("seconds_left")),
        "target_price": _number(row.get("target_price")),
        "fill_duration_ms": _number(row.get("path_fill_duration_ms")),
        "reprice_toward": _number(row.get("label_reprice_toward_touch")),
    }
    for window in WINDOWS_MS:
        pre = _events_between(takers, times, anchor - window, anchor, include_low=True, include_high=False)
        post = _events_between(takers, times, anchor, anchor + window, include_low=False, include_high=True)
        output[f"pre_{window}"] = pre
        output[f"post_{window}"] = post
    return output


def _event_stats(records: list[dict[str, Any]], key: str) -> dict[str, Any]:
    total_records = len(records)
    any_count = 0
    event_count = 0
    shares = 0.0
    first_delays: list[int] = []
    same_side = 0
    opposite_side = 0
    balancing = 0
    balancing_known = 0
    for record in records:
        events = record[key]
        if events:
            any_count += 1
            anchor = int(record["anchor_ms"])
            first_delays.append(min(abs(int(event["target_event_ms"]) - anchor) for event in events))
        event_count += len(events)
        for event in events:
            shares += float(event.get("target_latest_shares") or 0.0)
            if str(event.get("side")) == record["maker_side"]:
                same_side += 1
            else:
                opposite_side += 1
            is_balancing = _inventory_balancing(record.get("prior_delta"), str(event.get("side")))
            if is_balancing is not None:
                balancing_known += 1
                balancing += int(is_balancing)
    return {
        "makerAnchors": total_records,
        "anchorsWithAnyTaker": any_count,
        "anyTakerRate": any_count / total_records if total_records else None,
        "takerParents": event_count,
        "meanTakerParentsPerMakerAnchor": event_count / total_records if total_records else None,
        "totalTargetTakerShares": shares,
        "medianFirstAbsoluteDelayMs": statistics.median(first_delays) if first_delays else None,
        "sameMakerSideEventRate": same_side / event_count if event_count else None,
        "oppositeMakerSideEventRate": opposite_side / event_count if event_count else None,
        "inventoryBalancingEventRate": balancing / balancing_known if balancing_known else None,
        "inventoryBalancingKnownEvents": balancing_known,
    }


def _market_block_ci(records: list[dict[str, Any]], window: int, *, seed: int = 42, samples: int = 2000) -> dict[str, Any]:
    by_market: dict[int, list[float]] = defaultdict(list)
    for record in records:
        post = int(bool(record[f"post_{window}"]))
        pre = int(bool(record[f"pre_{window}"]))
        by_market[int(record["market_id"])].append(float(post - pre))
    market_means = [statistics.mean(values) for values in by_market.values() if values]
    observed = statistics.mean(market_means) if market_means else None
    if len(market_means) < 3:
        return {"markets": len(market_means), "meanMarketPostMinusPreAnyRate": observed, "bootstrap95": None}
    rng = random.Random(seed + window)
    boot: list[float] = []
    for _ in range(max(100, int(samples))):
        boot.append(statistics.mean(rng.choice(market_means) for _ in market_means))
    boot.sort()
    low = boot[int(0.025 * (len(boot) - 1))]
    high = boot[int(0.975 * (len(boot) - 1))]
    return {
        "markets": len(market_means),
        "meanMarketPostMinusPreAnyRate": observed,
        "bootstrap95": [low, high],
        "unit": "market-blocked mean of per-market Maker-anchor postAny-preAny",
    }


def _window_summary(records: list[dict[str, Any]], window: int, bootstrap_samples: int) -> dict[str, Any]:
    pre = _event_stats(records, f"pre_{window}")
    post = _event_stats(records, f"post_{window}")
    pre_rate = pre.get("anyTakerRate")
    post_rate = post.get("anyTakerRate")
    return {
        "windowMs": window,
        "pre": pre,
        "post": post,
        "rawPostMinusPreAnyRate": (
            float(post_rate - pre_rate) if post_rate is not None and pre_rate is not None else None
        ),
        "rawPostVsPreAnyRateRatio": (
            float(post_rate / pre_rate) if post_rate is not None and pre_rate not in (None, 0) else None
        ),
        "marketBlockInference": _market_block_ci(records, window, samples=bootstrap_samples),
    }


def _regimes(record: dict[str, Any]) -> dict[str, str]:
    seconds = record.get("seconds_left")
    if seconds is None:
        time = "UNKNOWN"
    elif seconds <= 60:
        time = "LATE_LE_60S"
    elif seconds > 200:
        time = "EARLY_GT_200S"
    else:
        time = "MID_60_200S"
    price_value = record.get("target_price")
    if price_value is None:
        price = "UNKNOWN"
    elif price_value < 0.33:
        price = "LOW_LT_033"
    elif price_value > 0.67:
        price = "HIGH_GT_067"
    else:
        price = "MID_033_067"
    imbalance = record.get("prior_imbalance_ratio")
    if imbalance is None:
        inventory = "UNKNOWN"
    elif imbalance <= 0.10:
        inventory = "BALANCED_LE_010"
    elif imbalance <= 0.25:
        inventory = "MODERATE_010_025"
    else:
        inventory = "IMBALANCED_GT_025"
    duration = record.get("fill_duration_ms")
    if duration is None:
        fill_speed = "UNKNOWN"
    elif duration <= 50:
        fill_speed = "INSTANT_LE_50MS"
    elif duration <= 250:
        fill_speed = "FAST_50_250MS"
    elif duration <= 1000:
        fill_speed = "MEDIUM_250_1000MS"
    else:
        fill_speed = "SLOW_GT_1000MS"
    reprice = record.get("reprice_toward")
    reprice_direction = "NA"
    if reprice is not None:
        reprice_direction = "TOWARD_TOUCH" if int(reprice) == 1 else "AWAY_FROM_TOUCH"
    return {
        "makerAction": record["post_action"],
        "fillCompletion": "FILLED_NEAR_18" if record["filled_near_18"] else "PARTIAL_OR_OTHER",
        "time": time,
        "price": price,
        "inventory": inventory,
        "fillSpeed": fill_speed,
        "repriceDirection": reprice_direction,
    }


def _regime_tables(records: list[dict[str, Any]], *, min_rows: int, bootstrap_samples: int) -> dict[str, Any]:
    dimensions = ("makerAction", "fillCompletion", "time", "price", "inventory", "fillSpeed", "repriceDirection")
    output: dict[str, Any] = {}
    augmented = [(record, _regimes(record)) for record in records]
    for dimension in dimensions:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record, regimes in augmented:
            grouped[regimes[dimension]].append(record)
        rows = []
        for value, subset in sorted(grouped.items()):
            if len(subset) < min_rows:
                continue
            rows.append({
                "value": value,
                "rows": len(subset),
                "window1000ms": _window_summary(subset, 1000, bootstrap_samples),
                "window5000ms": _window_summary(subset, 5000, bootstrap_samples),
            })
        output[dimension] = rows
    return output


def _reverse_anchor_summary(records: list[dict[str, Any]], takers_by_market: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
    makers_by_market: dict[int, list[int]] = defaultdict(list)
    for record in records:
        makers_by_market[int(record["market_id"])].append(int(record["anchor_ms"]))
    for values in makers_by_market.values():
        values.sort()
    output: dict[str, Any] = {}
    for window in WINDOWS_MS:
        total = before = after = 0
        for market_id, takers in takers_by_market.items():
            maker_times = makers_by_market.get(market_id, [])
            if not maker_times:
                continue
            for event in takers:
                at = int(event["target_event_ms"])
                left = bisect.bisect_left(maker_times, at)
                total += 1
                before += int(left > 0 and maker_times[left - 1] >= at - window)
                right = bisect.bisect_right(maker_times, at)
                after += int(right < len(maker_times) and maker_times[right] <= at + window)
        output[str(window)] = {
            "targetTakerAnchors": total,
            "makerWithinPriorWindowRate": before / total if total else None,
            "makerWithinPostWindowRate": after / total if total else None,
            "postMinusPriorRate": (after - before) / total if total else None,
        }
    return output


def analyze_link(
    *,
    maker_dataset_path: Path = DEFAULT_MAKER_DATASET,
    shadow_db_path: Path = DEFAULT_SHADOW_DB,
    report_path: Path = DEFAULT_REPORT,
    min_regime_rows: int = 30,
    bootstrap_samples: int = 2000,
) -> dict[str, Any]:
    makers = _load_makers(maker_dataset_path)
    shadow = _connect_readonly(shadow_db_path)
    try:
        for table in ("wallet_target_taker_mirror_meta", "wallet_target_taker_mirror_parents"):
            if not _has_table(shadow, table):
                raise RuntimeError(f"required 8776 target Taker table missing: {table}")
        meta = shadow.execute(
            "SELECT deployed_at_ms,excluded_market_id FROM wallet_target_taker_mirror_meta WHERE cohort=?",
            (TAKER_COHORT,),
        ).fetchone()
        if meta is None:
            raise RuntimeError(f"missing target Taker mirror meta for cohort {TAKER_COHORT}")
        deployed_at_ms = int(meta["deployed_at_ms"])
        excluded_market_id = int(meta["excluded_market_id"]) if meta["excluded_market_id"] is not None else None
        takers_by_market = _load_takers(shadow, deployed_at_ms, excluded_market_id)

        eligible_makers = [
            row for row in makers
            if int(float(row["last_target_ms"])) >= deployed_at_ms
            and (excluded_market_id is None or int(float(row["market_id"])) != excluded_market_id)
        ]
        records = [
            _record_for_maker(row, takers_by_market.get(int(float(row["market_id"])), []))
            for row in eligible_makers
        ]
        windows = {str(window): _window_summary(records, window, bootstrap_samples) for window in WINDOWS_MS}
        report: dict[str, Any] = {
            "reportVersion": REPORT_VERSION,
            "paperResearchOnly": True,
            "automaticStrategyPromotion": False,
            "causalClaim": False,
            "makerDataset": str(maker_dataset_path),
            "shadowDb": str(shadow_db_path),
            "targetTakerCohort": TAKER_COHORT,
            "targetTakerMirrorDeployedAtMs": deployed_at_ms,
            "excludedMarketId": excluded_market_id,
            "eligibleMakerAnchors": len(records),
            "eligibleMakerMarkets": len({record["market_id"] for record in records}),
            "targetTakerParents": sum(len(events) for events in takers_by_market.values()),
            "targetTakerMarkets": len(takers_by_market),
            "windows": windows,
            "regimeAnalysis": _regime_tables(
                records,
                min_rows=max(10, int(min_regime_rows)),
                bootstrap_samples=max(100, int(bootstrap_samples)),
            ),
            "reverseTakerAnchor": _reverse_anchor_summary(records, takers_by_market),
            "hypotheses": {
                "makerThenTaker": "compare symmetric target-Taker parent incidence before vs after each target Maker parent anchor",
                "fillCompletion": "test whether full ~18-share Maker completion changes nearby Taker hazard",
                "inventoryBalancing": "test whether nearby target Taker side tends to reduce prior Maker UP/DOWN share imbalance",
                "makerLifecycle": "compare Taker hazard after Maker no-action, same-price refill, reprice, and signed reprice direction",
                "temporalAsymmetry": "compare Maker-anchored pre/post and Taker-anchored pre/post clustering; asymmetry is not causality",
            },
            "coverageBoundary": (
                "Taker parent identity is target-wallet-derived, but zero-event intervals assume the 8776 target Taker mirror was operating after deployment. "
                "The report therefore measures observational temporal association, not proof that Maker actions cause Taker actions."
            ),
        }
        resolved = report_path.expanduser().resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        temp = resolved.with_suffix(resolved.suffix + ".tmp")
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(resolved)
        return report
    finally:
        shadow.close()
