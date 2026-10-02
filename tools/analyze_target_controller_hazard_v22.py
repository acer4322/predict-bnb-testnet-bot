from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
REPORT_VERSION = "TARGET_CONTROLLER_HAZARD_V22_COMMON_SUPPORT_MATCHED"
STRESS = "STRESS_2026_08_16"
ORDINARY = "ORDINARY_2026_08_17"
REGIMES = (STRESS, ORDINARY)
HORIZONS = (1, 3, 5, 15)
KINDS = ("taker", "repair", "add")

DEFAULT_STATES = ROOT / "data" / "research" / "target_controller_hazard_v21_states.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_controller_hazard_v22_matched_report.json"
DEFAULT_CELLS = ROOT / "data" / "research" / "target_controller_hazard_v22_matched_cells.csv"

CORE_FEATURES = ("risk_deficit", "abs_payoff_gap", "seconds_left")
CORE_PLUS_MAKER_GAP = ("risk_deficit", "abs_payoff_gap", "maker_abs_payoff_gap", "seconds_left")
COMMON_SURFACE_FEATURES = (
    "risk_deficit",
    "abs_payoff_gap",
    "maker_abs_payoff_gap",
    "seconds_left",
    "risk_growth_5s",
    "gap_growth_5s",
    "maker_gap_growth_5s",
    "same_payoff_gap_sign_age_ms",
    "same_maker_gap_sign_age_ms",
)
POLICY_DEPENDENT_FEATURES = (
    "time_since_last_taker_ms",
    "maker_streak_age_ms",
    "maker_shares_since_last_taker",
    "maker_notional_since_last_taker",
    "max_risk_since_taker_reset",
    "max_gap_since_taker_reset",
)

CELL_FIELDS = [
    "spec", "cell", "stress_n", "ordinary_n", "matched_weight",
    *[
        f"{regime}_{kind}_{h}s"
        for regime in ("stress", "ordinary")
        for h in HORIZONS
        for kind in KINDS
    ],
]


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _label(row: dict[str, Any], kind: str, horizon: int) -> int:
    raw = row.get(f"{kind}_within_{horizon}s")
    try:
        return 1 if int(float(raw)) != 0 else 0
    except (TypeError, ValueError):
        return 0


