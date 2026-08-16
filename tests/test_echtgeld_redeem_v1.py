from __future__ import annotations

import json
import sqlite3

from predict_bot.core import ApiTransportError
from predict_bot.echtgeld_redeem_v1 import EchtgeldRedeemManager


class FakeBinanceClient:
    def __init__(self, *, now_ms: int = 1_000_000) -> None:
        self.now_ms = now_ms
        self.position_rows: list[dict] = []
        self.batch_calls: list[dict] = []
        self.batch_response: dict = {"txHash": "0xredeem", "status": "PENDING"}
        self.batch_error: Exception | None = None
        self.redeem_status_response: dict = {"status": "PENDING"}
        self.redeem_status_calls: list[tuple[str, str]] = []
        self.closed = False

    def wallets(self) -> dict:
        return {"wallets": [{"walletAddress": "0xwallet", "walletId": "wallet-1"}]}

    def positions(self, wallet_address: str, *, tab: str, offset: int, limit: int) -> dict:
        assert wallet_address == "0xwallet"
        assert tab == "PENDING_CLAIM"
        return {"positions": list(self.position_rows), "hasMore": False}

    def batch_redeem(
        self,
        *,
        wallet_address: str,
        wallet_id: str,
        token_ids: list[str],
        chain_id: str = "56",
    ) -> dict:
        self.batch_calls.append(
            {
                "walletAddress": wallet_address,
                "walletId": wallet_id,
                "tokenIds": list(token_ids),
                "chainId": chain_id,
            }
        )
        if self.batch_error is not None:
            raise self.batch_error
        return dict(self.batch_response)

    def redeem_status(self, wallet_address: str, tx_hash: str) -> dict:
        self.redeem_status_calls.append((wallet_address, tx_hash))
        return dict(self.redeem_status_response)

    def server_timestamp_ms(self) -> int:
        return self.now_ms

    def close(self) -> None:
        self.closed = True


def _create_order_ledger(db_path, *, market_id: int = 111, side: str = "UP") -> None:
    with sqlite3.connect(db_path) as db:
        db.execute(
            """CREATE TABLE engine_orders (
                   venue TEXT NOT NULL,
                   status TEXT NOT NULL,
                   side TEXT NOT NULL,
                   result_json TEXT NOT NULL
               )"""
        )
        db.execute(
            "INSERT INTO engine_orders(venue,status,side,result_json) VALUES (?,?,?,?)",
            (
                "binance",
                "SUBMITTED",
                side,
                json.dumps({"venueMarketId": market_id, "side": side}),
            ),
        )


def _claimable(token_id: str, *, market_id: int, end_date_ms: int, side: str = "UP") -> dict:
    return {
        "tokenId": token_id,
        "marketId": market_id,
        "marketTopicId": market_id + 1000,
        "chainId": "56",
        "outcomeName": side,
        "positionStatus": "PENDING_CLAIM",
        "canClaim": True,
        "shares": 1.25,
        "value": 1.25,
        "endDate": end_date_ms,
    }


