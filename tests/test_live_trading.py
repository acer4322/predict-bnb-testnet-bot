from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
import time

import pytest

import predict_bot.live_trading as live_trading
from predict_bot.core import ApiHttpError, ApiTransportError
from predict_bot.live_trading import (
    LIVE_M0W_AMOUNT_WEI,
    LIVE_RESEARCH_STRATEGIES,
    LiveLedger,
    LiveM0WEngine,
)


INDEPENDENT_LIVE_RESEARCH_STRATEGIES = tuple(
    strategy
    for strategy in LIVE_RESEARCH_STRATEGIES
    if strategy != "R_FUTURES_LEAD_REVERSE"
)


class FakeTradingClient:
    def __init__(self, *_args):
        self.quote_calls = []
        self.place_calls = []
        self.history_orders = []
        self.history_error = None
        self.place_error = None
        self.quote_amount_in = str(LIVE_M0W_AMOUNT_WEI)
        self.quote_amount_ins = []
        self.quote_amount_in_by_token = {}
        self.quote_amount_out_by_token = {}
        self.quote_average_price = 0.40
        self.quote_average_prices = []
        self.quote_average_by_token = {}
        self.orderbooks_by_token = {}
        self.server_time_ms = 1_000_000
        self.pending_claim_positions = []
        self.redeem_calls = []
        self.redeem_status_calls = []
        self.position_calls = []
        self.position_payloads = {}
        self.redeem_error = None
        self.batch_redeem_response = {
            "batchId": "batch-1",
            "results": [
                {
                    "requestId": "request-1",
                    "txHash": "0xabc123",
                    "status": "PENDING",
                    "error": None,
                }
            ],
        }
        self.redeem_status_response = {
            "txHash": "0xabc123",
            "status": "SUCCESS",
        }

    def wallets(self):
        return {
            "wallets": [
                {"walletAddress": "0x1234567890abcdef", "walletId": "wallet-1"}
            ]
        }

    def quota_status(self):
        return {"dailyLimit": "100", "remainingDailyLimit": "99"}

    def payment_option_balances(self):
        return {
            "items": [
                {
                    "accountType": "CeDeFi",
                    "availableBalanceDisplay": "5.00",
                    "enabled": True,
                }
            ]
        }

    def portfolio(self, _wallet_address, **_filters):
        return {
            "activePositionsCount": 0,
            "totalRealizedPnl": "0",
            "totalUnrealizedPnl": "0",
            "totalPnl": "0",
            "totalCostBasis": "0",
            "totalCurrentValue": "0",
        }

    def get_quote(self, **kwargs):
        self.quote_calls.append(kwargs)
        quote_side = str(kwargs.get("side") or "BUY").upper()
        average_price = (
            self.quote_average_by_token.get(kwargs["token_id"])
            if kwargs["token_id"] in self.quote_average_by_token
            else (
                self.quote_average_prices.pop(0)
                if self.quote_average_prices
                else self.quote_average_price
            )
        )
        amount_in = (
            kwargs["amount_in_wei"]
            if quote_side == "SELL"
            else (
                self.quote_amount_in_by_token.get(kwargs["token_id"])
                if kwargs["token_id"] in self.quote_amount_in_by_token
                else (
                    self.quote_amount_ins.pop(0)
                    if self.quote_amount_ins else self.quote_amount_in
                )
            )
        )
        return {
            "quoteId": "quote-secret",
            "tokenId": kwargs["token_id"],
            "side": quote_side,
            "orderType": str(kwargs.get("order_type") or "LIMIT").upper(),
            "amountIn": amount_in,
            "amountOut": self.quote_amount_out_by_token.get(
                kwargs["token_id"], "2500000000000000000"
            ),
            "averagePrice": average_price,
            "expireAt": self.server_time_ms + 1_000_000,
        }

    def orderbook(self, _market_id, _token_id):
        if str(_token_id) in self.orderbooks_by_token:
            return self.orderbooks_by_token[str(_token_id)]
        return {"asks": [{"price": "0.40", "size": "10"}], "bids": []}

    def server_timestamp_ms(self):
        return self.server_time_ms

    def place_limit_order(self, **kwargs):
        self.place_calls.append(kwargs)
        if self.place_error:
            raise self.place_error
        return {"orderId": "54124"}

    def place_market_order(self, **kwargs):
        self.place_calls.append({**kwargs, "order_type": "MARKET"})
        if self.place_error:
            raise self.place_error
        return {"orderId": "market-54124"}

    def order_history(self, _wallet_address, *, limit=100):
        if self.history_error:
            raise self.history_error
        return {"orders": self.history_orders[:limit]}

    def positions(self, _wallet_address, *, tab, offset=0, limit=100):
        assert tab == "PENDING_CLAIM"
        page = self.pending_claim_positions[offset : offset + limit]
        return {
            "positions": page,
            "offset": offset,
            "limit": limit,
            "hasMore": offset + len(page) < len(self.pending_claim_positions),
            "summary": {
                "pendingClaimCount": len(self.pending_claim_positions),
                "totalClaimableAmount": str(
                    sum(float(item.get("value") or 0) for item in self.pending_claim_positions)
                ),
            },
        }

    def position_by_token(self, _wallet_address, token_id):
        self.position_calls.append(token_id)
        if str(token_id) in self.position_payloads:
            return {"position": dict(self.position_payloads[str(token_id)])}
        match = next(
            (
                item
                for item in self.pending_claim_positions
                if str(item.get("tokenId")) == str(token_id)
            ),
            None,
        )
        return {"position": dict(match or {})}

    def batch_redeem(self, **kwargs):
        self.redeem_calls.append(kwargs)
        if self.redeem_error:
            raise self.redeem_error
        return self.batch_redeem_response

    def redeem_status(self, wallet_address, tx_hash):
        self.redeem_status_calls.append((wallet_address, tx_hash))
        return self.redeem_status_response


def market_reference():
    return {
        "topic_id": 101,
        "market_id": 202,
        "start_ms": 1_800_000_000_000,
        "end_ms": 1_800_000_300_000,
        "fee_bps": 200,
        "start_price": 100.0,
        "up_token_id": "up-token",
        "down_token_id": "down-token",
    }


def signal(**overrides):
    payload = {
        "strategy": "M0W",
        "topic_id": 101,
        "market_id": 202,
        "side": "UP",
        "entry_price": 0.40,
        "seconds_left": 100.0,
        "signal_timestamp": "2026-07-19T00:00:00+00:00",
        "market_data_integrity_ok": True,
        "drawdown_control_start_price": 100.0,
        "drawdown_control_spot_price": 99.95,
        "m0w_gate": {
            "version": "M0W_GATE_V2_ADJACENT_OFFICIAL_WIN",
            "previous_market_id": 201,
            "previous_m0_status": "SETTLED_WIN",
            "previous_m0_pnl": 0.50,
            "previous_settlement_status": "OFFICIAL",
            "previous_market_is_adjacent": True,
            "current_market_start_ms": 1_800_000_000_000,
            "previous_market_start_ms": 1_799_999_700_000,
            "previous_market_end_ms": 1_800_000_000_000,
        },
    }
    payload.update(overrides)
    return payload


def f1_observer_gate(**overrides):
    payload = {
        "allowed": True,
        "status": "ALLOW",
        "profile": "F1",
        "paperOnly": False,
        "liveOrdersAffected": True,
        "reason": "F1 條件成立",
        "historicalState": "UNCERTAIN",
        "historicalSampleCount": 12,
        "currentMarketId": 202,
        "currentRangeScore": 1,
        "currentEffectiveCrossovers": 2,
        "currentBothSidesTouched": False,
        "currentTrendVeto": False,
        "currentPhase": "EARLY_0_60S",
        "currentShortEr": 0.30,
        "currentMedianEr60s": None,
        "dataQualityStatus": "READY",
        "minSettledSamples": 6,
    }
    payload.update(overrides)
    return payload


def claimable_position(**overrides):
    payload = {
        "marketId": 303,
        "marketTopicId": 302,
        "outcome": "Up",
        "tokenId": "winning-token",
        "chainId": "56",
        "shares": "1.92",
        "value": "1.92",
        "positionStatus": "PENDING_CLAIM",
        "canClaim": True,
        "endDate": 900_000,
    }
    payload.update(overrides)
    return payload


def m0_hourly_performance(
    *, win_rate_pct=60.0, win_then_loss_rate_pct=40.0
):
    hours = [
        {
            "hour": hour,
            "label": f"{hour:02d}:00–{hour:02d}:59",
            "settledTrades": 0,
            "wins": 0,
            "losses": 0,
            "winRatePct": None,
            "winThenLossCount": 0,
            "winThenLossOpportunities": 0,
            "winThenLossRatePct": None,
        }
        for hour in range(24)
    ]
    hours[8].update(
        {
            "settledTrades": 10,
            "wins": 6,
            "losses": 4,
            "winRatePct": win_rate_pct,
            "winThenLossCount": 2,
            "winThenLossOpportunities": 5,
            "winThenLossRatePct": win_then_loss_rate_pct,
        }
    )
    return {"timezone": "Asia/Taipei", "hours": hours}


def verified_prediction_book(**overrides):
    payload = {
        "market_id": 202,
        "orientation": "DIRECT_UP_VERIFIED",
        "received_monotonic_ns": time.monotonic_ns(),
        "book_age_ms": 0.0,
        "up_bid": 0.39,
        "up_ask": 0.40,
        "up_bid_size": 10.0,
        "up_ask_size": 10.0,
        "down_bid": 0.39,
        "down_ask": 0.40,
        "down_bid_size": 10.0,
        "down_ask_size": 10.0,
    }
    payload.update(overrides)
    return payload


def pair_prediction_book():
    return verified_prediction_book(
        up_bid=0.20,
        up_ask=0.21,
        down_bid=0.74,
        down_ask=0.75,
    )


def engine(
    tmp_path: Path,
    client: FakeTradingClient,
    *,
    hourly_provider=None,
    **engine_kwargs,
):
    engine_kwargs.setdefault(
        "current_verified_prediction_book", verified_prediction_book
    )
    result = LiveM0WEngine(
        api_key="key",
        api_secret="secret",
        configured_enabled=True,
        credential_source="TEST",
        current_market=market_reference,
        db_path=tmp_path / "live.db",
        m0_hourly_performance=hourly_provider,
        client_factory=lambda *_args: client,
        **engine_kwargs,
    )
    result._preflight()
    assert result.state()["armed"] is True
    return result


def force_legacy_m0w_rules_for_test(live: LiveM0WEngine) -> None:
    """Keep legacy M0W execution tests isolated from allowlist experiments."""
    with live.lock:
        live.live_rules = {
            **live.live_rules,
            "strategy": "M0W",
            "strategies": ["M0W"],
            "maxStakeUsdt": 1.0,
            "strategyStakesUsdt": [1.0],
            "strategyObserverEnabled": [False],
            "strategyObserverVersions": ["F1"],
            "strategyDrawdownControlEnabled": [False],
            "strategyLossCooldownEnabled": [False],
        }


def accepted_signal_kwargs(**overrides):
    values = {
        "topic_id": 101,
        "market_id": 202,
        "side": "UP",
        "token_id": "up-token",
        "signal_price": 0.40,
        "account_type": "SPOT",
        "signal_at": "2026-08-01T00:00:00+00:00",
        "strategy": "M0W",
        "max_stake_usdt": 1.0,
        "requested_amount_wei": str(LIVE_M0W_AMOUNT_WEI),
        "reliability_context": {},
        "event_message": "M0W UP accepted",
    }
    values.update(overrides)
    return values


def test_live_ledger_uses_wal_and_reports_durability_settings(tmp_path: Path):
    ledger = LiveLedger(tmp_path / "live.db")

    settings = ledger.sqlite_settings()

    assert settings["sqliteJournalMode"] == "WAL"
    assert settings["sqliteBusyTimeoutMs"] == 5000
    assert settings["sqliteSynchronous"] != "OFF"
    assert settings["liveDbPath"].endswith("live.db")
    assert settings["sqlitePathWarning"] is None


def test_accepted_signal_order_and_event_commit_together(tmp_path: Path):
    ledger = LiveLedger(tmp_path / "live.db")
    statements = []
    ledger.db.set_trace_callback(statements.append)

    local_id = ledger.record_accepted_signal(**accepted_signal_kwargs())

    assert local_id is not None
    order = ledger.db.execute(
        "SELECT status FROM live_orders WHERE id=?", (local_id,)
    ).fetchone()
    event = ledger.db.execute(
        "SELECT event_type FROM live_events WHERE market_id=202"
    ).fetchone()
    assert order["status"] == "QUOTE_REQUESTING"
    assert event["event_type"] == "SIGNAL_ACCEPTED"
    assert sum(statement == "COMMIT" for statement in statements) == 1


def test_accepted_signal_event_failure_rolls_back_order(tmp_path: Path):
    ledger = LiveLedger(tmp_path / "live.db")
    ledger.db.executescript(
        """
        CREATE TRIGGER fail_signal_accepted
        BEFORE INSERT ON live_events
        WHEN NEW.event_type='SIGNAL_ACCEPTED'
        BEGIN
            SELECT RAISE(ABORT, 'forced event failure');
        END;
        """
    )
    ledger.db.commit()

    with pytest.raises(live_trading.sqlite3.IntegrityError):
        ledger.record_accepted_signal(**accepted_signal_kwargs())

    assert ledger.db.execute("SELECT COUNT(*) FROM live_orders").fetchone()[0] == 0
    assert ledger.db.execute("SELECT COUNT(*) FROM live_events").fetchone()[0] == 0


