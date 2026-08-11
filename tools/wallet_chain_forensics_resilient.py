from __future__ import annotations

"""Resilient launcher for wallet_chain_forensics.

Keeps the main forensics implementation unchanged while adding transport retry
and automatic BSC RPC failover. This is intentionally read-only.
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

DEFAULT_FALLBACKS = (
    "https://bsc.drpc.org",
    "https://bsc-rpc.publicnode.com",
)


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
    )
    return any(marker in text for marker in transient_markers)


class ResilientJsonRpcClient(core.JsonRpcClient):
    def __init__(self, url: str, timeout: float = 30.0) -> None:
        configured = [url]
        env_fallbacks = os.environ.get("BSC_RPC_FALLBACK_URLS", "")
        if env_fallbacks:
            configured.extend(
                value.strip() for value in env_fallbacks.replace(";", ",").split(",")
            )
        configured.extend(DEFAULT_FALLBACKS)
        self.urls = _unique(configured)
        self._url_index = 0
        self.retry_rounds = max(1, int(os.environ.get("BSC_RPC_RETRY_ROUNDS", "3")))
        resolved_timeout = max(
            5.0, float(os.environ.get("BSC_RPC_TIMEOUT", str(timeout)))
        )
        super().__init__(self.urls[0], timeout=resolved_timeout)

    @property
    def active_url(self) -> str:
        return self.urls[self._url_index % len(self.urls)]

    def _rotate(self) -> None:
        self._url_index = (self._url_index + 1) % len(self.urls)
        self.url = self.active_url

    def call(self, method: str, params: list[object]) -> object:
        attempts = max(1, len(self.urls) * self.retry_rounds)
        last_error: Exception | None = None
        for attempt in range(attempts):
            self.url = self.active_url
            try:
                return super().call(method, params)
            except core.RpcError as exc:
                if not _retryable_rpc_error(exc):
                    raise
                last_error = exc
                current = self.active_url
                self._rotate()
                next_url = self.active_url
                print(
                    f"warning: transient RPC failure on {current} during {method}: {exc}; "
                    f"retrying via {next_url}",
                    flush=True,
                )
                if attempt + 1 < attempts:
                    time.sleep(min(0.5 * (2 ** min(attempt, 3)), 4.0))
        if last_error is not None:
            raise core.RpcError(
                f"all configured BSC RPC endpoints failed after {attempts} attempts; "
                f"last error: {last_error}"
            ) from last_error
        raise core.RpcError("RPC request failed without a captured error")


# The original main() resolves JsonRpcClient at runtime, so replacing this one
# symbol gives the entire existing scanner retry/failover semantics without
# duplicating its chain parsing or analysis logic.
core.JsonRpcClient = ResilientJsonRpcClient


if __name__ == "__main__":
    core.main()
