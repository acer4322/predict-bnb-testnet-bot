from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "tools" / "wallet_chain_forensics_resilient.py"
spec = importlib.util.spec_from_file_location("wallet_chain_forensics_resilient", MODULE_PATH)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_retryable_classification():
    assert mod._retryable_rpc_error(Exception("RPC transport failed: TimeoutError"))
    assert mod._retryable_rpc_error(Exception("RPC HTTP 429: rate limit"))
    assert mod._retryable_rpc_error(Exception("RPC HTTP 503: unavailable"))
    assert not mod._retryable_rpc_error(Exception("query returned more than 10000 results"))


def test_failover_rotates_endpoint(monkeypatch):
    calls: list[str] = []

    def fake_call(self, method, params):
        calls.append(self.url)
        if len(calls) == 1:
            raise mod.core.RpcError("RPC transport failed: TimeoutError: timed out")
        return "0x38"

    monkeypatch.setattr(mod.core.JsonRpcClient.__mro__[1], "call", fake_call)
    monkeypatch.setenv("BSC_RPC_FALLBACK_URLS", "https://fallback.example")
    monkeypatch.setenv("BSC_RPC_RETRY_ROUNDS", "1")
    client = mod.ResilientJsonRpcClient("https://primary.example", timeout=5)
    result = client.call("eth_chainId", [])
    assert result == "0x38"
    assert calls[0] == "https://primary.example"
    assert calls[1] == "https://fallback.example"