def test_accepted_signal_keeps_pair_side_ledger_keys_unique(tmp_path: Path):
    ledger = LiveLedger(tmp_path / "live.db")

    up_id = ledger.record_accepted_signal(
        **accepted_signal_kwargs(strategy="PAIR_ARB_010:UP", side="UP")
    )
    down_id = ledger.record_accepted_signal(
        **accepted_signal_kwargs(
            strategy="PAIR_ARB_010:DOWN", side="DOWN", token_id="down-token"
        )
    )
    duplicate = ledger.record_accepted_signal(
        **accepted_signal_kwargs(strategy="PAIR_ARB_010:UP", side="UP")
    )

    assert up_id is not None
    assert down_id is not None
    assert duplicate is None


def test_attempt_summary_counts_outcomes_and_latency_percentiles(tmp_path: Path):
    ledger = LiveLedger(tmp_path / "live.db")
    outcomes = [
        "SUBMITTED",
        "BLOCKED_STALE_PREDICTION_BOOK",
        "QUOTE_REJECTED",
        "PLACEMENT_AMBIGUOUS",
    ]
    for index, outcome in enumerate(outcomes, start=1):
        local_id = ledger.record_accepted_signal(
            **accepted_signal_kwargs(
                market_id=200 + index,
                event_message=f"attempt {index}",
            )
        )
        ledger.record_attempt_telemetry(
            local_id,
            {
                "finalOutcome": outcome,
                "eventToPlaceResponseMs": index * 10.0,
                "queueMs": index * 1.0,
                "preQuoteMs": index * 2.0,
                "quoteNetworkMs": index * 3.0,
                "quoteToPlaceMs": index * 4.0,
                "placeNetworkMs": index * 5.0,
                "quoteId": "must-not-persist",
                "signature": "must-not-persist",
            },
        )

    summary = ledger.attempt_summary()

    assert summary["sampleSize"] == 4
    assert summary["outcomes"]["submitted"] == 1
    assert summary["outcomes"]["blockedStaleBook"] == 1
    assert summary["outcomes"]["quoteRejected"] == 1
    assert summary["outcomes"]["placementAmbiguous"] == 1
    latency = summary["latency"]["eventToPlaceResponseMs"]
    assert latency == {"p50": 25.0, "p90": 37.0, "p95": 38.5, "max": 40.0}
    raw = ledger.db.execute(
        "SELECT telemetry_json FROM live_attempt_telemetry LIMIT 1"
    ).fetchone()[0]
    assert "quoteId" not in raw
    assert "signature" not in raw


def test_place_attempted_is_durable_before_network_place(tmp_path: Path):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    force_legacy_m0w_rules_for_test(live)
    observed_statuses = []

    def inspect_durable_barrier(**kwargs):
        with live_trading.sqlite3.connect(tmp_path / "live.db") as reader:
            observed_statuses.append(
                reader.execute(
                    "SELECT status FROM live_orders WHERE market_id=202"
                ).fetchone()[0]
            )
        return {"orderId": "barrier-verified"}

    client.place_limit_order = inspect_durable_barrier

    live.process_signal(signal())

    assert observed_statuses == ["PLACE_ATTEMPTED"]
    assert live.state()["orders"][0]["status"] == "SUBMITTED"


def test_live_engine_stop_closes_trading_client(tmp_path: Path):
    client = FakeTradingClient()
    closed = []
    client.close = lambda: closed.append(True)
    live = engine(tmp_path, client)

    live.stop()

    assert closed == [True]


def test_live_m0w_uses_limit_gtc_with_immutable_one_usdt_cap(tmp_path: Path):
    client = FakeTradingClient()
    live = engine(tmp_path, client)

    live.process_signal(signal())

    assert len(client.quote_calls) == 1
    assert client.quote_calls[0]["amount_in_wei"] == str(LIVE_M0W_AMOUNT_WEI)
    assert client.quote_calls[0]["price_limit"] == "0.4"
    assert len(client.place_calls) == 1
    assert client.place_calls[0]["price_limit"] == "0.4"
    state = live.state()
    assert state["maxStakeUsdt"] == 1.0
    assert state["orderType"] == "LIMIT"
    assert state["timeInForce"] == "GTC"
    assert state["sasStatus"] == "VERIFIED"
    assert state["orders"][0]["status"] == "SUBMITTED"
    assert state["orders"][0]["order_id"] == "54124"


def test_quote_preflight_verifies_permission_without_placing(tmp_path: Path):
    client = FakeTradingClient()
    live = engine(tmp_path, client)

    live._verify_quote_access()

    assert live.state()["quoteAccess"] == "VERIFIED"
    assert len(client.quote_calls) == 1
    assert client.place_calls == []
    assert live.state()["summary"]["signals"] == 0


def test_live_m0w_never_retries_same_market(tmp_path: Path):
    client = FakeTradingClient()
    live = engine(tmp_path, client)

    live.process_signal(signal())
    live.process_signal(signal())

    assert len(client.quote_calls) == 1
    assert len(client.place_calls) == 1
    assert live.state()["summary"]["signals"] == 1


def test_order_latency_reports_queue_quote_and_placement_segments(tmp_path: Path):
    client = FakeTradingClient()
    original_quote = client.get_quote
    original_place = client.place_limit_order

    def delayed_quote(**kwargs):
        time.sleep(0.01)
        return original_quote(**kwargs)

    def delayed_place(**kwargs):
        time.sleep(0.01)
        return original_place(**kwargs)

    client.get_quote = delayed_quote
    client.place_limit_order = delayed_place
    live = engine(tmp_path, client)
    force_legacy_m0w_rules_for_test(live)
    now_ns = time.monotonic_ns()
    payload = signal(
        market_event_received_monotonic_ns=now_ns - 20_000_000,
        strategy_decision_started_monotonic_ns=now_ns - 15_000_000,
        strategy_store_started_monotonic_ns=now_ns - 14_000_000,
        strategy_store_finished_monotonic_ns=now_ns - 10_000_000,
        live_candidate_created_monotonic_ns=now_ns - 7_000_000,
        _live_enqueued_monotonic_ns=now_ns - 5_000_000,
    )

    live.process_signal(payload)

    latency = live.state()["orderLatency"]
    assert latency["outcome"] == "SUBMITTED"
    assert latency["marketId"] == 202
    assert latency["queueMs"] >= 4
    assert latency["quoteNetworkMs"] >= 9
    assert latency["placeNetworkMs"] >= 9
    assert latency["totalMs"] >= 20
    assert latency["marketEventToDecisionStartMs"] == pytest.approx(5.0)
    assert latency["decisionAndStoreMs"] == pytest.approx(5.0)
    assert latency["storeMs"] == pytest.approx(4.0)
    assert latency["candidateToLiveQueueMs"] == pytest.approx(2.0)
    assert latency["marketEventToLiveQueueMs"] == pytest.approx(15.0)
    assert latency["marketEventToQuoteStartMs"] >= 20.0
    assert latency["marketEventToPlaceStartMs"] >= 29.0
    assert latency["eventToPlaceResponseMs"] >= 39.0
    assert latency["preLedgerMs"] is not None
    assert latency["acceptedLedgerMs"] is not None


def test_order_latency_is_backward_compatible_without_causal_timestamps():
    latency = LiveM0WEngine._order_latency_payload(
        {
            "market_id": 202,
            "selected_strategy": "M0W",
            "side": "UP",
            "live_enqueued_monotonic": 1.0,
            "processing_started_monotonic": 1.1,
            "quote_started_monotonic": 1.2,
            "quote_finished_monotonic": 1.3,
            "quote_network_seconds": 0.05,
        },
        outcome="SUBMITTED",
        placement_started_monotonic=1.4,
        placement_finished_monotonic=1.5,
    )

    assert latency["totalMs"] == pytest.approx(500.0)
    for field in (
        "marketEventToDecisionStartMs",
        "decisionAndStoreMs",
        "storeMs",
        "candidateToLiveQueueMs",
        "marketEventToLiveQueueMs",
        "marketEventToQuoteStartMs",
        "marketEventToPlaceStartMs",
        "eventToPlaceResponseMs",
        "preLedgerMs",
        "acceptedLedgerMs",
    ):
        assert latency[field] is None


@pytest.mark.parametrize(
    ("book", "expected_status", "expected_error_kind"),
    [
        (
            verified_prediction_book(market_id=999),
            "BLOCKED_PREDICTION_MARKET_MISMATCH",
            "LOCAL_MARKET_MISMATCH",
        ),
        (
            verified_prediction_book(orientation="UNVERIFIED"),
            "BLOCKED_PREDICTION_ORIENTATION_UNVERIFIED",
            "LOCAL_UNVERIFIED_BOOK",
        ),
        (
            verified_prediction_book(
                received_monotonic_ns=time.monotonic_ns() - 3_000_000_000
            ),
            "BLOCKED_STALE_PREDICTION_BOOK",
            "LOCAL_STALE_BOOK",
        ),
    ],
)
def test_latest_prediction_book_gate_blocks_before_binance_quote(
    tmp_path: Path,
    book: dict,
    expected_status: str,
    expected_error_kind: str,
):
    client = FakeTradingClient()
    live = engine(
        tmp_path,
        client,
        current_verified_prediction_book=lambda: dict(book),
    )
    force_legacy_m0w_rules_for_test(live)

    live.process_signal(signal())

    assert client.quote_calls == []
    assert client.place_calls == []
    order = live.state()["orders"][0]
    assert order["status"] == expected_status
    assert order["error_kind"] == expected_error_kind
    assert live.state()["orderLatency"]["outcome"] == expected_status


def test_local_price_moved_gate_blocks_before_quote(tmp_path: Path):
    client = FakeTradingClient()
    live = engine(
        tmp_path,
        client,
        current_verified_prediction_book=lambda: verified_prediction_book(
            up_ask=0.51
        ),
    )
    force_legacy_m0w_rules_for_test(live)

    live.process_signal(signal(entry_price=0.40))

    assert client.quote_calls == []
    order = live.state()["orders"][0]
    assert order["status"] == "BLOCKED_LOCAL_PRICE_MOVED"
    assert order["error_kind"] == "LOCAL_PRICE_MOVED"
    check = live.state()["lastLocalPriceCheck"]
    assert check["latestLocalAsk"] == pytest.approx(0.51)
    assert check["maximumExecutionPrice"] == pytest.approx(0.50)
    assert live.state()["attemptSummary"]["outcomes"][
        "blockedLocalPriceMoved"
    ] == 1


@pytest.mark.parametrize(
    ("latest_ask", "expected_limit"),
    [(0.44, "0.44"), (0.35, "0.4")],
)
def test_initial_quote_uses_latest_verified_ask_without_lowering_signal_limit(
    tmp_path: Path, latest_ask: float, expected_limit: str
):
    client = FakeTradingClient()
    live = engine(
        tmp_path,
        client,
        current_verified_prediction_book=lambda: verified_prediction_book(
            up_ask=latest_ask
        ),
    )
    force_legacy_m0w_rules_for_test(live)

    live.process_signal(signal(entry_price=0.40))

    assert client.quote_calls[0]["price_limit"] == expected_limit
    assert client.place_calls[0]["price_limit"] == expected_limit


def test_estimate_buy_vwap_covers_stake_without_mutating_levels():
    levels = [[0.20, 2.0], [0.40, 2.0]]
    original = [list(level) for level in levels]

    estimate = live_trading.estimate_buy_vwap(levels, "1.00")

    assert levels == original
    assert estimate["covered_stake"] == pytest.approx(1.0)
    assert estimate["capacity_ratio"] == pytest.approx(1.0)
    assert estimate["levels_consumed"] == 2
    assert estimate["estimated_vwap"] == pytest.approx(1.0 / 3.5)


def test_estimate_buy_vwap_reports_insufficient_depth():
    estimate = live_trading.estimate_buy_vwap([[0.25, 1.0]], 1.0)

    assert estimate == {
        "estimated_vwap": 0.25,
        "covered_stake": 0.25,
        "capacity_ratio": 0.25,
        "levels_consumed": 1,
    }


@pytest.mark.parametrize("ask_size", [None, 0.10])
def test_top_level_capacity_gate_fails_closed_before_quote(
    tmp_path: Path, ask_size,
):
    client = FakeTradingClient()
    live = engine(
        tmp_path,
        client,
        current_verified_prediction_book=lambda: verified_prediction_book(
            up_ask_size=ask_size
        ),
    )
    force_legacy_m0w_rules_for_test(live)

    live.process_signal(signal())

    assert client.quote_calls == []
    order = live.state()["orders"][0]
    assert order["status"] == "BLOCKED_INSUFFICIENT_TOP_LEVEL_CAPACITY"
    assert order["error_kind"] == "LOCAL_INSUFFICIENT_DEPTH"
    depth = live.state()["lastDepthCheck"]
    assert depth["topLevelCapacityRatio"] < 0.70


