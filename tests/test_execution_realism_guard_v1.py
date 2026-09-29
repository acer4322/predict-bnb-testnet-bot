from __future__ import annotations

import os

import pytest

from tools import execution_realism_guard_v1 as guard


def test_dream_fill_is_blocked_for_performance_grade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(guard.DIAGNOSTIC_ENV, raising=False)
    with pytest.raises(RuntimeError, match="optimistic execution is forbidden"):
        guard.require_performance_grade_execution(
            test_name="unit",
            maker_mode="QUEUECLEAR_PASS",
            taker_mode="INSTANT",
            closed_loop=True,
            uses_predict_execution_tape=False,
            uses_hftbacktest=False,
        )


def test_dream_fill_requires_explicit_diagnostic_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(guard.DIAGNOSTIC_ENV, "1")
    meta = guard.require_performance_grade_execution(
        test_name="unit",
        maker_mode="QUEUECLEAR_PASS",
        taker_mode="INSTANT",
        closed_loop=False,
        uses_predict_execution_tape=False,
        uses_hftbacktest=False,
    )
    assert meta["diagnosticOnly"] is True
    assert meta["executionEvidenceLabel"] == "DIAGNOSTIC_ONLY_OPTIMISTIC_EXECUTION"


def test_hft_predict_tape_closed_loop_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(guard.DIAGNOSTIC_ENV, raising=False)
    meta = guard.require_performance_grade_execution(
        test_name="unit",
        maker_mode="HFTBACKTEST_EXECUTION_TAPE_V1",
        taker_mode="HFT",
        closed_loop=True,
        uses_predict_execution_tape=True,
        uses_hftbacktest=True,
    )
    assert meta["diagnosticOnly"] is False
    assert meta["executionEvidenceLabel"] == "HFTBACKTEST_PREDICT_TAPE_CLOSED_LOOP"


def test_legacy_runner_is_blocked_without_diagnostic_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(guard.DIAGNOSTIC_ENV, raising=False)
    with pytest.raises(RuntimeError, match="dream-fill proxy"):
        guard.require_legacy_optimistic_diagnostic(test_name="legacy", fill_proxy="QUEUECLEAR_PASS")


def test_legacy_runner_can_only_be_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(guard.DIAGNOSTIC_ENV, "1")
    meta = guard.require_legacy_optimistic_diagnostic(test_name="legacy", fill_proxy="QUEUECLEAR_PASS")
    assert meta == {
        "executionEvidenceLabel": "DIAGNOSTIC_ONLY_OPTIMISTIC_EXECUTION",
        "fillProxy": "QUEUECLEAR_PASS",
        "diagnosticOnly": True,
    }
