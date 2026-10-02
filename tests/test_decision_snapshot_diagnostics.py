from __future__ import annotations

import json

import pytest

from predict_bot import live_trading
from predict_bot.decision_snapshot_diagnostics import (
    DECISION_SNAPSHOT_VERSION,
    build_blocked_decision_snapshot,
    decision_snapshot_from_payload,
    install_decision_snapshot_diagnostics,
)
from predict_bot.live_trading import LiveM0WEngine


class _FakeEngine:
    def current_market(self):
        return {"market_id": 6815339}

    def _latest_prediction_book_check(self, *, market_id: int, side: str):
        assert market_id == 6815339
        assert side == "UP"
        return (
            {
                "latest_ask": 0.34,
                "latest_ask_size": 12.0,
            },
            None,
            None,
            None,
            {
                "latestMarketId": 6815339,
                "orientation": "DIRECT_UP_VERIFIED",
                "latestLocalAsk": 0.34,
                "latestLocalAskSize": 12.0,
                "latestLocalBookAgeMs": 85.0,
            },
        )

    @staticmethod
    def _signal_monotonic_seconds(signal, key):
        assert key == "market_event_received_monotonic_ns"
        return 1.0

    @staticmethod
    def _elapsed_ms(started, finished):
        assert started == 1.0
        assert finished >= started
        return 27.5


def test_build_blocked_decision_snapshot_compares_signal_and_current_ask():
    snapshot = build_blocked_decision_snapshot(
        _FakeEngine(),
        {
            "market_id": 6815339,
            "side": "UP",
            "entry_price": 0.191,
            "signal_prediction_ask": 0.191,
            "signal_prediction_ask_size": 3.0,
            "signal_prediction_book_age_ms": 210.0,
            "market_event_received_monotonic_ns": 1_000_000_000,
        },
        "BLOCKED_DRAWDOWN_CONTROL",
    )

    assert snapshot is not None
    assert snapshot["decisionSnapshotVersion"] == DECISION_SNAPSHOT_VERSION
    assert snapshot["decisionCaptureStatus"] == "AVAILABLE"
    assert snapshot["signalPrice"] == 0.191
    assert snapshot["decisionLatestAsk"] == 0.34
    assert snapshot["decisionAskDelta"] == pytest.approx(0.149)
    assert snapshot["decisionAskAbsDelta"] == pytest.approx(0.149)
    assert snapshot["latestLocalBookAgeMs"] == 85.0
    assert snapshot["decisionMarketMatchesSignal"] is True
    assert snapshot["decisionCurrentMarketMatchesSignal"] is True
    assert snapshot["eventToDecisionSnapshotMs"] == 27.5


def test_unrelated_block_status_does_not_request_snapshot():
    snapshot = build_blocked_decision_snapshot(
        _FakeEngine(),
        {"market_id": 6815339, "side": "UP", "entry_price": 0.191},
        "BLOCKED_INVALID_SIGNAL",
    )
    assert snapshot is None


def test_decision_snapshot_payload_is_sanitized():
    snapshot = decision_snapshot_from_payload(
        {
            "decisionSnapshotVersion": DECISION_SNAPSHOT_VERSION,
            "signalPrice": 0.191,
            "decisionLatestAsk": 0.34,
            "latestLocalBookAgeMs": 85.0,
            "privateExchangePayload": {"must": "not leak"},
        }
    )

    assert snapshot is not None
    assert snapshot["decisionSnapshotVersion"] == DECISION_SNAPSHOT_VERSION
    assert snapshot["decisionLatestAsk"] == 0.34
    assert snapshot["signalPrice"] == 0.191
    assert snapshot["signalAsk"] == 0.191
    assert snapshot["decisionAskDelta"] == pytest.approx(0.149)
    assert snapshot["latestLocalBookAgeMs"] == 85.0
    assert "privateExchangePayload" not in snapshot


def test_old_sanitized_payload_is_recovered():
    snapshot = decision_snapshot_from_payload(
        json.dumps(
            {
                "signalPrice": 0.4422,
                "signalBookAgeMs": 150.0,
                "latestLocalAsk": 0.51,
                "latestLocalBookAgeMs": 82.0,
                "latestMarketId": 6815617,
                "orientation": "DIRECT_UP_VERIFIED",
                "eventToLocalCheckMs": 31.5,
            }
        )
    )

    assert snapshot is not None
    assert snapshot["decisionSnapshotVersion"] == DECISION_SNAPSHOT_VERSION
    assert snapshot["signalAsk"] == 0.4422
    assert snapshot["decisionLatestAsk"] == 0.51
    assert snapshot["decisionAskDelta"] == pytest.approx(0.0678)
    assert snapshot["decisionCaptureStatus"] == "AVAILABLE"
    assert snapshot["latestLocalBookAgeMs"] == 82.0
    assert snapshot["eventToDecisionSnapshotMs"] == 31.5


def test_safe_payload_preserves_full_snapshot_allow_list():
    install_decision_snapshot_diagnostics()
    encoded = live_trading._safe_payload(
        {
            "decisionSnapshotVersion": DECISION_SNAPSHOT_VERSION,
            "decisionSnapshotStage": "PRE_LEDGER_EARLY_BLOCK",
            "signalPrice": 0.4422,
            "signalAsk": 0.4422,
            "decisionLatestAsk": 0.51,
            "decisionAskDelta": 0.0678,
            "latestLocalBookAgeMs": 82.0,
            "privateExchangePayload": {"must": "not leak"},
        }
    )
    payload = json.loads(encoded)

    assert payload["decisionSnapshotVersion"] == DECISION_SNAPSHOT_VERSION
    assert payload["signalAsk"] == 0.4422
    assert payload["decisionLatestAsk"] == 0.51
    assert payload["decisionAskDelta"] == 0.0678
    assert "privateExchangePayload" not in payload


def test_install_is_idempotent():
    before = LiveM0WEngine._record_blocked_signal
    install_decision_snapshot_diagnostics()
    assert LiveM0WEngine._record_blocked_signal is before