def test_down_capacity_gate_uses_down_ask_size(tmp_path: Path):
    client = FakeTradingClient()
    live = engine(
        tmp_path,
        client,
        current_verified_prediction_book=lambda: verified_prediction_book(
            up_ask_size=100.0,
            down_ask=0.40,
            down_ask_size=0.10,
        ),
    )
    force_legacy_m0w_rules_for_test(live)

    live.process_signal(signal(side="DOWN"))

    assert client.quote_calls == []
    assert live.state()["orders"][0]["status"] == (
        "BLOCKED_INSUFFICIENT_TOP_LEVEL_CAPACITY"
    )


def test_estimated_vwap_above_ceiling_blocks_before_quote(tmp_path: Path):
    client = FakeTradingClient()
    live = engine(
        tmp_path,
        client,
        current_verified_prediction_book=lambda: verified_prediction_book(
            up_ask=0.49,
            up_ask_size=1.43,
            up_asks=[[0.49, 1.43], [0.90, 10.0]],
        ),
    )
    force_legacy_m0w_rules_for_test(live)

    live.process_signal(signal(entry_price=0.40))

    assert client.quote_calls == []
    order = live.state()["orders"][0]
    assert order["status"] == "BLOCKED_ESTIMATED_VWAP_TOO_HIGH"
    assert order["error_kind"] == "LOCAL_ESTIMATED_VWAP_TOO_HIGH"
    depth = live.state()["lastDepthCheck"]
    assert depth["vwapAvailable"] is True
    assert depth["estimatedVwap"] > 0.50


def test_missing_multilevel_depth_is_reported_unavailable_not_invented(
    tmp_path: Path,
):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    force_legacy_m0w_rules_for_test(live)

    live.process_signal(signal())

    assert len(client.quote_calls) == 1
    depth = live.state()["lastDepthCheck"]
    assert depth["status"] == "PASS"
    assert depth["vwapAvailable"] is False
    assert depth["estimatedVwap"] is None


def test_requote_telemetry_records_two_attempts_and_individual_network_times(
    tmp_path: Path,
):
    client = FakeTradingClient()
    client.quote_average_prices = [0.41, 0.41]
    live = engine(tmp_path, client)
    force_legacy_m0w_rules_for_test(live)

    live.process_signal(signal(entry_price=0.40))

    attempt = live.state()["lastQuoteAttempt"]
    assert attempt["quoteAttempts"] == 2
    assert attempt["requoteTriggered"] is True
    assert attempt["firstQuoteAveragePrice"] == pytest.approx(0.41)
    assert attempt["secondQuoteAveragePrice"] == pytest.approx(0.41)
    assert attempt["firstQuoteNetworkMs"] >= 0
    assert attempt["secondQuoteNetworkMs"] >= 0
    assert attempt["finalQuoteAveragePrice"] == pytest.approx(0.41)
    assert attempt["finalOutcome"] == "SUBMITTED"


def test_hourly_guard_uses_cached_snapshot_on_order_hot_path(tmp_path: Path):
    client = FakeTradingClient()
    provider_calls = 0

    def provider():
        nonlocal provider_calls
        provider_calls += 1
        return m0_hourly_performance()

    live = engine(tmp_path, client, hourly_provider=provider)
    calls_after_initial_snapshot = provider_calls

    live.process_signal(signal())

    assert len(client.place_calls) == 1
    assert provider_calls == calls_after_initial_snapshot


def test_slow_maintenance_does_not_block_order_worker(tmp_path: Path):
    client = FakeTradingClient()
    maintenance_started = threading.Event()
    release_maintenance = threading.Event()
    live = LiveM0WEngine(
        api_key="key",
        api_secret="secret",
        configured_enabled=True,
        credential_source="TEST",
        current_market=market_reference,
        current_verified_prediction_book=verified_prediction_book,
        db_path=tmp_path / "live.db",
        client_factory=lambda *_args: client,
    )

    def slow_refresh():
        maintenance_started.set()
        release_maintenance.wait(timeout=2)

    live._refresh_account = slow_refresh
    live.start()
    try:
        assert maintenance_started.wait(timeout=1)
        live.submit_signal(signal())
        deadline = time.monotonic() + 1
        while not client.place_calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(client.place_calls) == 1
    finally:
        release_maintenance.set()
        live.stop()


def test_order_sync_requests_restart_after_consecutive_transport_errors(
    tmp_path: Path,
):
    client = FakeTradingClient()
    restart_reasons = []
    live = engine(
        tmp_path,
        client,
        order_sync_restart_threshold=3,
        restart_request=restart_reasons.append,
    )
    live.process_signal(signal())
    client.history_error = ApiTransportError(
        "Request failed for order/history: getaddrinfo failed"
    )

    live._sync_orders()
    live._sync_orders()

    assert restart_reasons == []
    assert live.state()["orderSyncWatchdog"] == {
        "consecutiveTransportErrors": 2,
        "restartThreshold": 3,
        "restartRequested": False,
    }

    live._sync_orders()
    live._sync_orders()

    state = live.state()
    assert state["status"] == "RESTARTING"
    assert state["armed"] is False
    assert state["orderSyncWatchdog"]["consecutiveTransportErrors"] == 4
    assert state["orderSyncWatchdog"]["restartRequested"] is True
    assert len(restart_reasons) == 1
    assert "3 consecutive times" in restart_reasons[0]
    assert any(
        event["event_type"] == "API_RESTART_REQUESTED"
        for event in state["events"]
    )


def test_successful_order_sync_resets_transport_error_count(tmp_path: Path):
    client = FakeTradingClient()
    restart_reasons = []
    live = engine(
        tmp_path,
        client,
        order_sync_restart_threshold=3,
        restart_request=restart_reasons.append,
    )
    live.process_signal(signal())
    transport_error = ApiTransportError(
        "Request failed for order/history: getaddrinfo failed"
    )
    client.history_error = transport_error
    live._sync_orders()
    live._sync_orders()

    client.history_error = None
    live._sync_orders()

    client.history_error = transport_error
    live._sync_orders()
    live._sync_orders()

    assert restart_reasons == []
    assert (
        live.state()["orderSyncWatchdog"]["consecutiveTransportErrors"] == 2
    )


@pytest.mark.parametrize(
    "gate_patch",
    [
        {"previous_m0_status": "SETTLED_LOSS", "previous_m0_pnl": -1.0},
        {"previous_settlement_status": "PENDING"},
        {"previous_market_is_adjacent": False},
        {"previous_market_end_ms": 1_799_999_700_000},
    ],
)
def test_live_m0w_fails_closed_when_adjacent_win_gate_is_invalid(
    tmp_path: Path, gate_patch: dict,
):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    payload = signal()
    payload["m0w_gate"].update(gate_patch)

    live.process_signal(payload)

    assert client.quote_calls == []
    assert client.place_calls == []
    order = live.state()["orders"][0]
    assert order["status"] == "BLOCKED_STRATEGY_GATE"


def test_quote_above_one_usdt_cap_is_rejected_before_place(tmp_path: Path):
    client = FakeTradingClient()
    client.quote_amount_in = str(LIVE_M0W_AMOUNT_WEI + 1)
    live = engine(tmp_path, client)
    force_legacy_m0w_rules_for_test(live)
    event_ns = time.monotonic_ns() - 10_000_000

    live.process_signal(signal(market_event_received_monotonic_ns=event_ns))

    assert client.place_calls == []
    order = live.state()["orders"][0]
    assert order["status"] == "REJECTED"
    assert "hard cap" in order["error_message"]
    latency = live.state()["orderLatency"]
    assert latency["outcome"] == "QUOTE_REJECTED"
    assert latency["eventToPlaceResponseMs"] >= 10.0
    assert latency["marketEventToPlaceStartMs"] is None


def test_quote_within_ten_cent_gap_is_refreshed_once_at_hard_cap(tmp_path: Path):
    client = FakeTradingClient()
    client.quote_average_prices = [0.41, 0.41]
    live = engine(tmp_path, client)

    live.process_signal(signal(entry_price=0.40))

    assert [call["price_limit"] for call in client.quote_calls] == ["0.4", "0.5"]
    assert len(client.place_calls) == 1
    assert client.place_calls[0]["price_limit"] == "0.5"
    order = live.state()["orders"][0]
    assert order["status"] == "SUBMITTED"
    assert order["quote_average_price"] == pytest.approx(0.41)
    assert any(
        event["event_type"] == "QUOTE_REPRICE_REQUESTED"
        for event in live.state()["events"]
    )


def test_quote_beyond_ten_cent_gap_is_rejected_with_diagnostics(tmp_path: Path):
    client = FakeTradingClient()
    client.quote_average_price = 0.51
    live = engine(tmp_path, client)

    live.process_signal(signal(entry_price=0.40))

    assert len(client.quote_calls) == 1
    assert client.place_calls == []
    order = live.state()["orders"][0]
    assert order["status"] == "REJECTED"
    assert order["quote_average_price"] == pytest.approx(0.51)
    assert "permitted execution limit 0.5" in order["error_message"]


@pytest.mark.parametrize("strategy", ["M01", "M01T180"])
def test_low_price_entry_ceiling_is_a_trigger_and_quote_can_use_ten_cent_gap(
    tmp_path: Path, strategy: str
):
    client = FakeTradingClient()
    client.quote_average_prices = [0.31, 0.31]
    live = engine(tmp_path, client)
    live.update_live_rules({"strategy": strategy})

    live.process_signal(
        signal(
            strategy=strategy,
            m0w_gate=None,
            entry_price=0.30,
            maximum_entry_price=0.30,
        )
    )

    assert [call["price_limit"] for call in client.quote_calls] == ["0.4"]
    assert len(client.place_calls) == 1
    assert client.place_calls[0]["price_limit"] == "0.4"
    order = live.state()["orders"][0]
    assert order["quote_average_price"] == pytest.approx(0.31)
    assert order["status"] == "SUBMITTED"


def test_refreshed_quote_still_cannot_cross_ten_cent_gap(tmp_path: Path):
    client = FakeTradingClient()
    client.quote_average_prices = [0.41]
    live = engine(tmp_path, client)
    live.update_live_rules({"strategy": "M01"})

    live.process_signal(
        signal(
            strategy="M01",
            m0w_gate=None,
            entry_price=0.30,
            maximum_entry_price=0.30,
        )
    )

    assert [call["price_limit"] for call in client.quote_calls] == ["0.4"]
    assert client.place_calls == []
    order = live.state()["orders"][0]
    assert order["status"] == "REJECTED"
    assert order["quote_average_price"] == pytest.approx(0.41)
    assert "permitted execution limit 0.4" in order["error_message"]


def test_f1_is_selectable_and_places_only_with_current_allowed_observer_gate(
    tmp_path: Path,
):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    state = live.update_live_rules({"strategy": "M01O_F1"})
    assert "M01O_F1" in state["supportedStrategies"]

    live.process_signal(
        signal(
            strategy="M01O_F1",
            m0w_gate=None,
            entry_price=0.30,
            maximum_entry_price=0.30,
            observer_gate_required=True,
            market_observer_gate=f1_observer_gate(),
        )
    )

    assert [call["price_limit"] for call in client.quote_calls] == ["0.4"]
    assert len(client.place_calls) == 1
    assert live.state()["orders"][0]["status"] == "SUBMITTED"


@pytest.mark.parametrize("strategy", INDEPENDENT_LIVE_RESEARCH_STRATEGIES)
def test_research_strategy_is_selectable_and_uses_exact_signal_price_limit(
    tmp_path: Path, strategy: str,
):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    state = live.update_live_rules({"strategy": strategy})
    assert strategy in state["supportedStrategies"]

    live.process_signal(
        signal(strategy=strategy, m0w_gate=None, entry_price=0.40)
    )

    assert [call["price_limit"] for call in client.quote_calls] == ["0.4"]
    assert len(client.place_calls) == 1
    assert live.state()["orders"][0]["status"] == "SUBMITTED"


@pytest.mark.parametrize("strategy", INDEPENDENT_LIVE_RESEARCH_STRATEGIES)
def test_research_strategy_allows_one_quote_refresh_within_five_cent_gap(
    tmp_path: Path, strategy: str,
):
    client = FakeTradingClient()
    client.quote_average_prices = [0.44, 0.44]
    live = engine(tmp_path, client)
    live.update_live_rules({"strategy": strategy})

    live.process_signal(
        signal(strategy=strategy, m0w_gate=None, entry_price=0.40)
    )

    assert [call["price_limit"] for call in client.quote_calls] == ["0.4", "0.45"]
    assert len(client.place_calls) == 1
    assert client.place_calls[0]["price_limit"] == "0.45"
    assert live.state()["orders"][0]["status"] == "SUBMITTED"


@pytest.mark.parametrize("strategy", INDEPENDENT_LIVE_RESEARCH_STRATEGIES)
def test_research_strategy_rejects_quote_beyond_five_cent_gap(
    tmp_path: Path, strategy: str,
):
    client = FakeTradingClient()
    client.quote_average_price = 0.46
    live = engine(tmp_path, client)
    live.update_live_rules({"strategy": strategy})

    live.process_signal(
        signal(strategy=strategy, m0w_gate=None, entry_price=0.40)
    )

    assert [call["price_limit"] for call in client.quote_calls] == ["0.4"]
    assert client.place_calls == []
    order = live.state()["orders"][0]
    assert order["status"] == "REJECTED"
    assert "permitted execution limit 0.45" in order["error_message"]


