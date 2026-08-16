from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

from predict_bot.target_maker_direct_placement_v1 import (
    DEFAULT_BEHAVIOR_OUTPUT,
    DEFAULT_HAZARD_OUTPUT,
    PUBLIC_FEATURES,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_special_regime_v1_report.json"
REPORT_VERSION = "TARGET_MAKER_SPECIAL_REGIME_V1_EBM_STRESS_HOLDOUT"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepare_split(
    frame: Any,
    *,
    time_column: str,
    start_ms: int,
    end_ms: int | None,
    pd: Any,
    stress: Any,
) -> dict[str, Any]:
    special_mask = frame[time_column] >= start_ms
    if end_ms is not None:
        special_mask &= frame[time_column] < end_ms
    special_market_ids = sorted(
        int(v) for v in frame.loc[special_mask, "market_id"].unique().tolist()
    )
    historical = frame[
        (frame[time_column] < start_ms)
        & (~frame["market_id"].isin(special_market_ids))
    ].copy()
    special = frame[frame["market_id"].isin(special_market_ids) & special_mask].copy()
    historical_markets = stress._market_order(historical, time_column) if len(historical) else []
    special_markets = stress._market_order(special, time_column) if len(special) else []
    return {
        "historical": historical,
        "special": special,
        "historicalMarkets": historical_markets,
        "specialMarkets": special_markets,
    }


def _suite(
    *,
    frame: Any,
    split: dict[str, Any],
    label: str,
    feature_sets: dict[str, list[str]],
    shared: Any,
    stress: Any,
    deps: dict[str, Any],
    interactions: int,
    max_rounds: int,
    outer_bags: int,
    special_train_fraction: float,
    seed_base: int,
) -> dict[str, Any]:
    pd = deps["pd"]
    historical = split["historical"]
    special = split["special"]
    hist_markets = split["historicalMarkets"]
    special_markets = split["specialMarkets"]
    task: dict[str, Any] = {
        "label": label,
        "historicalRows": int(len(historical)),
        "specialRows": int(len(special)),
        "historicalPositiveRate": (
            float(pd.to_numeric(historical[label], errors="coerce").dropna().mean())
            if len(historical)
            else None
        ),
        "specialPositiveRate": (
            float(pd.to_numeric(special[label], errors="coerce").dropna().mean())
            if len(special)
            else None
        ),
        "featureSets": {},
    }
    if len(hist_markets) < 12 or len(special_markets) < 2:
        task["status"] = "INSUFFICIENT_MARKETS"
        return task

    normal_train, normal_cal, normal_test = stress._normal_reference_split(hist_markets)
    hist_train, hist_cal = stress._split_train_cal(hist_markets)
    fraction = min(0.8, max(0.2, float(special_train_fraction)))
    cut = max(1, min(len(special_markets) - 1, int(round(len(special_markets) * fraction))))
    special_early = special_markets[:cut]
    special_late = special_markets[cut:]

    analysis_frame = pd.concat([historical, special], ignore_index=True)
    working = analysis_frame[analysis_frame[label].notna()].copy()
    for feature_index, (name, requested) in enumerate(feature_sets.items()):
        features = stress._usable(requested, working, pd)
        if not features:
            continue
        normal = (
            stress._fit_eval(
                shared=shared,
                deps=deps,
                frame=working,
                label=label,
                features=features,
                train_markets=normal_train,
                calibration_markets=normal_cal,
                test_markets=normal_test,
                interactions=interactions,
                max_rounds=max_rounds,
                outer_bags=outer_bags,
                seed=seed_base + feature_index,
            )
            if normal_test
            else {"status": "INSUFFICIENT_NORMAL_TEST"}
        )
        special_a = stress._fit_eval(
            shared=shared,
            deps=deps,
            frame=working,
            label=label,
            features=features,
            train_markets=hist_train,
            calibration_markets=hist_cal,
            test_markets=special_markets,
            interactions=interactions,
            max_rounds=max_rounds,
            outer_bags=outer_bags,
            seed=seed_base + 100 + feature_index,
        )
        combined_prior = hist_markets + special_early
        retrain_train, retrain_cal = stress._split_train_cal(combined_prior)
        special_b = (
            stress._fit_eval(
                shared=shared,
                deps=deps,
                frame=working,
                label=label,
                features=features,
                train_markets=retrain_train,
                calibration_markets=retrain_cal,
                test_markets=special_late,
                interactions=interactions,
                max_rounds=max_rounds,
                outer_bags=outer_bags,
                seed=seed_base + 200 + feature_index,
            )
            if special_late
            else {"status": "INSUFFICIENT_SPECIAL_LATE"}
        )
        task["featureSets"][name] = {
            "features": features,
            "normalReference": normal,
            "experimentA_preSpecialToSpecial": special_a,
            "experimentB_earlySpecialToLateSpecial": special_b,
        }

    task["rankingBySpecialLogLossLift"] = sorted(
        [
            {
                "featureSet": name,
                "specialLogLossLift": values["experimentA_preSpecialToSpecial"].get(
                    "logLossLiftVsPrior"
                )
                if values["experimentA_preSpecialToSpecial"].get("status") == "OK"
                else None,
                "specialAuc": values["experimentA_preSpecialToSpecial"].get("test", {}).get(
                    "rocAuc"
                )
                if values["experimentA_preSpecialToSpecial"].get("status") == "OK"
                else None,
                "normalLogLossLift": values["normalReference"].get("logLossLiftVsPrior")
                if values["normalReference"].get("status") == "OK"
                else None,
                "normalAuc": values["normalReference"].get("test", {}).get("rocAuc")
                if values["normalReference"].get("status") == "OK"
                else None,
            }
            for name, values in task["featureSets"].items()
        ],
        key=lambda row: (
            row["specialLogLossLift"]
            if row["specialLogLossLift"] is not None
            else -999.0
        ),
        reverse=True,
    )
    task["status"] = "OK"
    return task


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Stress-test the existing direct Target Maker hazard/side/quote-level EBM tasks "
            "on a special regime. This diagnoses behavior/model regime shift; it does not "
            "pretend inferred anonymous-book placement identity is private-order ground truth."
        )
    )
    parser.add_argument("--hazard-dataset", type=Path, default=DEFAULT_HAZARD_OUTPUT)
    parser.add_argument("--behavior-dataset", type=Path, default=DEFAULT_BEHAVIOR_OUTPUT)
    parser.add_argument("--special-start", required=True)
    parser.add_argument("--special-end", default=None)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--special-train-fraction", type=float, default=0.50)
    parser.add_argument("--interactions", type=int, default=8)
    parser.add_argument("--max-rounds", type=int, default=2400)
    parser.add_argument("--outer-bags", type=int, default=6)
    args = parser.parse_args()

    maker = _load(
        ROOT / "tools" / "train_target_maker_direct_placement_v1.py",
        "maker_direct_special_source",
    )
    stress = _load(
        ROOT / "tools" / "train_target_taker_direct_eligibility_special_regime_v1.py",
        "taker_special_stress_helpers",
    )
    shared = stress._shared()
    deps = shared._imports()
    pd = deps["pd"]

    start_ms = stress._epoch_ms(args.special_start)
    end_ms = stress._epoch_ms(args.special_end)
    if start_ms is None:
        raise SystemExit("--special-start is required")
    if end_ms is not None and end_ms <= start_ms:
        raise SystemExit("--special-end must be after --special-start")

    hazard_path = args.hazard_dataset.expanduser().resolve()
    behavior_path = args.behavior_dataset.expanduser().resolve()
    if not hazard_path.exists() or not behavior_path.exists():
        raise SystemExit(
            "Maker direct datasets missing. Run: "
            "python tools/build_target_maker_direct_placement_v1_dataset.py"
        )

    hazard = pd.read_csv(hazard_path)
    behavior = pd.read_csv(behavior_path)
    for frame in (hazard, behavior):
        frame["market_id"] = pd.to_numeric(frame["market_id"], errors="raise").astype(int)
    hazard["decision_sampled_at_ms"] = pd.to_numeric(
        hazard["decision_sampled_at_ms"], errors="raise"
    ).astype("int64")
    behavior["placement_first_ms"] = pd.to_numeric(
        behavior["placement_first_ms"], errors="raise"
    ).astype("int64")

    hazard_split = _prepare_split(
        hazard,
        time_column="decision_sampled_at_ms",
        start_ms=start_ms,
        end_ms=end_ms,
        pd=pd,
        stress=stress,
    )
    behavior_split = _prepare_split(
        behavior,
        time_column="placement_first_ms",
        start_ms=start_ms,
        end_ms=end_ms,
        pd=pd,
        stress=stress,
    )

    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "specialWindow": {"startMs": start_ms, "endMs": end_ms},
        "purpose": (
            "Use the drawdown/special market as a stress regime to find where Target Maker "
            "placement timing and quote behavior differ from normal history."
        ),
        "pnlBoundary": (
            "This report does not infer Maker PnL from placement labels. It tests hazard, side, "
            "quote-level behavior and public-feature drift during the user-identified drawdown regime."
        ),
        "evidenceBoundary": (
            "Maker placement labels remain inferred from anonymous public book changes linked "
            "retrospectively to known Target Maker fills."
        ),
        "rows": {
            "hazardHistorical": int(len(hazard_split["historical"])),
            "hazardSpecial": int(len(hazard_split["special"])),
            "behaviorHistorical": int(len(behavior_split["historical"])),
            "behaviorSpecial": int(len(behavior_split["special"])),
            "hazardSpecialMarkets": len(hazard_split["specialMarkets"]),
            "behaviorSpecialMarkets": len(behavior_split["specialMarkets"]),
        },
        "hazardFeatureDrift": stress._drift(
            hazard_split["historical"], hazard_split["special"], PUBLIC_FEATURES, pd
        )[:35],
        "behaviorFeatureDrift": stress._drift(
            behavior_split["historical"], behavior_split["special"], PUBLIC_FEATURES, pd
        )[:35],
        "hazardTasks": {},
        "levelTasks": {},
    }

    interactions = max(0, int(args.interactions))
    max_rounds = max(100, int(args.max_rounds))
    outer_bags = max(2, int(args.outer_bags))
    fraction = float(args.special_train_fraction)

    hazard_sets = {
        "time_price": maker.HAZARD_FEATURE_SETS["time_price"],
        "compact_public": maker.HAZARD_FEATURE_SETS["compact_public"],
        "full_public": maker.HAZARD_FEATURE_SETS["full_public"],
    }
    for horizon in (1, 2, 5):
        label = f"label_next_inferred_placement_any_{horizon}s"
        report["hazardTasks"][f"maker_hazard_{horizon}s"] = _suite(
            frame=hazard,
            split=hazard_split,
            label=label,
            feature_sets=hazard_sets,
            shared=shared,
            stress=stress,
            deps=deps,
            interactions=interactions,
            max_rounds=max_rounds,
            outer_bags=outer_bags,
            special_train_fraction=fraction,
            seed_base=1000 + horizon * 100,
        )

    # Side is kept as a diagnostic because it has historically been the weak Maker layer.
    report["sideTask"] = _suite(
        frame=behavior,
        split=behavior_split,
        label="label_side_up",
        feature_sets={
            "time_price": maker.SIDE_FEATURE_SETS["time_price"],
            "full_public": maker.SIDE_FEATURE_SETS["full_public"],
        },
        shared=shared,
        stress=stress,
        deps=deps,
        interactions=interactions,
        max_rounds=max_rounds,
        outer_bags=outer_bags,
        special_train_fraction=fraction,
        seed_base=3000,
    )

    level_sets = {
        "compact_level": maker.LEVEL_FEATURE_SETS["compact_level"],
        "full_conditional": maker.LEVEL_FEATURE_SETS["full_conditional"],
    }
    for index, label in enumerate(
        ("label_at_or_improves_best_bid", "label_near_best_2ticks")
    ):
        report["levelTasks"][label] = _suite(
            frame=behavior,
            split=behavior_split,
            label=label,
            feature_sets=level_sets,
            shared=shared,
            stress=stress,
            deps=deps,
            interactions=interactions,
            max_rounds=max_rounds,
            outer_bags=outer_bags,
            special_train_fraction=fraction,
            seed_base=4000 + index * 200,
        )

    report_path = args.report.expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temp = report_path.with_suffix(report_path.suffix + ".tmp")
    temp.write_text(
        json.dumps(stress._clean(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(report_path)
    print(json.dumps(stress._clean(report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
