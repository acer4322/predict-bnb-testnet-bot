from __future__ import annotations

from types import SimpleNamespace

from predict_bot import unified_controller_cap100_echtgeld_slow_v1 as slow


def _runtime_without_init():
    r = object.__new__(slow.UnifiedControllerCap100EchtgeldSlowV1)
    r.orders = {}
    r.orphan_orders = {}
    r.maker_generation_blocked = False
    r.maker_generation_order_ids = set()
    r.last_maker_terminal_ms = None
    r.slow_metrics = {
        "makerLifecycleBlocks": 0,
        "makerGenerationsOpened": 0,
        "makerGenerationsTerminal": 0,
        "makerSettleWindowBlocks": 0,
    }
    return r


def test_resting_or_partial_maker_blocks_new_generation():
    r = _runtime_without_init()
    r.orders[("UP", 42)] = object()
    assert r._maker_generation_open(1000) is False
    assert r.slow_metrics["makerLifecycleBlocks"] == 1


def test_terminal_settle_window_then_reopens():
    r = _runtime_without_init()
    r.last_maker_terminal_ms = 10_000
    assert r._maker_generation_open(10_000 + slow.MAKER_TERMINAL_SETTLE_MS - 1) is False
    assert r._maker_generation_open(10_000 + slow.MAKER_TERMINAL_SETTLE_MS) is True


def test_collection_guard_only_allows_maker_that_reduces_existing_imbalance():
    r = _runtime_without_init()
    r.inventory = SimpleNamespace(maker_up=18.0, maker_down=0.0)
    assert r._maker_would_expand_imbalance("UP") is True
    assert r._maker_would_expand_imbalance("DOWN") is False
    r.inventory = SimpleNamespace(maker_up=0.0, maker_down=18.0)
    assert r._maker_would_expand_imbalance("DOWN") is True
    assert r._maker_would_expand_imbalance("UP") is False