@pytest.mark.parametrize("entry_price", [0.39, 0.70])
def test_ofi_event_cum_blocks_signal_outside_040_to_069(
    tmp_path: Path, entry_price: float,
):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    live.update_live_rules({"strategy": "R_OFI_EVENT_CUM"})

    live.process_signal(
        signal(
            strategy="R_OFI_EVENT_CUM",
            m0w_gate=None,
            entry_price=entry_price,
            research_signal=3.0,
            model_edge=0.03,
        )
    )

    assert client.quote_calls == []
    assert client.place_calls == []
    assert live.state()["orders"][0]["status"] == "BLOCKED_RESEARCH_PRICE_RANGE"


def test_ofi_event_cum_rejects_quote_below_040(tmp_path: Path):
    client = FakeTradingClient()
    client.quote_average_price = 0.39
    live = engine(tmp_path, client)
    live.update_live_rules({"strategy": "R_OFI_EVENT_CUM"})

    live.process_signal(
        signal(strategy="R_OFI_EVENT_CUM", m0w_gate=None, entry_price=0.42)
    )

    assert len(client.quote_calls) == 1
    assert client.place_calls == []
    order = live.state()["orders"][0]
    assert order["status"] == "REJECTED"
    assert "between 0.40 and 0.69" in order["error_message"]


def test_ofi_event_cum_caps_requote_at_069(tmp_path: Path):
    client = FakeTradingClient()
    client.quote_average_price = 0.70
    live = engine(tmp_path, client)
    live.update_live_rules({"strategy": "R_OFI_EVENT_CUM"})

    live.process_signal(
        signal(strategy="R_OFI_EVENT_CUM", m0w_gate=None, entry_price=0.66)
    )

    assert [call["price_limit"] for call in client.quote_calls] == ["0.66"]
    assert client.place_calls == []
    order = live.state()["orders"][0]
    assert order["status"] == "REJECTED"
    assert "permitted execution limit 0.69" in order["error_message"]


@pytest.mark.parametrize("entry_price", [0.39, 0.70])
def test_calibrated_value_uses_model_edge_without_old_fixed_price_range(
    tmp_path: Path, entry_price: float,
):
    client = FakeTradingClient()
    client.quote_average_price = entry_price
    live = engine(tmp_path, client)
    live.update_live_rules({"strategy": "R_CALIBRATED_VALUE"})

    live.process_signal(
        signal(
            strategy="R_CALIBRATED_VALUE",
            m0w_gate=None,
            entry_price=entry_price,
            model_edge=0.03,
        )
    )

    assert [call["price_limit"] for call in client.quote_calls] == [
        format(max(entry_price, 0.40), "g")
    ]
    assert len(client.place_calls) == 1
    assert live.state()["orders"][0]["status"] == "SUBMITTED"


def test_calibrated_value_requote_is_not_capped_by_old_069_ceiling(
    tmp_path: Path,
):
    client = FakeTradingClient()
    client.quote_average_prices = [0.70, 0.70]
    live = engine(tmp_path, client)
    live.update_live_rules({"strategy": "R_CALIBRATED_VALUE"})

    live.process_signal(
        signal(
            strategy="R_CALIBRATED_VALUE",
            m0w_gate=None,
            entry_price=0.66,
            model_edge=0.03,
        )
    )

    assert [call["price_limit"] for call in client.quote_calls] == ["0.66", "0.71"]
    assert len(client.place_calls) == 1
    assert live.state()["orders"][0]["status"] == "SUBMITTED"


def test_two_selected_research_strategies_can_both_trade_the_same_market(
    tmp_path: Path,
):
    client = FakeTradingClient()
    client.quote_amount_ins = ["1000000000000000000"]
    live = engine(tmp_path, client)
    live.update_live_rules({
        "strategies": ["R_CALIBRATED_VALUE", "R_OFI_EVENT_CUM"],
        "strategyStakesUsdt": [1.0, 1.0],
    })

    live.process_signal(
        signal(
            strategy="R_CALIBRATED_VALUE",
            m0w_gate=None,
            entry_price=0.50,
            model_edge=0.03,
        )
    )
    live.process_signal(
        signal(
            strategy="R_OFI_EVENT_CUM",
            m0w_gate=None,
            entry_price=0.50,
            research_signal=4.0,
        )
    )

    assert len(client.quote_calls) == 2
    assert len(client.place_calls) == 2
    assert {order["strategy"] for order in live.state()["orders"]} == {
        "R_CALIBRATED_VALUE",
        "R_OFI_EVENT_CUM",
    }


def test_futures_lead_reverse_must_be_dependent_second_strategy(tmp_path: Path):
    client = FakeTradingClient()
    live = engine(tmp_path, client)

    with pytest.raises(ValueError, match="dependent.*strategy 2"):
        live.update_live_rules({"strategy": "R_FUTURES_LEAD_REVERSE"})
    with pytest.raises(ValueError, match="dependent.*strategy 2"):
        live.update_live_rules({
            "strategies": ["R_FUTURES_LEAD_REVERSE", "R_FUTURES_LEAD"],
            "strategyStakesUsdt": [1.0, 2.0],
        })


def _lead_hedge_signals() -> tuple[dict, dict]:
    lead = signal(
        strategy="R_FUTURES_LEAD",
        m0w_gate=None,
        side="UP",
        entry_price=0.40,
    )
    reverse = signal(
        strategy="R_FUTURES_LEAD_REVERSE",
        m0w_gate=None,
        side="DOWN",
        entry_price=0.60,
        dependent_live_pair=True,
        source_strategy="R_FUTURES_LEAD",
        source_side="UP",
    )
    return lead, reverse


def _configure_lead_hedge_quotes(client: FakeTradingClient) -> None:
    client.quote_average_by_token = {"up-token": 0.40, "down-token": 0.60}
    client.quote_amount_in_by_token = {
        "up-token": "2000000000000000000",
        "down-token": "1000000000000000000",
    }


def test_futures_lead_hedge_quotes_both_before_placing_with_2_to_1_budget(
    tmp_path: Path,
):
    client = FakeTradingClient()
    _configure_lead_hedge_quotes(client)
    live = engine(tmp_path, client)
    state = live.update_live_rules({
        "strategies": [
            "R_FUTURES_LEAD", "R_FUTURES_LEAD_REVERSE", "R_MICROPRICE",
        ],
        "strategyStakesUsdt": [2.0, 1.0, 0.5],
    })
    lead, reverse = _lead_hedge_signals()

    live.process_signal(lead)
    assert client.quote_calls == []
    assert client.place_calls == []
    live.process_signal(reverse)

    assert state["supportedStrategies"].count("R_FUTURES_LEAD_REVERSE") == 1
    assert {call["amount_in_wei"] for call in client.quote_calls} == {
        "1000000000000000000",
        "2000000000000000000",
    }
    assert len(client.place_calls) == 2
    assert {
        (order["strategy"], order["max_stake_usdt"], order["status"])
        for order in live.state()["orders"]
    } == {
        ("R_FUTURES_LEAD", 2.0, "SUBMITTED"),
        ("R_FUTURES_LEAD_REVERSE", 1.0, "SUBMITTED"),
    }


def test_futures_lead_hedge_quote_failure_places_neither_leg(tmp_path: Path):
    client = FakeTradingClient()
    _configure_lead_hedge_quotes(client)
    client.quote_average_by_token["down-token"] = 0.70
    live = engine(tmp_path, client)
    live.update_live_rules({
        "strategies": ["R_FUTURES_LEAD", "R_FUTURES_LEAD_REVERSE"],
        "strategyStakesUsdt": [2.0, 1.0],
    })
    lead, reverse = _lead_hedge_signals()

    live.process_signal(lead)
    live.process_signal(reverse)

    assert client.place_calls == []
    assert {order["status"] for order in live.state()["orders"]} == {"REJECTED"}


def test_futures_lead_hedge_observer_blocks_both_legs_before_any_quote(
    tmp_path: Path,
):
    client = FakeTradingClient()
    _configure_lead_hedge_quotes(client)
    live = engine(tmp_path, client)
    live.update_live_rules({
        "strategies": ["R_FUTURES_LEAD", "R_FUTURES_LEAD_REVERSE"],
        "strategyStakesUsdt": [2.0, 1.0],
        "futuresLeadObserverEnabled": True,
        "futuresLeadObserverVersion": "V6",
    })
    lead, reverse = _lead_hedge_signals()
    blocked_gate = f1_observer_gate(currentBothSidesTouched=True)
    for leg in (lead, reverse):
        leg["futures_lead_observer_gate_required"] = True
        leg["futures_lead_observer_gate"] = blocked_gate

    live.process_signal(lead)
    live.process_signal(reverse)

    assert client.quote_calls == []
    assert client.place_calls == []
    assert {order["status"] for order in live.state()["orders"]} == {
        "BLOCKED_FUTURES_LEAD_OBSERVER"
    }


def test_futures_lead_hedge_one_sided_placement_pauses_runtime(tmp_path: Path):
    client = FakeTradingClient()
    _configure_lead_hedge_quotes(client)

    def selective_place(**kwargs):
        client.place_calls.append(kwargs)
        if kwargs["price_limit"] == "0.6":
            raise ApiHttpError("reverse rejected", status_code=400, detail="{}")
        return {"orderId": "lead-order"}

    client.place_limit_order = selective_place
    live = engine(tmp_path, client)
    live.update_live_rules({
        "strategies": ["R_FUTURES_LEAD", "R_FUTURES_LEAD_REVERSE"],
        "strategyStakesUsdt": [2.0, 1.0],
    })
    lead, reverse = _lead_hedge_signals()

    live.process_signal(lead)
    live.process_signal(reverse)

    state = live.state()
    assert len(client.place_calls) == 2
    assert state["runtimeEnabled"] is False
    assert state["armed"] is False
    assert state["status"] == "PAUSED_FUTURES_LEAD_HEDGE_INCOMPLETE"


@pytest.mark.parametrize(
    "seconds_left, expected_places",
    [(30.001, 1), (30.0, 0), (29.999, 0)],
)
def test_f1_live_strictly_blocks_at_thirty_seconds_and_below(
    tmp_path: Path, seconds_left: float, expected_places: int,
):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    live.update_live_rules({"strategy": "M01O_F1"})

    live.process_signal(
        signal(
            strategy="M01O_F1",
            m0w_gate=None,
            entry_price=0.30,
            maximum_entry_price=0.30,
            seconds_left=seconds_left,
            observer_gate_required=True,
            market_observer_gate=f1_observer_gate(),
        )
    )

    assert len(client.place_calls) == expected_places
    if expected_places == 0:
        assert live.state()["orders"][0]["status"] == (
            "SKIPPED_F1_LAST_30_SECONDS"
        )


def test_f1_live_rechecks_current_exchange_time_before_quote(tmp_path: Path):
    client = FakeTradingClient()
    client.server_time_ms = market_reference()["end_ms"] - 30_000
    live = engine(tmp_path, client)
    live.update_live_rules({"strategy": "M01O_F1"})

    live.process_signal(
        signal(
            strategy="M01O_F1",
            m0w_gate=None,
            entry_price=0.30,
            maximum_entry_price=0.30,
            seconds_left=30.001,
            observer_gate_required=True,
            market_observer_gate=f1_observer_gate(),
        )
    )

    assert client.quote_calls == []
    assert live.state()["orders"][0]["status"] == (
        "SKIPPED_F1_LAST_30_SECONDS"
    )


@pytest.mark.parametrize(
    "gate",
    [
        None,
        f1_observer_gate(
            allowed=False,
            status="BLOCK",
            reason="當輪接近單邊",
            currentTrendVeto=True,
        ),
        f1_observer_gate(currentMarketId=999),
    ],
)
def test_f1_fails_closed_when_observer_gate_is_missing_blocked_or_stale(
    tmp_path: Path, gate,
):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    live.update_live_rules({"strategy": "M01O_F1"})

    live.process_signal(
        signal(
            strategy="M01O_F1",
            m0w_gate=None,
            entry_price=0.30,
            maximum_entry_price=0.30,
            observer_gate_required=True,
            market_observer_gate=gate,
        )
    )

    assert client.quote_calls == []
    assert client.place_calls == []
    assert live.state()["orders"][0]["status"] == "BLOCKED_OBSERVER_GATE"


def test_transport_failure_is_ambiguous_and_not_retried(tmp_path: Path):
    client = FakeTradingClient()
    client.place_error = ApiTransportError("connection closed after send")
    live = engine(tmp_path, client)
    force_legacy_m0w_rules_for_test(live)
    event_ns = time.monotonic_ns() - 10_000_000

    live.process_signal(signal(market_event_received_monotonic_ns=event_ns))
    live.process_signal(signal(market_event_received_monotonic_ns=event_ns))

    assert len(client.place_calls) == 1
    assert live.state()["orders"][0]["status"] == "AMBIGUOUS"
    latency = live.state()["orderLatency"]
    assert latency["outcome"] == "PLACEMENT_AMBIGUOUS"
    assert latency["marketEventToPlaceStartMs"] >= 10.0
    assert latency["eventToPlaceResponseMs"] >= (
        latency["marketEventToPlaceStartMs"]
    )
    assert live.state()["attemptSummary"]["outcomes"][
        "placementAmbiguous"
    ] == 1


