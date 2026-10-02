from predict_bot.poly_gap_live_v33 import PersistedPaperPauseFallbackPolyGapLiveEngine


def _engine():
    engine = object.__new__(PersistedPaperPauseFallbackPolyGapLiveEngine)
    engine._paper_guard_degraded_fallback_count = 0
    engine._last_persisted_pause_fallback = None
    engine.last_chop_guard = {}
    engine._emit_guard_transition = lambda _state: None
    return engine


def test_heartbeat_stale_with_persisted_pause_off_allows_runtime_live_breaker(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(
        PersistedPaperPauseFallbackPolyGapLiveEngine.__mro__[1],
        "_refresh_chop_guard",
        lambda self, force=False: {
            "verified": False,
            "blocked": None,
            "error": "Paper CHOP health heartbeat stale: 7000ms > 5000ms",
            "reason": "local Paper CHOP health heartbeat could not be verified; new live entries fail closed",
        },
    )
    monkeypatch.setattr(
        engine,
        "_read_persisted_pause_state",
        lambda: {"paused": 0, "changed_at_ms": 123, "reason": "armed"},
    )

    state = engine._refresh_chop_guard(force=True)

    assert state["verified"] is True
    assert state["blocked"] is False
    assert state["degraded"] is True
    assert state["heartbeatVerified"] is False
    assert state["liveImmediateBreakerOwner"] == "LIVE_COMPLETED_REVERSAL_EXITS_RUNTIME_THRESHOLD"


def test_heartbeat_stale_with_persisted_pause_on_still_blocks(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(
        PersistedPaperPauseFallbackPolyGapLiveEngine.__mro__[1],
        "_refresh_chop_guard",
        lambda self, force=False: {
            "verified": False,
            "blocked": None,
            "error": "Paper CHOP health heartbeat stale: 7000ms > 5000ms",
            "reason": "local Paper CHOP health heartbeat could not be verified; new live entries fail closed",
        },
    )
    monkeypatch.setattr(
        engine,
        "_read_persisted_pause_state",
        lambda: {"paused": 1, "changed_at_ms": 456, "reason": "persistent chop pause"},
    )

    state = engine._refresh_chop_guard(force=True)

    assert state["verified"] is True
    assert state["blocked"] is True
    assert state["persistentPaused"] is True


def test_non_heartbeat_failure_remains_fail_closed(monkeypatch):
    engine = _engine()
    original = {
        "verified": False,
        "blocked": None,
        "error": "database disk image is malformed",
        "reason": "local Paper CHOP guard unavailable",
    }
    monkeypatch.setattr(
        PersistedPaperPauseFallbackPolyGapLiveEngine.__mro__[1],
        "_refresh_chop_guard",
        lambda self, force=False: dict(original),
    )

    state = engine._refresh_chop_guard(force=True)

    assert state["verified"] is False
    assert state["blocked"] is None
