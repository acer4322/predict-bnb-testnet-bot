from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_trainer():
    path = Path(__file__).resolve().parents[1] / "tools" / "train_target_maker_taker_state_link_v3.py"
    spec = importlib.util.spec_from_file_location("state_link_v3_trainer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_state_link_v3_ebm_runtime_smoke():
    trainer = _load_trainer()
    deps = trainer._imports()
    pd = deps["pd"]
    np = deps["np"]

    rows = 160
    duration = np.linspace(0.0, 3000.0, rows)
    imbalance = np.tile(np.array([0.02, 0.12, 0.32, 0.55]), rows // 4)
    direction = np.sin(np.linspace(0.0, 12.0, rows))
    X = pd.DataFrame(
        {
            "highres_fill_duration_ms": duration,
            "post_fill_maker_imbalance_ratio": imbalance,
            "side_aligned_direction_score": direction,
        }
    )
    y = pd.Series(
        (((duration >= 1500.0) & (imbalance >= 0.25)) | (direction > 0.8)).astype(int)
    )

    model = trainer._fit_model(
        deps,
        X,
        y,
        interactions=2,
        max_rounds=200,
        outer_bags=2,
        seed=7,
    )
    probability = model.predict_proba(X)[:, 1]
    metrics = trainer._metrics(deps, y, probability)

    assert len(probability) == rows
    assert 0.0 <= float(probability.min()) <= float(probability.max()) <= 1.0
    assert metrics["rocAuc"] is not None
    assert metrics["rocAuc"] > 0.7

    folds = trainer._walk_forward_folds(
        list(range(30)),
        min_train_markets=20,
        test_markets=5,
        max_folds=2,
    )
    assert len(folds) == 2
    assert set(folds[0]["trainMarkets"]).isdisjoint(folds[0]["testMarkets"])
