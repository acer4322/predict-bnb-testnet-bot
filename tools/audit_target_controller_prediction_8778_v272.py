from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import audit_target_maker_8778_predict_book_coverage_v2_5b as bookmod

REPORT_VERSION = "TARGET_CONTROLLER_PREDICTION_8778_AUDIT_V272"
STRESS = "STRESS_2026_08_16"
DEFAULT_STATES = ROOT / "data" / "research" / "target_controller_hazard_v21_states.csv"
DEFAULT_BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
DEFAULT_OUT = ROOT / "data" / "research" / "target_controller_prediction_8778_audit_v272.json"
DEFAULT_START = "2026-08-16T05:40:05.009737+08:00"
DEFAULT_END = "2026-08-16T06:00:02.210840+08:00"
FRESHNESS_BUCKETS_MS = (2000, 5000, 10000, 30000)
PRIMARY_MODE = "receivedStrict"
SECONDARY_MODE = "sourceStrict"


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _finite(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def _open_ro(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    uri = resolved.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _load_states(path: Path, start_ms: int, end_ms: int) -> list[dict[str, int]]:
    rows: list[dict[str, int]] = []
    with path.expanduser().resolve().open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"market_id", "regime", "sample_ms"}
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise RuntimeError("V2.1 states CSV missing columns: " + ", ".join(missing))
        for raw in reader:
            if str(raw.get("regime") or "") != STRESS:
                continue
            sample_ms = _int(raw.get("sample_ms"))
            if start_ms <= sample_ms <= end_ms:
                rows.append({"market_id": _int(raw.get("market_id")), "sample_ms": sample_ms})
    rows.sort(key=lambda r: (r["sample_ms"], r["market_id"]))
    return rows


def _valid_mid(result: bookmod.ReplayResult) -> tuple[float | None, bool]:
    bid = _finite(result.best_bid)
    ask = _finite(result.best_ask)
    if not result.reconstructable or bid is None or ask is None:
        return None, False
    if bid < 0.0 or ask < 0.0 or bid > 1.0 or ask > 1.0 or bid >= ask:
        return None, False
    mid = (bid + ask) / 2.0
    return mid, 0.0 <= mid <= 1.0


def audit_rows(
    states: Sequence[Mapping[str, int]],
    updates_by_market: Mapping[int, Sequence[bookmod.BookUpdate]],
    mode: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for state in states:
        market_id = int(state["market_id"])
        sample_ms = int(state["sample_ms"])
        result = bookmod.replay_strict_pre(updates_by_market.get(market_id, []), sample_ms, mode)
        mid, valid_mid = _valid_mid(result)
        age_ms = result.latest_age_ms
        out.append(
            {
                "market_id": market_id,
                "sample_ms": sample_ms,
                "reconstructable": bool(result.reconstructable),
                "valid_mid": bool(valid_mid),
                "prediction_mid": mid,
                "latest_age_ms": age_ms,
                "latest_update_id": result.latest_update_id,
                "checkpoint_update_id": result.checkpoint_update_id,
                "crossed_book": bool(result.crossed_book),
                "post_cutoff_regression_rows": int(result.post_cutoff_regression_rows),
                "ordering_timestamp_regression_count": int(result.ordering_timestamp_regression_count),
                "source_timestamp_regression_count": int(result.source_timestamp_regression_count),
                "fresh_2s": bool(valid_mid and age_ms is not None and 0 <= age_ms <= 2000),
            }
        )
    return out


def _quantile(values: Iterable[float], q: float) -> float | None:
    xs = sorted(float(v) for v in values if math.isfinite(float(v)))
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def _summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    reconstructable = sum(bool(r.get("reconstructable")) for r in rows)
    valid_mid = sum(bool(r.get("valid_mid")) for r in rows)
    ages = [float(r["latest_age_ms"]) for r in rows if r.get("valid_mid") and r.get("latest_age_ms") is not None]
    fresh: dict[str, Any] = {}
    for threshold in FRESHNESS_BUCKETS_MS:
        count = sum(
            bool(r.get("valid_mid"))
            and r.get("latest_age_ms") is not None
            and 0 <= float(r["latest_age_ms"]) <= threshold
            for r in rows
        )
        fresh[str(threshold)] = {"count": count, "rate": (count / n if n else None)}

    by_market_rows: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_market_rows[int(row["market_id"])].append(row)
    by_market = {str(m): _summarize_leaf(xs) for m, xs in sorted(by_market_rows.items())}

    return {
        "rows": n,
        "reconstructable": {"count": reconstructable, "rate": (reconstructable / n if n else None)},
        "validMid": {"count": valid_mid, "rate": (valid_mid / n if n else None)},
        "freshValidMidWithinMs": fresh,
        "latestAgeMs": {
            "n": len(ages),
            "median": statistics.median(ages) if ages else None,
            "p90": _quantile(ages, 0.90),
            "p95": _quantile(ages, 0.95),
            "p99": _quantile(ages, 0.99),
            "max": max(ages) if ages else None,
        },
        "integrity": {
            "crossedBookRows": sum(bool(r.get("crossed_book")) for r in rows),
            "postCutoffRegressionRowsTotal": sum(int(r.get("post_cutoff_regression_rows") or 0) for r in rows),
            "orderingTimestampRegressionsTotal": sum(int(r.get("ordering_timestamp_regression_count") or 0) for r in rows),
            "sourceTimestampRegressionsTotal": sum(int(r.get("source_timestamp_regression_count") or 0) for r in rows),
        },
        "byTargetMarket": by_market,
    }


def _summarize_leaf(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    reconstructable = sum(bool(r.get("reconstructable")) for r in rows)
    valid_mid = sum(bool(r.get("valid_mid")) for r in rows)
    fresh = {}
    for threshold in FRESHNESS_BUCKETS_MS:
        count = sum(
            bool(r.get("valid_mid"))
            and r.get("latest_age_ms") is not None
            and 0 <= float(r["latest_age_ms"]) <= threshold
            for r in rows
        )
        fresh[str(threshold)] = {"count": count, "rate": (count / n if n else None)}
    return {
        "rows": n,
        "reconstructableRate": reconstructable / n if n else None,
        "validMidRate": valid_mid / n if n else None,
        "freshValidMidWithinMs": fresh,
    }


def _decision(primary: Mapping[str, Any]) -> dict[str, Any]:
    overall = (((primary.get("freshValidMidWithinMs") or {}).get("2000") or {}).get("rate"))
    markets = primary.get("byTargetMarket") or {}
    market_rates = [
        (((entry.get("freshValidMidWithinMs") or {}).get("2000") or {}).get("rate"))
        for entry in markets.values()
    ]
    finite_rates = [float(x) for x in market_rates if x is not None]
    min_market = min(finite_rates) if finite_rates else None

    if overall is not None and overall >= 0.80 and min_market is not None and min_market >= 0.50:
        status = "READY_FOR_V272_EBM_AB"
    elif overall is not None and overall >= 0.30:
        status = "PARTIAL_8778_COVERAGE_DO_NOT_TREAT_MISSING_AS_NEGATIVE"
    else:
        status = "INSUFFICIENT_8778_COVERAGE_HYPOTHESIS_REMAINS_UNTESTED"
    return {
        "status": status,
        "primaryFresh2sRate": overall,
        "minimumMarketFresh2sRate": min_market,
        "rule": "READY only if receivedStrict fresh valid-mid coverage >=80% overall and >=50% in every Target market; otherwise do not interpret missing Prediction as a negative signal.",
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="V2.7.2 strict-past audit of 8778 Predict book coverage for the controller fragment")
    p.add_argument("--states", default=str(DEFAULT_STATES))
    p.add_argument("--book-db", default=str(DEFAULT_BOOK_DB))
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=DEFAULT_END)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    start_ms = bookmod.iso_to_ms(args.start)
    end_ms = bookmod.iso_to_ms(args.end)
    states_path = Path(args.states)
    book_path = Path(args.book_db)
    states = _load_states(states_path, start_ms, end_ms)
    market_counts = Counter(int(r["market_id"]) for r in states)
    market_ids = sorted(market_counts)

    if not states:
        raise SystemExit("no V2.1 stress fixed-grid states in requested V2.7.2 window")
    if not book_path.exists():
        raise SystemExit(f"8778 Predict book DB not found: {book_path}")

    with _open_ro(book_path) as conn:
        bookmod.require_table(conn, bookmod.BOOK_TABLE)
        updates_by_market = bookmod.load_updates(conn, market_ids)

    primary_rows = audit_rows(states, updates_by_market, PRIMARY_MODE)
    secondary_rows = audit_rows(states, updates_by_market, SECONDARY_MODE)
    primary = _summarize(primary_rows)
    secondary = _summarize(secondary_rows)

    report = {
        "version": REPORT_VERSION,
        "policy": {
            "window": {"start": args.start, "end": args.end},
            "targetNamespace": "V2.1 Target/Predict market_id; no microstructure 699xxxx market-id equality requirement",
            "primaryClock": "received_at_ms",
            "primaryStrictPast": "received_at_ms < fixed-grid sample_ms; equality excluded",
            "secondaryClock": "source_timestamp_ms (diagnostic only)",
            "predictionFreshnessGateMs": 2000,
            "observationOnly": True,
            "noLiveTradingChanges": True,
        },
        "source": {
            "states": str(states_path.expanduser().resolve()),
            "bookDb": str(book_path.expanduser().resolve()),
            "bookTable": bookmod.BOOK_TABLE,
            "fixedGridRowsInWindow": len(states),
            "targetMarketRows": {str(k): v for k, v in sorted(market_counts.items())},
            "retained8778UpdatesByTargetMarket": {str(k): len(updates_by_market.get(k, [])) for k in market_ids},
        },
        "receivedStrict": primary,
        "sourceStrictDiagnostic": secondary,
        "decision": _decision(primary),
    }

    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "version": REPORT_VERSION,
        "rows": len(states),
        "markets": market_ids,
        "receivedStrictFresh2s": primary["freshValidMidWithinMs"]["2000"],
        "decision": report["decision"],
        "report": str(out),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
