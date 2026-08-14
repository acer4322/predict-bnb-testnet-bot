from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "tools" / "train_target_maker_ebm_v2.py"
    spec = importlib.util.spec_from_file_location("target_maker_ebm_v2_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_multiclass_ebm_v2_runtime_smoke() -> None:
    module = _load_module()
    deps = module._imports()
    pd = deps["pd"]

    rows = []
    post_actions = [
        "NO_CONFIRMED_NEXT_PARENT_5S",
        "SAME_PRICE_REFILL_CONFIRMED_NEXT_PARENT",
        "REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT",
    ]
    for market_index in range(18):
        market_id = 10_000 + market_index
        for row_index in range(15):
            action_index = (market_index + row_index) % 3
            row = {feature: 0.0 for feature in module.POLICY_FEATURES}
            row.update(
                market_id=market_id,
                last_target_ms=1_000_000 + market_index * 300_000 + row_index * 1000,
                target_side="UP" if row_index % 2 == 0 else "DOWN",
                post_action=post_actions[action_index],
                seconds_left=float(285 - row_index * 17),
                target_price=float(0.10 + 0.04 * (row_index % 20)),
                native_price=float(0.90 - 0.04 * (row_index % 20)),
                resting_ms=float(150 + 100 * (row_index % 8)),
                observed_filled_near_18=float(action_index == 1),
                target_filled_shares=18.0 if action_index == 1 else 9.0 + action_index,
                prior_maker_imbalance_ratio=float((row_index % 5) * 0.08),
                side_aligned_spot_queue_imbalance=float((-1.0, 0.0, 1.0)[action_index]),
                side_aligned_direction_score=float((-0.5, 0.0, 0.5)[action_index]),
                spot_minus_strike_bps=float(row_index - 7),
            )
            rows.append(row)

    df = module._attach_action_label(pd.DataFrame(rows))
    train_markets, validation_markets, test_markets = module._market_splits(df)
    train = df[df["market_id"].isin(train_markets)].copy()
    validation = df[df["market_id"].isin(validation_markets)].copy()
    test = df[df["market_id"].isin(test_markets)].copy()

    y_train = train["next_action_index"].astype(int)
    y_validation = validation["next_action_index"].astype(int)
    y_test = test["next_action_index"].astype(int)
    X_train = module._numeric_frame(pd, train, module.POLICY_FEATURES)
    X_validation = module._numeric_frame(pd, validation, module.POLICY_FEATURES)
    X_test = module._numeric_frame(pd, test, module.POLICY_FEATURES)

    model = module._fit_ebm(
        deps=deps,
        X_train=X_train,
        y_train=y_train,
        interactions=0,
        random_state=42,
        max_rounds=100,
        outer_bags=2,
    )
    validation_probability = model.predict_proba(X_validation)
    assert validation_probability.shape == (len(validation), 3)

    calibrator = module._fit_probability_calibrator(deps, y_validation, validation_probability)
    test_probability = module._apply_probability_calibrator(deps, calibrator, model.predict_proba(X_test))
    assert test_probability.shape == (len(test), 3)
    assert deps["np"].allclose(test_probability.sum(axis=1), 1.0)

    metrics = module._multiclass_metrics(deps, y_test, test_probability)
    assert metrics["rows"] == len(test)
    assert set(metrics["perClass"]) == set(module.ACTION_CLASSES)

    regimes = module._derive_regimes(test)
    tables = module._empirical_action_tables(regimes, min_rows=5)
    assert tables["fill"]
    assert tables["time"]
