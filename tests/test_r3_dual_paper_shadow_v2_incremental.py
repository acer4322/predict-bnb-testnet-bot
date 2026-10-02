import sqlite3
import threading

import pytest

from src.predict_bot import r3_dual_paper_shadow_v2 as r3


def _normal_packet(_index, _checkpoint_ms, feature_row):
    return {
        "contextType": "NORMAL_FULL_FILL",
        "ownershipState": 0,
        "terminalCertainty": 1,
        "remainingObligationFraction": 0.0,
        "passiveProgressProbability": 1.0,
        "reconcileNeeded": 0,
        "cancelPending": 0,
        "unknownState": 0,
        "eventValidity": 1,
        "observationAgeMs": 0.0,
        "elapsedSincePriorMs": float(feature_row["age_since_last_ms"]),
        "hasPriorObservation": 1.0,
    }


@pytest.mark.parametrize("soft", [False, True])
def test_batched_model_scores_preserve_state_machine_semantics(soft):
    events = [
        (1_000, "MAKER", "UP", 0.43, 4.0),
        (2_500, "MAKER", "DOWN", 0.52, 2.0),
        (4_200, "TAKER", "DOWN", 0.54, 1.5),
        (7_800, "MAKER", "UP", 0.41, 3.0),
    ]
    rows = r3.features(events)

    scalar = r3.run_states(rows, _normal_packet, soft)
    batched = r3.run_states(rows, _normal_packet, soft, r3.model_scores(rows))

    assert len(batched) == len(scalar)
    for old, new in zip(scalar, batched):
        assert new[:3] == old[:3]
        assert new[3:9] == pytest.approx(old[3:9], abs=1e-12)
        assert new[9:] == old[9:]


def test_fingerprint_match_requires_current_source_and_both_outputs():
    runtime = object.__new__(r3.Runtime)
    runtime.db = sqlite3.connect(":memory:", check_same_thread=False)
    runtime.lock = threading.RLock()
    runtime.db.executescript(
        """
        create table r3_source_fingerprints_v2(
            lane text,
            market_id integer,
            source_fingerprint text,
            fingerprint_version text,
            processed_at_ms integer,
            primary key(lane, market_id)
        );
        create table r3_shadow_market_v2(
            strategy_version text,
            lane text,
            market_id integer
        );
        """
    )
    runtime.db.execute(
        "insert into r3_source_fingerprints_v2 values(?,?,?,?,?)",
        ("HFT_PAPER", 42, "same", r3.SOURCE_FINGERPRINT_VERSION, 1),
    )
    runtime.db.executemany(
        "insert into r3_shadow_market_v2 values(?,?,?)",
        [(r3.BASE, "HFT_PAPER", 42), (r3.SOFT, "HFT_PAPER", 42)],
    )

    assert runtime._fingerprint_matches("HFT_PAPER", 42, "same")
    assert not runtime._fingerprint_matches("HFT_PAPER", 42, "changed")

    runtime.db.execute(
        "delete from r3_shadow_market_v2 where strategy_version=?", (r3.SOFT,)
    )
    assert not runtime._fingerprint_matches("HFT_PAPER", 42, "same")
    runtime.db.close()


def test_unchanged_market_skips_recompute(monkeypatch):
    runtime = object.__new__(r3.Runtime)
    monkeypatch.setattr(
        runtime,
        "_dream_source",
        lambda _market_id: ("fingerprint", [(1_000, "MAKER", "UP", 0.4, 1.0)], []),
    )
    monkeypatch.setattr(runtime, "_fingerprint_matches", lambda *_args: True)

    def fail_write(*_args):
        raise AssertionError("unchanged market must not run model inference")

    monkeypatch.setattr(runtime, "_write", fail_write)
    assert runtime._refresh_dream_market(42) is False
