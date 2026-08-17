from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "analyze_target_taker_burst_hazard_trigger_replay_v1.py"
spec = importlib.util.spec_from_file_location("burst_trigger_replay", SCRIPT)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _row(t: int, score: float, label: int = 0, onset: int | None = None):
    return {
        "experiment": "X",
        "horizon": 5,
        "market_id": 1,
        "sampled_ms": t * 1000,
        "phase": "MID",
        "score": score,
        "label": label,
        "next_delta_ms": (onset - t) * 1000 if onset is not None else None,
        "next_onset_ms": onset * 1000 if onset is not None else None,
    }


def test_adaptive_threshold_uses_only_prior_scores():
    rows = [_row(i, float(i)) for i in range(5)]
    marked, coverage = mod._adaptive_marks(rows, fraction=0.20, window_rows=5, min_history_rows=3)
    assert marked[0]["adaptive_threshold"] is None
    assert marked[1]["adaptive_threshold"] is None
    assert marked[2]["adaptive_threshold"] is None
    # At t=3, history is [0,1,2]. Current score 3 must not influence its own threshold.
    assert marked[3]["adaptive_threshold"] == mod._quantile([0.0, 1.0, 2.0], 0.80)
    assert coverage["thresholdRows"] == 2


def test_level_cooldown_prevents_one_trigger_per_second_spam():
    rows = []
    for t in range(10):
        row = _row(t, 1.0, label=1 if t in (3, 4, 5, 8) else 0, onset=9 if t in (3, 4, 5, 8) else None)
        row["adaptive_threshold"] = 0.5
        row["above_threshold"] = True
        rows.append(row)
    result = mod._run_policy(rows, mode="LEVEL", cooldown_s=5)
    assert result["triggers"] == 2


def test_rising_policy_only_fires_on_below_to_above_transition():
    pattern = [False, True, True, False, True, True]
    rows = []
    for t, above in enumerate(pattern):
        row = _row(t, 1.0 if above else 0.0)
        row["adaptive_threshold"] = 0.5
        row["above_threshold"] = above
        rows.append(row)
    result = mod._run_policy(rows, mode="RISING", cooldown_s=2)
    assert result["triggers"] == 2
