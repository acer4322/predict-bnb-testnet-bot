from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "tools" / "wallet_chain_forensics.py"
spec = importlib.util.spec_from_file_location("wallet_chain_forensics", MODULE_PATH)
assert spec and spec.loader
wcf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wcf)

WALLET = "0x9ddbdc8bdf41ec79cd4f5c4dec14050f4b591a18"
OTHER = "0x1111111111111111111111111111111111111111"
EXCHANGE = "0x6beb5a40c032afc305961162d8204cda16decfa5"


def topic32_address(address: str) -> str:
    return wcf.address_topic(address)


def word(value: int) -> str:
    return f"{value:064x}"


def order_filled_log(
    *,
    maker_asset: int,
    taker_asset: int,
    maker_amount: int,
    taker_amount: int,
    taker: str,
    block: int = 100,
    log_index: int = 1,
) -> dict:
    return {
        "address": EXCHANGE,
        "topics": [
            wcf.ORDER_FILLED_TOPIC,
            "0x" + "22" * 32,
            topic32_address(WALLET),
            topic32_address(taker),
        ],
        "data": "0x" + "".join(
            [
                word(maker_asset),
                word(taker_asset),
                word(maker_amount),
                word(taker_amount),
                word(0),
            ]
        ),
        "blockNumber": hex(block),
        "logIndex": hex(log_index),
        "transactionHash": "0x" + "33" * 32,
    }


def test_address_topic_roundtrip():
    assert wcf.topic_address(wcf.address_topic(WALLET)) == WALLET


def test_decode_buy_and_taker_role():
    log = order_filled_log(
        maker_asset=0,
        taker_asset=12345,
        maker_amount=6 * wcf.WEI,
        taker_amount=10 * wcf.WEI,
        taker=EXCHANGE,
    )
    row = wcf.decode_order_filled(log, WALLET)
    assert row["action"] == "BUY"
    assert row["token_id"] == "12345"
    assert row["executed_price"] == 0.6
    assert row["shares"] == 10.0
    assert row["collateral_amount"] == 6.0
    assert row["execution_role"] == "TAKER"


def test_decode_sell_and_maker_role():
    log = order_filled_log(
        maker_asset=98765,
        taker_asset=0,
        maker_amount=10 * wcf.WEI,
        taker_amount=7 * wcf.WEI,
        taker=OTHER,
    )
    row = wcf.decode_order_filled(log, WALLET)
    assert row["action"] == "SELL"
    assert row["token_id"] == "98765"
    assert row["executed_price"] == 0.7
    assert row["shares"] == 10.0
    assert row["collateral_amount"] == 7.0
    assert row["execution_role"] == "MAKER"


def test_crypto_enrichment_and_summary_pattern():
    rows = []
    token_meta = {}
    for i in range(20):
        token = str(1000 + i)
        log = order_filled_log(
            maker_asset=0,
            taker_asset=int(token),
            maker_amount=6 * wcf.WEI,
            taker_amount=10 * wcf.WEI,
            taker=EXCHANGE,
            block=100 + i,
            log_index=i,
        )
        row = wcf.decode_order_filled(log, WALLET)
        row["block_timestamp"] = 1_785_000_000 + i * 300
        row["executed_at"] = f"2026-07-23T00:{i % 60:02d}:00+00:00"
        rows.append(row)
        token_meta[token] = {
            "market_topic_id": str(i),
            "market_id": str(i),
            "market_title": f"BTC market {i}",
            "chart_type": "CRYPTO_UP_DOWN",
            "symbol": "BTCUSDT",
            "start_ms": (row["block_timestamp"] - 240) * 1000,
            "end_ms": (row["block_timestamp"] + 60) * 1000,
            "outcome": "UP",
        }

    wcf.enrich_rows(rows, token_meta)
    summary = wcf.summarize(rows)
    assert summary["market_metadata"]["crypto_share_of_resolved"] == 1.0
    assert summary["market_metadata"]["symbol_market_counts"]["BTCUSDT"] == 20
    assert summary["first_buy_price"]["top_bin_concentration"] == 1.0
    assert any("crypto-market specialization" in item for item in summary["candidate_patterns"])
    assert any("first BUY price concentration" in item for item in summary["candidate_patterns"])
