from __future__ import annotations

import importlib.util
from pathlib import Path


def _load(name: str, rel: str):
    path = Path(__file__).resolve().parents[1] / rel
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pair = _load("profile_target_pair_cycles", "tools/profile_target_pair_cycles.py")
inv = _load("profile_target_inventory_objective", "tools/profile_target_inventory_objective.py")


def test_pair_scope_recognizes_btc_5m_and_eth_15m() -> None:
    assert pair.market_descriptor("Bitcoin Up or Down - August 12, 10AM-10:05AM ET") == ("BTC", 5)
    assert pair.market_descriptor("Ethereum Up or Down - August 12, 10AM-10:15AM ET") == ("ETH", 15)
    assert pair.is_crypto_duration("Bitcoin Up or Down - August 12, 10AM-10:05AM ET", {"BTC"}, {5})
    assert not pair.is_crypto_duration("Bitcoin Up or Down - August 12, 10AM-10:15AM ET", {"BTC"}, {5})


def test_inventory_scope_recognizes_btc_5m_and_bnb_15m() -> None:
    assert inv.market_descriptor("BTC Up or Down - August 12, 10AM-10:05AM ET") == ("BTC", 5)
    assert inv.market_descriptor("BNB Up or Down - August 12, 10AM-10:15AM ET") == ("BNB", 15)
    assert inv.parse_assets("bitcoin,eth,bnb") == {"BTC", "ETH", "BNB"}
    assert inv.parse_durations("5,15m") == {5, 15}
