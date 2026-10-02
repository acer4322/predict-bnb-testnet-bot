from __future__ import annotations

import threading
import time

from predict_bot.live_rules_async_preflight_patch import _patch_engine


class FakeEngine:
    def __init__(self) -> None:
        self.runtime_enabled = True
        self.saved_values: list[dict[str, object]] = []
        self.preflight_started = threading.Event()
        self.release_preflight = threading.Event()
        self.preflight_calls = 0
        self.status = "ARMED"
        self.armed = True
        self.last_error = None
        self.lock = threading.RLock()

    def _preflight(self) -> None:
        self.preflight_calls += 1
        self.preflight_started.set()
        self.release_preflight.wait(timeout=2.0)

    def update_live_rules(self, values: dict[str, object]) -> dict[str, object]:
        self.saved_values.append(dict(values))
        if self.runtime_enabled:
            self._preflight()
        return {"rules": dict(values)}


def test_rule_save_returns_before_network_preflight_finishes() -> None:
    _patch_engine(FakeEngine)
    engine = FakeEngine()

    started = time.monotonic()
    response = engine.update_live_rules({"strategy": "TEST"})
    elapsed = time.monotonic() - started

    assert elapsed < 0.25
    assert engine.saved_values == [{"strategy": "TEST"}]
    assert response["rules"] == {"strategy": "TEST"}
    assert response["liveRulesSave"]["status"] == "SAVED"
    assert response["liveRulesSave"]["preflightStatus"] == "QUEUED"
    assert engine.preflight_started.wait(timeout=1.0)

    engine.release_preflight.set()


def test_saves_while_preflight_runs_are_coalesced_into_latest_recheck() -> None:
    _patch_engine(FakeEngine)
    engine = FakeEngine()

    engine.update_live_rules({"strategy": "FIRST"})
    assert engine.preflight_started.wait(timeout=1.0)

    engine.update_live_rules({"strategy": "SECOND"})
    assert engine.saved_values == [
        {"strategy": "FIRST"},
        {"strategy": "SECOND"},
    ]

    engine.release_preflight.set()
    deadline = time.monotonic() + 1.0
    while engine.preflight_calls < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert engine.preflight_calls == 2
