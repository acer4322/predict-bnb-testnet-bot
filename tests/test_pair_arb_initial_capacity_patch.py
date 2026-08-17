from decimal import Decimal

from predict_bot import live_trading
from predict_bot.pair_arb_initial_capacity_patch import (
    PAIR_ARB_010_INITIAL_QUOTE_CAPACITY_RATIO,
)


WEI = 10**18


class _Ledger:
    def update_order(self, _local_id: int, **_values: object) -> None:
        return None


class _Client:
    def get_quote(self, **kwargs: object) -> dict[str, object]:
        amount = int(str(kwargs["amount_in_wei"]))
        token_id = str(kwargs["token_id"])
        return {
            "quoteId": f"quote-{token_id}",
            "tokenId": token_id,
            "amountIn": str(amount),
            "amountOut": str(amount),
            "averagePrice": "0.5",
            "expireAt": 9_999_999_999_999,
        }


def _accepted(strategy: str, ratio: Decimal) -> list[dict[str, object]]:
    requested = WEI
    quoted = int(Decimal(requested) * ratio)
    client = _Client()
    return [
        {
            "local_id": index,
            "selected_strategy": strategy,
            "side": side,
            "quote": {
                "quoteId": f"initial-{side}",
                "tokenId": side,
                "amountIn": str(quoted),
                "amountOut": str(quoted),
                "averagePrice": "0.5",
                "expireAt": 9_999_999_999_999,
            },
            "requested_amount_wei": requested,
            "expected_amount_out_wei": requested,
            "client": client,
            "wallet_address": "wallet",
            "token_id": side,
            "price_limit_text": "0.5",
            "fee_bps": 200,
        }
        for index, side in enumerate(("UP", "DOWN"), start=1)
    ]


def _engine() -> live_trading.LiveM0WEngine:
    engine = object.__new__(live_trading.LiveM0WEngine)
    engine.ledger = _Ledger()
    return engine


def test_pair_arb_010_accepts_repeated_68_7_percent_initial_capacity() -> None:
    requoted, error = _engine()._requote_qc_pair(
        _accepted("PAIR_ARB_010", Decimal("0.687")),
        minimum_capacity=live_trading.PAIR_ARB_MIN_QUOTE_CAPACITY_RATIO,
    )

    assert error == ""
    assert requoted is not None
    assert len(requoted) == 2


def test_pair_arb_010_still_rejects_below_65_percent() -> None:
    requoted, error = _engine()._requote_qc_pair(
        _accepted("PAIR_ARB_010", Decimal("0.649")),
        minimum_capacity=live_trading.PAIR_ARB_MIN_QUOTE_CAPACITY_RATIO,
    )

    assert requoted is None
    assert "64.9%" in error
    assert "65%" in error


def test_other_pair_strategy_does_not_inherit_lower_initial_gate() -> None:
    requoted, error = _engine()._requote_qc_pair(
        _accepted("PAIR_ARB_020", Decimal("0.687")),
        minimum_capacity=live_trading.PAIR_ARB_MIN_QUOTE_CAPACITY_RATIO,
    )

    assert requoted is None
    assert "68.7%" in error
    assert "70%" in error


def test_final_and_insurance_capacity_threshold_remain_70_percent() -> None:
    assert PAIR_ARB_010_INITIAL_QUOTE_CAPACITY_RATIO == Decimal("0.65")
    assert live_trading.PAIR_ARB_MIN_QUOTE_CAPACITY_RATIO == Decimal("0.70")
