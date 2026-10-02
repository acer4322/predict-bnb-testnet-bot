from __future__ import annotations

from predict_bot.xpair_canary_autopilot_server_v11 import (
    incident_automation_snapshot,
)


def safety(
    *,
    locked: bool = True,
    lock_kind: str = "INCIDENT",
    status: str = "PLACEMENT_INCOMPLETE_MANUAL_RECONCILE",
    btc_id: str = "btc-order",
    eth_id: str = "",
    btc_status: str = "SUBMITTED",
    eth_status: str = "REJECTED",
) -> dict:
    return {
        "locked": locked,
        "lockKind": lock_kind,
        "status": status,
        "btcOrderId": btc_id,
        "ethOrderId": eth_id,
        "btcStatus": btc_status,
        "ethStatus": eth_status,
    }


def test_explicit_rejected_missing_leg_is_eligible_for_worker() -> None:
    result = incident_automation_snapshot(safety())
    assert result["state"] == "AUTO_RECOVERY_WAITING_KNOWN_ORDER"
    assert result["automaticActionAllowed"] is True
    assert result["knownOrderSymbol"] == "BTC"
    assert result["missingOrderSymbol"] == "ETH"


def test_ambiguous_missing_leg_remains_hard_locked() -> None:
    result = incident_automation_snapshot(safety(eth_status="AMBIGUOUS"))
    assert result["state"] == "BLOCKED_NONDETERMINISTIC_MISSING_LEG"
    assert result["automaticActionAllowed"] is False
    assert "may have reached the exchange" in result["reason"]


def test_no_order_ids_requires_manual_reconciliation() -> None:
    result = incident_automation_snapshot(safety(btc_id="", eth_id=""))
    assert result["state"] == "BLOCKED_NO_ORDER_IDS"
    assert result["automaticActionAllowed"] is False


def test_two_order_ids_in_incomplete_state_are_not_compensated_blindly() -> None:
    result = incident_automation_snapshot(safety(eth_id="eth-order"))
    assert result["state"] == "BLOCKED_TWO_ORDER_IDS_IN_INCOMPLETE_STATE"
    assert result["automaticActionAllowed"] is False


def test_clear_state_reports_idle() -> None:
    result = incident_automation_snapshot(
        safety(locked=False, lock_kind="CLEAR", status="CLEAR")
    )
    assert result["state"] == "IDLE"
    assert result["automaticActionAllowed"] is False
