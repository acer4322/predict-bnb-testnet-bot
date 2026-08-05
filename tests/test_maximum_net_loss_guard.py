from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from predict_bot import live_trading


def _timestamp_after(reset_at: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(str(reset_at).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=seconds)).isoformat()


def _insert_settlement(
    ledger: live_trading.LiveLedger,
    *,
    strategy: str,
    market_id: int,
    pnl_usdt: float,
    updated_at: str,
) -> int:
    order_id = ledger.record_signal(
        topic_id=1,
        market_id=market_id,
        side="UP",
        token_id=f"token-{market_id}",
        signal_price=0.5,
        account_type="SPOT",
        signal_at=updated_at,
        strategy=strategy,
        max_stake_usdt=max(1.0, abs(pnl_usdt)),
        requested_amount_wei=str(int(max(1.0, abs(pnl_usdt)) * 10**18)),
    )
    assert order_id is not None
    cost = max(1.0, abs(pnl_usdt))
    payout = max(0.0, cost + pnl_usdt)
    result = "WIN" if pnl_usdt >= 0 else "LOSS"
    with ledger.lock:
        ledger.db.execute(
            """INSERT INTO live_strategy_settlements(
                   order_local_id, market_id, position_status, result,
                   cost_usdt, payout_usdt, pnl_usdt, roi_pct,
                   settled_at, updated_at
               ) VALUES (?, ?, 'SETTLED', ?, ?, ?, ?, ?, ?, ?)""",
            (
                order_id,
                market_id,
                result,
                cost,
                payout,
                pnl_usdt,
                pnl_usdt / cost * 100.0,
                updated_at,
                updated_at,
            ),
        )
        ledger.db.commit()
    return order_id


def _engine(path: Path) -> live_trading.LiveM0WEngine:
    return live_trading.LiveM0WEngine(
        api_key=None,
        api_secret=None,
        configured_enabled=False,
        credential_source="test",
        current_market=lambda: None,
        db_path=path,
        auto_redeem_enabled=False,
    )


def test_wins_offset_losses_and_manual_reset(tmp_path: Path) -> None:
    ledger = live_trading.LiveLedger(tmp_path / "live.db")
    configured = ledger.configure_maximum_net_loss_guard(True, 5.0)
    reset_at = str(configured["resetAt"])

    _insert_settlement(
        ledger,
        strategy="R_MICROPRICE",
        market_id=2001,
        pnl_usdt=-4.0,
        updated_at=_timestamp_after(reset_at, 1),
    )
    _insert_settlement(
        ledger,
        strategy="R_CALIBRATED_VALUE",
        market_id=2002,
        pnl_usdt=2.0,
        updated_at=_timestamp_after(reset_at, 2),
    )
    state = ledger.maximum_net_loss_guard_state()
    assert state["netPnlUsdt"] == -2.0
    assert state["currentLossUsdt"] == 2.0
    assert state["remainingBeforePauseUsdt"] == 3.0
    assert state["settledTrades"] == 2

    _insert_settlement(
        ledger,
        strategy="R_MICROPRICE_CONFIRM:CONFIRM_ADD_1",
        market_id=2003,
        pnl_usdt=3.0,
        updated_at=_timestamp_after(reset_at, 3),
    )
    recovered = ledger.maximum_net_loss_guard_state()
    assert recovered["netPnlUsdt"] == 1.0
    assert recovered["currentLossUsdt"] == 0.0

    reset = ledger.reset_maximum_net_loss_guard()
    assert reset["netPnlUsdt"] == 0.0
    assert reset["currentLossUsdt"] == 0.0
    assert reset["settledTrades"] == 0
    assert reset["enabled"] is True


def test_first_enable_starts_from_zero_instead_of_counting_history(
    tmp_path: Path,
) -> None:
    ledger = live_trading.LiveLedger(tmp_path / "live.db")
    initial = ledger.maximum_net_loss_guard_state()
    _insert_settlement(
        ledger,
        strategy="R_MICROPRICE",
        market_id=2101,
        pnl_usdt=-8.0,
        updated_at=_timestamp_after(str(initial["resetAt"]), 1),
    )
    assert ledger.maximum_net_loss_guard_state()["currentLossUsdt"] == 8.0

    enabled = ledger.configure_maximum_net_loss_guard(True, 5.0)
    assert enabled["enabled"] is True
    assert enabled["netPnlUsdt"] == 0.0
    assert enabled["currentLossUsdt"] == 0.0
    assert enabled["settledTrades"] == 0


