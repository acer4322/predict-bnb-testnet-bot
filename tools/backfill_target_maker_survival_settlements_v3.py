from __future__ import annotations

import argparse
import csv
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

import analyze_target_maker_taker_inventory_lifecycle_v1 as lifecycle
import backfill_target_taker_official_settlements_v1 as base

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_DATASET = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.csv"
DEFAULT_SIM_DB = ROOT / "data" / "simulation.db"
DEFAULT_OUTPUT_DB = ROOT / "data" / "research" / "target_maker_survival_settlements_v3.db"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_survival_settlements_v3_report.json"
VERSION = "TARGET_MAKER_HEAVY_SURVIVAL_SETTLEMENT_BACKFILL_V3_1_INTEGRITY"
DEFAULT_SPECIAL_START = "2026-08-16T12:00:00+08:00"
PREDICT_API_BASE = os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun").rstrip("/")
TRANSIENT_HTTP = {408, 425, 429, 500, 502, 503, 504}


def _market_ids_from_public_dataset(path: Path) -> list[int]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    market_ids: set[int] = set()
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if "market_id" not in set(reader.fieldnames or []):
            raise RuntimeError("public dataset missing market_id")
        for row in reader:
            try:
                market_ids.add(int(float(row["market_id"])))
            except (TypeError, ValueError):
                continue
    return sorted(market_ids)


