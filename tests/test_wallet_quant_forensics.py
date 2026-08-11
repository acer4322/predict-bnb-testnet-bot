from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "tools" / "wallet_quant_forensics.py"
spec = importlib.util.spec_from_file_location("wallet_quant_forensics", MODULE_PATH)
assert spec and spec.loader
wqf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wqf)

ADDRESS = "0x9ddbdc8bdf41ec79cd4f5c4dec14050f4b591a18"
OTHER = "0x1111111111111111111111111111111111111111"


def match(*, market_id=1, when="2026-08-12T00:04:30Z", quote="Bid", outcome="UP", makers=None):
    return {
        "market": {"id": market_id, "title": f"BTC market {market_id}", "variantData": {"type": "CRYPTO_UP_DOWN"}},
        "taker": {"quoteType": quote, "amount": "10", "price": "0.6", "outcome": {"name": outcome}, "signer": ADDRESS},
        "makers": makers or [{"quoteType": "Ask", "amount": "10", "price": "0.6", "outcome": {"name": outcome}, "signer": OTHER}],
        "amountFilled": "10",
        "priceExecuted": "0.6",
        "transactionHash": f"0x{market_id:064x}",
        "executedAt": when,
    }


def test_normalizers_support_decimal_and_wei():
    assert wqf.normalize_price("0.63") == 0.63
    assert wqf.normalize_price("630000000000000000") == 0.63
    assert wqf.normalize_amount("10000000000000000000") == 10.0


def test_extract_taker_and_multi_maker_size_confidence():
    rows = wqf.extract_wallet_fills([match()], ADDRESS)
    assert len(rows) == 1
    assert rows[0]["role"] == "TAKER"
    assert rows[0]["action"] == "BUY"
    assert rows[0]["filled_shares"] == 10.0

    makers = [
        {"quoteType": "Bid", "amount": "3", "price": "0.6", "outcome": {"name": "UP"}, "signer": ADDRESS},
        {"quoteType": "Bid", "amount": "7", "price": "0.6", "outcome": {"name": "UP"}, "signer": OTHER},
    ]
    event = match(makers=makers)
    event["taker"]["signer"] = OTHER
    rows = wqf.extract_wallet_fills([event], ADDRESS)
    assert rows[0]["role"] == "MAKER"
    assert rows[0]["filled_shares"] is None
    assert rows[0]["size_confidence"] == "aggregate_only_multi_maker"


def test_infer_end_time_from_local_observations(tmp_path: Path):
    path = tmp_path / "obs.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "market_id", "seconds_left"])
        writer.writeheader()
        writer.writerow({"timestamp": "2026-08-12T00:04:00+00:00", "market_id": "1", "seconds_left": "60"})
        writer.writerow({"timestamp": "2026-08-12T00:04:30+00:00", "market_id": "1", "seconds_left": "30"})
    ends = wqf.infer_market_end_times_from_observations(path)
    rows = wqf.extract_wallet_fills([match()], ADDRESS, inferred_end_ms=ends)
    assert abs(rows[0]["seconds_left"] - 30.0) < 0.001
    assert rows[0]["timing_basis"] == "local_observation_inferred_end"


def test_summary_detects_repeatable_fingerprint():
    rows = []
    for i in range(30):
        event = match(market_id=i + 1, when=f"2026-08-12T00:{i % 5:02d}:30Z")
        extracted = wqf.extract_wallet_fills([event], ADDRESS)
        extracted[0]["seconds_left"] = 34.0
        extracted[0]["notional_usdt"] = 6.0
        rows.extend(extracted)
    summary = wqf.summarize_fills(rows)
    assert summary["first_buy_price"]["top_bin_concentration"] == 1.0
    assert summary["first_buy_seconds_left"]["top_bin_concentration"] == 1.0
    assert summary["exact_notional"]["mode_concentration"] == 1.0
    assert any("entry timing" in item for item in summary["candidate_patterns"])
    assert any("entry price" in item for item in summary["candidate_patterns"])
