from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import math
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CHAIN_ID = 56
DEFAULT_RPC_URL = "https://bsc-rpc.publicnode.com"
DEFAULT_BINANCE_URL = "https://api.binance.com"
WEI = 10**18
ORDER_FILLED_TOPIC = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"

# Predict.fun BNB Mainnet exchange deployments. Source: Predict deployed-contract docs,
# checked 2026-08-12 (docs modified 2026-06-18).
EXCHANGES: dict[str, dict[str, Any]] = {
    "0x6beb5a40c032afc305961162d8204cda16decfa5": {
        "name": "CTFExchange", "yield_bearing": True, "neg_risk": False
    },
    "0x8a289d458f5a134ba40015085a8f50ffb681b41d": {
        "name": "NegRiskCtfExchange", "yield_bearing": True, "neg_risk": True
    },
    "0x8bc070bedab741406f4b1eb65a72bee27894b689": {
        "name": "CTFExchange", "yield_bearing": False, "neg_risk": False
    },
    "0x365fb81bd4a24d6303cd2f19c349de6894d8d58a": {
        "name": "NegRiskCtfExchange", "yield_bearing": False, "neg_risk": True
    },
}


def normalize_address(value: str) -> str:
    value = str(value or "").strip().lower()
    if not value.startswith("0x") or len(value) != 42:
        raise ValueError(f"invalid EVM address: {value!r}")
    int(value[2:], 16)
    return value


def address_topic(address: str) -> str:
    return "0x" + "0" * 24 + normalize_address(address)[2:]


def topic_address(topic: str) -> str:
    raw = str(topic).lower().removeprefix("0x")
    if len(raw) != 64:
        raise ValueError("invalid address topic")
    return "0x" + raw[-40:]


