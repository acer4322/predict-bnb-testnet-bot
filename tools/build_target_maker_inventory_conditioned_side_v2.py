from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import analyze_target_maker_taker_inventory_lifecycle_v1 as lifecycle

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BEHAVIOR = ROOT / "data" / "research" / "target_maker_direct_behavior_v1.csv"
DEFAULT_TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
DEFAULT_PUBLIC_DATASET = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.csv"
DEFAULT_OUTPUT = ROOT / "data" / "research" / "target_maker_inventory_conditioned_side_v2.csv"
DEFAULT_META = ROOT / "data" / "research" / "target_maker_inventory_conditioned_side_v2.meta.json"
DEFAULT_SPECIAL_START = "2026-08-16T12:00:00+08:00"
DATASET_VERSION = "TARGET_MAKER_INVENTORY_CONDITIONED_SIDE_V2_STRICT_PAST_OFFICIAL_FILLS"
EPS = 1e-9

MAKER_INVENTORY_FEATURES = [
    "maker_up_net_shares",
    "maker_down_net_shares",
    "maker_delta_shares",
    "maker_abs_delta_shares",
    "maker_gross_shares",
    "maker_paired_long_shares",
    "maker_delta_fraction",
    "prior_maker_parent_count",
    "prior_maker_up_parent_count",
    "prior_maker_down_parent_count",
    "last_maker_side_up",
    "last_maker_age_ms",
]

TAKER_FEEDBACK_FEATURES = [
    "taker_up_net_shares",
    "taker_down_net_shares",
    "taker_delta_shares",
    "taker_abs_delta_shares",
    "prior_taker_parent_count",
    "prior_taker_up_parent_count",
    "prior_taker_down_parent_count",
    "last_taker_side_up",
    "last_taker_age_ms",
]

COMBINED_INVENTORY_FEATURES = [
    "combined_up_net_shares",
    "combined_down_net_shares",
    "combined_delta_shares",
    "combined_abs_delta_shares",
    "combined_gross_shares",
    "combined_paired_long_shares",
    "combined_delta_fraction",
]

INVENTORY_FEATURES = MAKER_INVENTORY_FEATURES + TAKER_FEEDBACK_FEATURES + COMBINED_INVENTORY_FEATURES
EXTRA_METADATA = [
    "inventory_dataset_version",
    "regime",
    "maker_placement_index_market",
    "is_first_maker_placement",
    "strict_past_official_event_count",
    "strict_past_same_timestamp_excluded",
]


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return fields, rows


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({column: row.get(column, "") for column in columns} for row in rows)
    temp.replace(resolved)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(resolved)


def _fraction(delta: float, gross: float) -> float:
    return float(delta) / float(gross) if float(gross) > EPS else 0.0


def _state_features(
    state: dict[str, float],
    *,
    maker_seen: set[str],
    taker_seen: set[str],
    maker_side_seen: dict[str, set[str]],
    taker_side_seen: dict[str, set[str]],
    last_role_event: dict[str, dict[str, Any] | None],
    placement_ms: int,
) -> dict[str, Any]:
    metrics = lifecycle._state_metrics(state)
    maker_up = float(metrics["maker_up"])
    maker_down = float(metrics["maker_down"])
    taker_up = float(metrics["taker_up"])
    taker_down = float(metrics["taker_down"])
    combined_up = maker_up + taker_up
    combined_down = maker_down + taker_down
    maker_gross = abs(maker_up) + abs(maker_down)
    taker_gross = abs(taker_up) + abs(taker_down)
    combined_gross = abs(combined_up) + abs(combined_down)
    maker_delta = maker_up - maker_down
    taker_delta = taker_up - taker_down
    combined_delta = combined_up - combined_down

    def last_payload(role: str) -> tuple[Any, Any]:
        item = last_role_event.get(role)
        if item is None:
            return "", ""
        side = str(item.get("side") or "").upper()
        at_ms = int(item["event_ms"])
        age = int(placement_ms) - at_ms
        if age < 0:
            raise AssertionError("future Target event entered strict-past inventory state")
        return 1 if side == "UP" else 0 if side == "DOWN" else "", age

    last_maker_side_up, last_maker_age = last_payload("MAKER")
    last_taker_side_up, last_taker_age = last_payload("TAKER")
    return {
        "maker_up_net_shares": maker_up,
        "maker_down_net_shares": maker_down,
        "maker_delta_shares": maker_delta,
        "maker_abs_delta_shares": abs(maker_delta),
        "maker_gross_shares": maker_gross,
        "maker_paired_long_shares": max(0.0, min(maker_up, maker_down)),
        "maker_delta_fraction": _fraction(maker_delta, maker_gross),
        "taker_up_net_shares": taker_up,
        "taker_down_net_shares": taker_down,
        "taker_delta_shares": taker_delta,
        "taker_abs_delta_shares": abs(taker_delta),
        "combined_up_net_shares": combined_up,
        "combined_down_net_shares": combined_down,
        "combined_delta_shares": combined_delta,
        "combined_abs_delta_shares": abs(combined_delta),
        "combined_gross_shares": combined_gross,
        "combined_paired_long_shares": max(0.0, min(combined_up, combined_down)),
        "combined_delta_fraction": _fraction(combined_delta, combined_gross),
        "prior_maker_parent_count": len(maker_seen),
        "prior_taker_parent_count": len(taker_seen),
        "prior_maker_up_parent_count": len(maker_side_seen["UP"]),
        "prior_maker_down_parent_count": len(maker_side_seen["DOWN"]),
        "prior_taker_up_parent_count": len(taker_side_seen["UP"]),
        "prior_taker_down_parent_count": len(taker_side_seen["DOWN"]),
        "last_maker_side_up": last_maker_side_up,
        "last_maker_age_ms": last_maker_age,
        "last_taker_side_up": last_taker_side_up,
        "last_taker_age_ms": last_taker_age,
        "_taker_gross_debug": taker_gross,
    }


