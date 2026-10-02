from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import httpx

import backfill_target_maker_survival_settlements_v3 as v31
import backfill_target_taker_official_settlements_v1 as base

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RISK_CSV = ROOT / "data" / "research" / "target_maker_taker_repair_hazard_v2_risk.csv"
VERSION = "TARGET_MAKER_HEAVY_SURVIVAL_SETTLEMENT_BACKFILL_V3_2_CANONICAL_RESOLUTION"
LEGACY_INFERRED_SOURCE = "PREDICT_MARKET_DETAIL_API"
CANONICAL_SOURCE_PREFIX = "PREDICT_MARKET_DETAIL_API_"
DEFAULT_MIN_CANONICAL_COVERAGE = 0.95


def _normalize_up_down(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if text == "UP":
        return "UP"
    if text == "DOWN":
        return "DOWN"
    return None


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number


def _canonical_winner_from_detail(detail: dict[str, Any]) -> tuple[str | None, str | None, dict[str, Any]]:
    candidates: dict[str, str] = {}

    resolution = detail.get("resolution")
    if isinstance(resolution, dict):
        winner = _normalize_up_down(resolution.get("name"))
        status = str(resolution.get("status") or "").strip().upper()
        if winner is not None and status in {"", "WON"}:
            candidates["resolution"] = winner

    outcomes = detail.get("outcomes")
    outcome_winners: set[str] = set()
    if isinstance(outcomes, list):
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            if str(outcome.get("status") or "").strip().upper() != "WON":
                continue
            winner = _normalize_up_down(outcome.get("name"))
            if winner is not None:
                outcome_winners.add(winner)
    if len(outcome_winners) == 1:
        candidates["outcomesWon"] = next(iter(outcome_winners))
    elif len(outcome_winners) > 1:
        return None, None, {
            "kind": "CANONICAL_CONFLICT",
            "reason": "multiple UP/DOWN outcomes marked WON",
            "outcomeWinners": sorted(outcome_winners),
        }

    unique = set(candidates.values())
    if len(unique) > 1:
        return None, None, {
            "kind": "CANONICAL_CONFLICT",
            "reason": "resolution and outcomes[].status=WON disagree",
            "candidates": candidates,
        }
    if len(unique) == 1:
        winner = next(iter(unique))
        if set(candidates) == {"resolution", "outcomesWon"}:
            source = "RESOLUTION_AND_OUTCOMES_WON"
        elif "resolution" in candidates:
            source = "RESOLUTION"
        else:
            source = "OUTCOMES_WON"
        return winner, source, {"kind": "CANONICAL", "candidates": candidates}
    return None, None, {"kind": "NO_CANONICAL_UP_DOWN_OUTCOME"}


def _parse_canonical_detail(payload: Any) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    detail = base._normalize_detail(payload)
    if not detail:
        return None, {"kind": "UNRESOLVED_PAYLOAD"}

    variant = detail.get("variantData")
    start: float | None = None
    end: float | None = None
    if isinstance(variant, dict):
        start = _float_or_none(variant.get("startPrice"))
        end = _float_or_none(variant.get("endPrice"))

    winner, winner_source, canonical_detail = _canonical_winner_from_detail(detail)
    if canonical_detail.get("kind") == "CANONICAL_CONFLICT":
        return None, canonical_detail

    if winner is None:
        if start is None or end is None or not start > 0 or not end > 0:
            return None, {
                "kind": "NO_OFFICIAL_WINNER",
                "canonical": canonical_detail,
                "hasStartPrice": start is not None,
                "hasEndPrice": end is not None,
            }
        winner = "UP" if end > start else "DOWN"
        winner_source = "PRICE_FALLBACK"

    return {
        "start_price": start,
        "official_end_price": end,
        "official_winner": winner,
        "winner_source": winner_source,
    }, {
        "kind": "OK",
        "winnerSource": winner_source,
        "canonical": canonical_detail,
    }


def _source_for_winner_source(winner_source: str | None) -> str:
    source = str(winner_source or "UNKNOWN").strip().upper()
    return f"{CANONICAL_SOURCE_PREFIX}{source}"


def _is_legacy_inferred_source(source: Any) -> bool:
    return str(source or "").strip().upper() == LEGACY_INFERRED_SOURCE


def _fetch_predict_market_canonical(
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
            response = client.get(f"{v31.PREDICT_API_BASE}/v1/markets/{int(market_id)}")
            status_counts[str(response.status_code)] += 1
            if response.status_code >= 400:
                last_error = {
                    "kind": "HTTP",
                    "status": int(response.status_code),
                    "attempt": attempt,
                }
                if response.status_code not in v31.TRANSIENT_HTTP or attempt >= attempts:
                    return None, last_error
                import time
                time.sleep(v31._retry_delay_seconds(response, attempt, base_delay_ms))
                continue

            parsed, parse_detail = _parse_canonical_detail(response.json())
            if parsed is None:
                return None, {
                    **parse_detail,
                    "status": int(response.status_code),
                    "attempt": attempt,
                }
            winner_source = str(parsed.pop("winner_source"))
            return {
                "market_id": int(market_id),
                "topic_id": None,
                **parsed,
                "source": _source_for_winner_source(winner_source),
            }, {
                "kind": "OK",
                "status": int(response.status_code),
                "attempt": attempt,
                "winnerSource": winner_source,
            }
        except Exception as exc:
            name = type(exc).__name__
            exception_counts[name] += 1
            last_error = {"kind": "EXCEPTION", "exception": name, "attempt": attempt}
            if attempt >= attempts:
                return None, last_error
            import time
            time.sleep(v31._retry_delay_seconds(response, attempt, base_delay_ms))
    return None, last_error


def _prepare_cache_migration(path: Path) -> tuple[dict[int, str], int]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return {}, 0
    db = sqlite3.connect(resolved, timeout=10.0)
    db.row_factory = sqlite3.Row
    try:
        columns = {str(row[1]) for row in db.execute("PRAGMA table_info(market_settlements)")}
        required = {"market_id", "official_winner", "source"}
        if not required.issubset(columns):
            return {}, 0
        rows = db.execute(
            "SELECT market_id,official_winner,source FROM market_settlements"
        ).fetchall()
        legacy = {
            int(row["market_id"]): str(row["official_winner"] or "").upper()
            for row in rows
            if _is_legacy_inferred_source(row["source"])
        }
        if legacy:
            placeholders = ",".join("?" for _ in legacy)
            db.execute(
                f"DELETE FROM market_settlements WHERE market_id IN ({placeholders})",
                tuple(sorted(legacy)),
            )
            db.commit()
        return legacy, len(legacy)
    finally:
        db.close()


def _load_settlement_rows(path: Path) -> dict[int, dict[str, str]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return {}
    db = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10.0)
    db.row_factory = sqlite3.Row
    try:
        columns = {str(row[1]) for row in db.execute("PRAGMA table_info(market_settlements)")}
        if not {"market_id", "official_winner", "source"}.issubset(columns):
            return {}
        return {
            int(row["market_id"]): {
                "winner": str(row["official_winner"] or "").upper(),
                "source": str(row["source"] or ""),
            }
            for row in db.execute(
                "SELECT market_id,official_winner,source FROM market_settlements "
                "WHERE upper(status)='OFFICIAL'"
            )
            if str(row["official_winner"] or "").upper() in {"UP", "DOWN"}
        }
    finally:
        db.close()


def _risk_winners(path: Path) -> tuple[dict[int, str], dict[int, list[str]]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    by_market: dict[int, set[str]] = defaultdict(set)
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"market_id", "maker_heavy_side", "heavy_side_won"}
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise RuntimeError("risk CSV missing columns: " + ", ".join(missing))
        for row in reader:
            heavy = str(row.get("maker_heavy_side") or "").strip().upper()
            if heavy not in {"UP", "DOWN"}:
                continue
            raw_won = str(row.get("heavy_side_won") or "").strip()
            if raw_won == "":
                continue
            try:
                won = int(float(raw_won))
            except ValueError:
                continue
            if won not in {0, 1}:
                continue
            winner = heavy if won == 1 else ("DOWN" if heavy == "UP" else "UP")
            by_market[int(float(row["market_id"]))].add(winner)

    winners: dict[int, str] = {}
    conflicts: dict[int, list[str]] = {}
    for market_id, values in by_market.items():
        ordered = sorted(values)
        if len(ordered) == 1:
            winners[market_id] = ordered[0]
        elif ordered:
            conflicts[market_id] = ordered
    return winners, conflicts


def _winner_concordance_preview(
    risk_csv: Path,
    settlement_rows: dict[int, dict[str, str]],
) -> dict[str, Any]:
    risk_winners, conflicts = _risk_winners(risk_csv)
    overlap = sorted(set(risk_winners) & set(settlement_rows))
    mismatch_details: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    mismatch_count = 0
    for market_id in overlap:
        settlement = settlement_rows[market_id]
        if risk_winners[market_id] != settlement["winner"]:
            mismatch_count += 1
            source_counts[settlement["source"]] += 1
            if len(mismatch_details) < 100:
                mismatch_details.append(
                    {
                        "marketId": market_id,
                        "v2RiskWinner": risk_winners[market_id],
                        "settlementWinner": settlement["winner"],
                        "settlementSource": settlement["source"],
                    }
                )
    matched = len(overlap) - mismatch_count
    return {
        "riskWinnerMarkets": len(risk_winners),
        "settlementMarkets": len(settlement_rows),
        "overlapMarkets": len(overlap),
        "matchedWinnerMarkets": matched,
        "mismatchedWinnerMarkets": mismatch_count,
        "matchRate": matched / len(overlap) if overlap else None,
        "conflictingRiskWinnerMarkets": len(conflicts),
        "conflictingRiskWinnerDetailsFirst50": [
            {"marketId": market_id, "winners": conflicts[market_id]}
            for market_id in sorted(conflicts)[:50]
        ],
        "mismatchBySettlementSource": dict(sorted(source_counts.items())),
        "mismatchDetailsFirst100": mismatch_details,
        "passed": bool(overlap) and mismatch_count == 0 and not conflicts,
    }


def _canonical_outcome_audit(
    settlement_rows: dict[int, dict[str, str]],
    *,
    minimum: float,
) -> dict[str, Any]:
    source_counts = Counter(row["source"] for row in settlement_rows.values())
    canonical = sum(
        count
        for source, count in source_counts.items()
        if source in {
            "PREDICT_MARKET_DETAIL_API_RESOLUTION",
            "PREDICT_MARKET_DETAIL_API_OUTCOMES_WON",
            "PREDICT_MARKET_DETAIL_API_RESOLUTION_AND_OUTCOMES_WON",
        }
    )
    price_fallback = source_counts.get("PREDICT_MARKET_DETAIL_API_PRICE_FALLBACK", 0)
    simulation = sum(
        count for source, count in source_counts.items()
        if str(source).upper().startswith("SIMULATION_DB_")
    )
    total = len(settlement_rows)
    direct_or_sim = canonical + simulation
    coverage = direct_or_sim / total if total else None
    return {
        "settledMarkets": total,
        "canonicalPredictOutcomeMarkets": canonical,
        "simulationOfficialMarkets": simulation,
        "priceFallbackMarkets": price_fallback,
        "trustedDirectOrSimulationCoverage": coverage,
        "minimumRequired": float(minimum),
        "sourceCounts": dict(sorted(source_counts.items())),
        "passed": coverage is not None and coverage >= float(minimum),
    }


def _strip_option(argv: list[str], name: str) -> list[str]:
    result: list[str] = []
    skip_next = False
    for value in argv:
        if skip_next:
            skip_next = False
            continue
        if value == name:
            skip_next = True
            continue
        if value.startswith(name + "="):
            continue
        result.append(value)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output-db", type=Path, default=v31.DEFAULT_OUTPUT_DB)
    parser.add_argument("--report", type=Path, default=v31.DEFAULT_REPORT)
    parser.add_argument("--risk-csv", type=Path, default=DEFAULT_RISK_CSV)
    parser.add_argument(
        "--min-canonical-outcome-coverage",
        type=float,
        default=DEFAULT_MIN_CANONICAL_COVERAGE,
    )
    known, _unknown = parser.parse_known_args(sys.argv[1:])

    legacy_winners, legacy_count = _prepare_cache_migration(known.output_db)
    if legacy_count:
        print(
            f"V3.2 canonical settlement migration: invalidated {legacy_count} "
            f"legacy price-inferred Predict cache rows.",
            flush=True,
        )

    original_fetch = v31._fetch_predict_market
    original_argv = list(sys.argv)
    try:
        v31._fetch_predict_market = _fetch_predict_market_canonical
        child_argv = _strip_option(original_argv[1:], "--risk-csv")
        child_argv = _strip_option(child_argv, "--min-canonical-outcome-coverage")
        sys.argv = [original_argv[0], *child_argv]
        code = int(v31.main())
    finally:
        sys.argv = original_argv
        v31._fetch_predict_market = original_fetch

    report_path = known.report.expanduser().resolve()
    payload: dict[str, Any] = {}
    if report_path.exists():
        payload = json.loads(report_path.read_text(encoding="utf-8"))

    settlement_rows = _load_settlement_rows(known.output_db)
    changed_details: list[dict[str, Any]] = []
    for market_id, old_winner in legacy_winners.items():
        current = settlement_rows.get(market_id)
        if current is None or old_winner not in {"UP", "DOWN"}:
            continue
        if current["winner"] != old_winner and len(changed_details) < 100:
            changed_details.append(
                {
                    "marketId": market_id,
                    "oldPriceInferredWinner": old_winner,
                    "canonicalWinner": current["winner"],
                    "newSource": current["source"],
                }
            )
    changed_count = sum(
        1
        for market_id, old_winner in legacy_winners.items()
        if market_id in settlement_rows
        and old_winner in {"UP", "DOWN"}
        and settlement_rows[market_id]["winner"] != old_winner
    )

    minimum = min(1.0, max(0.0, float(known.min_canonical_outcome_coverage)))
    canonical_audit = _canonical_outcome_audit(settlement_rows, minimum=minimum)
    concordance = _winner_concordance_preview(known.risk_csv, settlement_rows)

    payload.update(
        {
            "reportVersion": VERSION,
            "sourcePolicy": (
                "Predict explicit resolved outcome is primary: resolution.name and/or "
                "outcomes[].status=WON. CRYPTO_UP_DOWN startPrice/endPrice is fallback only "
                "when no canonical UP/DOWN outcome is exposed."
            ),
            "winnerRule": (
                "Use canonical Predict resolved UP/DOWN outcome when available; only otherwise "
                "fallback to UP iff endPrice > startPrice, else DOWN."
            ),
            "cacheMigration": {
                "legacyPriceInferredRowsInvalidated": legacy_count,
                "winnerChangedAfterCanonicalRefresh": changed_count,
                "winnerChangesFirst100": changed_details,
            },
            "canonicalOutcomeAudit": canonical_audit,
            "winnerConcordancePreview": concordance,
        }
    )

    failures: list[str] = []
    if code != 0:
        failures.append(f"V3.1 coverage backfill returned {code}")
    if not canonical_audit["passed"]:
        failures.append("canonical outcome coverage guard failed")
    if not concordance["passed"]:
        failures.append("V2/V3 canonical winner concordance guard failed")
    payload["status"] = "OK" if not failures else "FAILED_INTEGRITY"
    payload["failures"] = failures
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("V3.2 CANONICAL OUTCOME AUDIT", flush=True)
    coverage_value = canonical_audit["trustedDirectOrSimulationCoverage"]
    coverage_text = f"{coverage_value:.1%}" if coverage_value is not None else "N/A"
    print(
        f"  canonical/simulation coverage={coverage_text} "
        f"required>={canonical_audit['minimumRequired']:.1%} "
        f"priceFallback={canonical_audit['priceFallbackMarkets']}",
        flush=True,
    )
    print(
        f"  V2/V3 overlap={concordance['overlapMarkets']} "
        f"mismatch={concordance['mismatchedWinnerMarkets']} "
        f"riskConflicts={concordance['conflictingRiskWinnerMarkets']}",
        flush=True,
    )
    print(
        f"  legacy cache refreshed={legacy_count} winnerChanges={changed_count}",
        flush=True,
    )
    print(f"Report: {report_path}", flush=True)

    if failures:
        print(
            "V3.2 SETTLEMENT INTEGRITY FAILED: " + "; ".join(failures) + ". "
            "Refusing survival training. Inspect canonicalOutcomeAudit, cacheMigration, "
            "and winnerConcordancePreview.",
            flush=True,
        )
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
