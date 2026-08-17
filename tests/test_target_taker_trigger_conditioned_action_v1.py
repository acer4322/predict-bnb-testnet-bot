from __future__ import annotations

import copy

import analyze_target_taker_trigger_conditioned_action_v1 as mod


def _source(*, success: int = 1, delta: int = 2000, burst_type: str = "SIDE_FLIP", side: str = "DOWN"):
    row = {
        "cap2_label_next_burst_5s": success,
        "cap2_next_burst_delta_ms": delta,
        "cap2_next_burst_type": burst_type,
        "cap2_next_burst_side": side,
        "seconds_left": 120,
        "macro_phase": "MID",
    }
    for feature in mod.onset.FROZEN16:
        row.setdefault(feature, 0.0)
    return row


def test_next_action_labels_distinguish_clean_mixed_and_first():
    clean = mod._next_action_labels(_source(burst_type="SIDE_FLIP", side="DOWN"))
    assert clean["success"] == 1
    assert clean["next_clean_mixed"] == "CLEAN"
    assert clean["next_burst_side"] == "DOWN"
    assert clean["next_is_subsequent"] == 1

    mixed = mod._next_action_labels(_source(burst_type="MIXED", side=""))
    assert mixed["next_clean_mixed"] == "MIXED"
    assert mixed["next_burst_side"] == ""
    assert mixed["next_is_subsequent"] == 1

    first = mod._next_action_labels(_source(burst_type="FIRST_ENTRY", side="UP"))
    assert first["next_clean_mixed"] == "CLEAN"
    assert first["next_is_subsequent"] == 0


def test_trigger_eligibility_does_not_use_target_action_labels(monkeypatch):
    monkeypatch.setattr(mod.actor, "_market_starts", lambda rows: {1: 1000})
    marked = [
        {
            "fold": 1,
            "market_id": 1,
            "sampled_ms": 5000,
            "phase": "MID",
            "score": 0.9,
            "adaptive_threshold": 0.8,
            "above_threshold": True,
        }
    ]
    index_a = {(1, 5000): _source(success=1, delta=2000, burst_type="SIDE_FLIP", side="DOWN")}
    index_b = {(1, 5000): _source(success=0, delta=9000, burst_type="MIXED", side="")}

    a = mod._extract_triggers(marked=marked, hazard_index=index_a, entry_delay_s=0, cooldown_s=2)
    b = mod._extract_triggers(marked=copy.deepcopy(marked), hazard_index=index_b, entry_delay_s=0, cooldown_s=2)

    assert [(r["market_id"], r["sampled_ms"]) for r in a] == [(1, 5000)]
    assert [(r["market_id"], r["sampled_ms"]) for r in b] == [(1, 5000)]
    assert a[0]["success"] == 1
    assert b[0]["success"] == 0


def test_extract_triggers_obeys_own_entry_and_cooldown(monkeypatch):
    monkeypatch.setattr(mod.actor, "_market_starts", lambda rows: {1: 1000})
    marked = [
        {"fold": 1, "market_id": 1, "sampled_ms": 2000, "phase": "OPEN", "score": 0.9, "adaptive_threshold": 0.8, "above_threshold": True},
        {"fold": 1, "market_id": 1, "sampled_ms": 5000, "phase": "OPEN", "score": 0.9, "adaptive_threshold": 0.8, "above_threshold": True},
        {"fold": 1, "market_id": 1, "sampled_ms": 7000, "phase": "OPEN", "score": 0.9, "adaptive_threshold": 0.8, "above_threshold": True},
        {"fold": 1, "market_id": 1, "sampled_ms": 10000, "phase": "OPEN", "score": 0.9, "adaptive_threshold": 0.8, "above_threshold": True},
    ]
    index = {(1, int(row["sampled_ms"])): _source() for row in marked}
    triggers = mod._extract_triggers(
        marked=marked,
        hazard_index=index,
        entry_delay_s=3,
        cooldown_s=5,
    )
    assert [row["sampled_ms"] for row in triggers] == [10000]


def test_dedupe_successful_keeps_earliest_trigger_for_same_burst():
    rows = [
        {"market_id": 1, "sampled_ms": 5000, "success": 1, "next_onset_ms": 9000},
        {"market_id": 1, "sampled_ms": 6000, "success": 1, "next_onset_ms": 9000},
        {"market_id": 1, "sampled_ms": 7000, "success": 1, "next_onset_ms": 10000},
        {"market_id": 1, "sampled_ms": 8000, "success": 0, "next_onset_ms": None},
    ]
    deduped = mod._dedupe_successful(rows)
    assert [(row["sampled_ms"], row["next_onset_ms"]) for row in deduped] == [
        (5000, 9000),
        (7000, 10000),
    ]
