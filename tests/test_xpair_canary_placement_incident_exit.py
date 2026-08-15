from __future__ import annotations

from predict_bot.xpair_canary_autopilot_server_v8 import (
    classify_explicit_incomplete_placement,
)


def safety(*, btc_id: str = "btc-order", eth_id: str = "", btc: str = "SUBMITTED", eth: str = "REJECTED"):
    return {
        "btcOrderId": btc_id,
        "ethOrderId": eth_id,
        "btcStatus": btc,
        "ethStatus": eth,
    }


def test_explicit_missing_leg_rejection_recovers_filled_known_leg() -> None:
    action, status, reason = classify_explicit_incomplete_placement(
        safety=safety(),
        known_order={
            "status": "FILLED",
            "filledShareQty": "4",
            "filledUsdtAmount": "1.5",
        },
    )
    assert action == "RECOVER_OR_UNWIND"
    assert status == "ONE_SIDED_PLACEMENT_REJECTED"
    assert "ETH placement was explicitly rejected" in reason


def test_ambiguous_missing_leg_is_never_compensated_automatically() -> None:
    action, status, _ = classify_explicit_incomplete_placement(
        safety=safety(eth="AMBIGUOUS"),
        known_order={
            "status": "FILLED",
            "filledShareQty": "4",
            "filledUsdtAmount": "1.5",
        },
    )
    assert action == "IGNORE"
    assert status == "MISSING_LEG_NOT_DETERMINISTIC"


def test_known_order_without_fill_resolves_no_exposure() -> None:
    action, status, _ = classify_explicit_incomplete_placement(
        safety=safety(),
        known_order={"status": "EXPIRED", "filledShareQty": "0"},
    )
    assert action == "RESOLVE_NO_EXPOSURE"
    assert status == "NO_EXPOSURE_PLACEMENT_INCOMPLETE"


def test_known_open_order_waits_instead_of_selling_early() -> None:
    action, status, _ = classify_explicit_incomplete_placement(
        safety=safety(),
        known_order={"status": "NEW", "filledShareQty": "0"},
    )
    assert action == "WAIT"
    assert status == "KNOWN_ORDER_NOT_TERMINAL"


def test_two_known_order_ids_are_not_handled_by_placement_incident_worker() -> None:
    action, status, _ = classify_explicit_incomplete_placement(
        safety=safety(eth_id="eth-order"),
        known_order={"status": "FILLED", "filledShareQty": "4"},
    )
    assert action == "IGNORE"
    assert status == "IDENTIFIER_COUNT_UNSAFE"