def _manager(tmp_path, monkeypatch, fake: FakeBinanceClient, *, venue: str = "binance"):
    monkeypatch.setenv("BINANCE_API_KEY", "test-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "test-secret")
    db_path = tmp_path / "echtgeld.db"
    return db_path, EchtgeldRedeemManager(
        db_path,
        venue_getter=lambda: venue,
        start_worker=False,
        enabled=True,
        client_factory=lambda _key, _secret: fake,
        wallet_selector=lambda _payload: {
            "walletAddress": "0xwallet",
            "walletId": "wallet-1",
        },
    )


def test_only_tracked_venue_claimable_market_is_admitted_and_60s_fence_applies(tmp_path, monkeypatch):
    fake = FakeBinanceClient(now_ms=1_000_000)
    db_path = tmp_path / "echtgeld.db"
    _create_order_ledger(db_path, market_id=111)
    fake.position_rows = [
        _claimable("tracked", market_id=111, end_date_ms=950_000),
        _claimable("other-wallet-position", market_id=222, end_date_ms=900_000),
        {
            **_claimable("venue-not-claimable", market_id=111, end_date_ms=900_000),
            "canClaim": False,
        },
    ]
    monkeypatch.setenv("BINANCE_API_KEY", "test-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "test-secret")
    manager = EchtgeldRedeemManager(
        db_path,
        venue_getter=lambda: "binance",
        start_worker=False,
        enabled=True,
        client_factory=lambda _key, _secret: fake,
        wallet_selector=lambda _payload: {"walletAddress": "0xwallet", "walletId": "wallet-1"},
    )

    assert manager.run_cycle() is True
    assert fake.batch_calls == []  # 950000 + 60000 > 1000000
    assert [row["token_id"] for row in manager.recent()] == ["tracked"]

    fake.now_ms = 1_020_000
    assert manager.run_cycle() is True
    assert [call["tokenIds"] for call in fake.batch_calls] == [["tracked"]]
    assert manager.recent()[0]["status"] == "SUBMITTED"
    manager.close()


def test_submit_transport_error_becomes_ambiguous_and_is_never_blindly_retried(tmp_path, monkeypatch):
    fake = FakeBinanceClient(now_ms=1_000_000)
    db_path = tmp_path / "echtgeld.db"
    _create_order_ledger(db_path, market_id=111)
    fake.position_rows = [_claimable("tracked", market_id=111, end_date_ms=900_000)]
    fake.batch_error = ApiTransportError("wire result is unknowable")
    monkeypatch.setenv("BINANCE_API_KEY", "test-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "test-secret")
    manager = EchtgeldRedeemManager(
        db_path,
        venue_getter=lambda: "binance",
        start_worker=False,
        enabled=True,
        client_factory=lambda _key, _secret: fake,
        wallet_selector=lambda _payload: {"walletAddress": "0xwallet", "walletId": "wallet-1"},
    )

    assert manager.run_cycle() is True
    assert len(fake.batch_calls) == 1
    assert manager.recent()[0]["status"] == "AMBIGUOUS"

    assert manager.run_cycle() is True
    assert len(fake.batch_calls) == 1
    assert manager.summary()["retryAmbiguousRedeem"] is False
    manager.close()


def test_submitted_redeem_reconciles_to_redeemed_instead_of_resubmitting(tmp_path, monkeypatch):
    fake = FakeBinanceClient(now_ms=1_000_000)
    db_path = tmp_path / "echtgeld.db"
    _create_order_ledger(db_path, market_id=111)
    fake.position_rows = [_claimable("tracked", market_id=111, end_date_ms=900_000)]
    monkeypatch.setenv("BINANCE_API_KEY", "test-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "test-secret")
    manager = EchtgeldRedeemManager(
        db_path,
        venue_getter=lambda: "binance",
        start_worker=False,
        enabled=True,
        client_factory=lambda _key, _secret: fake,
        wallet_selector=lambda _payload: {"walletAddress": "0xwallet", "walletId": "wallet-1"},
    )

    assert manager.run_cycle() is True
    assert len(fake.batch_calls) == 1
    assert manager.recent()[0]["tx_hash"] == "0xredeem"

    fake.redeem_status_response = {"status": "SUCCESS"}
    assert manager.run_cycle() is True
    assert len(fake.batch_calls) == 1
    assert fake.redeem_status_calls == [("0xwallet", "0xredeem")]
    assert manager.recent()[0]["status"] == "REDEEMED"
    assert manager.summary()["redeemedTotal"] == 1
    manager.close()


def test_restart_quarantines_prevenue_attempt_instead_of_replaying(tmp_path, monkeypatch):
    fake = FakeBinanceClient(now_ms=1_000_000)
    db_path = tmp_path / "echtgeld.db"
    _create_order_ledger(db_path, market_id=111)
    monkeypatch.setenv("BINANCE_API_KEY", "test-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "test-secret")

    first = EchtgeldRedeemManager(
        db_path,
        venue_getter=lambda: "binance",
        start_worker=False,
        enabled=True,
        client_factory=lambda _key, _secret: fake,
        wallet_selector=lambda _payload: {"walletAddress": "0xwallet", "walletId": "wallet-1"},
    )
    first.close()
    with sqlite3.connect(db_path) as db:
        db.execute(
            """INSERT INTO engine_redeems(
                   token_id,venue,venue_market_id,chain_id,end_date_ms,eligible_at_ms,status,
                   attempt_count,discovered_at_ms,last_checked_at_ms,response_json
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("crash-token", "binance", 111, "56", 900_000, 960_000, "ATTEMPTING", 1, 900_000, 900_000, "{}"),
        )

    restarted = EchtgeldRedeemManager(
        db_path,
        venue_getter=lambda: "binance",
        start_worker=False,
        enabled=True,
        client_factory=lambda _key, _secret: fake,
        wallet_selector=lambda _payload: {"walletAddress": "0xwallet", "walletId": "wallet-1"},
    )
    row = restarted.recent()[0]
    assert row["status"] == "AMBIGUOUS"
    assert "automatic replay is forbidden" in row["last_error"]
    restarted.close()


def test_non_binance_venue_does_not_touch_binance_claim_api(tmp_path, monkeypatch):
    fake = FakeBinanceClient(now_ms=1_000_000)
    db_path = tmp_path / "echtgeld.db"
    _create_order_ledger(db_path, market_id=111)
    fake.position_rows = [_claimable("tracked", market_id=111, end_date_ms=900_000)]
    monkeypatch.setenv("BINANCE_API_KEY", "test-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "test-secret")
    manager = EchtgeldRedeemManager(
        db_path,
        venue_getter=lambda: "predict",
        start_worker=False,
        enabled=True,
        client_factory=lambda _key, _secret: fake,
        wallet_selector=lambda _payload: {"walletAddress": "0xwallet", "walletId": "wallet-1"},
    )

    assert manager.run_cycle() is False
    assert manager.summary()["status"] == "UNSUPPORTED_VENUE"
    assert fake.batch_calls == []
    assert manager.recent() == []
    manager.close()
