from __future__ import annotations

import argparse
from decimal import Decimal

import pytest

from predict_bot.xpair_btc_eth_canary import (
    LIVE_CONFIRM_ENV,
    LIVE_CONFIRM_VALUE,
    CanaryLeg,
    CanaryPlan,
    CanaryStore,
    build_canary_plan,
    choose_trial,
    place_pair,
    preflight_wallet,
    quote_equal_share_pair,
    validate_args,
)
from predict_bot.xpair_btc_eth_paper import MarketRef


def market(symbol: str, market_id: int) -> MarketRef:
    return MarketRef(
        symbol=symbol,
        topic_id=market_id + 1000,
        market_id=market_id,
        title=f"{symbol} test",
        start_ms=1_000,
        end_ms=301_000,
        start_price=100.0,
        fee_bps=200,
        up_token_id=f"{symbol}-up",
        down_token_id=f"{symbol}-down",
    )


def trial(variant: str, cost: float, *, eligible: bool = True) -> dict:
    btc_side, eth_side = (
        ("DOWN", "UP")
        if variant == "BTC_DOWN_ETH_UP"
        else ("UP", "DOWN")
    )
    return {
        "variant": variant,
        "btc_side": btc_side,
        "eth_side": eth_side,
        "eligible": eligible,
        "cost_per_share": cost,
        "filled_shares": 2.5,
        "btc_vwap": 0.3,
        "eth_vwap": 0.5,
        "total_cost": 2.0,
    }


def test_choose_trial_fixed_and_cheapest() -> None:
    trials = [
        trial("BTC_DOWN_ETH_UP", 0.88),
        trial("BTC_UP_ETH_DOWN", 0.82),
    ]
    assert choose_trial(trials, "BTC_DOWN_ETH_UP")["variant"] == "BTC_DOWN_ETH_UP"
    assert choose_trial(trials, "CHEAPEST_ELIGIBLE")["variant"] == "BTC_UP_ETH_DOWN"
    assert (
        choose_trial(
            [trial("BTC_DOWN_ETH_UP", 0.88, eligible=False)],
            "CHEAPEST_ELIGIBLE",
        )
        is None
    )


def test_build_plan_preserves_equal_shares_and_pair_budget() -> None:
    plan = build_canary_plan(
        chosen=trial("BTC_DOWN_ETH_UP", 0.8),
        btc=market("BTCUSDT", 1),
        eth=market("ETHUSDT", 2),
        max_leg_reprice=Decimal("0.01"),
    )
    assert plan.target_shares == Decimal("2.5")
    assert plan.legs[0].side == "DOWN"
    assert plan.legs[1].side == "UP"
    assert plan.legs[0].requested_stake == Decimal("0.750000000000000000")
    assert plan.legs[1].requested_stake == Decimal("1.250000000000000000")
    assert (
        sum((leg.requested_stake for leg in plan.legs), Decimal("0"))
        == Decimal("2")
    )


class QuoteClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def server_timestamp_ms(self) -> int:
        return 1_000_000

    def get_quote(self, **kwargs):
        token = kwargs["token_id"]
        amount = int(kwargs["amount_in_wei"])
        price = Decimal("0.30") if "BTC" in token else Decimal("0.50")
        output = int((Decimal(amount) / price).to_integral_value())
        self.calls.append((token, amount))
        return {
            "quoteId": f"q-{len(self.calls)}",
            "side": "BUY",
            "orderType": "LIMIT",
            "tokenId": token,
            "amountIn": str(amount),
            "amountOut": str(output),
            "averagePrice": str(price),
            "expireAt": 1_010_000,
        }


def test_quote_only_requotes_both_legs_and_keeps_equal_shares() -> None:
    btc = market("BTCUSDT", 1)
    eth = market("ETHUSDT", 2)
    plan = CanaryPlan(
        variant="BTC_DOWN_ETH_UP",
        btc_market=btc,
        eth_market=eth,
        cost_per_share=Decimal("0.816"),
        target_shares=Decimal("2.5"),
        legs=(
            CanaryLeg(
                "BTC",
                "DOWN",
                btc.market_id,
                "BTC-token",
                200,
                Decimal("0.30"),
                Decimal("0.31"),
                Decimal("0.75"),
            ),
            CanaryLeg(
                "ETH",
                "UP",
                eth.market_id,
                "ETH-token",
                200,
                Decimal("0.50"),
                Decimal("0.51"),
                Decimal("1.25"),
            ),
        ),
    )
    client = QuoteClient()
    quotes, cost = quote_equal_share_pair(
        client,
        wallet_address="wallet",
        plan=plan,
        pair_budget=Decimal("2.00"),
        max_total_cost_per_share=Decimal("0.98"),
        slippage_bps=100,
    )
    assert len(client.calls) == 4
    outputs = [int(item["amountOut"]) for item in quotes]
    assert (max(outputs) - min(outputs)) / min(outputs) <= 0.0025
    assert cost < Decimal("0.98")