def _enrich_market_rows(
    behavior_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    regime: str,
) -> list[dict[str, Any]]:
    ordered_behavior = sorted(
        behavior_rows,
        key=lambda row: (int(float(row["placement_first_ms"])), str(row.get("parent_id") or "")),
    )
    ordered_events = sorted(events, key=lambda row: (int(row["event_ms"]), str(row.get("leg_id") or "")))
    state = lifecycle._empty_state()
    maker_seen: set[str] = set()
    taker_seen: set[str] = set()
    maker_side_seen: dict[str, set[str]] = {"UP": set(), "DOWN": set()}
    taker_side_seen: dict[str, set[str]] = {"UP": set(), "DOWN": set()}
    last_role_event: dict[str, dict[str, Any] | None] = {"MAKER": None, "TAKER": None}
    event_index = 0
    output: list[dict[str, Any]] = []

    for placement_index, source in enumerate(ordered_behavior, 1):
        placement_ms = int(float(source["placement_first_ms"]))
        while event_index < len(ordered_events) and int(ordered_events[event_index]["event_ms"]) < placement_ms:
            event = ordered_events[event_index]
            role = str(event.get("role") or "").upper()
            side = str(event.get("side") or "").upper()
            parent_id = str(event.get("parent_id") or "")
            lifecycle._apply_flow(state, role, side, str(event.get("quote_type") or ""), float(event["shares"]))
            if role == "MAKER":
                maker_seen.add(parent_id)
                if side in maker_side_seen:
                    maker_side_seen[side].add(parent_id)
                last_role_event["MAKER"] = event
            elif role == "TAKER":
                taker_seen.add(parent_id)
                if side in taker_side_seen:
                    taker_side_seen[side].add(parent_id)
                last_role_event["TAKER"] = event
            event_index += 1

        same_timestamp_excluded = 0
        cursor = event_index
        while cursor < len(ordered_events) and int(ordered_events[cursor]["event_ms"]) == placement_ms:
            same_timestamp_excluded += 1
            cursor += 1

        enriched = dict(source)
        enriched.update(
            _state_features(
                state,
                maker_seen=maker_seen,
                taker_seen=taker_seen,
                maker_side_seen=maker_side_seen,
                taker_side_seen=taker_side_seen,
                last_role_event=last_role_event,
                placement_ms=placement_ms,
            )
        )
        enriched.pop("_taker_gross_debug", None)
        enriched.update(
            {
                "inventory_dataset_version": DATASET_VERSION,
                "regime": regime,
                "maker_placement_index_market": placement_index,
                "is_first_maker_placement": int(placement_index == 1),
                "strict_past_official_event_count": event_index,
                "strict_past_same_timestamp_excluded": same_timestamp_excluded,
            }
        )
        output.append(enriched)
    return output


