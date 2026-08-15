from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from typing import Any

from predict_bot.target_maker_direct_placement_v1 import DEFAULT_BEHAVIOR_OUTPUT, DEFAULT_HAZARD_OUTPUT
from predict_bot.predict_wallet_maker_ebm_strategy_v1 import (
    EXPECTED_REPORT_VERSION,
    HAZARD_FEATURES,
    LEVEL_FEATURES,
    MODEL_DIR,
)

ROOT = Path(__file__).resolve().parents[1]


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fit the two fixed research-only Maker Direct EBM artifacts consumed by TARGET_MAKER_EBM_V1."
    )
    parser.add_argument("--hazard-dataset", type=Path, default=DEFAULT_HAZARD_OUTPUT)
    parser.add_argument("--behavior-dataset", type=Path, default=DEFAULT_BEHAVIOR_OUTPUT)
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    parser.add_argument("--interactions", type=int, default=8)
    parser.add_argument("--max-rounds", type=int, default=2200)
    parser.add_argument("--outer-bags", type=int, default=6)
    args = parser.parse_args()

    maker_trainer = _load_module(ROOT / "tools" / "train_target_maker_direct_placement_v1.py", "maker_direct_trainer_runtime")
    shared = maker_trainer._shared()
    deps = shared._imports()
    pd = deps["pd"]

    hazard_path = args.hazard_dataset.expanduser().resolve()
    behavior_path = args.behavior_dataset.expanduser().resolve()
    if not hazard_path.exists() or not behavior_path.exists():
        raise SystemExit("Maker direct datasets missing. Run tools/build_target_maker_direct_placement_v1_dataset.py first.")
    hazard = pd.read_csv(hazard_path)
    behavior = pd.read_csv(behavior_path)

    interactions = max(0, int(args.interactions))
    max_rounds = max(100, int(args.max_rounds))
    outer_bags = max(2, int(args.outer_bags))
    model_dir = args.model_dir.expanduser().resolve()
    model_dir.mkdir(parents=True, exist_ok=True)

    specs = [
        (
            "hazard_5s_compact",
            hazard,
            "label_next_inferred_placement_any_5s",
            list(HAZARD_FEATURES),
            model_dir / "hazard_5s_compact.joblib",
        ),
        (
            "level_2ticks_compact",
            behavior,
            "label_near_best_2ticks",
            list(LEVEL_FEATURES),
            model_dir / "level_2ticks_compact.joblib",
        ),
    ]

    outputs: list[dict[str, Any]] = []
    for index, (task, frame, target, features, output) in enumerate(specs):
        missing_columns = [name for name in features + [target] if name not in frame.columns]
        if missing_columns:
            raise SystemExit(f"{task}: dataset is missing columns: {missing_columns}")
        y = pd.to_numeric(frame[target], errors="coerce")
        valid = y.notna()
        fit_frame = frame.loc[valid]
        y = y.loc[valid].astype(int)
        if y.nunique() < 2:
            raise SystemExit(f"{task}: target has fewer than two classes")
        model = shared._fit_classifier(
            deps,
            shared._numeric(pd, fit_frame, features),
            y,
            interactions=min(interactions, max(0, len(features) // 2)),
            max_rounds=max_rounds,
            outer_bags=outer_bags,
            seed=9100 + index,
        )
        bundle = {
            "reportVersion": EXPECTED_REPORT_VERSION,
            "task": task,
            "target": target,
            "features": features,
            "rows": int(len(fit_frame)),
            "markets": int(pd.to_numeric(fit_frame["market_id"], errors="coerce").nunique()),
            "model": model,
            "researchOnly": True,
            "automaticStrategyPromotion": False,
            "causalClaim": False,
            "labelIdentity": "INFERRED_PLACEMENT_NOT_PRIVATE_ORDER_GROUND_TRUTH",
            "fitScope": "full currently available strict-pre dataset after separate walk-forward validation; frozen for forward paper inference",
        }
        deps["joblib"].dump(bundle, output)
        outputs.append({"task": task, "path": str(output), "rows": bundle["rows"], "markets": bundle["markets"]})

    print("Frozen Maker Direct EBM runtime artifacts written:")
    for row in outputs:
        print(f"  {row['task']}: {row['path']} ({row['rows']} rows / {row['markets']} markets)")
    print("researchOnly=true; automaticStrategyPromotion=false; live trading unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