def test_custom_live_rule_endpoint_fields_are_persistent(tmp_path: Path) -> None:
    engine = _engine(tmp_path / "live.db")
    state = engine.update_live_rules(
        {
            "maximumNetLossGuardEnabled": True,
            "maximumNetLossUsdt": 7.5,
        }
    )
    guard = state["maximumNetLossGuard"]
    assert guard["enabled"] is True
    assert guard["maximumLossUsdt"] == 7.5

    reloaded = engine.ledger.maximum_net_loss_guard_state()
    assert reloaded["enabled"] is True
    assert reloaded["maximumLossUsdt"] == 7.5


def test_official_settlement_at_limit_immediately_pauses_runtime(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path / "live.db")
    configured = engine.ledger.configure_maximum_net_loss_guard(True, 2.5)
    engine.runtime_enabled = True
    engine.armed = True
    engine.status = "LIVE"
    engine.ledger.set_runtime_enabled(True)

    reset_at = str(configured["resetAt"])
    market_id = 2201
    order_id = engine.ledger.record_signal(
        topic_id=1,
        market_id=market_id,
        side="UP",
        token_id="pair-token",
        signal_price=0.5,
        account_type="SPOT",
        signal_at=_timestamp_after(reset_at, 1),
        strategy="PAIR_ARB_010:UP",
        max_stake_usdt=3.0,
        requested_amount_wei=str(3 * 10**18),
    )
    assert order_id is not None
    order = {
        "id": order_id,
        "strategy": "PAIR_ARB_010:UP",
        "market_id": market_id,
        "quote_amount_in_wei": str(3 * 10**18),
        "filled_usdt_amount": 3.0,
        "filled_share_qty": 6.0,
        "quote_amount_out_wei": str(6 * 10**18),
        "market_provider_fee": 0.0,
        "network_fee": 0.0,
    }
    position = {
        "positionStatus": "SETTLED",
        "isWinner": False,
        "endDate": int(
            datetime.now(timezone.utc).timestamp() * 1000
        ),
    }

    settlement = engine.ledger.record_strategy_settlement(order, position)
    assert settlement is not None
    guard = engine.ledger.maximum_net_loss_guard_state()
    assert guard["tripped"] is True
    assert guard["currentLossUsdt"] == 3.0
    assert engine.runtime_enabled is False
    assert engine.armed is False
    assert engine.status == "PAUSED_MAXIMUM_NET_LOSS"
    assert engine.ledger.runtime_override() is False

    with engine.ledger.lock:
        event = engine.ledger.db.execute(
            """SELECT event_type FROM live_events
                WHERE event_type='MAXIMUM_NET_LOSS_PAUSED'
                ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    assert event is not None


def test_manual_resume_acknowledgement_only_retrips_after_loss_worsens(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path / "live.db")
    configured = engine.ledger.configure_maximum_net_loss_guard(True, 5.0)
    reset_at = str(configured["resetAt"])
    _insert_settlement(
        engine.ledger,
        strategy="R_MICROPRICE",
        market_id=2301,
        pnl_usdt=-6.0,
        updated_at=_timestamp_after(reset_at, 1),
    )
    engine.runtime_enabled = True
    engine.armed = True
    engine.evaluate_maximum_net_loss_guard("test")
    assert engine.ledger.maximum_net_loss_guard_state()["tripped"] is True

    acknowledged = engine.ledger.acknowledge_maximum_net_loss_resume()
    assert acknowledged["tripped"] is False
    assert acknowledged["acknowledgedLossUsdt"] == 6.0

    unchanged = engine.evaluate_maximum_net_loss_guard("manual resume")
    assert unchanged["tripped"] is False

    _insert_settlement(
        engine.ledger,
        strategy="R_MICROPRICE",
        market_id=2302,
        pnl_usdt=-1.0,
        updated_at=_timestamp_after(reset_at, 2),
    )
    engine.runtime_enabled = True
    engine.armed = True
    worsened = engine.evaluate_maximum_net_loss_guard("next loss")
    assert worsened["tripped"] is True
    assert worsened["currentLossUsdt"] == 7.0