def build_dataset(
    *,
    behavior_path: Path = DEFAULT_BEHAVIOR,
    target_db_path: Path = DEFAULT_TARGET_DB,
    public_dataset_path: Path = DEFAULT_PUBLIC_DATASET,
    output_path: Path = DEFAULT_OUTPUT,
    meta_path: Path = DEFAULT_META,
    special_start: str = DEFAULT_SPECIAL_START,
    asset: str = "BTC",
) -> dict[str, Any]:
    fields, behavior = _read_csv(behavior_path)
    required = {"market_id", "parent_id", "placement_first_ms", "label_side_up"}
    missing = sorted(required - set(fields))
    if missing:
        raise RuntimeError("Maker behavior dataset missing columns: " + ", ".join(missing))

    special_start_ms = lifecycle._epoch_ms(special_start)
    cohort, _, public_meta = lifecycle._load_public_cohorts(
        public_dataset_path,
        special_start_ms=special_start_ms,
    )
    db = lifecycle._connect_ro(target_db_path)
    try:
        events, event_meta = lifecycle._load_official_events(db, asset=str(asset).upper())
    finally:
        db.close()

    behavior_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in behavior:
        market_id = int(float(row["market_id"]))
        row["market_id"] = market_id
        behavior_by_market[market_id].append(row)
    events_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        events_by_market[int(event["market_id"])].append(event)

    output: list[dict[str, Any]] = []
    for market_id in sorted(behavior_by_market):
        output.extend(
            _enrich_market_rows(
                behavior_by_market[market_id],
                events_by_market.get(market_id, []),
                regime=cohort.get(market_id, "OUTSIDE_PUBLIC_COHORT"),
            )
        )

    output.sort(key=lambda row: (int(float(row["placement_first_ms"])), int(row["market_id"]), str(row.get("parent_id") or "")))
    columns = list(fields)
    for column in EXTRA_METADATA + INVENTORY_FEATURES:
        if column not in columns:
            columns.append(column)
    _write_csv(output_path, columns, output)

    ordinary = [row for row in output if row["regime"] == "ORDINARY_PRE_SPECIAL"]
    special = [row for row in output if row["regime"] == "SPECIAL"]
    outside = [row for row in output if row["regime"] == "OUTSIDE_PUBLIC_COHORT"]
    same_timestamp = sum(int(row["strict_past_same_timestamp_excluded"]) for row in output)
    no_prior_maker = sum(int(float(row["prior_maker_parent_count"])) == 0 for row in output)
    meta = {
        "datasetVersion": DATASET_VERSION,
        "paperResearchOnly": True,
        "labelIdentity": "INFERRED_PLACEMENT_SIDE_NOT_PRIVATE_ORDER_GROUND_TRUTH",
        "timestampBoundary": "Official Target fills enter inventory state only when event_ms < placement_first_ms. Same-timestamp fills are excluded.",
        "deploymentSubstitution": "Target strict-past inventory is a research teacher state. A deployable strategy must substitute the bot's own strict-past Maker/Taker inventory.",
        "behaviorInput": str(behavior_path.expanduser().resolve()),
        "targetDb": str(target_db_path.expanduser().resolve()),
        "publicDataset": str(public_dataset_path.expanduser().resolve()),
        "output": str(output_path.expanduser().resolve()),
        "rows": len(output),
        "markets": len({int(row["market_id"]) for row in output}),
        "ordinaryRows": len(ordinary),
        "ordinaryMarkets": len({int(row["market_id"]) for row in ordinary}),
        "specialRows": len(special),
        "specialMarkets": len({int(row["market_id"]) for row in special}),
        "outsideRows": len(outside),
        "sameTimestampOfficialFillLegsExcludedAcrossRows": same_timestamp,
        "rowsWithNoPriorMakerParent": no_prior_maker,
        "rowsWithPriorMakerParent": len(output) - no_prior_maker,
        "inventoryFeatures": INVENTORY_FEATURES,
        "makerInventoryFeatures": MAKER_INVENTORY_FEATURES,
        "takerFeedbackFeatures": TAKER_FEEDBACK_FEATURES,
        "combinedInventoryFeatures": COMBINED_INVENTORY_FEATURES,
        "officialEvents": event_meta,
        "publicCohort": public_meta,
    }
    _write_json(meta_path, meta)
    print(json.dumps(meta, ensure_ascii=False, indent=2), flush=True)
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description="Build strict-past Target Maker inventory-conditioned side dataset V2")
    parser.add_argument("--behavior-dataset", type=Path, default=DEFAULT_BEHAVIOR)
    parser.add_argument("--target-db", type=Path, default=DEFAULT_TARGET_DB)
    parser.add_argument("--public-dataset", type=Path, default=DEFAULT_PUBLIC_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--meta", type=Path, default=DEFAULT_META)
    parser.add_argument("--special-start", default=DEFAULT_SPECIAL_START)
    parser.add_argument("--asset", default="BTC")
    args = parser.parse_args()
    build_dataset(
        behavior_path=args.behavior_dataset,
        target_db_path=args.target_db,
        public_dataset_path=args.public_dataset,
        output_path=args.output,
        meta_path=args.meta,
        special_start=args.special_start,
        asset=args.asset,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