def test_sas_rejection_disarms_executor(tmp_path: Path):
    client = FakeTradingClient()
    client.place_error = ApiHttpError(
        "HTTP 400: -31003 SAS authorization required",
        status_code=400,
        detail='{"code":-31003}',
    )
    live = engine(tmp_path, client)

    live.process_signal(signal())

    state = live.state()
    assert state["armed"] is False
    assert state["status"] == "BLOCKED_SAS_REQUIRED"
    assert state["sasStatus"] == "BLOCKED_SAS_REQUIRED"
    assert state["orders"][0]["status"] == "REJECTED"


def test_paused_executor_records_signal_without_network_calls(tmp_path: Path):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    live.set_runtime_enabled(False)

    live.process_signal(signal())

    assert client.quote_calls == []
    assert client.place_calls == []
    assert live.state()["orders"][0]["status"] == "SKIPPED_PAUSED"


@pytest.mark.parametrize(
    ("win_rate_pct", "win_then_loss_rate_pct", "expected_reason"),
    [
        (49.99, 40.0, "M0 勝率 49.99% < 50.00%"),
        (60.0, 50.01, "一勝一敗率 50.01% > 50.00%"),
    ],
)
def test_hourly_guard_blocks_before_quote_when_threshold_is_breached(
    tmp_path: Path,
    win_rate_pct: float,
    win_then_loss_rate_pct: float,
    expected_reason: str,
):
    client = FakeTradingClient()
    live = engine(
        tmp_path,
        client,
        hourly_provider=lambda: m0_hourly_performance(
            win_rate_pct=win_rate_pct,
            win_then_loss_rate_pct=win_then_loss_rate_pct,
        ),
    )

    live.process_signal(signal())

    assert client.quote_calls == []
    assert client.place_calls == []
    state = live.state()
    assert state["hourlyGuard"]["hour"] == 8
    assert state["hourlyGuard"]["blocked"] is True
    assert expected_reason in state["hourlyGuard"]["reasons"]
    assert state["orders"][0]["status"] == "SKIPPED_M0_HOURLY_GUARD"
    assert expected_reason in state["orders"][0]["error_message"]


def test_hourly_guard_state_is_ready_before_first_signal(tmp_path: Path):
    client = FakeTradingClient()
    live = engine(
        tmp_path,
        client,
        hourly_provider=lambda: m0_hourly_performance(
            win_rate_pct=49.0,
            win_then_loss_rate_pct=40.0,
        ),
    )

    state = live.state()

    assert state["hourlyGuard"]["status"] != "WAITING"
    assert state["hourlyGuard"]["evaluatedAt"]
    assert client.quote_calls == []


def test_hourly_guard_allows_exact_fifty_percent_boundaries(tmp_path: Path):
    client = FakeTradingClient()
    live = engine(
        tmp_path,
        client,
        hourly_provider=lambda: m0_hourly_performance(
            win_rate_pct=50.0,
            win_then_loss_rate_pct=50.0,
        ),
    )

    live.process_signal(signal())

    assert len(client.quote_calls) == 1
    assert len(client.place_calls) == 1
    assert live.state()["hourlyGuard"]["status"] == "ALLOW"


def test_hourly_guard_ignores_unavailable_win_then_loss_rate(tmp_path: Path):
    client = FakeTradingClient()
    live = engine(
        tmp_path,
        client,
        hourly_provider=lambda: m0_hourly_performance(
            win_rate_pct=55.0,
            win_then_loss_rate_pct=None,
        ),
    )

    live.process_signal(signal())

    assert len(client.place_calls) == 1
    assert live.state()["hourlyGuard"]["blocked"] is False


def test_hourly_guard_provider_error_fails_closed(tmp_path: Path):
    client = FakeTradingClient()

    def unavailable():
        raise RuntimeError("statistics database unavailable")

    live = engine(tmp_path, client, hourly_provider=unavailable)

    live.process_signal(signal())

    assert client.quote_calls == []
    assert client.place_calls == []
    state = live.state()
    assert state["hourlyGuard"]["status"] == "ERROR"
    assert state["hourlyGuard"]["blocked"] is True
    assert state["orders"][0]["status"] == "SKIPPED_M0_HOURLY_GUARD"


def test_live_rules_select_strategy_and_change_exact_order_cap(tmp_path: Path):
    client = FakeTradingClient()
    client.quote_amount_in = "2500000000000000000"
    live = engine(
        tmp_path,
        client,
        hourly_provider=lambda: m0_hourly_performance(),
    )

    state = live.update_live_rules(
        {
            "strategy": "M1",
            "maxStakeUsdt": 2.5,
            "minHourlyWinRatePct": 45,
            "maxHourlyWinThenLossRatePct": 65,
        }
    )
    live.process_signal(signal(strategy="M0W"))
    assert client.quote_calls == []

    live.process_signal(signal(strategy="M1", m0w_gate=None))

    assert len(client.quote_calls) == 1
    assert client.quote_calls[0]["amount_in_wei"] == "2500000000000000000"
    assert len(client.place_calls) == 1
    order = live.state()["orders"][0]
    assert order["strategy"] == "M1"
    assert order["max_stake_usdt"] == pytest.approx(2.5)
    assert order["requested_amount_wei"] == "2500000000000000000"
    assert state["rules"] == {
        "strategy": "M1",
        "strategies": ["M1"],
        "maxStakeUsdt": 2.5,
        "strategyStakesUsdt": [2.5],
        "minHourlyWinRatePct": 45.0,
        "maxHourlyWinThenLossRatePct": 65.0,
        "futuresLeadObserverEnabled": False,
        "futuresLeadObserverVersion": "F1",
        "strategyObserverEnabled": [False],
        "strategyObserverVersions": ["F1"],
        "strategyDrawdownControlEnabled": [False],
        "strategyLossCooldownEnabled": [False],
        "reliabilityGateTags": [],
    }


def test_three_selected_live_strategies_use_independent_caps(tmp_path: Path):
    client = FakeTradingClient()
    client.quote_amount_ins = [
        "750000000000000000",
        "1250000000000000000",
        "500000000000000000",
    ]
    live = engine(tmp_path, client)
    live.update_live_rules({
        "strategies": ["M1", "M2", "M3"],
        "strategyStakesUsdt": [0.75, 1.25, 0.5],
    })

    live.process_signal(signal(strategy="M1", m0w_gate=None))
    live.process_signal(signal(strategy="M2", m0w_gate=None))
    live.process_signal(signal(strategy="M3", m0w_gate=None))

    assert len(client.place_calls) == 3
    assert live.state()["maxSelectableStrategies"] == 3
    assert {order["strategy"] for order in live.state()["orders"]} == {
        "M1", "M2", "M3",
    }
    assert {
        order["strategy"]: order["max_stake_usdt"]
        for order in live.state()["orders"]
    } == {"M1": 0.75, "M2": 1.25, "M3": 0.5}


@pytest.mark.parametrize(
    ("tag", "strategy", "overrides"),
    [
        ("RC_LOW_ENTRY", "R_CALIBRATED_VALUE", {"entry_price": 0.40}),
        ("MP_LATE_WINDOW", "R_MICROPRICE", {"seconds_left": 179.0}),
        ("MP_FRESH_BOOK", "R_MICROPRICE", {"book_age_ms": 500.0}),
    ],
)
def test_reliability_candidate_gates_are_selectable_default_off_and_fail_closed(
    tmp_path: Path, tag: str, strategy: str, overrides: dict,
):
    default_client = FakeTradingClient()
    default_live = engine(tmp_path / "default", default_client)
    default_state = default_live.update_live_rules({"strategy": strategy})
    assert default_state["rules"]["reliabilityGateTags"] == []
    default_live.process_signal(signal(strategy=strategy, m0w_gate=None, **overrides))
    assert len(default_client.place_calls) == 1

    gated_client = FakeTradingClient()
    gated_live = engine(tmp_path / "gated", gated_client)
    gated_state = gated_live.update_live_rules({
        "strategy": strategy,
        "reliabilityGateTags": [tag],
    })
    assert gated_state["rules"]["reliabilityGateTags"] == [tag]
    gated_live.process_signal(signal(strategy=strategy, m0w_gate=None, **overrides))
    assert gated_client.quote_calls == []
    assert gated_client.place_calls == []
    assert gated_live.state()["orders"][0]["status"] == "BLOCKED_RELIABILITY_GATE"


def test_real_fills_are_copied_into_reliability_counterfactual_research(
    tmp_path: Path,
):
    ledger = LiveLedger(tmp_path / "live.db")
    for market_id, entry_price, winner in (
        (301, 0.30, True),
        (302, 0.50, False),
    ):
        local_id = ledger.record_signal(
            topic_id=101,
            market_id=market_id,
            side="UP",
            token_id=f"token-{market_id}",
            signal_price=entry_price,
            account_type="SPOT",
            signal_at="2026-07-31T00:00:00+00:00",
            strategy="R_CALIBRATED_VALUE",
            max_stake_usdt=1.0,
            requested_amount_wei=str(LIVE_M0W_AMOUNT_WEI),
            reliability_context={
                "seconds_left": 60.0,
                "book_age_ms": 100.0,
            },
        )
        assert local_id is not None
        ledger.update_order(
            local_id,
            status="SUBMITTED",
            quote_average_price=entry_price,
            quote_amount_in_wei=str(LIVE_M0W_AMOUNT_WEI),
            quote_amount_out_wei="2500000000000000000",
        )
        ledger.sync_exchange_order(local_id, {
            "status": "FILLED",
            "filledUsdtAmount": "1.0",
            "filledShareQty": "2.5",
            "fillPercentage": "1",
            "marketProviderFee": "0",
            "networkFee": "0",
        })
        order = next(
            row for row in ledger.unsettled_filled_orders()
            if int(row["id"]) == local_id
        )
        assert ledger.record_strategy_settlement(order, {
            "positionStatus": "ENDED",
            "isWinner": winner,
            "endDate": 1_800_000_000_000 + market_id,
        }) is not None

    summary = ledger.reliability_research_summary()
    assert summary["source"] == "real_filled_orders_only"
    assert summary["copiedSamples"] == 2
    assert summary["settledSamples"] == 2
    low_entry = next(tag for tag in summary["tags"] if tag["id"] == "RC_LOW_ENTRY")
    assert low_entry["original"]["settledSamples"] == 2
    assert low_entry["allowed"]["settledSamples"] == 1
    assert low_entry["blocked"]["settledSamples"] == 1
    assert low_entry["deltaVsOriginalPnlUsdt"] > 0
    assert summary["recentSamples"][0]["tagDecisions"]


def test_live_rules_reject_more_than_three_selected_strategies(tmp_path: Path):
    live = engine(tmp_path, FakeTradingClient())

    with pytest.raises(ValueError, match="between one and 3"):
        live.update_live_rules({
            "strategies": ["M1", "M2", "M3", "M4"],
            "strategyStakesUsdt": [1.0, 1.0, 1.0, 1.0],
        })


def test_pair_arb_uses_one_shared_cap_for_both_live_legs(tmp_path: Path):
    client = FakeTradingClient()
    client.quote_amount_in = "500000000000000000"
    client.quote_average_price = 0.48
    live = engine(tmp_path, client)
    live.update_live_rules({"strategies": ["PAIR_ARB_010"]})

    for side in ("UP", "DOWN"):
        live.process_signal(signal(
            strategy="PAIR_ARB_010",
            side=side,
            entry_price=0.48,
            pair_total_price=0.96,
            pair_up_price=0.48,
            pair_down_price=0.48,
            pair_arb_leg=True,
            m0w_gate=None,
        ))

    assert len(client.place_calls) == 2
    assert sum(order["max_stake_usdt"] for order in live.state()["orders"]) == pytest.approx(1.0)
    assert {order["strategy"] for order in live.state()["orders"]} == {
        "PAIR_ARB_010:UP", "PAIR_ARB_010:DOWN"
    }


def test_futures_lead_observer_defaults_off_and_preserves_existing_execution(
    tmp_path: Path,
):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    state = live.update_live_rules({"strategy": "R_FUTURES_LEAD"})

    live.process_signal(signal(strategy="R_FUTURES_LEAD", m0w_gate=None))

    assert state["rules"]["futuresLeadObserverEnabled"] is False
    assert len(client.place_calls) == 1


def drawdown_history(_current_market_id: int, limit: int) -> list[dict]:
    return [
        {
            "market_id": 196 + index,
            "start_price": 100.0,
            "end_price": 99.95,
        }
        for index in range(limit)
    ]


