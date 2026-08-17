from __future__ import annotations

import csv
import json
import math
import random
import sqlite3
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .target_maker_ebm_dataset import DEFAULT_MAKER_DB
from .target_maker_ebm_v3_dataset import DEFAULT_OUTPUT as DEFAULT_MAKER_DATASET
from .target_maker_taker_link import DEFAULT_SHADOW_DB, TAKER_COHORT

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_taker_link_report_v2.json"
REPORT_VERSION = "TARGET_MAKER_TAKER_LINK_DISCOVERY_V2_TIMESTAMP_AWARE_HIGHRES_FILL"
BUCKET_WINDOWS_SECONDS = (1, 2, 5)


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


def _load_maker_rows(path: Path) -> list[dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    with resolved.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _highres_fill_evidence(db: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not _has_table(db, "maker_book_inference_v21_allocations"):
        raise RuntimeError("8778 V2.1 allocation table is missing")
    result: dict[str, dict[str, Any]] = {}
    query = """
        SELECT parent_id,market_id,MIN(source_ms) first_source_ms,MAX(source_ms) last_source_ms,
               COUNT(*) allocation_rows,COALESCE(SUM(allocated_quantity),0) allocated_shares
          FROM maker_book_inference_v21_allocations
         WHERE allocation_kind='TARGET_FILL_DECREASE' AND parent_id IS NOT NULL
         GROUP BY parent_id,market_id
    """
    for raw in db.execute(query):
        row = dict(raw)
        first = int(row["first_source_ms"])
        last = int(row["last_source_ms"])
        result[str(row["parent_id"])] = {
            "marketId": int(row["market_id"]),
            "firstSourceMs": first,
            "lastSourceMs": last,
            "fillDurationMs": max(0, last - first),
            "allocationRows": int(row["allocation_rows"]),
            "allocatedShares": float(row["allocated_shares"]),
        }
    return result


def _load_takers(
    db: sqlite3.Connection, deployed_at_ms: int, excluded_market_id: int | None
) -> dict[int, list[dict[str, Any]]]:
    if not _has_table(db, "wallet_target_taker_mirror_parents"):
        raise RuntimeError("8776 target Taker mirror parent table is missing")
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    query = """
        SELECT parent_id,market_id,order_hash,side,target_event_ms,target_last_event_ms,
               target_latest_shares,target_fill_legs,detection_lag_ms
          FROM wallet_target_taker_mirror_parents
         WHERE cohort=? AND target_event_ms>=?
         ORDER BY market_id,target_event_ms,parent_id
    """
    for raw in db.execute(query, (TAKER_COHORT, int(deployed_at_ms))):
        row = dict(raw)
        market_id = int(row["market_id"])
        if excluded_market_id is not None and market_id == excluded_market_id:
            continue
        row["secondBucket"] = int(row["target_event_ms"]) // 1000
        grouped[market_id].append(row)
    return grouped


def _timestamp_diagnostics(takers_by_market: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
    events = [event for values in takers_by_market.values() for event in values]
    residues = [int(event["target_event_ms"]) % 1000 for event in events]
    counts = Counter(residues)
    quantized = sum(count for residue, count in counts.items() if residue == 0)
    top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]
    return {
        "events": len(events),
        "exactSecondBoundaryRate": quantized / len(events) if events else None,
        "distinctMillisecondResidues": len(counts),
        "topMillisecondResidues": [{"residueMs": key, "events": count} for key, count in top],
        "subsecondOrderingUsable": False,
        "warning": (
            "Target Taker target_event_ms is treated as second-quantized. Same-second Maker/Taker ordering is ambiguous; "
            "250ms/500ms causal sequencing is intentionally not reported."
        ),
    }


def _inventory_balancing(prior_delta: float | None, taker_side: str) -> bool | None:
    if prior_delta is None or abs(prior_delta) <= 1e-9:
        return None
    heavy = "UP" if prior_delta > 0 else "DOWN"
    return taker_side != heavy


def _events_for_bucket_range(
    events: list[dict[str, Any]], low_bucket: int, high_bucket: int
) -> list[dict[str, Any]]:
    if low_bucket > high_bucket:
        return []
    return [event for event in events if low_bucket <= int(event["secondBucket"]) <= high_bucket]


def _record_for_maker(
    row: dict[str, Any], evidence: dict[str, Any] | None, takers: list[dict[str, Any]]
) -> dict[str, Any]:
    target_anchor = int(float(row["last_target_ms"]))
    highres_anchor = int(evidence["lastSourceMs"]) if evidence is not None else target_anchor
    anchor_bucket = highres_anchor // 1000
    prior_delta = _number(row.get("prior_maker_delta_shares"))
    output: dict[str, Any] = {
        "parent_id": str(row["parent_id"]),
        "market_id": int(float(row["market_id"])),
        "maker_side": str(row.get("target_side") or ""),
        "target_anchor_ms": target_anchor,
        "highres_anchor_ms": highres_anchor,
        "highres_evidence": evidence is not None,
        "anchor_second_bucket": anchor_bucket,
        "post_action": str(row.get("post_action") or ""),
        "filled_near_18": int(float(row.get("observed_filled_near_18") or 0)),
        "prior_delta": prior_delta,
        "prior_imbalance_ratio": _number(row.get("prior_maker_imbalance_ratio")),
        "target_price": _number(row.get("target_price")),
        "seconds_left": _number(row.get("seconds_left")),
        "fill_duration_ms": (float(evidence["fillDurationMs"]) if evidence is not None else _number(row.get("path_fill_duration_ms"))),
        "allocation_rows": int(evidence["allocationRows"]) if evidence is not None else None,
        "same_second": _events_for_bucket_range(takers, anchor_bucket, anchor_bucket),
    }
    for seconds in BUCKET_WINDOWS_SECONDS:
        output[f"prior_{seconds}s"] = _events_for_bucket_range(
            takers, anchor_bucket - seconds, anchor_bucket - 1
        )
        output[f"post_{seconds}s"] = _events_for_bucket_range(
            takers, anchor_bucket + 1, anchor_bucket + seconds
        )
    return output


def _event_stats(records: list[dict[str, Any]], key: str) -> dict[str, Any]:
    anchors = len(records)
    anchors_with = 0
    event_count = 0
    shares = 0.0
    same_side = 0
    opposite_side = 0
    balancing = 0
    balancing_known = 0
    for record in records:
        events = record[key]
        anchors_with += int(bool(events))
        event_count += len(events)
        for event in events:
            shares += float(event.get("target_latest_shares") or 0.0)
            if str(event.get("side")) == record["maker_side"]:
                same_side += 1
            else:
                opposite_side += 1
            balanced = _inventory_balancing(record.get("prior_delta"), str(event.get("side")))
            if balanced is not None:
                balancing_known += 1
                balancing += int(balanced)
    return {
        "makerAnchors": anchors,
        "anchorsWithAnyTaker": anchors_with,
        "anyTakerRate": anchors_with / anchors if anchors else None,
        "takerParents": event_count,
        "meanTakerParentsPerMakerAnchor": event_count / anchors if anchors else None,
        "totalTargetTakerShares": shares,
        "sameMakerSideEventRate": same_side / event_count if event_count else None,
        "oppositeMakerSideEventRate": opposite_side / event_count if event_count else None,
        "inventoryBalancingEventRate": balancing / balancing_known if balancing_known else None,
        "inventoryBalancingKnownEvents": balancing_known,
    }


def _market_block_ci(
    records: list[dict[str, Any]], seconds: int, *, samples: int, seed: int = 42
) -> dict[str, Any]:
    by_market: dict[int, list[float]] = defaultdict(list)
    for record in records:
        post = int(bool(record[f"post_{seconds}s"]))
        prior = int(bool(record[f"prior_{seconds}s"]))
        by_market[int(record["market_id"])].append(float(post - prior))
    market_means = [statistics.mean(values) for values in by_market.values() if values]
    observed = statistics.mean(market_means) if market_means else None
    if len(market_means) < 3:
        return {"markets": len(market_means), "meanMarketPostMinusPriorAnyRate": observed, "bootstrap95": None}
    rng = random.Random(seed + seconds)
    boot = [statistics.mean(rng.choice(market_means) for _ in market_means) for _ in range(max(100, samples))]
    boot.sort()
    return {
        "markets": len(market_means),
        "meanMarketPostMinusPriorAnyRate": observed,
        "bootstrap95": [boot[int(0.025 * (len(boot) - 1))], boot[int(0.975 * (len(boot) - 1))]],
        "unit": "market-blocked mean of per-market Maker-anchor next-bucket postAny-priorAny",
    }


def _bucket_summary(records: list[dict[str, Any]], seconds: int, bootstrap_samples: int) -> dict[str, Any]:
    prior = _event_stats(records, f"prior_{seconds}s")
    post = _event_stats(records, f"post_{seconds}s")
    prior_rate = prior["anyTakerRate"]
    post_rate = post["anyTakerRate"]
    return {
        "bucketWindowSeconds": seconds,
        "sameSecondExcludedAsAmbiguous": True,
        "prior": prior,
        "post": post,
        "rawPostMinusPriorAnyRate": (
            float(post_rate - prior_rate) if post_rate is not None and prior_rate is not None else None
        ),
        "rawPostVsPriorAnyRateRatio": (
            float(post_rate / prior_rate) if post_rate is not None and prior_rate not in (None, 0) else None
        ),
        "marketBlockInference": _market_block_ci(records, seconds, samples=bootstrap_samples),
    }


def _fill_speed_bin(value: float | None) -> str:
    if value is None:
        return "UNKNOWN"
    if value <= 250:
        return "LE_250MS"
    if value <= 500:
        return "250_500MS"
    if value <= 1000:
        return "500_1000MS"
    if value <= 1500:
        return "1000_1500MS"
    if value <= 2000:
        return "1500_2000MS"
    return "GT_2000MS"


FILL_SPEED_ORDER = ["LE_250MS", "250_500MS", "500_1000MS", "1000_1500MS", "1500_2000MS", "GT_2000MS"]


def _slow_fill_hazard(records: list[dict[str, Any]], bootstrap_samples: int) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[_fill_speed_bin(record.get("fill_duration_ms"))].append(record)
    rows: list[dict[str, Any]] = []
    rates: list[float] = []
    for label in FILL_SPEED_ORDER + (["UNKNOWN"] if grouped.get("UNKNOWN") else []):
        subset = grouped.get(label, [])
        if not subset:
            continue
        same = _event_stats(subset, "same_second")
        one = _bucket_summary(subset, 1, bootstrap_samples)
        post_rate = one["post"]["anyTakerRate"]
        if label != "UNKNOWN" and post_rate is not None:
            rates.append(float(post_rate))
        rows.append({
            "fillDurationBin": label,
            "makerAnchors": len(subset),
            "sameSecondAmbiguous": same,
            "nextFullSecond": one,
        })
    increasing = sum(int(b >= a) for a, b in zip(rates, rates[1:]))
    return {
        "durationSource": "8778 TARGET_FILL_DECREASE allocation source_ms when available; V3 target-event path only as fallback",
        "rows": rows,
        "postNextSecondMonotonicNonDecreasingTransitions": increasing,
        "postNextSecondTransitions": max(0, len(rates) - 1),
        "interpretation": (
            "A monotonic increase would support a slow-fill hazard hypothesis. Same-second Taker events are reported separately "
            "because second-quantized Taker timestamps cannot establish within-second ordering."
        ),
    }


def _reverse_bucket_summary(
    records: list[dict[str, Any]], takers_by_market: dict[int, list[dict[str, Any]]]
) -> dict[str, Any]:
    maker_buckets: dict[int, set[int]] = defaultdict(set)
    for record in records:
        maker_buckets[int(record["market_id"])].add(int(record["anchor_second_bucket"]))
    result: dict[str, Any] = {}
    for seconds in BUCKET_WINDOWS_SECONDS:
        total = prior = post = same = 0
        for market_id, takers in takers_by_market.items():
            buckets = maker_buckets.get(market_id, set())
            if not buckets:
                continue
            for event in takers:
                bucket = int(event["secondBucket"])
                total += 1
                same += int(bucket in buckets)
                prior += int(any((bucket - offset) in buckets for offset in range(1, seconds + 1)))
                post += int(any((bucket + offset) in buckets for offset in range(1, seconds + 1)))
        result[str(seconds)] = {
            "targetTakerAnchors": total,
            "sameSecondMakerRateAmbiguous": same / total if total else None,
            "makerInPriorFullBucketsRate": prior / total if total else None,
            "makerInPostFullBucketsRate": post / total if total else None,
            "postMinusPriorRate": (post - prior) / total if total else None,
        }
    return result


def analyze_link_v2(
    *,
    maker_dataset_path: Path = DEFAULT_MAKER_DATASET,
    maker_db_path: Path = DEFAULT_MAKER_DB,
    shadow_db_path: Path = DEFAULT_SHADOW_DB,
    report_path: Path = DEFAULT_REPORT,
    bootstrap_samples: int = 2000,
) -> dict[str, Any]:
    maker_rows = _load_maker_rows(maker_dataset_path)
    maker_db = _connect_readonly(maker_db_path)
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
        highres = _highres_fill_evidence(maker_db)

        eligible = [
            row for row in maker_rows
            if int(float(row["last_target_ms"])) >= deployed_at_ms
            and (excluded_market_id is None or int(float(row["market_id"])) != excluded_market_id)
        ]
        records = [
            _record_for_maker(
                row,
                highres.get(str(row["parent_id"])),
                takers_by_market.get(int(float(row["market_id"])), []),
            )
            for row in eligible
        ]
        highres_count = sum(int(record["highres_evidence"]) for record in records)
        report: dict[str, Any] = {
            "reportVersion": REPORT_VERSION,
            "paperResearchOnly": True,
            "automaticStrategyPromotion": False,
            "causalClaim": False,
            "makerDataset": str(maker_dataset_path),
            "makerDb": str(maker_db_path),
            "shadowDb": str(shadow_db_path),
            "targetTakerCohort": TAKER_COHORT,
            "eligibleMakerAnchors": len(records),
            "eligibleMakerMarkets": len({record["market_id"] for record in records}),
            "makerAnchorsWithHighResolution8778FillEvidence": highres_count,
            "highResolutionMakerAnchorCoverage": highres_count / len(records) if records else None,
            "timestampDiagnostics": _timestamp_diagnostics(takers_by_market),
            "sameSecondAmbiguous": _event_stats(records, "same_second"),
            "bucketWindows": {
                str(seconds): _bucket_summary(records, seconds, bootstrap_samples)
                for seconds in BUCKET_WINDOWS_SECONDS
            },
            "slowFillTakerHazard": _slow_fill_hazard(records, bootstrap_samples),
            "reverseTakerAnchorBuckets": _reverse_bucket_summary(records, takers_by_market),
            "interpretationBoundary": (
                "8778 allocation source_ms supplies high-resolution Maker fill evidence, but target Taker target_event_ms is treated "
                "as second-quantized. V2 therefore reports same-second events as ordering-ambiguous and uses only complete prior/post "
                "second buckets for temporal asymmetry. This is observational association, not causality."
            ),
        }
        resolved = report_path.expanduser().resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        temp = resolved.with_suffix(resolved.suffix + ".tmp")
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(resolved)
        return report
    finally:
        maker_db.close()
        shadow.close()