class PreflightClient:
    def __init__(self, *, active_orders=False, active_positions=0):
        self.active = active_orders
        self.positions = active_positions

    def wallets(self):
        return {"wallets": [{"walletAddress": "addr", "walletId": "wid"}]}

    def quota_status(self):
        return {"remainingDailyLimit": "100"}

    def payment_option_balances(self):
        return {
            "items": [
                {
                    "enabled": True,
                    "accountType": "SPOT",
                    "availableBalanceDisplay": "2.10",
                }
            ]
        }

    def active_orders(self, *args, **kwargs):
        return {"orders": [{"orderId": "open"}]} if self.active else {"orders": []}

    def portfolio(self, *args, **kwargs):
        return {"activePositionsCount": self.positions}


def test_preflight_requires_clean_wallet_and_buffer() -> None:
    addr, wid, available = preflight_wallet(
        PreflightClient(),
        account_type="SPOT",
        required_balance=Decimal("2.10"),
    )
    assert (addr, wid, available) == ("addr", "wid", Decimal("2.10"))
    with pytest.raises(RuntimeError, match="active Prediction orders"):
        preflight_wallet(
            PreflightClient(active_orders=True),
            account_type="SPOT",
            required_balance=Decimal("2.10"),
        )


class PlaceClient:
    def __init__(self) -> None:
        self.calls = 0

    def place_limit_order(self, **kwargs):
        self.calls += 1
        if kwargs["quote_id"] == "bad":
            raise ValueError("rejected")
        return {"orderId": "ok-order"}


def test_pair_placement_attempts_each_leg_once_and_never_retries() -> None:
    btc = market("BTCUSDT", 1)
    eth = market("ETHUSDT", 2)
    legs = (
        CanaryLeg(
            "BTC",
            "DOWN",
            btc.market_id,
            "btc",
            200,
            Decimal("0.3"),
            Decimal("0.31"),
            Decimal("0.75"),
        ),
        CanaryLeg(
            "ETH",
            "UP",
            eth.market_id,
            "eth",
            200,
            Decimal("0.5"),
            Decimal("0.51"),
            Decimal("1.25"),
        ),
    )
    plan = CanaryPlan(
        "BTC_DOWN_ETH_UP",
        btc,
        eth,
        Decimal("0.8"),
        Decimal("2.5"),
        legs,
    )
    client = PlaceClient()
    results = place_pair(
        client,
        wallet_address="addr",
        wallet_id="wid",
        account_type="SPOT",
        slippage_bps=100,
        plan=plan,
        quotes=({"quoteId": "good"}, {"quoteId": "bad"}),
    )
    assert client.calls == 2
    assert sorted(result["status"] for result in results) == [
        "REJECTED",
        "SUBMITTED",
    ]


def args(mode="dry-run", execute_live=False) -> argparse.Namespace:
    return argparse.Namespace(
        pair_budget_usdt=Decimal("2.00"),
        balance_buffer_usdt=Decimal("0.10"),
        max_total_cost=Decimal("0.98"),
        max_leg_reprice=Decimal("0.01"),
        entry_window_seconds=10.0,
        entry_seconds_left=180.0,
        slippage_bps=100,
        mode=mode,
        execute_live=execute_live,
    )


def test_live_requires_two_unlocks(monkeypatch) -> None:
    with pytest.raises(SystemExit, match="--execute-live"):
        validate_args(args(mode="live"))
    monkeypatch.delenv(LIVE_CONFIRM_ENV, raising=False)
    with pytest.raises(SystemExit, match=LIVE_CONFIRM_ENV):
        validate_args(args(mode="live", execute_live=True))
    monkeypatch.setenv(LIVE_CONFIRM_ENV, LIVE_CONFIRM_VALUE)
    validate_args(args(mode="live", execute_live=True))


def test_store_deduplicates_one_round(tmp_path) -> None:
    store = CanaryStore(tmp_path / "canary.db")
    first = store.begin(
        mode="dry-run",
        status="PLANNED",
        btc_market_id=1,
        eth_market_id=2,
        pair_budget=Decimal("2"),
    )
    second = store.begin(
        mode="dry-run",
        status="PLANNED",
        btc_market_id=1,
        eth_market_id=2,
        pair_budget=Decimal("2"),
    )
    assert first == second
    assert store.has_round(1, 2)
    store.close()