def test_drawdown_control_defaults_off_and_persists_per_strategy_slot(
    tmp_path: Path,
):
    client = FakeTradingClient()
    live = engine(tmp_path, client, drawdown_market_history=drawdown_history)
    initial = live.update_live_rules({
        "strategies": ["M1", "M2"],
        "strategyStakesUsdt": [1.0, 1.0],
    })
    assert initial["rules"]["strategyDrawdownControlEnabled"] == [False, False]

    live.update_live_rules({"strategyDrawdownControlEnabled": [True, False]})
    reloaded = LiveM0WEngine(
        api_key=None,
        api_secret=None,
        configured_enabled=False,
        credential_source="TEST",
        current_market=market_reference,
        db_path=tmp_path / "live.db",
        drawdown_market_history=drawdown_history,
    )
    assert reloaded.state()["rules"]["strategyDrawdownControlEnabled"] == [
        True,
        False,
    ]


def test_drawdown_control_blocks_only_enabled_strategy_before_quote(
    tmp_path: Path,
):
    client = FakeTradingClient()
    live = engine(tmp_path, client, drawdown_market_history=drawdown_history)
    live.update_live_rules({
        "strategies": ["M1", "M2"],
        "strategyStakesUsdt": [1.0, 1.0],
        "strategyDrawdownControlEnabled": [True, False],
    })

    live.process_signal(signal(strategy="M1", m0w_gate=None, side="UP"))
    live.process_signal(signal(strategy="M2", m0w_gate=None, side="UP"))

    assert len(client.quote_calls) == 1
    assert len(client.place_calls) == 1
    assert {
        order["strategy"]: order["status"] for order in live.state()["orders"]
    } == {
        "M1": "BLOCKED_DRAWDOWN_CONTROL",
        "M2": "SUBMITTED",
    }


def test_drawdown_control_fails_closed_when_history_is_incomplete(
    tmp_path: Path,
):
    client = FakeTradingClient()
    live = engine(
        tmp_path,
        client,
        drawdown_market_history=lambda _market_id, _limit: [],
    )
    live.update_live_rules({
        "strategy": "M1",
        "strategyDrawdownControlEnabled": [True],
    })

    live.process_signal(signal(strategy="M1", m0w_gate=None))

    assert client.quote_calls == []
    order = live.state()["orders"][0]
    assert order["status"] == "BLOCKED_DRAWDOWN_CONTROL"
    assert "history 0/6 is incomplete" in order["error_message"]


def _record_official_strategy_result(
    live: LiveM0WEngine,
    *,
    strategy: str,
    market_id: int,
    result: str,
) -> int:
    local_id = live.ledger.record_signal(
        topic_id=101,
        market_id=market_id,
        side="UP",
        token_id=f"token-{strategy}-{market_id}",
        signal_price=0.40,
        account_type="SPOT",
        signal_at=f"2026-07-18T00:{market_id % 60:02d}:00+00:00",
        strategy=strategy,
        max_stake_usdt=1.0,
        requested_amount_wei=str(LIVE_M0W_AMOUNT_WEI),
    )
    assert local_id is not None
    live.ledger.update_order(
        local_id,
        status="FILLED",
        filled_usdt_amount=1.0,
        quote_amount_in_wei=str(LIVE_M0W_AMOUNT_WEI),
        quote_amount_out_wei="2500000000000000000",
        network_fee=0.0,
        market_provider_fee=0.0,
    )
    order = next(
        item
        for item in live.ledger.unsettled_filled_orders()
        if int(item["id"]) == local_id
    )
    settlement = live.ledger.record_strategy_settlement(
        order,
        {
            "positionStatus": "ENDED",
            "isWinner": result == "WIN",
            "endDate": 1_800_000_000_000 + market_id,
        },
    )
    assert settlement is not None
    return local_id


def test_two_loss_cooldown_is_independent_and_persists_per_strategy_slot(
    tmp_path: Path,
):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    initial = live.update_live_rules({
        "strategies": ["M1", "M2"],
        "strategyStakesUsdt": [1.0, 1.0],
    })
    assert initial["rules"]["strategyLossCooldownEnabled"] == [False, False]

    live.update_live_rules({"strategyLossCooldownEnabled": [True, False]})
    _record_official_strategy_result(
        live, strategy="M1", market_id=190, result="LOSS"
    )
    _record_official_strategy_result(
        live, strategy="M2", market_id=190, result="LOSS"
    )
    _record_official_strategy_result(
        live, strategy="M1", market_id=191, result="LOSS"
    )

    reloaded = LiveM0WEngine(
        api_key="key",
        api_secret="secret",
        configured_enabled=True,
        credential_source="TEST",
        current_market=market_reference,
        current_verified_prediction_book=verified_prediction_book,
        db_path=tmp_path / "live.db",
        client_factory=lambda *_args: client,
    )
    reloaded._preflight()
    states = {
        item["strategy"]: item
        for item in reloaded.state()["strategyLossCooldownStates"]
    }
    assert reloaded.state()["rules"]["strategyLossCooldownEnabled"] == [
        True,
        False,
    ]
    assert states["M1"]["consecutiveLosses"] == 2
    assert states["M1"]["cooldownPending"] is True
    assert states["M2"]["consecutiveLosses"] == 1

    reloaded.process_signal(signal(strategy="M1", m0w_gate=None))
    reloaded.process_signal(signal(strategy="M2", m0w_gate=None))

    current_orders = {
        item["strategy"]: item
        for item in reloaded.state()["orders"]
        if int(item["market_id"]) == 202
    }
    assert current_orders["M1"]["status"] == "SKIPPED_TWO_LOSS_COOLDOWN"
    assert current_orders["M2"]["status"] == "SUBMITTED"
    assert len(client.quote_calls) == 1
    m1_state = reloaded.ledger.loss_cooldown_state("M1")
    assert m1_state["consecutiveLosses"] == 0
    assert m1_state["cooldownPending"] is False
    assert m1_state["lastSkippedMarketId"] == 202


def test_two_loss_cooldown_win_resets_only_its_strategy(tmp_path: Path):
    live = engine(tmp_path, FakeTradingClient())
    _record_official_strategy_result(
        live, strategy="M1", market_id=190, result="LOSS"
    )
    _record_official_strategy_result(
        live, strategy="M2", market_id=190, result="LOSS"
    )
    _record_official_strategy_result(
        live, strategy="M1", market_id=191, result="WIN"
    )

    assert live.ledger.loss_cooldown_state("M1")["consecutiveLosses"] == 0
    assert live.ledger.loss_cooldown_state("M2")["consecutiveLosses"] == 1


def test_futures_lead_observer_v2_can_pass_when_original_f1_blocks_history(
    tmp_path: Path,
):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    live.update_live_rules({
        "strategy": "R_FUTURES_LEAD",
        "futuresLeadObserverEnabled": True,
        "futuresLeadObserverVersion": "V2",
    })
    gate = f1_observer_gate(
        allowed=False,
        status="BLOCK",
        historicalState="TREND",
        reason="historical TREND",
    )

    live.process_signal(signal(
        strategy="R_FUTURES_LEAD",
        m0w_gate=None,
        futures_lead_observer_gate_required=True,
        futures_lead_observer_gate=gate,
    ))

    assert len(client.place_calls) == 1


def test_two_live_strategies_use_independent_observer_versions(tmp_path: Path):
    gate = f1_observer_gate(
        allowed=False,
        status="BLOCK",
        historicalState="TREND",
        currentEffectiveCrossovers=2,
        currentBothSidesTouched=True,
        reason="historical TREND and dual touch",
    )

    first_client = FakeTradingClient()
    first_path = tmp_path / "first"
    first_path.mkdir()
    first = engine(first_path, first_client)
    first.update_live_rules({
        "strategies": ["R_FUTURES_LEAD", "R_OFI"],
        "strategyStakesUsdt": [1.0, 1.0],
        "strategyObserverEnabled": [True, True],
        "strategyObserverVersions": ["V2", "V6"],
    })
    envelope = {
        "m0w_gate": None,
        "strategy_observer_gate_required": True,
        "strategy_observer_gate": gate,
    }
    first.process_signal(signal(strategy="R_FUTURES_LEAD", **envelope))
    first.process_signal(signal(strategy="R_OFI", **envelope))

    assert len(first_client.place_calls) == 1
    assert any(
        order["strategy"] == "R_FUTURES_LEAD"
        and order["status"] == "SUBMITTED"
        for order in first.state()["orders"]
    )
    assert first.state()["rules"]["strategyObserverVersions"] == ["V2", "V6"]
    assert {
        order["strategy"]: order["status"] for order in first.state()["orders"]
    }["R_OFI"] == "BLOCKED_STRATEGY_OBSERVER"

    second_client = FakeTradingClient()
    second_path = tmp_path / "second"
    second_path.mkdir()
    second = engine(second_path, second_client)
    second.update_live_rules({
        "strategies": ["R_FUTURES_LEAD", "R_OFI"],
        "strategyStakesUsdt": [1.0, 1.0],
        "strategyObserverEnabled": [True, True],
        "strategyObserverVersions": ["V6", "V2"],
    })
    second.process_signal(signal(strategy="R_FUTURES_LEAD", **envelope))
    second.process_signal(signal(strategy="R_OFI", **envelope))

    assert len(second_client.place_calls) == 1
    assert second.state()["orders"][0]["strategy"] == "R_OFI"


@pytest.mark.parametrize("strategy", ["R_MICROPRICE", "R_CALIBRATED_VALUE"])
def test_microprice_and_calibrated_value_support_live_observer(
    tmp_path: Path, strategy: str,
):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    live.update_live_rules({
        "strategy": strategy,
        "futuresLeadObserverEnabled": True,
        "futuresLeadObserverVersion": "V6",
    })

    live.process_signal(signal(
        strategy=strategy,
        m0w_gate=None,
        strategy_observer_gate_required=True,
        strategy_observer_gate=f1_observer_gate(currentBothSidesTouched=True),
    ))

    assert client.quote_calls == []
    assert client.place_calls == []
    assert live.state()["orders"][0]["status"] == "BLOCKED_STRATEGY_OBSERVER"


def test_r_ofi_observer_fails_closed_without_live_authorized_context(
    tmp_path: Path,
):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    live.update_live_rules({
        "strategy": "R_OFI",
        "strategyObserverEnabled": [True],
        "strategyObserverVersions": ["V3"],
    })

    live.process_signal(signal(strategy="R_OFI", m0w_gate=None))

    assert client.quote_calls == []
    assert live.state()["orders"][0]["status"] == "BLOCKED_STRATEGY_OBSERVER"


@pytest.mark.parametrize(
    "gate",
    [None, f1_observer_gate(currentBothSidesTouched=True)],
)
def test_futures_lead_observer_v6_fails_closed_before_quote(
    tmp_path: Path, gate,
):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    live.update_live_rules({
        "strategy": "R_FUTURES_LEAD",
        "futuresLeadObserverEnabled": True,
        "futuresLeadObserverVersion": "V6",
    })

    live.process_signal(signal(
        strategy="R_FUTURES_LEAD",
        m0w_gate=None,
        futures_lead_observer_gate_required=True,
        futures_lead_observer_gate=gate,
    ))

    assert client.quote_calls == []
    assert live.state()["orders"][0]["status"] == (
        "BLOCKED_FUTURES_LEAD_OBSERVER"
    )


def test_pair_arb_places_neither_leg_when_one_quote_is_unsafe(tmp_path: Path):
    client = FakeTradingClient()
    client.quote_average_by_token = {
        "up-token": 0.31,
        "down-token": 0.68,
    }
    client.quote_amount_in_by_token = {
        "up-token": "218750000000000000",
        "down-token": "781250000000000000",
    }
    live = engine(
        tmp_path,
        client,
        current_verified_prediction_book=pair_prediction_book,
    )
    live.update_live_rules({
        "strategies": ["PAIR_ARB_010"],
        "strategyStakesUsdt": [1.0],
    })

    for side, price in (("UP", 0.21), ("DOWN", 0.75)):
        live.process_signal(signal(
            strategy="PAIR_ARB_010",
            side=side,
            entry_price=price,
            pair_total_price=0.96,
            pair_up_price=0.21,
            pair_down_price=0.75,
            pair_arb_leg=True,
            m0w_gate=None,
        ))

    assert len(client.quote_calls) == 2
    assert client.place_calls == []
    orders = live.state()["orders"]
    assert {order["strategy"] for order in orders} == {
        "PAIR_ARB_010:UP", "PAIR_ARB_010:DOWN"
    }
    assert {order["status"] for order in orders} == {"REJECTED"}


def test_risk_pair_cannot_be_selected_for_live_execution(tmp_path: Path):
    client = FakeTradingClient()
    client.quote_average_by_token = {
        "up-token": 0.31,
        "down-token": 0.68,
    }
    client.quote_amount_in_by_token = {
        "up-token": "218750000000000000",
        "down-token": "781250000000000000",
    }
    live = engine(tmp_path, client)
    with pytest.raises(ValueError, match="strategy must be one of"):
        live.update_live_rules({
            "strategies": ["PAIR_ARB_RISK_020"],
            "strategyStakesUsdt": [1.0],
        })


