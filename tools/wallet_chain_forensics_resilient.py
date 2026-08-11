from __future__ import annotations

"""Resilient launcher for wallet_chain_forensics.

Method-aware RPC routing:
- block/time/chain queries prefer official BNB endpoints, which have much higher
  public limits but intentionally disable eth_getLogs;
- eth_getLogs uses third-party log-capable endpoints only.

This keeps scarce third-party free-tier quota for the one method that actually
needs it. The launcher is read-only and does not alter trading code.
"""

import importlib.util
import os
import time
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("wallet_chain_forensics.py")
spec = importlib.util.spec_from_file_location("wallet_chain_forensics_core", MODULE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

# Official BNB endpoints are suitable for eth_chainId / eth_blockNumber /
# eth_getBlockByNumber. BNB docs state eth_getLogs is disabled on these public
# Mainnet endpoints, so they are never used for log scans here.
DEFAULT_BLOCK_RPCS = (
    "https://bsc-dataseed.bnbchain.org",
    "https://bsc-dataseed-public.bnbchain.org",
    "https://bsc-dataseed.nariox.org",
    "https://bsc-dataseed.defibit.io",
)

# Third-party endpoints used only for eth_getLogs. User-provided BSC_RPC_URL or
# BSC_RPC_FALLBACK_URLS are placed ahead of these defaults.
DEFAULT_LOG_RPCS = (
    "https://bsc-rpc.publicnode.com",
    "https://bsc.drpc.org",
)


def _split_urls(value: str) -> list[str]:
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        value = str(value or "").strip().rstrip("/")
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _retryable_rpc_error(exc: Exception) -> bool:
    text = str(exc).lower()
    if "rpc transport failed" in text:
        return True
    if "rpc http 408" in text or "rpc http 429" in text:
        return True
    if any(f"rpc http {status}" in text for status in (500, 502, 503, 504, 520, 522, 524)):
        return True
    transient_markers = (
        "timeout",
        "timed out",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "remote end closed",
        "rate limit",
        "too many requests",
        "public endpoint rate limit",
    )
    return any(marker in text for marker in transient_markers)


class ResilientJsonRpcClient(core.JsonRpcClient):
    def __init__(self, url: str, timeout: float = 30.0) -> None:
        user_primary = str(url or "").strip()
        env_fallbacks = _split_urls(os.environ.get("BSC_RPC_FALLBACK_URLS", ""))
        env_block = _split_urls(os.environ.get("BSC_BLOCK_RPC_URLS", ""))
        env_logs = _split_urls(os.environ.get("BSC_LOG_RPC_URLS", ""))

        self.block_urls = _unique(env_block + list(DEFAULT_BLOCK_RPCS) + [user_primary] + env_fallbacks)
        self.log_urls = _unique(env_logs + [user_primary] + env_fallbacks + list(DEFAULT_LOG_RPCS))
        self.retry_rounds = max(1, int(os.environ.get("BSC_RPC_RETRY_ROUNDS", "3")))
        self.retry_base_sleep = max(0.1, float(os.environ.get("BSC_RPC_RETRY_SLEEP", "0.75")))
        resolved_timeout = max(5.0, float(os.environ.get("BSC_RPC_TIMEOUT", str(timeout))))
        initial = self.block_urls[0] if self.block_urls else user_primary
        super().__init__(initial, timeout=resolved_timeout)
        self._method_cursor: dict[str, int] = {"block": 0, "logs": 0}

    def _pool_for(self, method: str) -> tuple[str, list[str]]:
        if method == "eth_getLogs":
            return "logs", self.log_urls
        return "block", self.block_urls

    def _attempt_call(self, method: str, params: list[object], url: str) -> object:
        self.url = url
        return super().call(method, params)

    def call(self, method: str, params: list[object]) -> object:
        pool_name, urls = self._pool_for(method)
        if not urls:
            raise core.RpcError(f"no RPC endpoints configured for {pool_name} requests")

        start = self._method_cursor.get(pool_name, 0) % len(urls)
        attempts = max(1, len(urls) * self.retry_rounds)
        last_error: Exception | None = None

        for attempt in range(attempts):
            idx = (start + attempt) % len(urls)
            endpoint = urls[idx]
            try:
                result = self._attempt_call(method, params, endpoint)
                self._method_cursor[pool_name] = idx
                return result
            except core.RpcError as exc:
                # Range/result-size errors for eth_getLogs are deliberately not
                # retried here. The core scanner will split the block range.
                if not _retryable_rpc_error(exc):
                    raise
                last_error = exc
                next_endpoint = urls[(idx + 1) % len(urls)]
                print(
                    f"warning: transient {pool_name} RPC failure on {endpoint} during {method}: {exc}; "
                    f"retrying via {next_endpoint}",
                    flush=True,
                )
                if attempt + 1 < attempts:
                    delay = min(self.retry_base_sleep * (2 ** min(attempt // max(1, len(urls)), 3)), 6.0)
                    time.sleep(delay)

        raise core.RpcError(
            f"all configured {pool_name} RPC endpoints failed after {attempts} attempts; "
            f"last error: {last_error}"
        ) from last_error


# core.main() resolves JsonRpcClient at runtime, so replacing this symbol gives
# the existing scanner method-aware routing without duplicating parsing logic.
core.JsonRpcClient = ResilientJsonRpcClient


if __name__ == "__main__":
    core.main()
