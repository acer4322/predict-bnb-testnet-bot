from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ASSET", "ETH")
os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ENABLED", "false")

from predict_bot.wallet_maker_clone_predict_direct_v8 import (  # noqa: E402
    PredictDirectV8WalletMakerCloneEngine,
    derive_outcome_books,
    normalize_predict_order_update,
    parse_exact_5m_window,
)


def _close(engine: PredictDirectV8WalletMakerCloneEngine) -> None:
    engine.http.close()
    if engine.direct_client is not None:
        engine.direct_client.close()
    with engine.db_lock:
        engine.db.commit()
        engine.db.close()


def test_parse_exact_eth_5m_title_to_utc() -> None:
    reference_ms = int(datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    window = parse_exact_5m_window(
        "Ethereum Up or Down - August 11, 7:15PM-7:20PM ET",
        reference_ms,
    )
    assert window is not None
    assert datetime.fromtimestamp(window.start_ms / 1000, tz=timezone.utc) == datetime(
        2026, 8, 11, 23, 15, tzinfo=timezone.utc
    )
    assert window.end_ms - window.start_ms == 300_000


def test_parse_rejects_non_five_minute_title() -> None:
    assert (
        parse_exact_5m_window("Ethereum Up or Down - August 11, 7:15PM-7:30PM ET")
        is None
    )


def test_derive_outcome_books_when_up_is_yes() -> None:
    raw = {
        "success": True,
        "data": {
            "bids": [[0.49, 10.0], [0.48, 5.0]],
            "asks": [[0.51, 7.0], [0.52, 8.0]],
        },
    }
    books = derive_outcome_books(raw, yes_outcome="UP", precision=2)
    assert books["UP"]["bestBid"] == pytest.approx(0.49)
    assert books["UP"]["bestAsk"] == pytest.approx(0.51)
    assert books["DOWN"]["bestBid"] == pytest.approx(0.49)
    assert books["DOWN"]["bestAsk"] == pytest.approx(0.51)
    assert books["DOWN"]["bestBidSize"] == pytest.approx(7.0)
    assert books["DOWN"]["bestAskSize"] == pytest.approx(10.0)


def test_derive_outcome_books_when_down_is_yes() -> None:
    raw = {"data": {"bids": [[0.60, 2.0]], "asks": [[0.62, 3.0]]}}
    books = derive_outcome_books(raw, yes_outcome="DOWN", precision=2)
    assert books["DOWN"]["bestBid"] == pytest.approx(0.60)
    assert books["DOWN"]["bestAsk"] == pytest.approx(0.62)
    assert books["UP"]["bestBid"] == pytest.approx(0.38)
    assert books["UP"]["bestAsk"] == pytest.approx(0.40)


def test_normalize_predict_partial_fill_uses_signed_cost_fraction() -> None:
    remote = {
        "status": "OPEN",
        "amount": "2000000000000000000",
        "amountFilled": "1000000000000000000",
        "order": {
            "makerAmount": "1000000000000000000",
            "takerAmount": "2000000000000000000",
        },
    }
    update = normalize_predict_order_update(remote)
    assert update["state"] == "PARTIAL_FILL"
    assert update["maker_usdt_amount"] == pytest.approx(1.0)
    assert update["maker_share_qty"] == pytest.approx(2.0)
    assert update["filled_share_qty"] == pytest.approx(1.0)
    assert update["filled_usdt_amount"] == pytest.approx(0.5)
    assert update["fill_percentage"] == pytest.approx(0.5)


def test_predict_direct_engine_keeps_v8_limits_and_separate_cancel_columns(tmp_path) -> None:
    engine = PredictDirectV8WalletMakerCloneEngine(tmp_path / "predict-direct-v8.db")
    try:
        settings = engine._settings()
        assert engine.VERSION == "WALLET_MAKER_CLONE_PREDICT_DIRECT_V8_BOUNDED_PAIRED_RISK"
        assert settings["maximumEntryCount"] >= 1
        assert settings["maximumLossUsdt"] > 0
        assert settings["venueMinimumOrderUsdt"] is None
        columns = {
            str(row[1])
            for row in engine.db.execute("PRAGMA table_info(wallet_maker_clone_orders)").fetchall()
        }
        assert "direct_removed_from_book" in columns
        assert "direct_cancel_onchain_confirmed" in columns
        assert "direct_reward_earning_rate" in columns
    finally:
        _close(engine)


def test_predict_direct_fails_closed_without_signing_credentials(tmp_path, monkeypatch) -> None:
    for name in (
        "PREDICT_FUN_API_KEY",
        "PREDICT_FUN_PRIVATE_KEY",
        "PREDICT_FUN_PRIVY_PRIVATE_KEY",
        "PREDICT_FUN_ACCOUNT_ADDRESS",
        "PREDICT_FUN_JWT",
    ):
        monkeypatch.delenv(name, raising=False)
    engine = PredictDirectV8WalletMakerCloneEngine(tmp_path / "predict-direct-no-creds.db")
    try:
        assert engine._ensure_client() is False
        assert engine.status == "CONFIG_REQUIRED"
        assert "PREDICT_FUN_API_KEY" in str(engine.last_error)
    finally:
        _close(engine)