@pytest.mark.parametrize(
    "up_amount_out, expected_places",
    [
        ("500000000000000000", 0),
        ("744000000000000000", 2),
    ],
)
def test_pair_quote_capacity_uses_seventy_percent_minimum(
    tmp_path: Path, up_amount_out: str, expected_places: int,
):
    client = FakeTradingClient()
    client.quote_average_by_token = {
        "up-token": 0.21,
        "down-token": 0.75,
    }
    client.quote_amount_in_by_token = {
        "up-token": "218750000000000000",
        "down-token": "781250000000000000",
    }
    client.quote_amount_out_by_token = {
        "up-token": up_amount_out,
        "down-token": "2500000000000000000",
    }
    live = engine(
        tmp_path,
        client,
        current_verified_prediction_book=pair_prediction_book,
    )
    live.update_live_rules({
        "strategies": ["PAIR_ARB_010"],
        "strategyStakesUsdt": [1.0],
    })

    for side, price in (("UP", 0.21), ("DOWN", 0.75)):
        live.process_signal(signal(
            strategy="PAIR_ARB_010",
            side=side,
            entry_price=price,
            pair_total_price=0.96,
            pair_up_price=0.21,
            pair_down_price=0.75,
            pair_arb_leg=True,
            m0w_gate=None,
        ))

    assert len(client.place_calls) == expected_places
    orders = live.state()["orders"]
    if expected_places == 0:
        assert {order["status"] for order in orders} == {"REJECTED"}
        assert {order["error_kind"] for order in orders} == {
            "PAIR_QUOTE_CAPACITY_LOW"
        }
    else:
        assert {order["status"] for order in orders} == {"SUBMITTED"}


@pytest.mark.parametrize(
    "amount_out, expected_places, expected_audit_status",
    [
        ("1200000000000000000", 2, "ACCEPTED"),
        ("1060000000000000000", 0, "REJECTED"),
    ],
)
def test_qc_pair_requotes_equal_shares_and_requires_locked_profit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    amount_out: str,
    expected_places: int,
    expected_audit_status: str,
):
    monkeypatch.setattr(live_trading, "PAIR_ARB_QC_MIN_SHADOW_SAMPLES", 0)
    client = FakeTradingClient()
    client.quote_average_by_token = {
        "up-token": 0.40,
        "down-token": 0.40,
    }
    client.quote_amount_in_by_token = {
        "up-token": "500000000000000000",
        "down-token": "500000000000000000",
    }
    client.quote_amount_out_by_token = {
        "up-token": amount_out,
        "down-token": amount_out,
    }
    live = engine(tmp_path, client)
    live.update_live_rules({
        "strategies": ["PAIR_ARB_QC_015"],
        "strategyStakesUsdt": [1.0],
    })

    for side in ("UP", "DOWN"):
        live.process_signal(signal(
            strategy="PAIR_ARB_QC_015",
            side=side,
            entry_price=0.45,
            pair_total_price=0.90,
            pair_up_price=0.45,
            pair_down_price=0.45,
            pair_arb_leg=True,
            m0w_gate=None,
        ))

    assert len(client.quote_calls) == 4
    assert len(client.place_calls) == expected_places
    audits = live.ledger.recent_pair_quote_audits()
    assert len(audits) == 1
    assert audits[0]["status"] == expected_audit_status
    if expected_places:
        assert audits[0]["locked_pnl_usdt"] >= 0.05
        assert audits[0]["locked_roi"] >= 0.015
        assert audits[0]["net_share_mismatch_ratio"] <= 0.0025


def test_qc_shadow_blocks_only_qc_while_f1_keeps_placing(
    tmp_path: Path,
):
    client = FakeTradingClient()
    client.quote_average_by_token = {
        "up-token": 0.40,
        "down-token": 0.40,
    }
    client.quote_amount_in_by_token = {
        "up-token": "5000000000000000000",
        "down-token": "5000000000000000000",
    }
    client.quote_amount_out_by_token = {
        "up-token": "12000000000000000000",
        "down-token": "12000000000000000000",
    }
    live = engine(tmp_path, client)

    state = live.update_live_rules({
        "strategies": ["M01O_F1", "PAIR_ARB_QC_015"],
        "strategyStakesUsdt": [1.5, 10.0],
    })
    assert state["runtimeEnabled"] is True
    assert state["armed"] is True

    for side in ("UP", "DOWN"):
        live.process_signal(signal(
            strategy="PAIR_ARB_QC_015",
            side=side,
            entry_price=0.45,
            pair_total_price=0.90,
            pair_up_price=0.45,
            pair_down_price=0.45,
            pair_arb_leg=True,
            m0w_gate=None,
        ))

    assert len(client.quote_calls) == 4
    assert client.place_calls == []
    audits = live.ledger.recent_pair_quote_audits()
    assert audits[0]["status"] == "SHADOW_ACCEPTED"
    assert live.ledger.pair_qc_shadow_sample_count("PAIR_ARB_QC_015") == 1
    assert {row["status"] for row in live.state()["orders"]} == {
        "SHADOW_ACCEPTED_NO_PLACEMENT"
    }

    client.quote_amount_in_by_token["up-token"] = "1500000000000000000"
    client.quote_amount_out_by_token["up-token"] = "3750000000000000000"
    live.process_signal(signal(
        strategy="M01O_F1",
        m0w_gate=None,
        entry_price=0.30,
        maximum_entry_price=0.30,
        observer_gate_required=True,
        market_observer_gate=f1_observer_gate(),
    ))

    assert len(client.place_calls) == 1
    f1_order = next(
        row for row in live.state()["orders"]
        if row["strategy"] == "M01O_F1"
    )
    assert f1_order["status"] == "SUBMITTED"
    assert live.state()["runtimeEnabled"] is True
    assert live.state()["armed"] is True


def test_one_sided_pair_fill_persistently_pauses_live_runtime(tmp_path: Path):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    live.update_live_rules({
        "strategies": ["M01O_F1", "PAIR_ARB_010"],
        "strategyStakesUsdt": [1.5, 10.0],
    })
    old_timestamp = "2026-07-19T00:00:00+00:00"
    for side, status, filled in (
        ("UP", "PENDING", 0.0),
        ("DOWN", "FILLED", 1.0),
    ):
        local_id = live.ledger.record_signal(
            topic_id=101,
            market_id=202,
            side=side,
            token_id=f"{side.lower()}-token",
            signal_price=0.5,
            account_type="SPOT",
            signal_at=old_timestamp,
            strategy=f"PAIR_ARB_010:{side}",
            max_stake_usdt=5.0,
            requested_amount_wei="5000000000000000000",
        )
        assert local_id is not None
        live.ledger.update_order(
            local_id,
            status=status,
            order_id=f"order-{side}",
            filled_usdt_amount=filled,
            filled_share_qty=filled * 2,
            attempted_at=old_timestamp,
            submitted_at=old_timestamp,
        )

    assert any(
        row["status"] == "PENDING" for row in live.ledger.pending_orders()
    )
    live._enforce_pair_execution_safety()

    state = live.state()
    assert state["runtimeEnabled"] is False
    assert state["armed"] is False
    assert state["status"] == "PAUSED_PAIR_FILL_MISMATCH"
    assert live.ledger.runtime_override() is False
    assert live.ledger.pair_incident("PAIR_ARB_010", 202)["status"] == "ACTIVE"


