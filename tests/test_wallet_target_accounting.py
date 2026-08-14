from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.predict_bot.predict_wallet_shadow_observer_v4_3 import WalletShadowObserver
from src.predict_bot.predict_wallet_target_accounting import account_target_market


def _raw(wallet: str, *, role: str, order_hash: str, fee_type: str, fee: float) -> str:
    participant = {
        "signer": wallet,
        "hash": order_hash,
        "fee": {"type": fee_type, "amount": str(int(fee * 10**18))},
    }
    return json.dumps({"taker": participant, "makers": [participant] if role == "MAKER" else []})


def test_target_accounting_deducts_share_fee_and_splits_roles() -> None:
    wallet = "0xabc"
    events = [
        {"role": "MAKER", "side": "UP", "quote_type": "BID", "order_hash": "m", "shares": 18, "price": 0.4,
         "raw_json": _raw(wallet, role="MAKER", order_hash="m", fee_type="SHARES", fee=0)},
        {"role": "TAKER", "side": "DOWN", "quote_type": "BID", "order_hash": "t", "shares": 10, "price": 0.2,
         "raw_json": _raw(wallet, role="TAKER", order_hash="t", fee_type="SHARES", fee=0.2)},
    ]
    result = account_target_market(events, winner="UP", wallet=wallet)
    assert result["buyNotionalUsdt"] == pytest.approx(9.2)
    assert result["payoutUsdt"] == pytest.approx(18)
    assert result["netPnlUsdt"] == pytest.approx(8.8)
    assert result["maker"]["netPnlUsdt"] == pytest.approx(10.8)
    assert result["taker"]["netPnlUsdt"] == pytest.approx(-2.0)
    assert result["down"]["netShares"] == pytest.approx(9.8)
    assert result["capitalConvictionSide"] == "UP"


def test_observer_stores_target_official_result_and_live_context(tmp_path: Path) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing-simulation.db")
    try:
        wallet = observer.wallet
        leg = {
            "legId": "leg-1", "marketId": 123, "role": "TAKER", "side": "UP", "quoteType": "BID",
            "orderHash": "order-1", "eventMs": 1_786_569_895_000, "price": 0.4, "shares": 5.0,
        }
        raw = json.loads(_raw(wallet, role="TAKER", order_hash="order-1", fee_type="SHARES", fee=0.1))
        raw["market"] = {"id": 123, "boostEndsAt": "2026-08-12T21:25:00.000Z"}
        observer.last_predict_book = {"secondsLeft": 5.0, "upBid": 0.39, "upAsk": 0.4, "downBid": 0.6, "downAsk": 0.61}
        observer.last_core = {"side": "UP", "source": "TEST"}
        observer._persist_target_leg(leg, raw)
        observer._store_target_market_result(123, {"title": "test"}, "UP", resolved_at_ms=1_786_569_901_000)

        context = observer.db.execute("SELECT * FROM wallet_shadow_target_event_context WHERE leg_id='leg-1'").fetchone()
        result = observer.db.execute("SELECT * FROM wallet_shadow_target_market_results WHERE market_id=123").fetchone()
        assert context is not None
        assert context["scheduled_seconds_left"] == pytest.approx(5.0)
        assert result is not None
        assert result["status"] == "WIN"
        assert result["payout_usdt"] == pytest.approx(4.9)
        assert result["net_pnl_usdt"] == pytest.approx(2.9)
    finally:
        observer.stop()
