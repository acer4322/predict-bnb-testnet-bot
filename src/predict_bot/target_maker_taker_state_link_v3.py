from __future__ import annotations

import csv
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from .target_maker_ebm_dataset import DEFAULT_MAKER_DB
from .target_maker_ebm_v3_dataset import (
    DEFAULT_OUTPUT as DEFAULT_MAKER_DATASET,
    POLICY_FEATURE_COLUMNS,
)
from .target_maker_taker_link import DEFAULT_SHADOW_DB, TAKER_COHORT

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "data" / "research" / "target_maker_taker_state_link_v3.csv"
DEFAULT_META_OUTPUT = ROOT / "data" / "research" / "target_maker_taker_state_link_v3.meta.json"
DATASET_VERSION = "TARGET_MAKER_TAKER_STATE_LINK_V3_POSTFILL_STATE_SECOND_BUCKET_LABELS"
WINDOW_SECONDS = (1, 2, 5)

POST_FILL_FEATURES = [
    "highres_fill_duration_ms",
    "highres_allocation_rows",
    "highres_allocated_shares",
    "post_fill_maker_up_shares",
    "post_fill_maker_down_shares",
    "post_fill_maker_delta_shares",
    "post_fill_maker_imbalance_ratio",
    "post_fill_maker_paired_coverage",
    "side_aligned_post_fill_delta_shares",
    "fill_duration_ge_1500ms",
    "fill_duration_ge_2000ms",
]
STATE_FEATURE_COLUMNS = list(dict.fromkeys(POLICY_FEATURE_COLUMNS + POST_FILL_FEATURES))

METADATA_COLUMNS = [
    "dataset_version",
    "parent_id",
    "market_id",
    "order_hash",
    "target_side",
    "native_book_side",
    "last_target_ms",
    "highres_anchor_ms",
    "anchor_second_bucket",
    "highres_evidence",
    "same_second_taker_parents_ambiguous",
    "post_action",
]
LABEL_COLUMNS: list[str] = []
for seconds in WINDOW_SECONDS:
    LABEL_COLUMNS.extend(
        [
            f"label_next_taker_any_{seconds}s",
            f"label_next_taker_inventory_balancing_{seconds}s",
            f"label_next_taker_balancing_given_taker_{seconds}s",
            f"label_next_taker_same_maker_side_{seconds}s",
            f"next_taker_parent_count_{seconds}s",
            f"next_taker_total_shares_{seconds}s",
        ]
    )

OUTPUT_COLUMNS = METADATA_COLUMNS + STATE_FEATURE_COLUMNS + LABEL_COLUMNS


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
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone() is not None


def _load_rows(path: Path) -> list[dict[str, Any]]:
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
    db: sqlite3.Connection,
    deployed_at_ms: int,
    excluded_market_id: int | None,
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


def _events_between(
    events: list[dict[str, Any]],
    low_bucket: int,
    high_bucket: int,
) -> list[dict[str, Any]]:
    if low_bucket > high_bucket:
        return []
    return [
        event
        for event in events
        if low_bucket <= int(event["secondBucket"]) <= high_bucket
    ]


def _post_fill_inventory(row: dict[str, Any]) -> dict[str, float | None]:
    prior_up = _number(row.get("prior_maker_up_shares")) or 0.0
    prior_down = _number(row.get("prior_maker_down_shares")) or 0.0
    filled = max(0.0, _number(row.get("target_filled_shares")) or 0.0)
    side = str(row.get("target_side") or "")
    post_up = prior_up + (filled if side == "UP" else 0.0)
    post_down = prior_down + (filled if side == "DOWN" else 0.0)
    delta = post_up - post_down
    total = post_up + post_down
    imbalance = abs(delta) / total if total > 1e-9 else None
    paired = 1.0 - imbalance if imbalance is not None else None
    sign = 1.0 if side == "UP" else -1.0
    return {
        "post_fill_maker_up_shares": post_up,
        "post_fill_maker_down_shares": post_down,
        "post_fill_maker_delta_shares": delta,
        "post_fill_maker_imbalance_ratio": imbalance,
        "post_fill_maker_paired_coverage": paired,
        "side_aligned_post_fill_delta_shares": delta * sign,
    }


