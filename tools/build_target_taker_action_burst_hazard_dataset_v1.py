from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import analyze_target_taker_action_bursts_v2 as burst_v2
from build_target_taker_direct_eligibility_special_regime_official_v2 import (
    DEFAULT_OFFICIAL_TARGET_DB,
    _merge_labels,
)
from predict_bot.target_maker_taker_link import DEFAULT_SHADOW_DB

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_DATASET = ROOT / "data" / "research" / "target_taker_direct_eligibility_special_regime_v1.csv"
DEFAULT_OUTPUT = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.csv"
DEFAULT_META = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.meta.json"
DEFAULT_PREFLIGHT = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1_preflight.json"
DATASET_VERSION = "TARGET_TAKER_ACTION_BURST_HAZARD_V1_CAP2_PRIMARY_CAP3_AUDIT"
HORIZONS = (1, 2, 5)
PRIMARY_CAP = 2
AUDIT_CAP = 3
IDLE_GAP_SECONDS = 1


def _epoch_ms(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        pass
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"time must include timezone offset or be epoch ms, got {value!r}")
    return int(dt.timestamp() * 1000)


def _macro_phase(seconds_left: Any) -> str:
    try:
        value = float(seconds_left)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if not math.isfinite(value):
        return "UNKNOWN"
    if value > 180:
        return "OPEN"
    if value > 60:
        return "MID"
    return "TAIL"


