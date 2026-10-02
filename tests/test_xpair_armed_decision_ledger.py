from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from predict_bot.xpair_btc_eth_canary import CanaryStore
from predict_bot.xpair_canary_autopilot_server_v2 import record_armed_decision
from predict_bot.xpair_canary_autopilot_server_v6 import (
    expose_armed_decision_messages,
)


def trials(first_status: str, second_status: str):
    return [
        {
            "variant": "BTC_UP_ETH_DOWN",
            "entry_status": first_status,
        },
        {
            "variant": "BTC_DOWN_ETH_UP",
            "entry_status": second_status,
        },
    ]


def test_armed_no_eligible_decision_is_persisted(tmp_path: Path) -> None:
    store = CanaryStore(tmp_path / "xpair.db")
    try:
        run_id = record_armed_decision(
            store,
            status="ARMED_NO_ELIGIBLE_VARIANT",
            btc_market_id=101,
            eth_market_id=202,
            pair_budget=Decimal("3.00"),
            selection="BTC_DOWN_ETH_UP",
            trials=trials("ELIGIBLE", "SKIPPED_PRICE"),
            message=(
                "selection=BTC_DOWN_ETH_UP; "
                "BTC_UP_ETH_DOWN=ELIGIBLE, BTC_DOWN_ETH_UP=SKIPPED_PRICE"
            ),
        )
        row = store.db.execute(
            "SELECT * FROM canary_runs WHERE id=?", (run_id,)
        ).fetchone()
        assert row is not None
        assert row["mode"] == "LIVE_ARMED_MONITOR"
        assert row["status"] == "ARMED_NO_ELIGIBLE_VARIANT"
        assert "BTC_DOWN_ETH_UP=SKIPPED_PRICE" in row["message"]
        details = json.loads(row["details_json"])
        assert details["armed"] is True
        assert details["selection"] == "BTC_DOWN_ETH_UP"
        assert details["trials"][1]["entry_status"] == "SKIPPED_PRICE"
    finally:
        store.close()


def test_same_market_updates_latest_armed_reason(tmp_path: Path) -> None:
    store = CanaryStore(tmp_path / "xpair.db")
    try:
        first_id = record_armed_decision(
            store,
            status="ARMED_NO_ELIGIBLE_VARIANT",
            btc_market_id=101,
            eth_market_id=202,
            pair_budget=Decimal("3.00"),
            selection="BTC_DOWN_ETH_UP",
            trials=trials("ELIGIBLE", "SKIPPED_PRICE"),
            message="first decision",
        )
        second_id = record_armed_decision(
            store,
            status="ARMED_NO_ELIGIBLE_VARIANT",
            btc_market_id=101,
            eth_market_id=202,
            pair_budget=Decimal("4.00"),
            selection="BTC_DOWN_ETH_UP",
            trials=trials("SKIPPED_PRICE", "SKIPPED_DEPTH"),
            message="second decision",
        )
        assert first_id == second_id
        assert store.db.execute("SELECT COUNT(*) FROM canary_runs").fetchone()[0] == 1
        row = store.db.execute(
            "SELECT pair_budget_usdt, message FROM canary_runs WHERE id=?",
            (first_id,),
        ).fetchone()
        assert row["pair_budget_usdt"] == 4.0
        assert row["message"] == "second decision"
    finally:
        store.close()


def test_armed_reason_is_exposed_in_dashboard_history() -> None:
    payload = {
        "recentRuns": [
            {
                "status": "ARMED_NO_ELIGIBLE_VARIANT",
                "message": (
                    "selection=BTC_DOWN_ETH_UP; "
                    "BTC_UP_ETH_DOWN=ELIGIBLE, BTC_DOWN_ETH_UP=SKIPPED_PRICE"
                ),
            }
        ]
    }
    decorated = expose_armed_decision_messages(payload)
    run = decorated["recentRuns"][0]
    assert run["canonical_status"] == "ARMED_NO_ELIGIBLE_VARIANT"
    assert "BTC_DOWN_ETH_UP=SKIPPED_PRICE" in run["status"]
