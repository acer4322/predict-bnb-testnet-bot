from predict_bot.poly_gap_live_v38 import (
    BINANCE_LEADING,
    LEADER_GUARD_ENTRY_AND_KILL,
    LEADER_GUARD_ENTRY_ONLY,
    LEADER_GUARD_OFF,
    MIXED,
    POLY_LEADING,
    leader_guard_policy,
)


def decide(mode, regime, *, fresh=True, locked=False, active=False):
    return leader_guard_policy(
        mode=mode,
        regime=regime,
        fresh=fresh,
        market_locked=locked,
        active_open=active,
    )


def test_off_never_blocks_entry():
    assert decide(LEADER_GUARD_OFF, BINANCE_LEADING, fresh=False) == "ALLOW"


def test_entry_only_allows_only_fresh_poly_leading():
    assert decide(LEADER_GUARD_ENTRY_ONLY, POLY_LEADING) == "ALLOW"
    assert decide(LEADER_GUARD_ENTRY_ONLY, BINANCE_LEADING) == "BLOCK_NOT_POLY"
    assert decide(LEADER_GUARD_ENTRY_ONLY, MIXED) == "BLOCK_NOT_POLY"
    assert decide(LEADER_GUARD_ENTRY_ONLY, "INSUFFICIENT_DATA") == "BLOCK_NOT_POLY"
    assert decide(LEADER_GUARD_ENTRY_ONLY, POLY_LEADING, fresh=False) == "BLOCK_UNAVAILABLE"


def test_entry_only_does_not_force_existing_position_out():
    assert decide(LEADER_GUARD_ENTRY_ONLY, BINANCE_LEADING, active=True) == "BLOCK_NOT_POLY"
    assert decide(LEADER_GUARD_ENTRY_ONLY, MIXED, active=True) == "BLOCK_NOT_POLY"


def test_kill_mode_locks_on_explicit_binance_or_mixed():
    assert decide(LEADER_GUARD_ENTRY_AND_KILL, BINANCE_LEADING) == "LOCK_MARKET"
    assert decide(LEADER_GUARD_ENTRY_AND_KILL, MIXED) == "LOCK_MARKET"
    assert decide(LEADER_GUARD_ENTRY_AND_KILL, BINANCE_LEADING, active=True) == "LOCK_AND_EXIT"
    assert decide(LEADER_GUARD_ENTRY_AND_KILL, MIXED, active=True) == "LOCK_AND_EXIT"


def test_kill_mode_does_not_exit_on_insufficient_or_stale_data():
    assert decide(LEADER_GUARD_ENTRY_AND_KILL, "INSUFFICIENT_DATA", active=True) == "BLOCK_NOT_POLY"
    assert decide(
        LEADER_GUARD_ENTRY_AND_KILL,
        POLY_LEADING,
        fresh=False,
        active=True,
    ) == "BLOCK_UNAVAILABLE"


def test_persisted_market_lock_cannot_rearm_same_market():
    assert decide(
        LEADER_GUARD_ENTRY_AND_KILL,
        POLY_LEADING,
        locked=True,
        active=False,
    ) == "BLOCK_LOCKED"
    assert decide(
        LEADER_GUARD_ENTRY_AND_KILL,
        POLY_LEADING,
        locked=True,
        active=True,
    ) == "EXIT_LOCKED"