def test_settled_historical_pair_mismatch_does_not_pause_new_runtime(
    tmp_path: Path,
):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    live.update_live_rules({
        "strategies": ["M01O_F1", "PAIR_ARB_010"],
        "strategyStakesUsdt": [1.5, 10.0],
    })
    old_timestamp = "2026-07-19T00:00:00+00:00"
    filled_id = None
    for side, status, filled in (
        ("UP", "REJECTED", 0.0),
        ("DOWN", "FILLED", 1.0),
    ):
        local_id = live.ledger.record_signal(
            topic_id=101,
            market_id=202,
            side=side,
            token_id=f"{side.lower()}-token",
            signal_price=0.5,
            account_type="SPOT",
            signal_at=old_timestamp,
            strategy=f"PAIR_ARB_010:{side}",
            max_stake_usdt=5.0,
            requested_amount_wei="5000000000000000000",
        )
        assert local_id is not None
        live.ledger.update_order(
            local_id,
            status=status,
            order_id=f"order-{side}",
            filled_usdt_amount=filled,
            filled_share_qty=filled * 2,
            attempted_at=old_timestamp,
            submitted_at=old_timestamp,
        )
        if filled:
            filled_id = local_id
    assert filled_id is not None
    with live.ledger.lock:
        live.ledger.db.execute(
            """INSERT INTO live_strategy_settlements(
                   order_local_id, market_id, position_status, result,
                   cost_usdt, payout_usdt, pnl_usdt, roi_pct,
                   settled_at, updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                filled_id, 202, "SETTLED", "WIN", 1.0, 2.0, 1.0, 100.0,
                old_timestamp, old_timestamp,
            ),
        )
        live.ledger.db.commit()

    live._enforce_pair_execution_safety()

    state = live.state()
    assert state["runtimeEnabled"] is True
    assert state["armed"] is False
    assert live.ledger.pair_incident("PAIR_ARB_010", 202)["status"] == "SETTLED"


def _record_filled_pair_for_insurance(
    live: LiveM0WEngine, *, up_shares: float, down_shares: float
) -> None:
    for side, shares in (("UP", up_shares), ("DOWN", down_shares)):
        local_id = live.ledger.record_signal(
            topic_id=101,
            market_id=202,
            side=side,
            token_id=f"{side.lower()}-token",
            signal_price=0.5,
            account_type="SPOT",
            signal_at="2026-07-19T00:00:00+00:00",
            strategy=f"PAIR_ARB_RISK_020:{side}",
            max_stake_usdt=5.0,
            requested_amount_wei="5000000000000000000",
        )
        assert local_id is not None
        live.ledger.update_order(
            local_id,
            status="FILLED",
            order_id=f"buy-{side}",
            filled_usdt_amount=4.5,
            filled_share_qty=shares,
            fill_percentage=1.0,
        )


def test_pair_insurance_requires_loss_in_both_outcomes(tmp_path: Path):
    client = FakeTradingClient()
    client.server_time_ms = market_reference()["end_ms"] - 20_000
    client.orderbooks_by_token = {
        "up-token": {"bids": [{"price": "0.85", "size": "20"}]},
        "down-token": {"bids": [{"price": "0.15", "size": "20"}]},
    }
    client.position_payloads = {
        "up-token": {"shares": "9.10", "positionStatus": "ONGOING"},
        "down-token": {"shares": "8.40", "positionStatus": "ONGOING"},
    }
    live = engine(tmp_path, client)
    _record_filled_pair_for_insurance(live, up_shares=9.10, down_shares=8.40)

    live._run_pair_loss_insurance()

    assert live.ledger.recent_pair_insurance() == []
    assert not any(call.get("side") == "SELL" for call in client.quote_calls)


def test_pair_insurance_sells_low_leg_after_three_second_confirmation(
    tmp_path: Path,
):
    client = FakeTradingClient()
    client.server_time_ms = market_reference()["end_ms"] - 20_000
    client.quote_average_by_token["down-token"] = 0.15
    client.orderbooks_by_token = {
        "up-token": {"bids": [{"price": "0.85", "size": "20"}]},
        "down-token": {"bids": [{"price": "0.15", "size": "20"}]},
    }
    client.position_payloads = {
        "up-token": {"shares": "8.50", "positionStatus": "ONGOING"},
        "down-token": {"shares": "8.40", "positionStatus": "ONGOING"},
    }
    live = engine(tmp_path, client)
    _record_filled_pair_for_insurance(live, up_shares=8.50, down_shares=8.40)

    live._run_pair_loss_insurance()
    armed = live.ledger.recent_pair_insurance()[0]
    assert armed["status"] == "ARMED"
    assert armed["low_side"] == "DOWN"
    assert not any(call.get("side") == "SELL" for call in client.quote_calls)

    old = (datetime.now(timezone.utc) - timedelta(seconds=4)).isoformat()
    with live.ledger.lock:
        live.ledger.db.execute(
            "UPDATE live_pair_insurance SET armed_at=? WHERE id=?",
            (old, int(armed["id"])),
        )
        live.ledger.db.commit()
    live._run_pair_loss_insurance()

    sell_quotes = [call for call in client.quote_calls if call.get("side") == "SELL"]
    assert len(sell_quotes) == 1
    assert sell_quotes[0]["token_id"] == "down-token"
    assert sell_quotes[0]["price_limit"] == "0.15"
    assert sell_quotes[0]["amount_in_wei"] == "8400000000000000000"
    assert live.ledger.recent_pair_insurance()[0]["status"] == "SUBMITTED"


def _record_filled_futures_lead(
    live: LiveM0WEngine, strategy: str = "R_FUTURES_LEAD"
) -> int:
    local_id = live.ledger.record_signal(
        topic_id=101,
        market_id=202,
        side="UP",
        token_id="up-token",
        signal_price=0.40,
        account_type="SPOT",
        signal_at="2026-07-19T00:00:00+00:00",
        strategy=strategy,
        max_stake_usdt=1.0,
        requested_amount_wei=str(LIVE_M0W_AMOUNT_WEI),
    )
    assert local_id is not None
    live.ledger.update_order(
        local_id,
        status="FILLED",
        order_id="buy-futures-lead",
        quote_amount_in_wei="1000000000000000000",
        quote_amount_out_wei="2500000000000000000",
        filled_usdt_amount=1.0,
        filled_share_qty=2.5,
        fill_percentage=1.0,
    )
    return local_id


@pytest.mark.parametrize("order_type", ["LIMIT", "MARKET"])
def test_manual_sell_submits_selected_exit_type(
    tmp_path: Path, order_type: str,
):
    client = FakeTradingClient()
    client.server_time_ms = market_reference()["end_ms"] - 60_000
    client.position_payloads["up-token"] = {
        "shares": "2.5", "positionStatus": "ONGOING",
    }
    client.orderbooks_by_token["up-token"] = {
        "bids": [{"price": "0.55", "size": "10"}],
    }
    client.quote_average_by_token["up-token"] = 0.55
    live = engine(tmp_path, client)
    local_id = _record_filled_futures_lead(live)

    state = live.manual_sell(local_id, order_type)

    quote = client.quote_calls[-1]
    assert quote["side"] == "SELL"
    assert quote["order_type"] == order_type
    assert quote["amount_in_wei"] == "2500000000000000000"
    assert quote["price_limit"] == ("0.55" if order_type == "LIMIT" else None)
    exit_row = state["manualExits"][0]
    assert exit_row["status"] == "SUBMITTED"
    assert exit_row["order_type"] == order_type
    assert state["activePositions"][0]["exit_status"] == "SUBMITTED"
    if order_type == "MARKET":
        assert client.place_calls[-1]["order_type"] == "MARKET"


def test_manual_sell_allows_any_live_strategy_position(tmp_path: Path):
    client = FakeTradingClient()
    client.server_time_ms = market_reference()["end_ms"] - 60_000
    client.position_payloads["up-token"] = {
        "shares": "2.5", "positionStatus": "ONGOING",
    }
    client.orderbooks_by_token["up-token"] = {
        "bids": [{"price": "0.55", "size": "10"}],
    }
    client.quote_average_by_token["up-token"] = 0.55
    live = engine(tmp_path, client)
    local_id = _record_filled_futures_lead(live, strategy="M0W")

    state = live.manual_sell(local_id, "LIMIT")

    assert client.quote_calls[-1]["side"] == "SELL"
    assert state["activePositions"][0]["strategy"] == "M0W"
    assert state["activePositions"][0]["exit_status"] == "SUBMITTED"


def test_manual_sell_fill_sync_removes_active_position(tmp_path: Path):
    client = FakeTradingClient()
    client.server_time_ms = market_reference()["end_ms"] - 60_000
    client.position_payloads["up-token"] = {
        "shares": "2.5", "positionStatus": "ONGOING",
    }
    client.orderbooks_by_token["up-token"] = {
        "bids": [{"price": "0.55", "size": "10"}],
    }
    client.quote_average_by_token["up-token"] = 0.55
    live = engine(tmp_path, client)
    local_id = _record_filled_futures_lead(live)
    live.manual_sell(local_id, "MARKET")
    client.history_orders = [{
        "orderId": "market-54124",
        "status": "FILLED",
        "filledUsdtAmount": "1.35",
        "filledShareQty": "2.5",
        "fillPercentage": "1",
        "realizedPnl": "0.35",
    }]

    live._sync_orders()

    state = live.state()
    assert state["activePositions"] == []
    assert state["manualExits"][0]["status"] == "FILLED"
    assert state["manualExits"][0]["realized_pnl"] == pytest.approx(0.35)


def test_live_rule_thresholds_change_hourly_guard_decision(tmp_path: Path):
    client = FakeTradingClient()
    live = engine(
        tmp_path,
        client,
        hourly_provider=lambda: m0_hourly_performance(
            win_rate_pct=50.0,
            win_then_loss_rate_pct=55.0,
        ),
    )
    live.update_live_rules(
        {
            "minHourlyWinRatePct": 49.0,
            "maxHourlyWinThenLossRatePct": 60.0,
        }
    )

    live.process_signal(signal())

    assert len(client.place_calls) == 1
    guard = live.state()["hourlyGuard"]
    assert guard["status"] == "ALLOW"
    assert guard["minWinRatePct"] == pytest.approx(49.0)
    assert guard["maxWinThenLossRatePct"] == pytest.approx(60.0)


def test_live_rules_persist_in_separate_live_ledger(tmp_path: Path):
    db_path = tmp_path / "persistent-rules.db"
    first = LiveM0WEngine(
        api_key="key",
        api_secret="secret",
        configured_enabled=False,
        credential_source="TEST",
        current_market=market_reference,
        db_path=db_path,
    )
    first.update_live_rules(
        {
            "strategy": "M7_3",
            "maxStakeUsdt": 3.25,
                "minHourlyWinRatePct": 57.5,
                "maxHourlyWinThenLossRatePct": 42.5,
                "futuresLeadObserverEnabled": True,
                "futuresLeadObserverVersion": "V4",
        }
    )

    second = LiveM0WEngine(
        api_key="key",
        api_secret="secret",
        configured_enabled=False,
        credential_source="TEST",
        current_market=market_reference,
        db_path=db_path,
    )

    assert second.state()["rules"] == {
        "strategy": "M7_3",
        "strategies": ["M7_3"],
        "maxStakeUsdt": 3.25,
        "strategyStakesUsdt": [3.25],
        "minHourlyWinRatePct": 57.5,
        "maxHourlyWinThenLossRatePct": 42.5,
        "futuresLeadObserverEnabled": True,
        "futuresLeadObserverVersion": "V4",
        "strategyObserverEnabled": [True],
        "strategyObserverVersions": ["V4"],
        "strategyDrawdownControlEnabled": [False],
        "strategyLossCooldownEnabled": [False],
        "reliabilityGateTags": [],
    }


@pytest.mark.parametrize(
    "patch",
    [
        {"strategy": "B2"},
        {"maxStakeUsdt": 0},
        {"maxStakeUsdt": 100.01},
        {"minHourlyWinRatePct": -0.01},
        {"minHourlyWinRatePct": 100.01},
        {"maxHourlyWinThenLossRatePct": -0.01},
        {"maxHourlyWinThenLossRatePct": 100.01},
    ],
)
def test_invalid_live_rules_are_rejected_without_mutation(
    tmp_path: Path, patch: dict,
):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    before = live.state()["rules"]

    with pytest.raises(ValueError):
        live.update_live_rules(patch)

    assert live.state()["rules"] == before


def test_live_ledger_allows_one_attempt_per_strategy_and_market(tmp_path: Path):
    client = FakeTradingClient()
    live = engine(tmp_path, client)
    live.update_live_rules(
        {
            "strategies": ["M0", "M1"],
            "strategyStakesUsdt": [1.0, 1.0],
        }
    )
    live.process_signal(signal(strategy="M0", m0w_gate=None))
    live.process_signal(signal(strategy="M1", m0w_gate=None))
    live.process_signal(signal(strategy="M0", m0w_gate=None))
    live.process_signal(signal(strategy="M1", m0w_gate=None))

    assert len(client.place_calls) == 2
    assert len(live.state()["orders"]) == 2


def test_auto_redeem_claims_once_even_while_trading_is_paused(tmp_path: Path):
    client = FakeTradingClient()
    client.pending_claim_positions = [claimable_position()]
    db_path = tmp_path / "paused-live.db"
    ledger = LiveLedger(db_path)
    ledger.set_runtime_enabled(False)
    live = LiveM0WEngine(
        api_key="key",
        api_secret="secret",
        configured_enabled=True,
        credential_source="TEST",
        current_market=market_reference,
        db_path=db_path,
        client_factory=lambda *_args: client,
    )
    live._preflight()

    assert live.state()["status"] == "PAUSED"
    assert live.state()["armed"] is False
    assert live.run_redeem_cycle() is True
    assert len(client.redeem_calls) == 1
    assert client.redeem_calls[0]["token_ids"] == ["winning-token"]
    state = live.state()
    assert state["autoRedeem"]["summary"]["completed"] == 1
    assert state["autoRedeem"]["redeems"][0]["attempt_count"] == 1

    live.run_redeem_cycle()
    assert len(client.redeem_calls) == 1


def test_auto_redeem_waits_full_minute_after_settlement(tmp_path: Path):
    client = FakeTradingClient()
    client.pending_claim_positions = [claimable_position(endDate=950_001)]
    live = engine(tmp_path, client)

    live.run_redeem_cycle()

    assert client.redeem_calls == []
    redeem = live.state()["autoRedeem"]["redeems"][0]
    assert redeem["status"] == "DISCOVERED"
    assert redeem["attempt_count"] == 0


@pytest.mark.parametrize(
    "position_patch",
    [
        {"canClaim": False},
        {"positionStatus": "ENDED"},
        {"shares": "0"},
        {"value": "0"},
    ],
)
def test_auto_redeem_skips_non_claimable_or_zero_value_positions(
    tmp_path: Path, position_patch: dict,
):
    client = FakeTradingClient()
    client.pending_claim_positions = [claimable_position(**position_patch)]
    live = engine(tmp_path, client)

    live.run_redeem_cycle()

    assert client.redeem_calls == []
    assert live.state()["autoRedeem"]["summary"]["discovered"] == 0


def test_auto_redeem_transport_ambiguity_is_never_retried(tmp_path: Path):
    client = FakeTradingClient()
    client.pending_claim_positions = [claimable_position()]
    client.redeem_error = ApiTransportError("connection closed after send")
    live = engine(tmp_path, client)

    live.run_redeem_cycle()
    live.run_redeem_cycle()

    assert len(client.redeem_calls) == 1
    redeem = live.state()["autoRedeem"]["redeems"][0]
    assert redeem["status"] == "AMBIGUOUS"
    assert redeem["attempt_count"] == 1


def test_live_strategy_performance_uses_only_own_settled_fills(tmp_path: Path):
    client = FakeTradingClient()
    live = engine(tmp_path, client)

    def add_fill(
        market_id: int,
        token_id: str,
        *,
        quote_in: str,
        quote_out: str,
        filled_usdt: float,
        provider_fee: float,
    ) -> int:
        local_id = live.ledger.record_signal(
            topic_id=market_id + 1,
            market_id=market_id,
            side="UP",
            token_id=token_id,
            signal_price=0.5,
            account_type="SPOT",
            signal_at="2026-07-19T00:00:00+00:00",
        )
        assert local_id is not None
        live.ledger.update_order(
            local_id,
            status="FILLED",
            quote_amount_in_wei=quote_in,
            quote_amount_out_wei=quote_out,
            filled_usdt_amount=filled_usdt,
            filled_share_qty=float(int(quote_out) / 10**18),
            market_provider_fee=provider_fee,
            network_fee=0.0,
        )
        return local_id

    winner_id = add_fill(
        301,
        "winner-token",
        quote_in="999600000000000000",
        quote_out="1960000000000000000",
        filled_usdt=1.0,
        provider_fee=0.033896,
    )
    loser_id = add_fill(
        302,
        "loser-token",
        quote_in="998800000000000000",
        quote_out="2270000000000000000",
        filled_usdt=1.0,
        provider_fee=0.0,
    )
    add_fill(
        303,
        "open-token",
        quote_in="998400000000000000",
        quote_out="2080000000000000000",
        filled_usdt=1.0,
        provider_fee=0.03744,
    )
    client.position_payloads = {
        "winner-token": {
            "positionStatus": "CLAIMED",
            "isWinner": True,
            "endDate": 900_000,
            # Deliberately unrelated aggregate account values: the strategy
            # calculation must use its own signed quote/fill instead.
            "totalCost": "99",
            "realizedPnl": "42",
        },
        "loser-token": {
            "positionStatus": "CLAIMED",
            "isWinner": False,
            "endDate": 900_000,
            "totalCost": "77",
            "unrealizedPnl": "-77",
        },
        "open-token": {
            "positionStatus": "OPEN",
            "canClaim": False,
            "endDate": 1_200_000,
        },
    }

    live._sync_strategy_settlements()
    live._sync_strategy_settlements()

    performance = live.state()["performance"]
    assert performance["executedTrades"] == 3
    assert performance["settledTrades"] == 2
    assert performance["unsettledTrades"] == 1
    assert performance["wins"] == 1
    assert performance["losses"] == 1
    assert performance["winRatePct"] == pytest.approx(50.0)
    assert performance["settledCostUsdt"] == pytest.approx(1.9984)
    assert performance["settledPayoutUsdt"] == pytest.approx(1.926104)
    assert performance["profitUsdt"] == pytest.approx(-0.072296)
    assert performance["roiPct"] == pytest.approx(
        (-0.072296 / 1.9984) * 100
    )

    orders = {item["id"]: item for item in live.state()["orders"]}
    assert orders[winner_id]["settlement_result"] == "WIN"
    assert orders[winner_id]["settlement_pnl_usdt"] == pytest.approx(0.926504)
    assert orders[loser_id]["settlement_result"] == "LOSS"
    assert orders[loser_id]["settlement_pnl_usdt"] == pytest.approx(-0.9988)


@pytest.mark.parametrize("price", [0, 1, -0.1, 1.1, float("nan")])
def test_invalid_signal_price_never_reaches_quote(tmp_path: Path, price: float):
    client = FakeTradingClient()
    live = engine(tmp_path, client)

    live.process_signal(signal(entry_price=price))

    assert client.quote_calls == []
    assert live.state()["orders"][0]["status"] == "BLOCKED_INVALID_PRICE"