def hex_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value), 16)


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class RpcError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class JsonRpcClient:
    def __init__(self, url: str, timeout: float = 30.0) -> None:
        self.url = url
        self.timeout = timeout
        self._request_id = 0
        self._block_cache: dict[int, dict[str, Any]] = {}

    def call(self, method: str, params: list[Any]) -> Any:
        self._request_id += 1
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
        ).encode()
        req = urllib.request.Request(
            self.url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "predict-wallet-chain-forensics/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                body = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            raise RpcError(f"RPC HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RpcError(f"RPC transport failed: {type(exc).__name__}: {exc}") from exc
        if not isinstance(body, dict):
            raise RpcError("RPC returned a non-object response")
        if body.get("error"):
            error = body["error"] if isinstance(body["error"], dict) else {}
            raise RpcError(str(error.get("message") or body["error"]), code=error.get("code"))
        return body.get("result")

    def chain_id(self) -> int:
        return hex_int(self.call("eth_chainId", []))

    def latest_block_number(self) -> int:
        return hex_int(self.call("eth_blockNumber", []))

    def block(self, number: int) -> dict[str, Any]:
        if number not in self._block_cache:
            payload = self.call("eth_getBlockByNumber", [hex(number), False])
            if not isinstance(payload, dict):
                raise RpcError(f"block {number} not found")
            self._block_cache[number] = payload
        return self._block_cache[number]

    def block_timestamp(self, number: int) -> int:
        return hex_int(self.block(number)["timestamp"])

    def find_first_block_at_or_after(self, timestamp_s: int, latest: int | None = None) -> int:
        hi = self.latest_block_number() if latest is None else int(latest)
        if self.block_timestamp(hi) < timestamp_s:
            return hi
        lo = 0
        while lo < hi:
            mid = (lo + hi) // 2
            if self.block_timestamp(mid) < timestamp_s:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def get_logs(self, filter_obj: dict[str, Any]) -> list[dict[str, Any]]:
        result = self.call("eth_getLogs", [filter_obj])
        if not isinstance(result, list):
            raise RpcError("eth_getLogs returned a non-list response")
        return [row for row in result if isinstance(row, dict)]


def _filter(
    from_block: int, to_block: int, addresses: list[str], topics: list[Any]
) -> dict[str, Any]:
    return {
        "fromBlock": hex(from_block),
        "toBlock": hex(to_block),
        "address": addresses if len(addresses) > 1 else addresses[0],
        "topics": topics,
    }


def _get_logs_adaptive(
    rpc: JsonRpcClient,
    from_block: int,
    to_block: int,
    addresses: list[str],
    topics: list[Any],
    *,
    depth: int = 0,
    max_depth: int = 22,
) -> list[dict[str, Any]]:
    try:
        return rpc.get_logs(_filter(from_block, to_block, addresses, topics))
    except RpcError:
        if from_block < to_block and depth < max_depth:
            mid = (from_block + to_block) // 2
            return _get_logs_adaptive(
                rpc, from_block, mid, addresses, topics,
                depth=depth + 1, max_depth=max_depth,
            ) + _get_logs_adaptive(
                rpc, mid + 1, to_block, addresses, topics,
                depth=depth + 1, max_depth=max_depth,
            )
        if len(addresses) > 1:
            rows: list[dict[str, Any]] = []
            for address in addresses:
                rows.extend(
                    _get_logs_adaptive(
                        rpc, from_block, to_block, [address], topics,
                        depth=depth + 1, max_depth=max_depth,
                    )
                )
            return rows
        raise


def scan_wallet_order_filled(
    rpc: JsonRpcClient,
    wallet: str,
    from_block: int,
    to_block: int,
    *,
    chunk_blocks: int = 100_000,
) -> list[dict[str, Any]]:
    """Scan OrderFilled events whose indexed maker is the target wallet.

    Predict's SDK uses the Predict account as the order maker. Filtering only the
    indexed maker topic avoids double-counting the same active order through the
    counterparty-facing taker field in matched transactions.
    """
    addresses = list(EXCHANGES)
    maker_topic = address_topic(wallet)
    found: list[dict[str, Any]] = []
    cursor = int(from_block)
    while cursor <= to_block:
        end = min(to_block, cursor + max(1, int(chunk_blocks)) - 1)
        found.extend(
            _get_logs_adaptive(
                rpc,
                cursor,
                end,
                addresses,
                [ORDER_FILLED_TOPIC, None, maker_topic],
            )
        )
        cursor = end + 1
    dedup: dict[tuple[str, int], dict[str, Any]] = {}
    for log in found:
        key = (
            str(log.get("transactionHash") or ""),
            hex_int(log.get("logIndex") or "0x0"),
        )
        dedup[key] = log
    return sorted(
        dedup.values(),
        key=lambda row: (
            hex_int(row.get("blockNumber") or "0x0"),
            hex_int(row.get("logIndex") or "0x0"),
        ),
    )


def decode_order_filled(log: dict[str, Any], wallet: str) -> dict[str, Any]:
    topics = log.get("topics") or []
    if len(topics) < 4 or str(topics[0]).lower() != ORDER_FILLED_TOPIC:
        raise ValueError("not an OrderFilled log")
    maker = topic_address(topics[2])
    if maker != normalize_address(wallet):
        raise ValueError("OrderFilled maker does not match target wallet")
    taker = topic_address(topics[3])
    raw = str(log.get("data") or "0x").removeprefix("0x")
    if len(raw) < 64 * 5:
        raise ValueError("OrderFilled data is too short")
    words = [int(raw[index:index + 64], 16) for index in range(0, 64 * 5, 64)]
    maker_asset, taker_asset, maker_amount, taker_amount, fee = words
    contract = normalize_address(str(log.get("address") or ""))
    exchange = EXCHANGES.get(
        contract,
        {"name": "UNKNOWN", "yield_bearing": None, "neg_risk": None},
    )

    if maker_asset == 0 and taker_asset != 0:
        action = "BUY"
        token_id = str(taker_asset)
        collateral_raw = maker_amount
        shares_raw = taker_amount
        action_confidence = "exact_from_asset_ids"
    elif taker_asset == 0 and maker_asset != 0:
        action = "SELL"
        token_id = str(maker_asset)
        collateral_raw = taker_amount
        shares_raw = maker_amount
        action_confidence = "exact_from_asset_ids"
    else:
        action = "TOKEN_SWAP"
        token_id = ""
        collateral_raw = None
        shares_raw = None
        action_confidence = "non_collateral_match"

    price = (
        collateral_raw / shares_raw
        if collateral_raw is not None and shares_raw
        else None
    )
    # In the exchange matched-order path the active/taker order is emitted with
    # taker=address(this). Passive maker orders contain the active order maker.
    execution_role = "TAKER" if taker == contract else "MAKER"

    return {
        "block_number": hex_int(log.get("blockNumber") or "0x0"),
        "log_index": hex_int(log.get("logIndex") or "0x0"),
        "transaction_hash": str(log.get("transactionHash") or ""),
        "order_hash": str(topics[1]),
        "wallet": maker,
        "counterparty": taker,
        "exchange_contract": contract,
        "exchange_name": exchange["name"],
        "yield_bearing": exchange["yield_bearing"],
        "neg_risk": exchange["neg_risk"],
        "execution_role": execution_role,
        "action": action,
        "action_confidence": action_confidence,
        "token_id": token_id,
        "executed_price": price,
        "shares": shares_raw / WEI if shares_raw is not None else None,
        "collateral_amount": (
            collateral_raw / WEI if collateral_raw is not None else None
        ),
        "maker_asset_id": str(maker_asset),
        "taker_asset_id": str(taker_asset),
        "maker_amount_raw": str(maker_amount),
        "taker_amount_raw": str(taker_amount),
        "fee_raw": str(fee),
    }


def attach_block_times(rpc: JsonRpcClient, rows: list[dict[str, Any]]) -> None:
    cache: dict[int, tuple[int, str]] = {}
    for row in rows:
        block_number = int(row["block_number"])
        if block_number not in cache:
            ts = rpc.block_timestamp(block_number)
            cache[block_number] = (
                ts,
                datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            )
        row["block_timestamp"], row["executed_at"] = cache[block_number]


class BinanceMarketMetadataClient:
    """Optional metadata enrichment using the user's existing Binance HMAC key."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = DEFAULT_BINANCE_URL,
        timeout: float = 20.0,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._offset_ms: int | None = None

    def _json(
        self, url: str, *, headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        req = urllib.request.Request(
            url, headers=headers or {"Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            payload = json.load(response)
        if not isinstance(payload, dict):
            raise RuntimeError("unexpected Binance response")
        return payload

    def server_ms(self) -> int:
        now = int(time.time() * 1000)
        if self._offset_ms is None:
            payload = self._json(f"{self.base_url}/api/v3/time")
            self._offset_ms = int(payload["serverTime"]) - now
        return int(time.time() * 1000) + self._offset_ms

    def signed_get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        signed = dict(params)
        signed["recvWindow"] = 5000
        signed["timestamp"] = self.server_ms()
        query = urllib.parse.urlencode(signed)
        signature = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        return self._json(
            f"{self.base_url}{path}?{query}&signature={signature}",
            headers={
                "Accept": "application/json",
                "X-MBX-APIKEY": self.api_key,
                "User-Agent": "predict-wallet-chain-forensics/0.1",
            },
        )

    def token_metadata(
        self, *, since_ms: int | None, max_pages: int = 100
    ) -> dict[str, dict[str, Any]]:
        mapping: dict[str, dict[str, Any]] = {}
        offset = 0
        for _ in range(max(1, int(max_pages))):
            payload = self.signed_get(
                "/sapi/v1/w3w/wallet/prediction/market/list",
                {
                    "sortBy": "END_DATE",
                    "orderBy": "DESC",
                    "offset": offset,
                    "limit": 100,
                },
            )
            topics = payload.get("marketTopics") or []
            oldest_end: int | None = None
            for topic in topics:
                if not isinstance(topic, dict):
                    continue
                try:
                    end_ms = int(topic.get("endDate") or 0)
                except (TypeError, ValueError):
                    end_ms = 0
                if end_ms:
                    oldest_end = (
                        end_ms if oldest_end is None else min(oldest_end, end_ms)
                    )
                variant = (
                    topic.get("variantData")
                    if isinstance(topic.get("variantData"), dict)
                    else {}
                )
                for market in topic.get("markets") or []:
                    if not isinstance(market, dict):
                        continue
                    for outcome in market.get("outcomes") or []:
                        if (
                            not isinstance(outcome, dict)
                            or outcome.get("tokenId") is None
                        ):
                            continue
                        token = str(outcome["tokenId"])
                        mapping[token] = {
                            "market_topic_id": str(topic.get("marketTopicId") or ""),
                            "market_id": str(
                                market.get("marketId") or market.get("id") or ""
                            ),
                            "market_title": str(
                                topic.get("title") or market.get("title") or ""
                            ),
                            "chart_type": str(topic.get("chartType") or ""),
                            "symbol": str(
                                topic.get("symbol")
                                or variant.get("priceFeedSymbol")
                                or ""
                            ),
                            "start_ms": int(topic.get("startDate") or 0) or None,
                            "end_ms": end_ms or None,
                            "outcome": str(
                                outcome.get("name") or outcome.get("label") or ""
                            ),
                        }
            if not payload.get("hasMore") or not topics:
                break
            if (
                since_ms is not None
                and oldest_end is not None
                and oldest_end < since_ms - 86_400_000
            ):
                break
            offset += int(payload.get("limit") or 100)
        return mapping


def classify_market(meta: dict[str, Any] | None) -> str:
    if not meta:
        return "UNKNOWN"
    chart = str(meta.get("chart_type") or "").upper()
    if chart == "CRYPTO_UP_DOWN" or chart.startswith("CRYPTO"):
        return "CRYPTO"
    return "OTHER"


def enrich_rows(
    rows: list[dict[str, Any]], token_meta: dict[str, dict[str, Any]]
) -> None:
    empty = {
        "market_topic_id": "",
        "market_id": "",
        "market_title": "",
        "chart_type": "",
        "symbol": "",
        "start_ms": None,
        "end_ms": None,
        "outcome": "",
    }
    for row in rows:
        meta = token_meta.get(str(row.get("token_id") or ""))
        row.update(meta or empty)
        row["market_family"] = classify_market(meta)
        ts = row.get("block_timestamp")
        end_ms = row.get("end_ms")
        row["seconds_left"] = (
            (end_ms / 1000) - ts
            if ts is not None and end_ms
            else None
        )
        row["market_key"] = str(
            row.get("market_id") or row.get("token_id") or row.get("order_hash") or ""
        )


def percentile(values: list[float], q: float) -> float | None:
    values = sorted(v for v in values if math.isfinite(v))
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    pos = (len(values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return values[lo] if lo == hi else values[lo] * (hi - pos) + values[hi] * (pos - lo)


def concentration(
    values: list[float], width: float
) -> tuple[float | None, str | None]:
    bins = Counter(math.floor(v / width) for v in values if math.isfinite(v))
    if not bins:
        return None, None
    bucket, count = bins.most_common(1)[0]
    return count / sum(bins.values()), f"{bucket * width:.2f}-{(bucket + 1) * width:.2f}"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    direct = [r for r in rows if r.get("action") in {"BUY", "SELL"}]
    market_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in direct:
        market_groups[
            str(row.get("market_key") or row.get("token_id") or "")
        ].append(row)
    for group in market_groups.values():
        group.sort(
            key=lambda r: (r.get("block_timestamp") or 0, r.get("log_index") or 0)
        )

    first_buys = [
        next((r for r in group if r.get("action") == "BUY"), None)
        for group in market_groups.values()
    ]
    first_buys = [r for r in first_buys if r]
    first_prices = [
        float(r["executed_price"])
        for r in first_buys
        if r.get("executed_price") is not None
    ]
    first_tminus = [
        float(r["seconds_left"])
        for r in first_buys
        if r.get("seconds_left") is not None
        and float(r["seconds_left"]) >= -5
    ]
    notionals = [
        round(float(r["collateral_amount"]), 2)
        for r in direct
        if r.get("collateral_amount") is not None
    ]
    price_conc, price_bin = concentration(first_prices, 0.05)
    time_conc, time_bin = concentration(first_tminus, 10.0)
    size_counts = Counter(notionals)
    size_mode, size_count = (
        size_counts.most_common(1)[0] if size_counts else (None, 0)
    )
    size_conc = size_count / len(notionals) if notionals else None

    known_market: dict[str, str] = {}
    known_symbol: dict[str, str] = {}
    for key, group in market_groups.items():
        family = next(
            (
                str(r.get("market_family"))
                for r in group
                if r.get("market_family") in {"CRYPTO", "OTHER"}
            ),
            "UNKNOWN",
        )
        if family != "UNKNOWN":
            known_market[key] = family
        symbol = next((str(r.get("symbol")) for r in group if r.get("symbol")), "")
        if symbol:
            known_symbol[key] = symbol
    family_counts = Counter(known_market.values())
    known_n = sum(family_counts.values())
    crypto_share = family_counts.get("CRYPTO", 0) / known_n if known_n else None
    symbol_market_counts = Counter(known_symbol.values())

    roles = Counter(str(r.get("execution_role") or "UNKNOWN") for r in direct)
    taker_share = roles.get("TAKER", 0) / len(direct) if direct else None
    actions = Counter(str(r.get("action") or "UNKNOWN") for r in direct)
    contract_counts = Counter(str(r.get("exchange_contract") or "") for r in direct)

    times = sorted(
        int(r["block_timestamp"])
        for r in direct
        if r.get("block_timestamp") is not None
    )
    gaps = [b - a for a, b in zip(times, times[1:]) if b >= a]
    burst_2s = (
        sum(1 for gap in gaps if gap <= 2) / len(gaps) if gaps else None
    )
    hour_counts = Counter(
        datetime.fromtimestamp(ts, tz=timezone.utc).hour for ts in times
    )

    repeat_markets = 0
    sell_markets = 0
    both_action_markets = 0
    for group in market_groups.values():
        action_set = {r.get("action") for r in group}
        if "SELL" in action_set:
            sell_markets += 1
        if {"BUY", "SELL"}.issubset(action_set):
            both_action_markets += 1
        token_actions = Counter(
            (r.get("token_id"), r.get("action")) for r in group
        )
        if any(
            count >= 2 and action == "BUY"
            for (_, action), count in token_actions.items()
        ):
            repeat_markets += 1

    candidates: list[str] = []
    if known_n >= 10 and crypto_share is not None and crypto_share >= 0.80:
        candidates.append(
            f"crypto-market specialization: {crypto_share:.0%} of metadata-resolved traded markets are crypto"
        )
    if len(first_buys) >= 20 and price_conc is not None and price_conc >= 0.40:
        candidates.append(
            f"first BUY price concentration: {price_conc:.0%} fall in {price_bin}"
        )
    if len(first_tminus) >= 20 and time_conc is not None and time_conc >= 0.40:
        candidates.append(
            f"first BUY timing concentration: {time_conc:.0%} fall in T-{time_bin}s"
        )
    if len(notionals) >= 20 and size_conc is not None and size_conc >= 0.50:
        candidates.append(
            f"rule-like sizing: {size_conc:.0%} of direct fills have ${size_mode:.2f} collateral amount"
        )
    if len(direct) >= 50 and taker_share is not None and taker_share >= 0.70:
        candidates.append(
            f"taker-heavy execution: {taker_share:.0%} of order fills are active/taker-side"
        )
    if len(direct) >= 50 and taker_share is not None and taker_share <= 0.30:
        candidates.append(
            f"maker-heavy execution: {1 - taker_share:.0%} of order fills are passive/maker-side"
        )
    if len(market_groups) >= 20 and repeat_markets / len(market_groups) >= 0.40:
        candidates.append(
            f"same-token scaling: {repeat_markets / len(market_groups):.0%} of grouped markets contain repeated BUY fills"
        )
    if len(gaps) >= 50 and burst_2s is not None and burst_2s >= 0.40:
        candidates.append(
            f"highly bursty automation candidate: {burst_2s:.0%} of adjacent fills are <=2s apart"
        )

    return {
        "order_fill_events": len(rows),
        "direct_buy_sell_events": len(direct),
        "transactions": len({r.get("transaction_hash") for r in rows}),
        "unique_token_ids": len(
            {r.get("token_id") for r in direct if r.get("token_id")}
        ),
        "grouped_markets": len(market_groups),
        "action_counts": dict(actions),
        "execution_role_counts": dict(roles),
        "taker_share": taker_share,
        "market_metadata": {
            "resolved_markets": known_n,
            "family_counts": dict(family_counts),
            "crypto_share_of_resolved": crypto_share,
            "symbol_market_counts": dict(symbol_market_counts.most_common()),
        },
        "first_buy_price": {
            "count": len(first_prices),
            "median": statistics.median(first_prices) if first_prices else None,
            "p25": percentile(first_prices, 0.25),
            "p75": percentile(first_prices, 0.75),
            "top_0_05_bin": price_bin,
            "top_bin_concentration": price_conc,
        },
        "first_buy_seconds_left": {
            "count": len(first_tminus),
            "median": statistics.median(first_tminus) if first_tminus else None,
            "p25": percentile(first_tminus, 0.25),
            "p75": percentile(first_tminus, 0.75),
            "top_10s_bin": time_bin,
            "top_bin_concentration": time_conc,
        },
        "collateral_amount": {
            "count": len(notionals),
            "median": statistics.median(notionals) if notionals else None,
            "mode": size_mode,
            "mode_concentration": size_conc,
        },
        "behavior": {
            "markets_with_sell": sell_markets,
            "markets_with_buy_and_sell": both_action_markets,
            "markets_with_repeated_buys": repeat_markets,
            "adjacent_fill_gap_median_s": statistics.median(gaps) if gaps else None,
            "adjacent_fills_within_2s_share": burst_2s,
            "utc_hour_counts": {str(k): v for k, v in sorted(hour_counts.items())},
        },
        "exchange_contract_counts": dict(contract_counts),
        "candidate_patterns": candidates,
        "warnings": [
            "Crypto/other percentages are computed only from token IDs resolved through Binance Prediction metadata; unresolved on-chain markets stay UNKNOWN."
        ],
    }


def market_rollup(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("action") in {"BUY", "SELL"}:
            groups[
                str(row.get("market_key") or row.get("token_id") or "")
            ].append(row)
    result: list[dict[str, Any]] = []
    for key, group in groups.items():
        group.sort(
            key=lambda r: (r.get("block_timestamp") or 0, r.get("log_index") or 0)
        )
        buys = [r for r in group if r.get("action") == "BUY"]
        sells = [r for r in group if r.get("action") == "SELL"]
        buy_prices = [
            float(r["executed_price"])
            for r in buys
            if r.get("executed_price") is not None
        ]
        result.append(
            {
                "market_key": key,
                "market_id": next(
                    (r.get("market_id") for r in group if r.get("market_id")), ""
                ),
                "title": next(
                    (
                        r.get("market_title")
                        for r in group
                        if r.get("market_title")
                    ),
                    "",
                ),
                "family": next(
                    (
                        r.get("market_family")
                        for r in group
                        if r.get("market_family") != "UNKNOWN"
                    ),
                    "UNKNOWN",
                ),
                "symbol": next(
                    (r.get("symbol") for r in group if r.get("symbol")), ""
                ),
                "fills": len(group),
                "buys": len(buys),
                "sells": len(sells),
                "first_fill_at": group[0].get("executed_at"),
                "last_fill_at": group[-1].get("executed_at"),
                "first_buy_price": buys[0].get("executed_price") if buys else None,
                "first_buy_seconds_left": (
                    buys[0].get("seconds_left") if buys else None
                ),
                "avg_buy_price": statistics.mean(buy_prices) if buy_prices else None,
                "taker_fills": sum(
                    1 for r in group if r.get("execution_role") == "TAKER"
                ),
                "maker_fills": sum(
                    1 for r in group if r.get("execution_role") == "MAKER"
                ),
            }
        )
    result.sort(key=lambda r: str(r.get("first_fill_at") or ""))
    return result


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def render_report(
    address: str,
    summary: dict[str, Any],
    *,
    from_block: int,
    to_block: int,
    rpc_label: str,
    enriched: bool,
) -> str:
    meta = summary["market_metadata"]
    lines = [
        f"# Predict Wallet Chain Forensics — `{address}`",
        "",
        "## Coverage",
        "",
        f"- Blocks: **{from_block:,} → {to_block:,}**",
        f"- Wallet-owned `OrderFilled` events: **{summary['order_fill_events']}**",
        f"- Direct BUY/SELL events: **{summary['direct_buy_sell_events']}**",
        f"- Unique outcome token IDs: **{summary['unique_token_ids']}**",
        f"- Metadata-resolved markets: **{meta['resolved_markets']}**",
        f"- Binance metadata enrichment: **{'yes' if enriched else 'no'}**",
        "",
        "## Market selection",
        "",
        f"- Resolved Crypto share: **{meta['crypto_share_of_resolved']}**",
        f"- Families: `{meta['family_counts']}`",
        f"- Symbols: `{meta['symbol_market_counts']}`",
        "",
        "## Execution",
        "",
        f"- Action counts: `{summary['action_counts']}`",
        f"- Execution roles: `{summary['execution_role_counts']}`",
        f"- Taker share: **{summary['taker_share']}**",
        f"- First BUY median price: **{summary['first_buy_price']['median']}**",
        f"- First BUY median T-minus: **{summary['first_buy_seconds_left']['median']}**",
        f"- Collateral amount median/mode: **{summary['collateral_amount']['median']} / {summary['collateral_amount']['mode']}**",
        "",
        "## Candidate rules",
        "",
    ]
    lines.extend(
        [f"- {item}" for item in summary["candidate_patterns"]]
        or ["- No candidate crossed the conservative threshold yet."]
    )
    lines.extend(
        [
            "",
            "## Method notes",
            "",
            "- The scanner filters the indexed `maker` field, not `taker`, so matched orders are not double-counted from counterparty events.",
            "- BUY/SELL is decoded from `makerAssetId/takerAssetId`: collateral asset ID 0 → outcome token is BUY; the reverse is SELL.",
            "- TAKER is inferred when the event's `taker` address equals the emitting exchange contract, matching the exchange's active-order event path.",
            "- Market family/title/symbol are optional enrichment from the user's existing Binance Prediction HMAC API. Chain-only rows remain `UNKNOWN` instead of being guessed.",
            "- `collateral_amount` assumes Predict's 18-decimal order units; price is a ratio and is unaffected by that decimal assumption.",
            f"- RPC: `{rpc_label}` (credentials, if any, are never written to the report).",
        ]
    )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Predict.fun BNB-chain wallet forensics; no Predict API key required"
        )
    )
    parser.add_argument("address")
    parser.add_argument(
        "--days", type=float, default=14.0,
        help="lookback window when --from-block is omitted",
    )
    parser.add_argument(
        "--since-utc", help="ISO timestamp, e.g. 2026-08-01T00:00:00Z"
    )
    parser.add_argument("--from-block", type=int)
    parser.add_argument("--to-block", type=int)
    parser.add_argument("--chunk-blocks", type=int, default=100_000)
    parser.add_argument(
        "--rpc-url",
        help="RPC URL; prefer BSC_RPC_URL env if the URL contains a secret",
    )
    parser.add_argument("--rpc-url-env", default="BSC_RPC_URL")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/wallet_chain_forensics")
    )
    parser.add_argument("--no-binance-enrich", action="store_true")
    parser.add_argument("--binance-pages", type=int, default=100)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    wallet = normalize_address(args.address)
    rpc_url = args.rpc_url or os.environ.get(args.rpc_url_env) or DEFAULT_RPC_URL
    rpc = JsonRpcClient(rpc_url)
    if rpc.chain_id() != CHAIN_ID:
        raise SystemExit("RPC is not BNB Smart Chain mainnet (chainId 56)")
    latest = rpc.latest_block_number()
    to_block = min(args.to_block, latest) if args.to_block is not None else latest
    if args.from_block is not None:
        from_block = args.from_block
        since_dt = datetime.fromtimestamp(
            rpc.block_timestamp(from_block), tz=timezone.utc
        )
    else:
        since_dt = (
            parse_utc(args.since_utc)
            if args.since_utc
            else datetime.now(timezone.utc)
            - timedelta(days=max(0.01, args.days))
        )
        from_block = rpc.find_first_block_at_or_after(
            int(since_dt.timestamp()), latest=to_block
        )
    if from_block > to_block:
        raise SystemExit("from-block is after to-block")

    print(f"wallet={wallet} blocks={from_block}->{to_block}", flush=True)
    logs = scan_wallet_order_filled(
        rpc, wallet, from_block, to_block, chunk_blocks=args.chunk_blocks
    )
    rows = [decode_order_filled(log, wallet) for log in logs]
    attach_block_times(rpc, rows)

    token_meta: dict[str, dict[str, Any]] = {}
    enriched = False
    if not args.no_binance_enrich:
        key = os.environ.get("BINANCE_API_KEY", "")
        secret = os.environ.get("BINANCE_API_SECRET", "")
        if key and secret:
            try:
                token_meta = BinanceMarketMetadataClient(key, secret).token_metadata(
                    since_ms=int(since_dt.timestamp() * 1000),
                    max_pages=args.binance_pages,
                )
                enriched = True
            except Exception as exc:
                print(
                    f"warning: Binance metadata enrichment failed: {type(exc).__name__}: {exc}",
                    flush=True,
                )
        else:
            print(
                "note: BINANCE_API_KEY/BINANCE_API_SECRET not set; keeping chain-only market labels",
                flush=True,
            )
    enrich_rows(rows, token_meta)
    summary = summarize(rows)
    markets = market_rollup(rows)

    target = args.output_dir / wallet
    dump_json(
        target / "order_filled_raw.json",
        {
            "address": wallet,
            "from_block": from_block,
            "to_block": to_block,
            "data": logs,
        },
    )
    dump_json(target / "binance_token_metadata.json", token_meta)
    dump_json(target / "summary.json", summary)
    write_csv(target / "fills_chain.csv", rows)
    write_csv(target / "markets.csv", markets)
    rpc_label = urllib.parse.urlsplit(rpc_url).hostname or "custom-rpc"
    (target / "report.md").write_text(
        render_report(
            wallet,
            summary,
            from_block=from_block,
            to_block=to_block,
            rpc_label=rpc_label,
            enriched=enriched,
        ),
        encoding="utf-8",
    )

    print(
        f"events={summary['order_fill_events']} direct={summary['direct_buy_sell_events']} tokens={summary['unique_token_ids']}"
    )
    print(
        f"crypto_share_resolved={summary['market_metadata']['crypto_share_of_resolved']}"
    )
    print(f"output={target}")
    for item in summary["candidate_patterns"]:
        print(f"candidate: {item}")


if __name__ == "__main__":
    main()