def _quantile(values: Iterable[float], q: float) -> float | None:
    xs = sorted(float(v) for v in values if math.isfinite(float(v)))
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    q = min(1.0, max(0.0, float(q)))
    pos = q * (len(xs) - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def _pooled_edges(rows: list[dict[str, Any]], feature: str, bins: int) -> list[float]:
    values = [_finite(r.get(feature)) for r in rows]
    xs = [v for v in values if v is not None]
    if len(xs) < 2 or bins <= 1:
        return []
    candidates = [_quantile(xs, i / bins) for i in range(1, bins)]
    edges: list[float] = []
    for value in candidates:
        if value is None:
            continue
        if not edges or value > edges[-1]:
            edges.append(float(value))
    return edges


def _bin(value: Any, edges: list[float]) -> int | None:
    number = _finite(value)
    if number is None:
        return None
    return bisect.bisect_right(edges, number)


def _rate(rows: list[dict[str, Any]], kind: str, horizon: int) -> float | None:
    if not rows:
        return None
    return sum(_label(r, kind, horizon) for r in rows) / len(rows)


def _ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b <= 0:
        return None
    return a / b


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def _load_states(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        required = {
            "regime", "market_id", "sample_ms", "seconds_left",
            "risk_deficit", "abs_payoff_gap", "maker_abs_payoff_gap",
            *[f"{kind}_within_{h}s" for h in HORIZONS for kind in KINDS],
        }
        missing = sorted(required - fields)
        if missing:
            raise RuntimeError("states CSV missing columns: " + ", ".join(missing))
        rows = [dict(row) for row in reader if str(row.get("regime") or "") in REGIMES]
    return rows, {
        "path": str(resolved),
        "rows": len(rows),
        "stressRows": sum(r["regime"] == STRESS for r in rows),
        "ordinaryRows": sum(r["regime"] == ORDINARY for r in rows),
        "markets": len({(r["regime"], r["market_id"]) for r in rows}),
    }


def _common_surface(
    rows: list[dict[str, Any]], feature: str, bins: int
) -> dict[str, Any]:
    edges = _pooled_edges(rows, feature, bins)
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        idx = _bin(row.get(feature), edges)
        if idx is not None:
            grouped[(row["regime"], idx)].append(row)

    out = []
    for idx in range(len(edges) + 1):
        sr = grouped.get((STRESS, idx), [])
        orows = grouped.get((ORDINARY, idx), [])
        values = [
            _finite(r.get(feature))
            for r in sr + orows
            if _finite(r.get(feature)) is not None
        ]
        if not values:
            continue
        record: dict[str, Any] = {
            "bin": idx + 1,
            "min": min(values),
            "max": max(values),
            "stressN": len(sr),
            "ordinaryN": len(orows),
        }
        for h in (5, 15):
            record[f"takerStress{h}s"] = _rate(sr, "taker", h)
            record[f"takerOrdinary{h}s"] = _rate(orows, "taker", h)
            record[f"takerRatio{h}s"] = _ratio(
                record[f"takerStress{h}s"], record[f"takerOrdinary{h}s"]
            )
            record[f"repairStress{h}s"] = _rate(sr, "repair", h)
            record[f"repairOrdinary{h}s"] = _rate(orows, "repair", h)
            record[f"addStress{h}s"] = _rate(sr, "add", h)
            record[f"addOrdinary{h}s"] = _rate(orows, "add", h)
        out.append(record)
    return {"feature": feature, "pooledEdges": edges, "bins": out}


def _matched_spec(
    rows: list[dict[str, Any]],
    *,
    name: str,
    features: tuple[str, ...],
    bins: int,
    min_per_regime: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    edges = {feature: _pooled_edges(rows, feature, bins) for feature in features}
    cells: dict[tuple[int, ...], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: {STRESS: [], ORDINARY: []}
    )
    excluded_missing = 0
    for row in rows:
        idxs = tuple(_bin(row.get(feature), edges[feature]) for feature in features)
        if any(idx is None for idx in idxs):
            excluded_missing += 1
            continue
        cells[tuple(int(idx) for idx in idxs)][row["regime"]].append(row)

    eligible = []
    cell_rows: list[dict[str, Any]] = []
    for cell, by_regime in sorted(cells.items()):
        sr, orows = by_regime[STRESS], by_regime[ORDINARY]
        if len(sr) < min_per_regime or len(orows) < min_per_regime:
            continue
        weight = min(len(sr), len(orows))
        eligible.append((cell, sr, orows, weight))
        flat: dict[str, Any] = {
            "spec": name,
            "cell": "|".join(map(str, cell)),
            "stress_n": len(sr),
            "ordinary_n": len(orows),
            "matched_weight": weight,
        }
        for regime_key, cell_data in (("stress", sr), ("ordinary", orows)):
            for h in HORIZONS:
                for kind in KINDS:
                    flat[f"{regime_key}_{kind}_{h}s"] = _rate(cell_data, kind, h)
        cell_rows.append(flat)

    matched_weight = sum(weight for _, _, _, weight in eligible)
    result: dict[str, Any] = {
        "features": list(features),
        "binsPerFeatureRequested": bins,
        "pooledEdges": edges,
        "minPerRegimePerCell": min_per_regime,
        "cellsTotal": len(cells),
        "cellsEligible": len(eligible),
        "matchedPairsWeight": matched_weight,
        "stressCoverage": matched_weight / max(1, sum(r["regime"] == STRESS for r in rows)),
        "ordinaryCoverage": matched_weight / max(1, sum(r["regime"] == ORDINARY for r in rows)),
        "excludedMissingFeatureRows": excluded_missing,
        "hazard": {},
    }
    for h in HORIZONS:
        result["hazard"][f"{h}s"] = {}
        for kind in KINDS:
            if matched_weight <= 0:
                stress_rate = ordinary_rate = None
            else:
                stress_rate = sum(
                    weight * (_rate(sr, kind, h) or 0.0)
                    for _, sr, _, weight in eligible
                ) / matched_weight
                ordinary_rate = sum(
                    weight * (_rate(orows, kind, h) or 0.0)
                    for _, _, orows, weight in eligible
                ) / matched_weight
            result["hazard"][f"{h}s"][kind] = {
                "stress": stress_rate,
                "ordinary": ordinary_rate,
                "ratio": _ratio(stress_rate, ordinary_rate),
                "difference": (
                    stress_rate - ordinary_rate
                    if stress_rate is not None and ordinary_rate is not None
                    else None
                ),
            }
    return result, cell_rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Common-support matched comparison of Target Controller V2.1 fixed-grid states."
    )
    parser.add_argument("--states", type=Path, default=DEFAULT_STATES)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--cells", type=Path, default=DEFAULT_CELLS)
    parser.add_argument("--bins", type=int, default=4)
    parser.add_argument("--surface-bins", type=int, default=5)
    parser.add_argument("--min-per-regime", type=int, default=10)
    args = parser.parse_args()

    rows, source = _load_states(args.states)
    if not rows:
        raise SystemExit("no V2.1 states found")
    bins = max(2, int(args.bins))
    surface_bins = max(2, int(args.surface_bins))
    minimum = max(1, int(args.min_per_regime))

    specs = {}
    all_cells: list[dict[str, Any]] = []
    for name, features in (
        ("CORE_RISK_GAP_TIME", CORE_FEATURES),
        ("CORE_PLUS_MAKER_GAP", CORE_PLUS_MAKER_GAP),
    ):
        report, cells = _matched_spec(
            rows, name=name, features=features, bins=bins, min_per_regime=minimum
        )
        specs[name] = report
        all_cells.extend(cells)

    common = {
        feature: _common_surface(rows, feature, surface_bins)
        for feature in COMMON_SURFACE_FEATURES
        if any(feature in row for row in rows)
    }
    policy_dependent = {
        feature: _common_surface(rows, feature, surface_bins)
        for feature in POLICY_DEPENDENT_FEATURES
        if any(feature in row for row in rows)
    }

    payload = {
        "reportVersion": REPORT_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "paperResearchOnly": True,
        "noModelFit": True,
        "noStrategyPromotion": True,
        "purpose": (
            "Test whether the 2026-08-16 stress Taker hazard remains elevated after "
            "putting stress and ordinary states on common pooled bins and common support."
        ),
        "source": source,
        "matchingPolicy": {
            "regimes": list(REGIMES),
            "pooledEdgesSharedAcrossRegimes": True,
            "balancedCellWeight": "min(stress_n, ordinary_n)",
            "coreControls": list(CORE_FEATURES),
            "corePlusMakerGapControls": list(CORE_PLUS_MAKER_GAP),
            "explicitlyNotUsedAsCoreControls": list(POLICY_DEPENDENT_FEATURES),
            "whyExcluded": (
                "These lifecycle-reset variables are partly caused by prior Taker policy. "
                "Conditioning on them can mechanically absorb the regime effect (post-treatment/circular control)."
            ),
        },
        "matched": specs,
        "commonOneDimensionalSurfaces": common,
        "policyDependentDiagnosticSurfaces": policy_dependent,
        "guardrails": [
            "Input is the V2.1 fixed-grid strict-past state table; this tool does not reopen SQLite.",
            "Matching is descriptive coarsened common-support adjustment, not causal identification.",
            "Do not interpret time-since-last-Taker or since-reset accumulators as exogenous confounders.",
            "No EBM/model fitting and no strategy promotion.",
        ],
        "outputs": {"report": str(args.report), "cellsCsv": str(args.cells)},
    }
    _write_csv(args.cells, all_cells, CELL_FIELDS)
    _write_json(args.report, payload)

    print(REPORT_VERSION)
    print(f"states={len(rows):,} stress={source['stressRows']:,} ordinary={source['ordinaryRows']:,}")
    for name, report in specs.items():
        h5 = report["hazard"]["5s"]["taker"]
        print(
            f"{name}: cells={report['cellsEligible']}/{report['cellsTotal']} "
            f"matched_weight={report['matchedPairsWeight']:,} "
            f"5s stress={h5['stress']} ordinary={h5['ordinary']} ratio={h5['ratio']}"
        )
    print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
