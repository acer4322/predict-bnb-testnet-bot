from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

DEFAULT_BASE_URL = "https://api.predict.fun"
DEFAULT_OBSERVATIONS = Path("data/binance_production_observations.csv")
WEI = Decimal(10) ** 18


def normalize_address(value: str) -> str:
    value = str(value or "").strip().lower()
    if not value.startswith("0x") or len(value) != 42:
        raise ValueError(f"invalid EVM address: {value!r}")
    int(value[2:], 16)
    return value


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def normalize_price(value: Any) -> float | None:
    number = _decimal(value)
    if number is None:
        return None
    if number > 1 and number >= Decimal("1000000000"):
        number /= WEI
    if number < 0 or number > 1:
        return None
    return float(number)


def normalize_amount(value: Any) -> float | None:
    number = _decimal(value)
    if number is None:
        return None
    if abs(number) >= Decimal("1000000000000"):
        number /= WEI
    return float(number)


def parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 10_000_000_000:
            raw /= 1000.0
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    text = str(value).strip()
    try:
        numeric = float(text)
    except ValueError:
        numeric = None
    if numeric is not None and text.replace(".", "", 1).isdigit():
        if numeric > 10_000_000_000:
            numeric /= 1000.0
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def dt_ms(value: datetime | None) -> int | None:
    return None if value is None else int(value.timestamp() * 1000)


def _find_time_value(obj: Any, keys: set[str]) -> datetime | None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key.lower() in keys:
                parsed = parse_timestamp(value)
                if parsed is not None:
                    return parsed
        for value in obj.values():
            parsed = _find_time_value(value, keys)
            if parsed is not None:
                return parsed
    elif isinstance(obj, list):
        for value in obj:
            parsed = _find_time_value(value, keys)
            if parsed is not None:
                return parsed
    return None


