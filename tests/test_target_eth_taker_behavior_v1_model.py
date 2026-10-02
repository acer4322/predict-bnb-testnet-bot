from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_trainer():
    path = Path(__file__).resolve().parents[1] / "tools" / "train_target_eth_taker_behavior_v1.py"
    spec = importlib.util.spec_from_file_location("eth_taker_trainer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_eth_side_model_has_no_chosen_side_leakage_and_size_is_conditional():
    trainer = _load_trainer()
    for features in trainer.SIDE_FEATURE_SETS.values():
        assert "label_side_up" not in features
        assert not any(feature.startswith("chosen_") for feature in features)
    assert any("label_side_up" in features for features in trainer.SIZE_FEATURE_SETS.values())
    assert any(any(feature.startswith("chosen_") for feature in features) for features in trainer.SIZE_FEATURE_SETS.values())


def test_eth_hazard_walk_forward_keeps_chronological_negative_capable_market_sequence():
    trainer = _load_trainer()
    markets = list(range(100, 140))
    folds = trainer._folds(markets, min_train=20, test_markets=8, max_folds=2)
    assert len(folds) == 2
    assert folds[0]["testMarkets"] == list(range(120, 128))
    assert max(folds[0]["trainMarkets"] + folds[0]["calibrationMarkets"]) < min(folds[0]["testMarkets"])
    assert set(folds[0]["trainMarkets"]).isdisjoint(folds[0]["testMarkets"])