def _is_balancing(post_fill_delta: float | None, taker_side: str) -> bool | None:
    if post_fill_delta is None or abs(post_fill_delta) <= 1e-9:
        return None
    heavy_side = "UP" if post_fill_delta > 0 else "DOWN"
    return taker_side != heavy_side


def _labels(
    *,
    row: dict[str, Any],
    anchor_bucket: int,
    events: list[dict[str, Any]],
    post_fill_delta: float | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    maker_side = str(row.get("target_side") or "")
    for seconds in WINDOW_SECONDS:
        post = _events_between(events, anchor_bucket + 1, anchor_bucket + seconds)
        balancing_flags = [
            flag
            for flag in (
                _is_balancing(post_fill_delta, str(event.get("side") or ""))
                for event in post
            )
            if flag is not None
        ]
        any_taker = int(bool(post))
        any_balancing = int(any(balancing_flags)) if balancing_flags else 0
        result[f"label_next_taker_any_{seconds}s"] = any_taker
        result[f"label_next_taker_inventory_balancing_{seconds}s"] = any_balancing
        result[f"label_next_taker_balancing_given_taker_{seconds}s"] = (
            any_balancing if post and balancing_flags else None
        )
        result[f"label_next_taker_same_maker_side_{seconds}s"] = (
            int(any(str(event.get("side") or "") == maker_side for event in post))
            if post
            else 0
        )
        result[f"next_taker_parent_count_{seconds}s"] = len(post)
        result[f"next_taker_total_shares_{seconds}s"] = sum(
            float(event.get("target_latest_shares") or 0.0) for event in post
        )
    return result


def build_state_link_dataset(
    *,
    maker_dataset_path: Path = DEFAULT_MAKER_DATASET,
    maker_db_path: Path = DEFAULT_MAKER_DB,
    shadow_db_path: Path = DEFAULT_SHADOW_DB,
    output_path: Path = DEFAULT_OUTPUT,
    meta_output_path: Path = DEFAULT_META_OUTPUT,
) -> dict[str, Any]:
    maker_rows = _load_rows(maker_dataset_path)
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
        excluded_market_id = (
            int(meta["excluded_market_id"]) if meta["excluded_market_id"] is not None else None
        )
        takers_by_market = _load_takers(shadow, deployed_at_ms, excluded_market_id)
        highres = _highres_fill_evidence(maker_db)

        eligible = [
            row
            for row in maker_rows
            if int(float(row["last_target_ms"])) >= deployed_at_ms
            and (
                excluded_market_id is None
                or int(float(row["market_id"])) != excluded_market_id
            )
        ]

        output_rows: list[dict[str, Any]] = []
        highres_count = 0
        same_second_count = 0
        for base in eligible:
            parent_id = str(base["parent_id"])
            market_id = int(float(base["market_id"]))
            evidence = highres.get(parent_id)
            target_anchor_ms = int(float(base["last_target_ms"]))
            highres_anchor_ms = (
                int(evidence["lastSourceMs"]) if evidence is not None else target_anchor_ms
            )
            anchor_bucket = highres_anchor_ms // 1000
            events = takers_by_market.get(market_id, [])
            same_second = _events_between(events, anchor_bucket, anchor_bucket)
            highres_count += int(evidence is not None)
            same_second_count += int(bool(same_second))

            row: dict[str, Any] = {
                column: base.get(column) for column in POLICY_FEATURE_COLUMNS
            }
            row.update(
                {
                    "dataset_version": DATASET_VERSION,
                    "parent_id": parent_id,
                    "market_id": market_id,
                    "order_hash": base.get("order_hash"),
                    "target_side": base.get("target_side"),
                    "native_book_side": base.get("native_book_side"),
                    "last_target_ms": target_anchor_ms,
                    "highres_anchor_ms": highres_anchor_ms,
                    "anchor_second_bucket": anchor_bucket,
                    "highres_evidence": int(evidence is not None),
                    "same_second_taker_parents_ambiguous": len(same_second),
                    "post_action": base.get("post_action"),
                    "highres_fill_duration_ms": (
                        float(evidence["fillDurationMs"])
                        if evidence is not None
                        else _number(base.get("path_fill_duration_ms"))
                    ),
                    "highres_allocation_rows": (
                        int(evidence["allocationRows"]) if evidence is not None else None
                    ),
                    "highres_allocated_shares": (
                        float(evidence["allocatedShares"]) if evidence is not None else None
                    ),
                }
            )
            duration = _number(row.get("highres_fill_duration_ms"))
            row["fill_duration_ge_1500ms"] = (
                int(duration >= 1500.0) if duration is not None else None
            )
            row["fill_duration_ge_2000ms"] = (
                int(duration > 2000.0) if duration is not None else None
            )
            inventory = _post_fill_inventory(base)
            row.update(inventory)
            row.update(
                _labels(
                    row=base,
                    anchor_bucket=anchor_bucket,
                    events=events,
                    post_fill_delta=inventory["post_fill_maker_delta_shares"],
                )
            )
            output_rows.append(row)

        output_path = output_path.expanduser().resolve()
        meta_output_path = meta_output_path.expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        meta_output_path.parent.mkdir(parents=True, exist_ok=True)

        temp = output_path.with_suffix(output_path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in output_rows:
                writer.writerow({column: row.get(column) for column in OUTPUT_COLUMNS})
        temp.replace(output_path)

        label_rates: dict[str, Any] = {}
        for label in LABEL_COLUMNS:
            if not label.startswith("label_"):
                continue
            values = [_number(row.get(label)) for row in output_rows]
            known = [value for value in values if value is not None]
            label_rates[label] = {
                "knownRows": len(known),
                "positiveRate": (
                    sum(float(value) for value in known) / len(known) if known else None
                ),
            }

        meta_report = {
            "datasetVersion": DATASET_VERSION,
            "paperResearchOnly": True,
            "automaticStrategyPromotion": False,
            "causalClaim": False,
            "rows": len(output_rows),
            "markets": len({int(row["market_id"]) for row in output_rows}),
            "highResolutionMakerAnchorCoverage": (
                highres_count / len(output_rows) if output_rows else None
            ),
            "anchorsWithSameSecondAmbiguousTakerRate": (
                same_second_count / len(output_rows) if output_rows else None
            ),
            "timestampBoundary": (
                "Maker fill anchor prefers 8778 TARGET_FILL_DECREASE source_ms. Target Taker "
                "target_event_ms is second-quantized; same-second Taker events are metadata only "
                "and excluded from all forward labels."
            ),
            "inventoryBoundary": (
                "Taker repair labels use post-fill Maker inventory: the current parent filled "
                "shares are added to prior UP/DOWN inventory before deciding whether a later "
                "Taker buys the lighter side."
            ),
            "featureColumns": STATE_FEATURE_COLUMNS,
            "labelColumns": LABEL_COLUMNS,
            "labelRates": label_rates,
            "sources": {
                "makerDataset": str(maker_dataset_path),
                "makerDb": str(maker_db_path),
                "shadowDb": str(shadow_db_path),
            },
            "output": str(output_path),
        }
        meta_temp = meta_output_path.with_suffix(meta_output_path.suffix + ".tmp")
        meta_temp.write_text(
            json.dumps(meta_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        meta_temp.replace(meta_output_path)
        return meta_report
    finally:
        maker_db.close()
        shadow.close()
