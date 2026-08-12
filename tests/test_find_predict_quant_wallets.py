from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "find_predict_quant_wallets.py"
SPEC = importlib.util.spec_from_file_location("find_predict_quant_wallets", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def _wei(value: float) -> str:
    return str(int(round(value * 10**18)))


def test_crypto_slug_and_seconds_left() -> None:
    match = {
        "executedAt": "2026-08-12T00:04:50.000Z",
        "market": {
            "id": 123,
            "categorySlug": "eth-updown-5m-1786492800",
            "title": "Ethereum Up or Down",
            "marketVariant": "CRYPTO_UP_DOWN",
        },
    }
    meta = m.market_meta(match)
    assert meta["asset"] == "ETH"
    assert meta["durationMinutes"] == 5
    assert meta["isCrypto"] is True
    executed = m.iso_dt(match["executedAt"])
    assert executed is not None
    assert m.seconds_left(meta, executed) == 10.0


def test_match_parses_maker_and_taker() -> None:
    maker = "0x" + "1" * 40
    taker = "0x" + "2" * 40
    match = {
        "executedAt": "2026-08-12T00:01:00.000Z",
        "market": {
            "id": 1,
            "categorySlug": "bnb-updown-5m-1786492800",
            "title": "BNB Up or Down",
            "marketVariant": "CRYPTO_UP_DOWN",
        },
        "makers": [
            {
                "signer": maker,
                "hash": "0xmaker",
                "amount": _wei(2.0),
                "price": _wei(0.40),
                "quoteType": "Bid",
                "outcome": {"name": "Up"},
            }
        ],
        "taker": {
            "signer": taker,
            "hash": "0xtaker",
            "amount": _wei(2.0),
            "price": _wei(0.60),
            "quoteType": "Bid",
            "outcome": {"name": "Down"},
        },
    }
    events = m.events_from_match(match)
    assert len(events) == 2
    maker_event = next(x for x in events if x.role == "MAKER")
    taker_event = next(x for x in events if x.role == "TAKER")
    assert maker_event.wallet == maker
    assert taker_event.wallet == taker
    assert maker_event.asset == "BNB"
    assert maker_event.cost_usdt == 0.8
    assert maker_event.counterparty_wallets == (taker,)
    assert taker_event.counterparty_wallets == (maker,)


def test_profiles_reward_maker_two_sided_replenishment() -> None:
    wallet = "0x" + "a" * 40
    counterparty = "0x" + "b" * 40
    events = []
    base = datetime(2026, 8, 12, tzinfo=timezone.utc)
    for market_id in range(1, 11):
        for outcome, price in (("UP", 0.45), ("DOWN", 0.45)):
            for parent in range(2):
                events.append(
                    m.Event(
                        wallet=wallet,
                        role="MAKER",
                        market_id=market_id,
                        market_title="test",
                        category_slug=f"eth-updown-5m-{1786492800 + market_id * 300}",
                        asset="ETH",
                        duration_minutes=5,
                        is_crypto=True,
                        executed_at=base,
                        seconds_left=290.0,
                        outcome=outcome,
                        quote_type="BID",
                        order_hash=f"{market_id}:{outcome}:{parent}",
                        shares=2.0,
                        price=price,
                        cost_usdt=0.9,
                        potential_profit_usdt=1.1,
                        counterparty_wallets=(counterparty,),
                    )
                )
    profiles = m.build_profiles(
        events,
        assets={"ETH"},
        durations={5},
        min_fills=1,
        min_markets=1,
        excluded=set(),
    )
    assert len(profiles) == 1
    p = profiles[0]
    assert p["makerShare"] == 1.0
    assert p["twoSidedBidMarketShare"] == 1.0
    assert p["repeatedSameOutcomeBidMarketShare"] == 1.0
    assert "MARKET_MAKER" in p["tags"]
    assert "COMPLEMENT_CANDIDATE" in p["tags"]
    assert "REPLENISHMENT_BOT" in p["tags"]
    assert 0.0 <= p["quantCandidateScore"] <= 100.0