def _coverage_payload(
    cohort: dict[int, str],
    resolved_ids: set[int],
    *,
    min_ordinary: float,
    min_special: float,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    thresholds = {
        "ORDINARY_PRE_SPECIAL": float(min_ordinary),
        "SPECIAL": float(min_special),
    }
    for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
        requested = {market_id for market_id, value in cohort.items() if value == regime}
        resolved = requested & resolved_ids
        unresolved = sorted(requested - resolved)
        coverage = len(resolved) / len(requested) if requested else None
        threshold = thresholds[regime]
        result[regime] = {
            "requested": len(requested),
            "resolved": len(resolved),
            "unresolved": len(unresolved),
            "coverage": coverage,
            "minimumRequired": threshold,
            "passed": coverage is not None and coverage >= threshold,
            "unresolvedMarketIdsFirst50": unresolved[:50],
        }
    result["passed"] = all(
        bool(result[regime]["passed"]) for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL")
    )
    return result


def _retry_delay_seconds(response: httpx.Response | None, attempt: int, base_delay_ms: int) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
    return max(0.0, float(base_delay_ms) / 1000.0) * (2 ** max(0, attempt - 1))


def _fetch_predict_market(
    client: httpx.Client,
    market_id: int,
    *,
    retries: int,
    base_delay_ms: int,
    status_counts: Counter[str],
    exception_counts: Counter[str],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    attempts = max(1, int(retries) + 1)
    last_error: dict[str, Any] = {"kind": "UNKNOWN"}
    for attempt in range(1, attempts + 1):
        response: httpx.Response | None = None
        try:
            response = client.get(f"{PREDICT_API_BASE}/v1/markets/{int(market_id)}")
            status_counts[str(response.status_code)] += 1
            if response.status_code >= 400:
                last_error = {
                    "kind": "HTTP",
                    "status": int(response.status_code),
                    "attempt": attempt,
                }
                if response.status_code not in TRANSIENT_HTTP or attempt >= attempts:
                    return None, last_error
                time.sleep(_retry_delay_seconds(response, attempt, base_delay_ms))
                continue
            parsed = base._parse_official_detail(response.json())
            if parsed is None:
                return None, {"kind": "UNRESOLVED_PAYLOAD", "status": int(response.status_code)}
            return {
                "market_id": int(market_id),
                "topic_id": None,
                **parsed,
                "source": "PREDICT_MARKET_DETAIL_API",
            }, {"kind": "OK", "status": int(response.status_code), "attempt": attempt}
        except Exception as exc:
            name = type(exc).__name__
            exception_counts[name] += 1
            last_error = {"kind": "EXCEPTION", "exception": name, "attempt": attempt}
            if attempt >= attempts:
                return None, last_error
            time.sleep(_retry_delay_seconds(response, attempt, base_delay_ms))
    return None, last_error


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill official Predict settlements for the frozen public cohort and refuse "
            "survival training when ordinary/special outcome coverage is incomplete."
        )
    )
    parser.add_argument("--public-dataset", type=Path, default=DEFAULT_PUBLIC_DATASET)
    parser.add_argument("--simulation-db", type=Path, default=DEFAULT_SIM_DB)
    parser.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--special-start", default=DEFAULT_SPECIAL_START)
    parser.add_argument("--min-ordinary-coverage", type=float, default=0.95)
    parser.add_argument("--min-special-coverage", type=float, default=0.95)
    parser.add_argument("--api-retries", type=int, default=3)
    parser.add_argument("--retry-base-delay-ms", type=int, default=300)
    args = parser.parse_args()

    min_ordinary = min(1.0, max(0.0, float(args.min_ordinary_coverage)))
    min_special = min(1.0, max(0.0, float(args.min_special_coverage)))
    special_start_ms = lifecycle._epoch_ms(args.special_start)
    cohort, _, public_meta = lifecycle._load_public_cohorts(
        args.public_dataset, special_start_ms=special_start_ms
    )
    market_ids = _market_ids_from_public_dataset(args.public_dataset)
    cohort_ids = set(cohort)
    if set(market_ids) != cohort_ids:
        raise SystemExit(
            "public cohort mismatch: market-id loader and ordinary/special cohort resolver disagree"
        )
    if not market_ids:
        raise SystemExit("no markets in frozen public dataset")

    sim = base._connect_ro(args.simulation_db)
    out = base._create_output(args.output_db)
    headers = {
        "Accept": "application/json",
        "User-Agent": "Target-Maker-Survival-Settlement-Backfill-V3.1/1.0",
    }
    if os.environ.get("PREDICT_FUN_API_KEY"):
        headers["x-api-key"] = os.environ["PREDICT_FUN_API_KEY"]
    client = httpx.Client(
        timeout=httpx.Timeout(10.0, connect=3.0), trust_env=False, headers=headers
    )

    counts: dict[str, int] = {
        "requestedMarkets": len(market_ids),
        "existingOutput": 0,
        "simulationDb": 0,
        "predictApi": 0,
        "unresolved": 0,
        "apiErrors": 0,
    }
    status_counts: Counter[str] = Counter()
    exception_counts: Counter[str] = Counter()
    unresolved: list[int] = []
    unresolved_details: list[dict[str, Any]] = []
    resolved_ids: set[int] = set()

    try:
        for index, market_id in enumerate(market_ids, 1):
            existing = out.execute(
                "SELECT status,official_winner FROM market_settlements WHERE market_id=? LIMIT 1",
                (market_id,),
            ).fetchone()
            if (
                existing is not None
                and str(existing["status"] or "").upper() == "OFFICIAL"
                and str(existing["official_winner"] or "").upper() in {"UP", "DOWN"}
            ):
                counts["existingOutput"] += 1
                resolved_ids.add(market_id)
                continue

            copied = base._simulation_lookup(sim, market_id)
            if copied is not None:
                base._upsert(out, copied)
                counts["simulationDb"] += 1
                resolved_ids.add(market_id)
                continue

            resolved, detail = _fetch_predict_market(
                client,
                market_id,
                retries=max(0, int(args.api_retries)),
                base_delay_ms=max(0, int(args.retry_base_delay_ms)),
                status_counts=status_counts,
                exception_counts=exception_counts,
            )
            if resolved is not None:
                base._upsert(out, resolved)
                counts["predictApi"] += 1
                resolved_ids.add(market_id)
            else:
                counts["unresolved"] += 1
                if detail.get("kind") in {"HTTP", "EXCEPTION"}:
                    counts["apiErrors"] += 1
                unresolved.append(market_id)
                if len(unresolved_details) < 100:
                    unresolved_details.append({"marketId": market_id, **detail})

            if index % 25 == 0 or index == len(market_ids):
                out.commit()
                print(
                    f"settlements {index}/{len(market_ids)} | existing={counts['existingOutput']} "
                    f"sim={counts['simulationDb']} api={counts['predictApi']} unresolved={counts['unresolved']}",
                    flush=True,
                )
        out.commit()
    finally:
        client.close()
        out.close()
        if sim is not None:
            sim.close()

    coverage_by_regime = _coverage_payload(
        cohort,
        resolved_ids,
        min_ordinary=min_ordinary,
        min_special=min_special,
    )
    resolved_count = len(resolved_ids)
    report = {
        "reportVersion": VERSION,
        "paperResearchOnly": True,
        "status": "OK" if coverage_by_regime["passed"] else "FAILED_COVERAGE_GUARD",
        "sourcePolicy": "simulation.db OFFICIAL first; otherwise Predict market-detail start/end price",
        "winnerRule": "UP iff official endPrice > startPrice, else DOWN",
        "publicDataset": str(args.public_dataset.expanduser().resolve()),
        "publicResearch": public_meta,
        "counts": counts,
        "resolvedCoverage": resolved_count / len(market_ids),
        "coverageByRegime": coverage_by_regime,
        "apiDiagnostics": {
            "httpStatusCounts": dict(sorted(status_counts.items())),
            "exceptionCounts": dict(sorted(exception_counts.items())),
            "retries": max(0, int(args.api_retries)),
            "retryBaseDelayMs": max(0, int(args.retry_base_delay_ms)),
            "unresolvedDetailsFirst100": unresolved_details,
        },
        "unresolvedMarketIdsFirst50": unresolved[:50],
        "outputDb": str(args.output_db.expanduser().resolve()),
        "generatedAtMs": int(time.time() * 1000),
    }
    resolved_report = args.report.expanduser().resolve()
    resolved_report.parent.mkdir(parents=True, exist_ok=True)
    resolved_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"Resolved official settlements: {resolved_count}/{len(market_ids)} "
        f"({resolved_count / len(market_ids):.1%})",
        flush=True,
    )
    for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
        payload = coverage_by_regime[regime]
        print(
            f"  {regime}: {payload['resolved']}/{payload['requested']} "
            f"coverage={payload['coverage']:.1%} required>={payload['minimumRequired']:.1%}",
            flush=True,
        )
    print(f"Report: {resolved_report}", flush=True)
    if not coverage_by_regime["passed"]:
        print(
            "COVERAGE GUARD FAILED. Refusing to continue to V3.1 survival training. "
            "Inspect apiDiagnostics before changing any threshold.",
            flush=True,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
