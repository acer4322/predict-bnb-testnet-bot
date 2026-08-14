from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "tools" / "train_target_maker_ebm_v3.py"
    spec = importlib.util.spec_from_file_location("target_maker_ebm_v3_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hierarchical_ebm_v3_walk_forward_runtime_smoke() -> None:
    module = _load_module()
    deps = module._imports()
    pd = deps["pd"]

    rows = []
    for market_index in range(20):
        market_id = 20_000 + market_index
        for row_index in range(12):
            continued = int((market_index + row_index) % 2 == 0)
            refill = int(row_index % 3 == 0) if continued else None
            toward = int(row_index % 4 < 2) if continued and refill == 0 else None
            row = {feature: 0.0 for feature in module.POLICY_FEATURE_COLUMNS}
            row.update({
                "market_id": market_id,
                "last_target_ms": 2_000_000 + market_index * 300_000 + row_index * 1000,
                "label_continue": continued,
                "label_refill_given_continue": refill,
                "label_reprice_toward_touch": toward,
                "label_reprice_ticks_abs": 1.0 if toward is not None else None,
                "post_action_delay_ms": 400.0 if continued else None,
                "post_action": (
                    "SAME_PRICE_REFILL_CONFIRMED_NEXT_PARENT"
                    if refill == 1
                    else "REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT"
                    if continued
                    else "NO_CONFIRMED_NEXT_PARENT_5S"
                ),
                "seconds_left": float(280 - row_index * 15),
                "resting_ms": float(100 + row_index * 40),
                "target_price": float(0.15 + 0.05 * (row_index % 10)),
                "observed_filled_near_18": float(row_index % 2 == 0),
                "path_fill_duration_ms": float((row_index % 5) * 150),
                "traj_side_predict_mid_delta_250ms": float((continued * 2 - 1) * 0.01),
            })
            rows.append(row)

    df = pd.DataFrame(rows)
    markets = module._market_order(df)
    folds = module._walk_forward_folds(markets, min_train_markets=10, test_markets=5, max_folds=1)
    frame = module._task_frame(df, "label_continue")
    result = module._run_fold(
        deps=deps,
        frame=frame,
        label="label_continue",
        features=module.POLICY_FEATURE_COLUMNS[:8],
        fold=folds[0],
        interactions=0,
        max_rounds=100,
        outer_bags=2,
        seed=43,
    )
    assert result["status"] == "OK"
    assert result["test"]["rows"] > 0
    assert result["test"]["logLoss"] >= 0
    assert "logLossLiftVsPrior" in result

    empirical = module._empirical_structure(df)
    assert empirical["continueDelay"]["rows"] > 0
    assert empirical["signedReprice"]["rows"] > 0
