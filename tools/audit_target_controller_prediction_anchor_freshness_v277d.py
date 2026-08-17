from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

import audit_target_controller_prediction_clock_history_v277c as v277c
import analyze_target_controller_prediction_raw_unseen_v277 as v277

v276 = v277.v276
base = v276.base
ROOT = base.ROOT
VERSION = "TARGET_CONTROLLER_PREDICTION_ANCHOR_FRESHNESS_V277D"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_controller_prediction_anchor_freshness_v277d_report.json"


def _num(value: Any) -> float | None:
    return base._num(value)


def _quantile(values: list[float], q: float) -> float | None:
    xs = sorted(float(x) for x in values)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = max(0.0, min(1.0, float(q))) * (len(xs) - 1)
    lo = int(pos)
    hi = min(len(xs) - 1, lo + 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def _anchor_stats(
    market_id: int,
    rows: list[Mapping[str, Any]],
    audited: Mapping[tuple[int, int], Mapping[str, Any]],
    offset_ms: int,
) -> dict[str, Any]:
    requested = 0
    fresh = 0
    reconstructable = 0
    valid_mid = 0
    ages: list[float] = []
    negative_age = 0
    for row in rows:
        requested += 1
        t = int(row["sample_ms"]) - int(offset_ms)
        pred = audited.get((int(market_id), t))
        if not pred:
            continue
        reconstructable += int(bool(pred.get("reconstructable")))
        valid_mid += int(bool(pred.get("valid_mid")))
        fresh += int(bool(pred.get("fresh_2s")))
        age = _num(pred.get("latest_age_ms"))
        if age is not None:
            ages.append(age)
            negative_age += int(age < 0)
    return {
        "requested": requested,
        "fresh2s": fresh,
        "fresh2sRate": fresh / requested if requested else None,
        "reconstructable": reconstructable,
        "reconstructableRate": reconstructable / requested if requested else None,
        "validMid": valid_mid,
        "validMidRate": valid_mid / requested if requested else None,
        "latestAgeMsMedian": _quantile(ages, 0.50),
        "latestAgeMsP90": _quantile(ages, 0.90),
        "latestAgeMsP95": _quantile(ages, 0.95),
        "latestAgeMsMax": max(ages) if ages else None,
        "negativeAgeCount": negative_age,
    }


def _span_supported(
    market_id: int,
    row: Mapping[str, Any],
    audited: Mapping[tuple[int, int], Mapping[str, Any]],
) -> bool:
    t = int(row["sample_ms"])
    points: list[tuple[int, float]] = []
    for offset in v277c.ANCHOR_OFFSETS_MS:
        anchor_ms = t - int(offset)
        pred = audited.get((int(market_id), anchor_ms))
        value = v277c.v272._fresh_mid(pred)
        if value is not None:
            points.append((anchor_ms, value))
    points.sort()
    if len(points) < 2:
        return False
    return (points[-1][0] - points[0][0]) / 3000.0 >= 0.65


def _market_row(
    meta: Mapping[str, Any],
    joined_rows: list[Mapping[str, Any]],
    audited: Mapping[tuple[int, int], Mapping[str, Any]],
    retained_updates: int,
) -> dict[str, Any]:
    market_id = int(meta["marketId"])
    anchors = {
        str(offset): _anchor_stats(market_id, joined_rows, audited, int(offset))
        for offset in v277c.ANCHOR_OFFSETS_MS
    }
    span_supported = sum(_span_supported(market_id, row, audited) for row in joined_rows)
    return {
        "marketId": market_id,
        "bucketStartMs": int(meta["bucketStartMs"]),
        "bucketEndMs": int(meta["bucketEndMs"]),
        "stateRows": int(meta.get("stateRows") or 0),
        "freshMicroJoinedRows": len(joined_rows),
        "retained8778Updates": int(retained_updates),
        "updatesPerMarketSecond": int(retained_updates) / 300.0,
        "anchorFreshness": anchors,
        "clock3sSpanSupportedRows": span_supported,
        "clock3sSpanSupportedRateOnMicroRows": span_supported / len(joined_rows) if joined_rows else None,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="V2.7.7d no-label audit of per-market receivedStrict Prediction anchor freshness")
    p.add_argument("--previous-report", type=Path, default=v277.DEFAULT_PREVIOUS)
    p.add_argument("--official-db", type=Path, default=v276.DEFAULT_OFFICIAL_DB)
    p.add_argument("--micro-db", type=Path, default=v276.DEFAULT_MICRO_DB)
    p.add_argument("--book-db", type=Path, default=v276.DEFAULT_BOOK_DB)
    p.add_argument("--asset", default="BTC")
    p.add_argument("--cutover", default=v276.hazard.v2.DEFAULT_CUTOVER)
    p.add_argument("--gap-minutes", type=float, default=30.0)
    p.add_argument("--candidate-markets", type=int, default=96)
    p.add_argument("--completion-grace-ms", type=int, default=5000)
    p.add_argument("--idle-gap-ms", type=int, default=1000)
    p.add_argument("--burst-cap-ms", type=int, default=3000)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = p.parse_args()

    excluded = v277._previous_exclusions(args.previous_report)
    raw_events, source_audit = v276.hazard.compat._load_source_compat(args.official_db, "OFFICIAL", str(args.asset).upper())
    events, selection_audit = v276.hazard._select(raw_events, v276.hazard.v2._parse_ms(args.cutover))
    states, candidate_meta, recent_source = v276._build_recent_states(
        events, args.gap_minutes, args.candidate_markets, args.completion_grace_ms,
        args.idle_gap_ms, args.burst_cap_ms,
    )
    candidate_meta = [m for m in candidate_meta if int(m["marketId"]) not in excluded]
    allowed = {int(m["marketId"]) for m in candidate_meta}
    states = [r for r in states if int(r["market_id"]) in allowed]
    states.sort(key=lambda r: (int(r["sample_ms"]), int(r["market_id"])))
    if not states:
        raise RuntimeError("no unseen recent states after exclusions")

    start_ms = min(int(r["sample_ms"]) for r in states)
    end_ms = max(int(r["sample_ms"]) for r in states)
    snapshots, _ = base._load_micro(args.micro_db, start_ms, end_ms)
    joined, micro_join_audit = base.build_training_rows(states, snapshots, {})
    audited, anchor_audit = v277c._load_clock_audit(joined, args.book_db)

    joined_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in joined:
        joined_by_market[int(row["market_id"])].append(row)
    meta_by_market = {int(m["marketId"]): m for m in candidate_meta}
    retained = anchor_audit.get("retained8778UpdatesByTargetMarket") or {}

    markets: list[dict[str, Any]] = []
    for market_id, rows in joined_by_market.items():
        meta = meta_by_market.get(market_id)
        if meta is None:
            continue
        markets.append(_market_row(meta, rows, audited, int(retained.get(str(market_id), 0))))
    markets.sort(key=lambda m: (int(m["bucketStartMs"]), int(m["marketId"])), reverse=True)

    report = {
        "version": VERSION,
        "policy": {
            "purpose": "Diagnose whether the newest markets have enough 8778 updates but fail the existing <=2s receivedStrict freshness rule due to timestamp age/staleness.",
            "exclusions": sorted(excluded),
            "anchorOffsetsMs": list(v277c.ANCHOR_OFFSETS_MS),
            "freshnessMs": 2000,
            "receivedStrict": True,
            "noLabelInspection": True,
            "noModelFitting": True,
            "noThresholdChanges": True,
            "noLiveTradingChanges": True,
        },
        "source": {
            "sourceAudit": source_audit,
            "selectionAudit": selection_audit,
            "recentSession": recent_source,
            "statesAfterExclusion": len(states),
            "microSnapshotsLoaded": len(snapshots),
            "freshMicroJoinedRows": len(joined),
            "microJoinAudit": dict(micro_join_audit),
            "clockAnchorAudit": anchor_audit,
        },
        "marketsWithFreshMicro": len(markets),
        "markets": markets,
        "latest12Markets": markets[:12],
        "decision": {
            "status": "DIAGNOSTIC_ONLY_NO_MODEL_FIT",
            "deployment": "NO_MODEL_FIT",
            "instruction": "Inspect per-market current and t-1/t-2/t-3 fresh2s rates plus age quantiles. Do not relax freshness or support thresholds from this report.",
        },
    }
    out = args.report.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "version": VERSION,
        "marketsWithFreshMicro": len(markets),
        "latest12Markets": [
            {
                "marketId": m["marketId"],
                "microRows": m["freshMicroJoinedRows"],
                "updates": m["retained8778Updates"],
                "currentFresh2sRate": m["anchorFreshness"]["0"]["fresh2sRate"],
                "currentAgeP95Ms": m["anchorFreshness"]["0"]["latestAgeMsP95"],
                "span3sRate": m["clock3sSpanSupportedRateOnMicroRows"],
            }
            for m in markets[:12]
        ],
        "report": str(out),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
