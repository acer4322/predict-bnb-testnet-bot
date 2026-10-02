from __future__ import annotations

import math

import pytest

from predict_bot.target_taker_reentry_v1_analysis import (
    FEATURE_SETS,
    FORBIDDEN_FEATURE_PREFIXES,
    FULL_FEATURES,
    PUBLIC_ONLY_FEATURES,
    _balanced_training_weights,
    _common_task_frame,
    _episode_weights,
    _validate_feature_contract,
    _walk_forward_folds,
)
from predict_bot.target_taker_reentry_v1_dataset import DATASET_VERSION


def test_reentry_feature_contract_is_nested_and_has_no_future_target_inputs() -> None:
    _validate_feature_contract()
    assert FEATURE_SETS["PUBLIC_ONLY"] == PUBLIC_ONLY_FEATURES
    assert FEATURE_SETS["PUBLIC_ACTOR"][: len(PUBLIC_ONLY_FEATURES)] == PUBLIC_ONLY_FEATURES
    assert FEATURE_SETS["PUBLIC_ACTOR_DELTA"][: len(FEATURE_SETS["PUBLIC_ACTOR"])] == FEATURE_SETS["PUBLIC_ACTOR"]
    for features in FEATURE_SETS.values():
        assert len(features) == len(set(features))
        assert not any(feature.startswith(FORBIDDEN_FEATURE_PREFIXES) for feature in features)


def test_episode_weighting_gives_each_prior_entry_equal_mass_and_balances_training_classes() -> None:
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame(
        {
            "__episode_id": ["a", "a", "a", "a", "b", "c", "c"],
            "label": [0, 0, 0, 0, 1, 1, 1],
        }
    )
    base = _episode_weights(frame)
    masses = frame.assign(w=base).groupby("__episode_id")["w"].sum().to_dict()
    assert masses["a"] == pytest.approx(masses["b"])
    assert masses["b"] == pytest.approx(masses["c"])

    training = _balanced_training_weights(frame, "label")
    class_mass = frame.assign(w=training).groupby("label")["w"].sum().to_dict()
    assert class_mass[0] == pytest.approx(class_mass[1])
    assert float(training.mean()) == pytest.approx(1.0)


def test_market_walk_forward_has_no_market_leakage_and_is_chronological() -> None:
    markets = list(range(1, 171))
    folds = _walk_forward_folds(
        markets,
        min_train_markets=80,
        min_calibration_markets=12,
        calibration_fraction=0.15,
        test_markets=30,
        max_folds=3,
    )
    assert folds
    previous_test_max = 0
    for fold in folds:
        train = fold["trainMarkets"]
        calibration = fold["calibrationMarkets"]
        test = fold["testMarkets"]
        assert set(train).isdisjoint(calibration)
        assert set(train).isdisjoint(test)
        assert set(calibration).isdisjoint(test)
        assert max(train) < min(calibration) < min(test)
        assert min(test) > previous_test_max
        previous_test_max = max(test)


def test_common_cohort_requires_frozen16_and_full_feature_complete_case() -> None:
    pd = pytest.importorskip("pandas")
    np = pytest.importorskip("numpy")
    label = "label_same_side_reentry_within_5000ms"
    rows = []
    for index in range(4):
        row = {
            "dataset_version": DATASET_VERSION,
            "market_id": 100 + index,
            "decision_sampled_at_ms": 1_000_000 + index,
            "actor_prev_parent_id": f"p{index}",
            "actor_prev_parent_source": "TEST",
            "frozen16_complete": 1,
            label: index % 2,
        }
        for feature in FULL_FEATURES:
            row[feature] = 1.0
        rows.append(row)

    # Row 1: frozen-side contract not complete.
    rows[1]["frozen16_complete"] = 0
    # Row 2: largest family has a missing value; all families must lose this row
    # so feature-set comparisons stay on one common cohort.
    rows[2][FULL_FEATURES[-1]] = np.nan
    # Row 3: ambiguous horizon label.
    rows[3][label] = np.nan

    frame = pd.DataFrame(rows)
    deps = {"pd": pd, "np": np}
    eligible, diagnostics = _common_task_frame(deps, frame, label)
    assert len(eligible) == 1
    assert diagnostics["commonCompleteRows"] == 1
    assert diagnostics["labelKnownRows"] == 3
    assert diagnostics["labelKnownFrozen16Rows"] == 2
    assert eligible.iloc[0]["actor_prev_parent_id"] == "p0"
    assert all(math.isfinite(float(eligible.iloc[0][feature])) for feature in FULL_FEATURES)
