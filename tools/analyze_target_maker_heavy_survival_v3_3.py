from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import analyze_target_maker_heavy_survival_v3 as v3
import analyze_target_maker_heavy_survival_v3_1 as v31

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
REPORT_VERSION = "TARGET_MAKER_HEAVY_SURVIVAL_V3_3_PORTFOLIO_PNL_SEMANTICS"
MIN_TARGET_PNL_COVERAGE = 0.95

_SETTLEMENTS: dict[int, str] = {}
_TARGET_PNL: dict[int, dict[str, float]] = {}
_INTEGRITY: dict[str, Any] = {}
_PORTFOLIO_AUDIT: dict[str, Any] = {}


def _profit_class(value: float, eps: float = 1e-9) -> str:
    if value > eps:
        return "WIN"
    if value < -eps:
        return "LOSS"
    return "FLAT"


def _connect_ro(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    db = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _load_target_pnl(path: Path, asset: str = "BTC") -> dict[int, dict[str, float]]:
    db = _connect_ro(path)
    try:
        columns = {str(row[1]) for row in db.execute("PRAGMA table_info(target_market_results)")}
        required = {"market_id", "asset", "net_pnl_usdt", "maker_net_pnl_usdt", "taker_net_pnl_usdt"}
        missing = sorted(required - columns)
        if missing:
            raise RuntimeError("target_market_results missing columns: " + ", ".join(missing))
        result: dict[int, dict[str, float]] = {}
        for row in db.execute(
            "SELECT market_id,net_pnl_usdt,maker_net_pnl_usdt,taker_net_pnl_usdt "
            "FROM target_market_results WHERE asset=?",
            (str(asset).upper(),),
        ):
            try:
                combined = float(row["net_pnl_usdt"])
                maker = float(row["maker_net_pnl_usdt"])
                taker = float(row["taker_net_pnl_usdt"])
            except (TypeError, ValueError):
                continue
            if not all(math.isfinite(x) for x in (combined, maker, taker)):
                continue
            result[int(row["market_id"])] = {
                "net_pnl_usdt": combined,
                "maker_net_pnl_usdt": maker,
                "taker_net_pnl_usdt": taker,
            }
        return result
    finally:
        db.close()


def _target_pnl_coverage(risk_rows: list[dict[str, Any]], pnl: dict[int, dict[str, float]], minimum: float) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
        requested = {
            int(row["market_id"])
            for row in risk_rows
            if str(row.get("regime") or "") == regime
        }
        resolved = requested & set(pnl)
        missing = sorted(requested - resolved)
        coverage = len(resolved) / len(requested) if requested else None
        result[regime] = {
            "requestedMarkets": len(requested),
            "pnlMarkets": len(resolved),
            "missingMarkets": len(missing),
            "coverage": coverage,
            "minimumRequired": float(minimum),
            "passed": coverage is not None and coverage >= float(minimum),
            "missingMarketIdsFirst50": missing[:50],
        }
    result["passed"] = all(result[r]["passed"] for r in ("ORDINARY_PRE_SPECIAL", "SPECIAL"))
    return result


def _canonicalize_risk_outcomes(
    risk_rows: list[dict[str, Any]],
    settlements: dict[int, str],
    pnl: dict[int, dict[str, float]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output: list[dict[str, Any]] = []
    compared = 0
    changed = 0
    changed_markets: set[int] = set()
    for source in risk_rows:
        row = dict(source)
        market_id = int(row["market_id"])
        heavy = str(row.get("maker_heavy_side") or "").upper()
        official = str(settlements.get(market_id) or "").upper()
        if heavy in {"UP", "DOWN"} and official in {"UP", "DOWN"}:
            canonical = int(heavy == official)
            legacy_raw = row.get("heavy_side_won")
            legacy = "" if legacy_raw is None else str(legacy_raw).strip()
            if legacy != "":
                try:
                    compared += 1
                    if int(float(legacy)) != canonical:
                        changed += 1
                        changed_markets.add(market_id)
                except ValueError:
                    pass
            row["heavy_side_won"] = canonical
            row["heavy_side_outcome_won"] = canonical
            row["market_outcome"] = official
        target = pnl.get(market_id)
        if target is not None:
            combined = float(target["net_pnl_usdt"])
            row["target_net_pnl_usdt"] = combined
            row["target_maker_net_pnl_usdt"] = float(target["maker_net_pnl_usdt"])
            row["target_taker_net_pnl_usdt"] = float(target["taker_net_pnl_usdt"])
            row["target_profit_class"] = _profit_class(combined)
            row["target_win"] = int(combined > 1e-9)
        output.append(row)
    return output, {
        "legacyHeavyOutcomeRowsCompared": compared,
        "rowsChangedToCanonicalOutcome": changed,
        "marketsChangedToCanonicalOutcome": len(changed_markets),
        "changedMarketIdsFirst50": sorted(changed_markets)[:50],
        "note": "Legacy V2 heavy_side_won is ignored for calibration after this audit; canonical settlement outcome is re-derived per row.",
    }


def _market_blocked_mean_repair(rows: list[dict[str, Any]]) -> float | None:
    grouped: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        grouped[int(row["market_id"])].append(int(row.get("repair_taker_5s") or 0))
    if not grouped:
        return None
    return statistics.fmean(statistics.fmean(values) for values in grouped.values())


def _portfolio_repair_audit(risk_rows: list[dict[str, Any]], pnl: dict[int, dict[str, float]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
        rows = [
            row for row in risk_rows
            if str(row.get("regime") or "") == regime
            and str(row.get("lifecycle_state") or "") == "POST_FIRST_TAKER"
            and int(row["market_id"]) in pnl
        ]
        by_class: dict[str, Any] = {}
        for cls in ("WIN", "LOSS", "FLAT"):
            subset = [
                row for row in rows
                if _profit_class(float(pnl[int(row["market_id"])]["net_pnl_usdt"])) == cls
            ]
            markets = sorted({int(row["market_id"]) for row in subset})
            market_pnls = [float(pnl[mid]["net_pnl_usdt"]) for mid in markets]
            by_class[cls] = {
                "rows": len(subset),
                "markets": len(markets),
                "meanTargetNetPnlUsdt": statistics.fmean(market_pnls) if market_pnls else None,
                "repairRate5s": (
                    sum(int(row.get("repair_taker_5s") or 0) for row in subset) / len(subset)
                    if subset else None
                ),
                "marketBlockedMeanRepairRate5s": _market_blocked_mean_repair(subset),
                "meanRepairShares5s": (
                    statistics.fmean(float(row.get("repair_shares_5s") or 0.0) for row in subset)
                    if subset else None
                ),
            }

        market_ids = sorted({int(row["market_id"]) for row in rows})
        composition: dict[str, int] = defaultdict(int)
        examples: dict[str, list[int]] = defaultdict(list)
        for market_id in market_ids:
            item = pnl[market_id]
            maker = _profit_class(float(item["maker_net_pnl_usdt"]))
            taker = _profit_class(float(item["taker_net_pnl_usdt"]))
            combined = _profit_class(float(item["net_pnl_usdt"]))
            key = f"MAKER_{maker}_TAKER_{taker}_COMBINED_{combined}"
            composition[key] += 1
            if len(examples[key]) < 20:
                examples[key].append(market_id)

        result[regime] = {
            "definition": "WIN iff combined target_market_results.net_pnl_usdt > 0; LOSS iff < 0; FLAT iff == 0",
            "byTargetProfitClass": by_class,
            "makerTakerCombinedCompositionMarketCounts": dict(sorted(composition.items())),
            "makerTakerCombinedCompositionExamples": dict(sorted(examples.items())),
        }
    return result


def _repair_audit_v33(
    risk_rows: list[dict[str, Any]],
    score_rows_by_set: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    canonical_rows, audit = _canonicalize_risk_outcomes(risk_rows, _SETTLEMENTS, _TARGET_PNL)
    result = v31._repair_audit_exact_v31(canonical_rows, score_rows_by_set)
    for feature_set in result.values():
        for payload in feature_set.values():
            if isinstance(payload, dict):
                payload["heavyOutcomeLabelDefinition"] = (
                    "canonical official market outcome relative to current Maker-heavy side; "
                    "this is NOT Target portfolio win/loss"
                )
    _INTEGRITY["legacyV2HeavyOutcomeReplacement"] = audit
    return result


def _write_json_v33(path: Path, payload: dict[str, Any]) -> None:
    if isinstance(payload, dict) and payload.get("models") is not None and payload.get("repairAudit") is not None:
        payload["reportVersion"] = REPORT_VERSION
        payload["targetWinDefinition"] = (
            "Target portfolio WIN iff target_market_results.net_pnl_usdt > 0; LOSS iff < 0; FLAT iff == 0."
        )
        payload["marketOutcomeDefinition"] = (
            "Official UP/DOWN is used only to settle outcome exposure and evaluate heavy-side survival probability; "
            "it is not Target win/loss."
        )
        payload["baseline"] = (
            "raw target-blind Predict midpoint plus fold-wise past-only calibrated Predict; "
            "model promotion comparisons use calibrated Predict as the probability-quality baseline"
        )
        payload["integrityAudit"] = _INTEGRITY
        payload["portfolioPnlRepairAudit"] = _PORTFOLIO_AUDIT
        guardrails = list(payload.get("interpretationGuardrails") or [])
        additions = [
            "Target win/loss is defined only by combined target_market_results.net_pnl_usdt: positive WIN, negative LOSS, zero FLAT.",
            "Official UP/DOWN is market outcome only. A losing Taker leg can coexist with a profitable Target market when Maker PnL dominates, and vice versa.",
            "Heavy-side survival calibration uses canonical market outcome re-derived from the settlement DB and ignores legacy V2 heavy_side_won values.",
            "Target portfolio PnL is an offline future outcome label only and is never fed into model fitting or live decision features.",
            "Repair scores join by exact market_id + sampled_ms only; second-bucket and future joins are forbidden.",
            "For log-loss/Brier claims, compare EBM against fold-wise past-only calibrated Predict as well as raw Predict.",
        ]
        for item in additions:
            if item not in guardrails:
                guardrails.append(item)
        payload["interpretationGuardrails"] = guardrails
    v31._ORIGINAL_WRITE_JSON(path, payload)


def _strip_option(argv: list[str], name: str) -> list[str]:
    output: list[str] = []
    skip = False
    for value in argv:
        if skip:
            skip = False
            continue
        if value == name:
            skip = True
            continue
        if value.startswith(name + "="):
            continue
        output.append(value)
    return output


def _preflight(argv: list[str]) -> tuple[list[dict[str, Any]], float]:
    global _SETTLEMENTS, _TARGET_PNL, _INTEGRITY, _PORTFOLIO_AUDIT
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--public-dataset", type=Path, default=v3.DEFAULT_PUBLIC_DATASET)
    parser.add_argument("--settlement-db", type=Path, default=v3.DEFAULT_SETTLEMENT_DB)
    parser.add_argument("--risk-csv", type=Path, default=v3.DEFAULT_RISK_CSV)
    parser.add_argument("--target-db", type=Path, default=DEFAULT_TARGET_DB)
    parser.add_argument("--asset", default="BTC")
    parser.add_argument("--special-start", default=v3.DEFAULT_SPECIAL_START)
    parser.add_argument("--min-target-pnl-coverage", type=float, default=MIN_TARGET_PNL_COVERAGE)
    known, _ = parser.parse_known_args(argv)

    special_start_ms = v3.lifecycle._epoch_ms(known.special_start)
    cohort, _, _ = v3.lifecycle._load_public_cohorts(known.public_dataset, special_start_ms=special_start_ms)
    _SETTLEMENTS = v3._load_settlements(known.settlement_db)
    risk_rows = v3._load_risk_rows(known.risk_csv)
    _TARGET_PNL = _load_target_pnl(known.target_db, str(known.asset))

    settlement_coverage = v31._coverage_guard(cohort, _SETTLEMENTS)
    minimum = min(1.0, max(0.0, float(known.min_target_pnl_coverage)))
    pnl_coverage = _target_pnl_coverage(risk_rows, _TARGET_PNL, minimum)
    legacy_outcome_diag = v31._winner_concordance(risk_rows, _SETTLEMENTS)
    canonical_rows, replacement = _canonicalize_risk_outcomes(risk_rows, _SETTLEMENTS, _TARGET_PNL)
    _PORTFOLIO_AUDIT = _portfolio_repair_audit(canonical_rows, _TARGET_PNL)

    _INTEGRITY = {
        "status": "OK" if settlement_coverage["passed"] and pnl_coverage["passed"] else "FAILED",
        "settlementCoverage": settlement_coverage,
        "targetPortfolioPnlCoverage": pnl_coverage,
        "targetWinDefinition": "combined net_pnl_usdt > 0; loss < 0; flat == 0",
        "legacyV2OutcomeConcordanceDiagnostic": {**legacy_outcome_diag, "enforced": False},
        "legacyV2HeavyOutcomeReplacement": replacement,
        "repairJoinPolicy": "exact market_id + sampled_ms; no second-bucket fallback; no future join",
        "probabilityBaselinePolicy": "raw Predict + fold-wise past-only calibrated Predict",
    }

    print("V3.3 PORTFOLIO-PNL INTEGRITY PREFLIGHT", flush=True)
    ordinary = settlement_coverage["ORDINARY_PRE_SPECIAL"]
    special = settlement_coverage["SPECIAL"]
    print(
        f"  market outcomes ordinary={ordinary['settledMarkets']}/{ordinary['requestedMarkets']} "
        f"({ordinary['coverage']:.1%}) special={special['settledMarkets']}/{special['requestedMarkets']} "
        f"({special['coverage']:.1%})",
        flush=True,
    )
    po = pnl_coverage["ORDINARY_PRE_SPECIAL"]
    ps = pnl_coverage["SPECIAL"]
    print(
        f"  Target portfolio PnL ordinary={po['pnlMarkets']}/{po['requestedMarkets']} ({po['coverage']:.1%}) "
        f"special={ps['pnlMarkets']}/{ps['requestedMarkets']} ({ps['coverage']:.1%})",
        flush=True,
    )
    print(
        f"  legacy V2 market-outcome mismatch={legacy_outcome_diag['mismatchedWinnerMarkets']} "
        f"(diagnostic only; ignored and re-derived from canonical settlement)",
        flush=True,
    )

    failures: list[str] = []
    if not settlement_coverage["passed"]:
        failures.append("market-outcome settlement coverage guard failed")
    if not pnl_coverage["passed"]:
        failures.append("Target portfolio PnL coverage guard failed")
    if failures:
        raise SystemExit("V3.3 INTEGRITY FAILED: " + "; ".join(failures) + ". Refusing to train/audit.")
    return risk_rows, minimum


def main() -> int:
    original_argv = list(sys.argv)
    _preflight(original_argv[1:])
    child_argv = _strip_option(original_argv[1:], "--target-db")
    child_argv = _strip_option(child_argv, "--min-target-pnl-coverage")
    try:
        sys.argv = [original_argv[0], *child_argv]
        v3._fit_walkforward = v31._fit_walkforward_v31
        v3._score_special = v31._score_special_v31
        v3._write_scores = v31._write_scores_v31
        v3._repair_audit = _repair_audit_v33
        v3._write_json = _write_json_v33
        return v3.main()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())