def market_time_bounds(market: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    start = _find_time_value(
        market,
        {"startdate", "startat", "startsat", "opentime", "opensat", "begintime", "beginat"},
    )
    end = _find_time_value(
        market,
        {"enddate", "endat", "endsat", "closetime", "closesat", "settlementtime", "settlesat"},
    )
    return start, end


def infer_market_end_times_from_observations(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    candidates: dict[str, list[int]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return {}
        fields = {name.lower(): name for name in reader.fieldnames}
        market_col = fields.get("market_id") or fields.get("marketid")
        ts_col = fields.get("timestamp") or fields.get("observed_at") or fields.get("timestamp_utc")
        left_col = fields.get("seconds_left") or fields.get("secondsremaining")
        if not market_col or not ts_col or not left_col:
            return {}
        for row in reader:
            market_id = str(row.get(market_col, "")).strip()
            observed = parse_timestamp(row.get(ts_col))
            try:
                seconds_left = float(row.get(left_col, ""))
            except (TypeError, ValueError):
                continue
            if not market_id or observed is None or not math.isfinite(seconds_left):
                continue
            candidates[market_id].append(int(observed.timestamp() * 1000 + seconds_left * 1000))
    result: dict[str, int] = {}
    for market_id, values in candidates.items():
        if values:
            result[market_id] = int(statistics.median(values))
    return result


class PredictApiError(RuntimeError):
    pass


class PredictApiClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 20.0) -> None:
        if not api_key:
            raise ValueError("Predict API key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = {key: value for key, value in (params or {}).items() if value is not None}
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "btc-5m-wallet-quant-forensics/0.1",
                "x-api-key": self.api_key,
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            hint = " Check PREDICT_API_KEY." if exc.code in {401, 403} else ""
            raise PredictApiError(f"Predict API HTTP {exc.code} for {path}: {detail}{hint}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise PredictApiError(f"Predict API request failed for {path}: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("success") is False:
            raise PredictApiError(f"Predict API rejected {path}: {payload}")
        return payload

    def paginate(self, path: str, params: dict[str, Any], *, page_size: int, max_pages: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for page_index in range(max(1, max_pages)):
            page_params = dict(params)
            page_params["first"] = max(1, min(500, int(page_size)))
            if cursor:
                page_params["after"] = cursor
            payload = self.get(path, page_params)
            data = payload.get("data") or []
            if not isinstance(data, list):
                raise PredictApiError(f"Unexpected data shape from {path}")
            rows.extend(item for item in data if isinstance(item, dict))
            next_cursor = payload.get("cursor")
            if not data or not next_cursor or next_cursor == cursor or str(next_cursor) in seen_cursors:
                break
            seen_cursors.add(str(next_cursor))
            cursor = str(next_cursor)
            if page_index + 1 < max_pages:
                time.sleep(0.03)
        return rows

    def matches(self, address: str, *, page_size: int = 100, max_pages: int = 100) -> list[dict[str, Any]]:
        return self.paginate(
            "/v1/orders/matches",
            {"signerAddress": normalize_address(address)},
            page_size=page_size,
            max_pages=max_pages,
        )

    def positions(self, address: str, *, page_size: int = 100, max_pages: int = 100) -> list[dict[str, Any]]:
        return self.paginate(
            f"/v1/positions/{normalize_address(address)}",
            {},
            page_size=page_size,
            max_pages=max_pages,
        )


def _outcome_name(order_leg: dict[str, Any]) -> str:
    outcome = order_leg.get("outcome")
    if isinstance(outcome, dict):
        return str(outcome.get("name") or outcome.get("label") or outcome.get("indexSet") or "").upper()
    return str(outcome or "").upper()


def _outcome_status(order_leg: dict[str, Any]) -> str:
    outcome = order_leg.get("outcome")
    if isinstance(outcome, dict):
        return str(outcome.get("status") or "").upper()
    return ""


def quote_action(quote_type: Any) -> str:
    value = str(quote_type or "").strip().upper()
    if value == "BID":
        return "BUY"
    if value == "ASK":
        return "SELL"
    return value or "UNKNOWN"


def _market_id(market: dict[str, Any]) -> str:
    value = market.get("id", market.get("marketId", ""))
    return str(value)


def _wallet_legs(match: dict[str, Any], address: str) -> list[tuple[str, dict[str, Any], int]]:
    target = normalize_address(address)
    result: list[tuple[str, dict[str, Any], int]] = []
    taker = match.get("taker")
    if isinstance(taker, dict) and str(taker.get("signer", "")).lower() == target:
        result.append(("TAKER", taker, 1))
    makers = [maker for maker in (match.get("makers") or []) if isinstance(maker, dict)]
    maker_count = len(makers)
    for maker in makers:
        if str(maker.get("signer", "")).lower() == target:
            result.append(("MAKER", maker, maker_count))
    return result


def extract_wallet_fills(
    matches: Iterable[dict[str, Any]],
    address: str,
    *,
    inferred_end_ms: dict[str, int] | None = None,
    estimate_aligned_5m: bool = False,
) -> list[dict[str, Any]]:
    end_map = inferred_end_ms or {}
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for match in matches:
        market = match.get("market") if isinstance(match.get("market"), dict) else {}
        market_id = _market_id(market)
        executed = parse_timestamp(match.get("executedAt"))
        executed_ms = dt_ms(executed)
        price = normalize_price(match.get("priceExecuted"))
        aggregate_filled = normalize_amount(match.get("amountFilled"))
        _, market_end = market_time_bounds(market)
        market_end_ms = dt_ms(market_end)
        timing_basis = "api_market_end" if market_end_ms is not None else ""
        if market_end_ms is None and market_id in end_map:
            market_end_ms = int(end_map[market_id])
            timing_basis = "local_observation_inferred_end"
        if market_end_ms is None and estimate_aligned_5m and executed_ms is not None:
            market_end_ms = ((executed_ms // 300_000) + 1) * 300_000
            timing_basis = "assumed_utc_aligned_5m"
        seconds_left = (
            (market_end_ms - executed_ms) / 1000.0
            if market_end_ms is not None and executed_ms is not None
            else None
        )
        wallet_legs = _wallet_legs(match, address)
        for role, leg, maker_count in wallet_legs:
            if role == "TAKER":
                filled_shares = aggregate_filled
                size_confidence = "exact_taker_match"
            elif maker_count == 1:
                filled_shares = aggregate_filled
                size_confidence = "exact_single_maker_match"
            else:
                filled_shares = None
                size_confidence = "aggregate_only_multi_maker"
            order_amount_hint = normalize_amount(leg.get("amount"))
            notional = filled_shares * price if filled_shares is not None and price is not None else None
            key = (
                match.get("transactionHash"),
                market_id,
                role,
                leg.get("hash"),
                leg.get("quoteType"),
                _outcome_name(leg),
                match.get("executedAt"),
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "executed_at": executed.isoformat() if executed else str(match.get("executedAt") or ""),
                    "market_id": market_id,
                    "market_title": str(market.get("title") or market.get("question") or ""),
                    "category_slug": str(market.get("categorySlug") or ""),
                    "market_variant": str(market.get("marketVariant") or ""),
                    "price_feed_symbol": str((market.get("variantData") or {}).get("priceFeedSymbol") or ""),
                    "start_price": (market.get("variantData") or {}).get("startPrice"),
                    "end_price": (market.get("variantData") or {}).get("endPrice"),
                    "resolution_outcome": str((market.get("resolution") or {}).get("name") or ""),
                    "resolution_status": str((market.get("resolution") or {}).get("status") or ""),
                    "role": role,
                    "quote_type": str(leg.get("quoteType") or "").upper(),
                    "action": quote_action(leg.get("quoteType")),
                    "outcome": _outcome_name(leg),
                    "outcome_status": _outcome_status(leg),
                    "executed_price": price,
                    "filled_shares": filled_shares,
                    "notional_usdt": notional,
                    "size_confidence": size_confidence,
                    "order_amount_hint": order_amount_hint,
                    "match_filled_shares": aggregate_filled,
                    "seconds_left": seconds_left,
                    "timing_basis": timing_basis,
                    "transaction_hash": str(match.get("transactionHash") or ""),
                    "settlement_id": str(match.get("settlementId") or ""),
                    "order_hash": str(leg.get("hash") or ""),
                    "wallet_signer": str(leg.get("signer") or "").lower(),
                    "fee_amount": normalize_amount((leg.get("fee") or {}).get("amount")),
                    "fee_type": str((leg.get("fee") or {}).get("type") or ""),
                    "maker_count_in_match": maker_count if role == "MAKER" else len(match.get("makers") or []),
                    "raw_price_executed": match.get("priceExecuted"),
                    "raw_amount_filled": match.get("amountFilled"),
                }
            )
    rows.sort(key=lambda row: (row.get("executed_at") or "", row.get("market_id") or ""))
    return rows


def _percentile(values: list[float], q: float) -> float | None:
    values = sorted(value for value in values if math.isfinite(value))
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    index = (len(values) - 1) * q
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return values[low]
    return values[low] * (high - index) + values[high] * (index - low)


def _histogram(values: Iterable[float], width: float, *, clamp: tuple[float, float] | None = None) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for value in values:
        if not math.isfinite(value):
            continue
        if clamp and not (clamp[0] <= value <= clamp[1]):
            continue
        lower = math.floor(value / width) * width
        upper = lower + width
        counts[f"{lower:.2f}-{upper:.2f}"] += 1
    return dict(sorted(counts.items(), key=lambda item: float(item[0].split("-", 1)[0])))


def _concentration(values: list[float], width: float) -> tuple[float | None, str | None]:
    if not values:
        return None, None
    bins: Counter[int] = Counter(math.floor(value / width) for value in values if math.isfinite(value))
    if not bins:
        return None, None
    index, count = bins.most_common(1)[0]
    return count / sum(bins.values()), f"{index * width:.2f}-{(index + 1) * width:.2f}"


def summarize_fills(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_market[str(row.get("market_id") or "")].append(row)
    for market_rows in by_market.values():
        market_rows.sort(key=lambda row: row.get("executed_at") or "")

    buys = [row for row in rows if row.get("action") == "BUY"]
    sells = [row for row in rows if row.get("action") == "SELL"]
    exact_size_rows = [row for row in rows if row.get("notional_usdt") is not None]
    first_buys: list[dict[str, Any]] = []
    repeat_add_markets = 0
    sell_markets = 0
    both_outcome_markets = 0
    for market_rows in by_market.values():
        market_buys = [row for row in market_rows if row.get("action") == "BUY"]
        market_sells = [row for row in market_rows if row.get("action") == "SELL"]
        if market_buys:
            first_buys.append(market_buys[0])
        if market_sells:
            sell_markets += 1
        buy_outcomes = {str(row.get("outcome")) for row in market_buys if row.get("outcome")}
        if len(buy_outcomes) >= 2:
            both_outcome_markets += 1
        counts = Counter(str(row.get("outcome")) for row in market_buys if row.get("outcome"))
        if any(count >= 2 for count in counts.values()):
            repeat_add_markets += 1

    first_prices = [float(row["executed_price"]) for row in first_buys if row.get("executed_price") is not None]
    first_timings = [float(row["seconds_left"]) for row in first_buys if row.get("seconds_left") is not None]
    exact_notionals = [round(float(row["notional_usdt"]), 2) for row in exact_size_rows]
    price_conc, price_mode = _concentration(first_prices, 0.05)
    timing_conc, timing_mode = _concentration(first_timings, 10.0)
    size_counter = Counter(exact_notionals)
    size_mode, size_count = size_counter.most_common(1)[0] if size_counter else (None, 0)
    size_conc = size_count / len(exact_notionals) if exact_notionals else None
    maker_count = sum(1 for row in rows if row.get("role") == "MAKER")
    markets = len(by_market)

    candidates: list[str] = []
    sample_ok = len(first_buys) >= 20
    if sample_ok and timing_conc is not None and timing_conc >= 0.40:
        candidates.append(f"entry timing is concentrated: {timing_conc:.0%} of first buys fall in the {timing_mode}s-left bin")
    if sample_ok and price_conc is not None and price_conc >= 0.40:
        candidates.append(f"entry price is concentrated: {price_conc:.0%} of first buys fall in the {price_mode} price bin")
    if len(exact_notionals) >= 20 and size_conc is not None and size_conc >= 0.50:
        candidates.append(f"position sizing may be rule-based: {size_conc:.0%} of exact fills share the ${size_mode:.2f} notional mode")
    maker_share = maker_count / len(rows) if rows else None
    if len(rows) >= 50 and maker_share is not None and maker_share >= 0.70:
        candidates.append(f"maker-heavy execution: {maker_share:.0%} of wallet fill legs are maker-side")
    if markets >= 20 and repeat_add_markets / markets >= 0.40:
        candidates.append(f"same-outcome scaling is common: {repeat_add_markets / markets:.0%} of markets contain repeated buys on one outcome")
    if markets >= 20 and sell_markets / markets <= 0.10:
        candidates.append(f"few observed exits: only {sell_markets / markets:.0%} of traded markets contain a SELL fill; hold-to-resolution is a candidate")
    if markets >= 20 and both_outcome_markets / markets >= 0.25:
        candidates.append(f"two-sided participation is material: {both_outcome_markets / markets:.0%} of markets contain BUY fills on both outcomes")

    return {
        "fill_legs": len(rows),
        "markets_traded": markets,
        "buy_fill_legs": len(buys),
        "sell_fill_legs": len(sells),
        "maker_fill_legs": maker_count,
        "taker_fill_legs": sum(1 for row in rows if row.get("role") == "TAKER"),
        "maker_share": maker_share,
        "markets_with_sell": sell_markets,
        "markets_with_repeated_same_outcome_buys": repeat_add_markets,
        "markets_with_buys_on_both_outcomes": both_outcome_markets,
        "first_buy_count": len(first_buys),
        "first_buy_price": {
            "median": statistics.median(first_prices) if first_prices else None,
            "p25": _percentile(first_prices, 0.25),
            "p75": _percentile(first_prices, 0.75),
            "histogram_0_05": _histogram(first_prices, 0.05, clamp=(0.0, 1.0)),
            "top_bin_concentration": price_conc,
            "top_bin": price_mode,
        },
        "first_buy_seconds_left": {
            "count": len(first_timings),
            "median": statistics.median(first_timings) if first_timings else None,
            "p25": _percentile(first_timings, 0.25),
            "p75": _percentile(first_timings, 0.75),
            "histogram_10s": _histogram(first_timings, 10.0),
            "top_bin_concentration": timing_conc,
            "top_bin": timing_mode,
        },
        "exact_notional": {
            "count": len(exact_notionals),
            "median": statistics.median(exact_notionals) if exact_notionals else None,
            "mode": size_mode,
            "mode_concentration": size_conc,
        },
        "candidate_patterns": candidates,
        "warnings": (["fewer than 20 markets/first entries: pattern inference is preliminary"] if not sample_ok else []),
    }


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def render_report(address: str, summary: dict[str, Any], *, observations_path: Path, positions_count: int) -> str:
    price = summary["first_buy_price"]
    timing = summary["first_buy_seconds_left"]
    lines = [
        f"# Wallet Quant Forensics — `{address}`",
        "",
        "## Coverage",
        "",
        f"- Fill legs attributed to wallet: **{summary['fill_legs']}**",
        f"- Distinct markets: **{summary['markets_traded']}**",
        f"- BUY / SELL fill legs: **{summary['buy_fill_legs']} / {summary['sell_fill_legs']}**",
        f"- MAKER / TAKER fill legs: **{summary['maker_fill_legs']} / {summary['taker_fill_legs']}**",
        f"- Position rows returned by API: **{positions_count}**",
        "",
        "## First-entry fingerprint",
        "",
        f"- First BUY median price: **{price['median']}**",
        f"- First BUY 0.05-bin concentration: **{price['top_bin_concentration']}** in **{price['top_bin']}**",
        f"- First BUY median seconds left: **{timing['median']}**",
        f"- First BUY 10s-bin concentration: **{timing['top_bin_concentration']}** in **{timing['top_bin']}**",
        "",
        "## Behaviour",
        "",
        f"- Markets with SELL: **{summary['markets_with_sell']}**",
        f"- Markets with repeated same-outcome BUYs: **{summary['markets_with_repeated_same_outcome_buys']}**",
        f"- Markets with BUYs on both outcomes: **{summary['markets_with_buys_on_both_outcomes']}**",
        f"- Maker share: **{summary['maker_share']}**",
        "",
        "## Candidate patterns",
        "",
    ]
    if summary["candidate_patterns"]:
        lines.extend(f"- {item}" for item in summary["candidate_patterns"])
    else:
        lines.append("- No strong rule fingerprint crossed the conservative first-pass thresholds.")
    lines.extend(
        [
            "",
            "## Timing notes",
            "",
            f"- Local observation source: `{observations_path}`",
            "- `seconds_left` is exact only when the Predict market exposes an end time or the same market ID was found in the local Binance observation CSV.",
            "- `assumed_utc_aligned_5m` is explicitly an estimate and must not be treated as exact timing.",
            "",
            "## Size notes",
            "",
            "- Taker fills and single-maker matches use the match-level filled amount.",
            "- Multi-maker matches are marked `aggregate_only_multi_maker`; wallet-specific filled size is intentionally left blank to avoid false sizing inference.",
        ]
    )
    return "\n".join(lines) + "\n"


def load_json_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    raise ValueError(f"unsupported JSON shape in {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Predict.fun wallet trade collector and quant fingerprint analyzer"
    )
    parser.add_argument("address", help="EVM wallet/Predict account address to inspect")
    parser.add_argument("--api-key-env", default="PREDICT_API_KEY")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--observations-csv", type=Path, default=DEFAULT_OBSERVATIONS)
    parser.add_argument("--output-dir", type=Path, default=Path("data/wallet_forensics"))
    parser.add_argument("--offline-matches", type=Path, help="analyze an existing matches JSON instead of calling the API")
    parser.add_argument("--offline-positions", type=Path, help="optional existing positions JSON")
    parser.add_argument("--skip-positions", action="store_true")
    parser.add_argument(
        "--estimate-aligned-5m",
        action="store_true",
        help="estimate T-minus from UTC 5-minute buckets only when no exact/local market end is available",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    address = normalize_address(args.address)
    end_map = infer_market_end_times_from_observations(args.observations_csv)

    if args.offline_matches:
        matches = load_json_rows(args.offline_matches)
        positions = load_json_rows(args.offline_positions) if args.offline_positions else []
    else:
        api_key = os.environ.get(args.api_key_env, "")
        if not api_key:
            raise SystemExit(
                f"Missing {args.api_key_env}. Request a Predict.fun API key, set it only as a local environment variable, "
                "or use --offline-matches."
            )
        client = PredictApiClient(api_key, args.base_url)
        matches = client.matches(address, page_size=args.page_size, max_pages=args.max_pages)
        positions = [] if args.skip_positions else client.positions(
            address, page_size=args.page_size, max_pages=args.max_pages
        )

    fills = extract_wallet_fills(
        matches,
        address,
        inferred_end_ms=end_map,
        estimate_aligned_5m=args.estimate_aligned_5m,
    )
    summary = summarize_fills(fills)
    target_dir = args.output_dir / address
    _json_dump(target_dir / "matches_raw.json", {"address": address, "data": matches})
    _json_dump(target_dir / "positions_raw.json", {"address": address, "data": positions})
    _json_dump(target_dir / "summary.json", summary)
    write_csv(target_dir / "fills.csv", fills)
    (target_dir / "report.md").write_text(
        render_report(address, summary, observations_path=args.observations_csv, positions_count=len(positions)),
        encoding="utf-8",
    )

    print(f"wallet={address}")
    print(f"matches={len(matches)} fill_legs={len(fills)} markets={summary['markets_traded']}")
    print(f"maker_share={summary['maker_share']} first_buy_price_median={summary['first_buy_price']['median']}")
    print(f"first_buy_seconds_left_median={summary['first_buy_seconds_left']['median']}")
    print(f"output={target_dir}")
    for pattern in summary["candidate_patterns"]:
        print(f"candidate: {pattern}")


if __name__ == "__main__":
    main()