def _bucket(ms: int) -> int:
    return (int(ms) // 1000) * 1000


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(resolved)


def _load_public(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    required = {"market_id", "decision_sampled_at_ms", "seconds_left"}
    missing = sorted(required - set(fieldnames))
    if missing:
        raise RuntimeError(f"public dataset missing columns: {', '.join(missing)}")
    rows.sort(key=lambda row: (int(row["market_id"]), int(row["decision_sampled_at_ms"])))
    return rows, fieldnames


def _build_bursts(parents: list[dict[str, Any]], cap_s: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for index, row in enumerate(parents, 1):
        events.append(
            {
                "market_id": int(row["market_id"]),
                "target_event_ms": int(row["target_event_ms"]),
                "side": str(row.get("side") or "").upper(),
                "event_index": index,
                "parent_id": str(row.get("parent_id") or index),
                "event_type": "PARENT",
                "phase": "UNKNOWN",
                "macro_phase": "UNKNOWN",
            }
        )
    bursts, _episode_summary = burst_v2._build_capped_bursts(
        events,
        idle_gap_s=IDLE_GAP_SECONDS,
        max_duration_s=int(cap_s),
    )
    return bursts


def _index_bursts(bursts: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in bursts:
        by_market[int(row["market_id"])].append(row)
    result: dict[int, dict[str, Any]] = {}
    for market_id, rows in by_market.items():
        ordered = sorted(rows, key=lambda row: (int(row["burst_onset_ms"]), int(row["burst_index"])))
        result[market_id] = {
            "rows": ordered,
            "onsets": [_bucket(int(row["burst_onset_ms"])) for row in ordered],
            "ends": [_bucket(int(row["burst_end_ms"])) for row in ordered],
        }
    return result


def _annotate_one(
    *,
    market_id: int,
    decision_ms: int,
    index: dict[int, dict[str, Any]],
    prefix: str,
) -> dict[str, Any]:
    entry = index.get(int(market_id))
    result: dict[str, Any] = {}
    decision_bucket = _bucket(decision_ms)
    if entry is None:
        result[f"{prefix}_position_state"] = "NO_TARGET_BURST"
        result[f"{prefix}_burst_active"] = 0
        result[f"{prefix}_risk_idle"] = 1
        result[f"{prefix}_risk_post_first_idle"] = 0
        result[f"{prefix}_ms_since_last_burst_end"] = ""
        result[f"{prefix}_next_burst_delta_ms"] = ""
        result[f"{prefix}_next_burst_type"] = ""
        result[f"{prefix}_next_burst_side"] = ""
        for horizon in HORIZONS:
            result[f"{prefix}_label_next_burst_{horizon}s"] = 0
        return result

    rows = entry["rows"]
    onsets = entry["onsets"]
    ends = entry["ends"]
    first_onset = onsets[0]
    result[f"{prefix}_position_state"] = "PRE_FIRST" if decision_bucket < first_onset else "POST_FIRST"

    prior_pos = bisect.bisect_right(onsets, decision_bucket) - 1
    active = 0
    last_end: int | None = None
    if prior_pos >= 0:
        last_end = ends[prior_pos]
        if onsets[prior_pos] <= decision_bucket <= last_end:
            active = 1
    result[f"{prefix}_burst_active"] = active
    result[f"{prefix}_risk_idle"] = int(not active)
    result[f"{prefix}_risk_post_first_idle"] = int(not active and decision_bucket >= first_onset)
    result[f"{prefix}_ms_since_last_burst_end"] = (
        max(0, decision_bucket - last_end) if last_end is not None and decision_bucket > last_end else ""
    )

    # Strict future-second boundary: same-second burst onset is forbidden as a positive label.
    next_pos = bisect.bisect_right(onsets, decision_bucket)
    if next_pos < len(onsets):
        next_onset = onsets[next_pos]
        delta = next_onset - decision_bucket
        next_row = rows[next_pos]
        result[f"{prefix}_next_burst_delta_ms"] = delta
        result[f"{prefix}_next_burst_type"] = str(next_row.get("burst_type") or "")
        result[f"{prefix}_next_burst_side"] = str(next_row.get("side") or "")
        for horizon in HORIZONS:
            result[f"{prefix}_label_next_burst_{horizon}s"] = int(0 < delta <= horizon * 1000)
    else:
        result[f"{prefix}_next_burst_delta_ms"] = ""
        result[f"{prefix}_next_burst_type"] = ""
        result[f"{prefix}_next_burst_side"] = ""
        for horizon in HORIZONS:
            result[f"{prefix}_label_next_burst_{horizon}s"] = 0
    return result


def _stats(
    rows: list[dict[str, Any]],
    *,
    prefix: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> dict[str, Any]:
    selected = []
    for row in rows:
        sampled = int(row["decision_sampled_at_ms"])
        if start_ms is not None and sampled < start_ms:
            continue
        if end_ms is not None and sampled >= end_ms:
            continue
        selected.append(row)

    result: dict[str, Any] = {
        "rows": len(selected),
        "markets": len({int(row["market_id"]) for row in selected}),
        "positionStates": dict(Counter(str(row.get(f"{prefix}_position_state") or "") for row in selected)),
        "activeRows": sum(int(row.get(f"{prefix}_burst_active") or 0) for row in selected),
        "idleRows": sum(int(row.get(f"{prefix}_risk_idle") or 0) for row in selected),
        "postFirstIdleRows": sum(int(row.get(f"{prefix}_risk_post_first_idle") or 0) for row in selected),
        "labels": {},
        "byMacroPhase": {},
    }
    for horizon in (2, 5):
        key = f"{prefix}_label_next_burst_{horizon}s"
        positives = sum(int(row.get(key) or 0) for row in selected)
        idle = [row for row in selected if int(row.get(f"{prefix}_risk_idle") or 0) == 1]
        idle_pos = sum(int(row.get(key) or 0) for row in idle)
        post_idle = [row for row in selected if int(row.get(f"{prefix}_risk_post_first_idle") or 0) == 1]
        post_idle_pos = sum(int(row.get(key) or 0) for row in post_idle)
        result["labels"][f"next{horizon}s"] = {
            "positives": positives,
            "positiveRate": positives / len(selected) if selected else None,
            "idlePositives": idle_pos,
            "idlePositiveRate": idle_pos / len(idle) if idle else None,
            "postFirstIdlePositives": post_idle_pos,
            "postFirstIdlePositiveRate": post_idle_pos / len(post_idle) if post_idle else None,
        }

    for phase in ("OPEN", "MID", "TAIL"):
        phase_rows = [row for row in selected if str(row.get("macro_phase")) == phase]
        phase_payload: dict[str, Any] = {"rows": len(phase_rows)}
        for horizon in (2, 5):
            key = f"{prefix}_label_next_burst_{horizon}s"
            positives = sum(int(row.get(key) or 0) for row in phase_rows)
            idle = [row for row in phase_rows if int(row.get(f"{prefix}_risk_idle") or 0) == 1]
            idle_pos = sum(int(row.get(key) or 0) for row in idle)
            phase_payload[f"next{horizon}sPositiveRate"] = positives / len(phase_rows) if phase_rows else None
            phase_payload[f"next{horizon}sIdlePositiveRate"] = idle_pos / len(idle) if idle else None
        result["byMacroPhase"][phase] = phase_payload
    return result


def _agreement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for horizon in (2, 5):
        a_key = f"cap2_label_next_burst_{horizon}s"
        b_key = f"cap3_label_next_burst_{horizon}s"
        same = sum(int(row.get(a_key) or 0) == int(row.get(b_key) or 0) for row in rows)
        both = sum(int(row.get(a_key) or 0) == 1 and int(row.get(b_key) or 0) == 1 for row in rows)
        either = sum(int(row.get(a_key) or 0) == 1 or int(row.get(b_key) or 0) == 1 for row in rows)
        result[f"next{horizon}s"] = {
            "rowAgreement": same / len(rows) if rows else None,
            "positiveJaccard": both / either if either else None,
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build full-history public risk set labeled by bounded Target Taker action-burst onsets.")
    parser.add_argument("--public-dataset", type=Path, default=DEFAULT_PUBLIC_DATASET)
    parser.add_argument("--legacy-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--official-db", type=Path, default=DEFAULT_OFFICIAL_TARGET_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--meta", type=Path, default=DEFAULT_META)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--special-start", default="2026-08-16T12:00:00+08:00")
    parser.add_argument("--special-end", default=None)
    args = parser.parse_args()

    print(DATASET_VERSION, flush=True)
    print("[1/6] Load existing public per-second feature dataset...", flush=True)
    public_rows, public_fields = _load_public(args.public_dataset)
    print(f"      rows={len(public_rows):,} markets={len({int(r['market_id']) for r in public_rows}):,}", flush=True)

    print("[2/6] Merge full observed Target Taker parent history (legacy + official)...", flush=True)
    parents, source_stats, _deployed, _excluded = _merge_labels(args.legacy_db, args.official_db)
    print(f"      merged parents={len(parents):,} markets={len({int(r['market_id']) for r in parents}):,}", flush=True)

    print("[3/6] Build bounded action bursts: cap=2s primary, cap=3s audit...", flush=True)
    cap2 = _build_bursts(parents, PRIMARY_CAP)
    cap3 = _build_bursts(parents, AUDIT_CAP)
    cap2_index = _index_bursts(cap2)
    cap3_index = _index_bursts(cap3)
    print(f"      cap2 bursts={len(cap2):,} | cap3 bursts={len(cap3):,}", flush=True)

    print("[4/6] Label every public decision second with strict-future burst onset targets...", flush=True)
    output_rows: list[dict[str, Any]] = []
    for idx, raw in enumerate(public_rows, 1):
        row: dict[str, Any] = dict(raw)
        market_id = int(raw["market_id"])
        sampled = int(raw["decision_sampled_at_ms"])
        row["macro_phase"] = _macro_phase(raw.get("seconds_left"))
        row.update(_annotate_one(market_id=market_id, decision_ms=sampled, index=cap2_index, prefix="cap2"))
        row.update(_annotate_one(market_id=market_id, decision_ms=sampled, index=cap3_index, prefix="cap3"))
        output_rows.append(row)
        if idx % 50000 == 0:
            print(f"      labeled {idx:,}/{len(public_rows):,}", flush=True)

    extra_fields = ["macro_phase"]
    for prefix in ("cap2", "cap3"):
        extra_fields.extend(
            [
                f"{prefix}_position_state",
                f"{prefix}_burst_active",
                f"{prefix}_risk_idle",
                f"{prefix}_risk_post_first_idle",
                f"{prefix}_ms_since_last_burst_end",
                f"{prefix}_next_burst_delta_ms",
                f"{prefix}_next_burst_type",
                f"{prefix}_next_burst_side",
            ]
        )
        extra_fields.extend(f"{prefix}_label_next_burst_{h}s" for h in HORIZONS)
    fields = list(dict.fromkeys(public_fields + extra_fields))
    resolved_output = args.output.expanduser().resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved_output.with_suffix(resolved_output.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(output_rows)
    temp.replace(resolved_output)

    print("[5/6] Build coverage + label preflight...", flush=True)
    special_start = _epoch_ms(args.special_start)
    special_end = _epoch_ms(args.special_end)
    if special_start is None:
        raise SystemExit("--special-start is required")
    historical = [row for row in output_rows if int(row["decision_sampled_at_ms"]) < special_start]
    special = [
        row for row in output_rows
        if int(row["decision_sampled_at_ms"]) >= special_start
        and (special_end is None or int(row["decision_sampled_at_ms"]) < special_end)
    ]
    preflight = {
        "datasetVersion": DATASET_VERSION,
        "specialStartMs": special_start,
        "specialEndMs": special_end,
        "rows": len(output_rows),
        "markets": len({int(row["market_id"]) for row in output_rows}),
        "mergedParents": len(parents),
        "mergedParentMarkets": len({int(row["market_id"]) for row in parents}),
        "cap2Bursts": len(cap2),
        "cap3Bursts": len(cap3),
        "cap2BurstMarkets": len(cap2_index),
        "cap3BurstMarkets": len(cap3_index),
        "historical": _stats(historical, prefix="cap2"),
        "special": _stats(special, prefix="cap2"),
        "cap3AuditSpecial": _stats(special, prefix="cap3"),
        "cap2VsCap3AgreementAllRows": _agreement(output_rows),
        "cap2VsCap3AgreementSpecialRows": _agreement(special),
        "targetLabelSources": source_stats,
        "boundary": "Burst labels use bisect_right(onset_bucket, decision_bucket): same-second Target burst onset is never a positive future label.",
    }
    failures: list[str] = []
    if not historical:
        failures.append("no historical public rows before special start")
    if not special:
        failures.append("no special public rows")
    if preflight["historical"]["labels"]["next5s"]["positives"] <= 0:
        failures.append("no historical 5s burst positives")
    if preflight["special"]["labels"]["next5s"]["positives"] <= 0:
        failures.append("no special 5s burst positives")
    if preflight["special"]["labels"]["next5s"]["postFirstIdlePositives"] <= 0:
        failures.append("no special post-first idle 5s burst positives")
    preflight["status"] = "FAILED" if failures else "OK"
    preflight["failures"] = failures
    _write_json(args.preflight, preflight)

    meta = {
        "datasetVersion": DATASET_VERSION,
        "publicDataset": str(args.public_dataset.expanduser().resolve()),
        "legacyDb": str(args.legacy_db.expanduser().resolve()),
        "officialDb": str(args.official_db.expanduser().resolve()),
        "output": str(resolved_output),
        "rows": len(output_rows),
        "idleGapSeconds": IDLE_GAP_SECONDS,
        "primaryActionBurstCapSeconds": PRIMARY_CAP,
        "auditActionBurstCapSeconds": AUDIT_CAP,
        "strictFutureSecond": True,
        "riskDefinitions": {
            "risk_idle": "decision second is outside the current retrospective Target action-burst interval",
            "risk_post_first_idle": "outside active burst and after the market's first Target burst onset",
        },
        "targetLabelSources": source_stats,
    }
    _write_json(args.meta, meta)

    print("[6/6] Preflight summary", flush=True)
    print(json.dumps(preflight, ensure_ascii=False, indent=2), flush=True)
    if failures:
        raise SystemExit("BURST HAZARD PREFLIGHT FAILED: " + "; ".join(failures))
    print("TARGET_TAKER_ACTION_BURST_HAZARD_V1 PREFLIGHT OK", flush=True)
    print(f"dataset: {resolved_output}", flush=True)
    print(f"preflight: {args.preflight.expanduser().resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
