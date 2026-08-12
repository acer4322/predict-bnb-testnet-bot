from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import re
import sqlite3
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

API_BASE = os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun").rstrip("/")
WEI = 10**18
ET = ZoneInfo("America/New_York")
DEFAULT_WALLET = "0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03"
DEFAULT_OFFSETS = (0.0, 0.5, 1.0, 2.0, 3.0, 5.0)
UPDOWN_TITLE = re.compile(
    r"^(Bitcoin|BTC|Ethereum|ETH|BNB) Up or Down - (.+),\s*(\d{1,2})(?::(\d{2}))?(AM|PM)-"
    r"(\d{1,2})(?::(\d{2}))?(AM|PM) ET$",
    re.I,
)
ASSET_ALIASES = {
    "BITCOIN": "BTC",
    "BTC": "BTC",
    "ETHEREUM": "ETH",
    "ETH": "ETH",
    "BNB": "BNB",
}


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def dec_wei(value: Any) -> float | None:
    try:
        return float(value) / WEI
    except (TypeError, ValueError, OverflowError):
        return None


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def dt_ms(value: Any) -> int | None:
    parsed = parse_iso(value)
    return int(parsed.timestamp() * 1000) if parsed is not None else None


def median(values: Iterable[float | None]) -> float | None:
    rows = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return statistics.median(rows) if rows else None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    rows = sorted(float(v) for v in values)
    if len(rows) == 1:
        return rows[0]
    x = max(0.0, min(1.0, q)) * (len(rows) - 1)
    lo = int(math.floor(x))
    hi = min(len(rows) - 1, lo + 1)
    weight = x - lo
    return rows[lo] * (1.0 - weight) + rows[hi] * weight


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    denom = math.sqrt(sum(v * v for v in dx) * sum(v * v for v in dy))
    if denom <= 1e-15:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / denom


def norm_address(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text.startswith("0x") and len(text) == 42 else ""


def signer(row: dict[str, Any]) -> str:
    return norm_address(row.get("signer") or row.get("maker"))


def outcome_name(row: dict[str, Any]) -> str:
    raw = row.get("outcome")
    if isinstance(raw, dict):
        raw = raw.get("name")
    return str(raw or "").upper().strip()


def quote_type(row: dict[str, Any]) -> str:
    return str(row.get("quoteType") or row.get("quote_type") or "").upper().strip()


def minute_of_day(hour: int, minute: int, ampm: str) -> int:
    hour %= 12
    if ampm.upper() == "PM":
        hour += 12
    return hour * 60 + minute


def market_descriptor(title: Any) -> dict[str, Any] | None:
    text = str(title or "").strip()
    match = UPDOWN_TITLE.match(text)
    if not match:
        return None
    asset = ASSET_ALIASES.get(str(match.group(1) or "").upper())
    if not asset:
        return None
    sh, sm, sap, eh, em, eap = (
        int(match.group(3)),
        int(match.group(4) or 0),
        match.group(5),
        int(match.group(6)),
        int(match.group(7) or 0),
        match.group(8),
    )
    start = minute_of_day(sh, sm, sap)
    end = minute_of_day(eh, em, eap)
    if end < start:
        end += 24 * 60
    duration = end - start
    return {
        "asset": asset,
        "dateText": str(match.group(2) or "").strip(),
        "startHour": sh,
        "startMinute": sm,
        "startAmPm": sap,
        "durationMinutes": duration,
    }


def infer_market_bucket(title: Any, event_ms: int) -> tuple[str, int, int] | None:
    desc = market_descriptor(title)
    if desc is None:
        return None
    event_dt = datetime.fromtimestamp(event_ms / 1000.0, tz=timezone.utc)
    local_year = event_dt.astimezone(ET).year
    candidates: list[datetime] = []
    for year in (local_year - 1, local_year, local_year + 1):
        parsed = None
        for fmt in ("%B %d %Y", "%b %d %Y", "%B %d, %Y", "%b %d, %Y"):
            try:
                parsed = datetime.strptime(f"{desc['dateText']} {year}", fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            continue
        hour = int(desc["startHour"]) % 12 + (12 if str(desc["startAmPm"]).upper() == "PM" else 0)
        local = datetime(parsed.year, parsed.month, parsed.day, hour, int(desc["startMinute"]), tzinfo=ET)
        candidates.append(local.astimezone(timezone.utc))
    if candidates:
        chosen = min(candidates, key=lambda row: abs((row - event_dt).total_seconds()))
        return str(desc["asset"]), int(desc["durationMinutes"]), int(chosen.timestamp())
    duration = max(1, int(desc["durationMinutes"]))
    bucket = int(event_ms // (duration * 60_000)) * duration * 60
    return str(desc["asset"]), duration, bucket


def parse_assets(value: str) -> set[str]:
    out: set[str] = set()
    for piece in str(value).split(","):
        key = piece.strip().upper()
        if key:
            out.add(ASSET_ALIASES.get(key, key))
    return {x for x in out if x in {"BTC", "ETH", "BNB"}}


def parse_durations(value: str) -> set[int]:
    out: set[int] = set()
    for piece in str(value).split(","):
        text = piece.strip().lower().removesuffix("m")
        if text:
            out.add(int(text))
    return {x for x in out if x > 0}


def parse_offsets(value: str) -> tuple[float, ...]:
    rows = sorted({max(0.0, float(x.strip())) for x in str(value).split(",") if x.strip()})
    return tuple(rows) if rows else DEFAULT_OFFSETS


def api_get(path: str, params: dict[str, Any], api_key: str, *, timeout: float = 20.0, retries: int = 4) -> dict[str, Any]:
    query = urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{API_BASE}{path}" + (f"?{query}" if query else "")
    headers = {
        "x-api-key": api_key,
        "Accept": "application/json",
        "User-Agent": "BTC-5M-Lab-PredictWalletFairValueExecution/1.0",
    }
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            with urlopen(Request(url, headers=headers), timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("Unexpected Predict response shape")
            return payload
        except HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            last_error = RuntimeError(f"Predict GET {path} HTTP {exc.code}: {detail[:500]}")
            if exc.code not in {429, 500, 502, 503, 504} or attempt + 1 >= retries:
                raise last_error
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 >= retries:
                raise RuntimeError(f"Predict GET {path} failed: {type(exc).__name__}: {exc}") from exc
        time.sleep(min(5.0, 0.5 * (2**attempt)))
    raise RuntimeError(f"Predict GET {path} failed: {last_error}")


def fetch_taker_matches(
    *, wallet: str, api_key: str, hours: float, pages: int, page_size: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0.1, hours))
    matches: list[dict[str, Any]] = []
    after: str | None = None
    seen: set[str] = set()
    oldest: datetime | None = None
    stopped_by_cutoff = False
    pages_fetched = 0
    cursor_remaining = False
    for _ in range(max(1, pages)):
        payload = api_get(
            "/v1/orders/matches",
            {
                "first": max(1, min(500, page_size)),
                "after": after,
                "signerAddress": wallet,
                "isSignerMaker": "false",
            },
            api_key,
        )
        pages_fetched += 1
        rows = payload.get("data") if isinstance(payload.get("data"), list) else []
        if not rows:
            cursor_remaining = False
            break
        old_seen = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            executed = parse_iso(row.get("executedAt"))
            if executed is not None:
                oldest = executed if oldest is None else min(oldest, executed)
                if executed < cutoff:
                    old_seen = True
                    continue
            matches.append(row)
        cursor = str(payload.get("cursor") or "").strip()
        cursor_remaining = bool(cursor)
        if old_seen:
            stopped_by_cutoff = True
            break
        if not cursor or cursor in seen:
            break
        seen.add(cursor)
        after = cursor
    coverage = {
        "requestedHours": hours,
        "pagesFetched": pages_fetched,
        "matchRowsKept": len(matches),
        "oldestFetchedUtc": oldest.isoformat() if oldest is not None else None,
        "reachedRequestedCutoff": stopped_by_cutoff,
        "truncatedByPageLimit": bool(pages_fetched >= max(1, pages) and cursor_remaining and not stopped_by_cutoff),
    }
    return matches, coverage


def _scope_for_title(title: Any, event_ms: int, assets: set[str], durations: set[int]) -> tuple[str, int, int] | None:
    inferred = infer_market_bucket(title, event_ms)
    if inferred is None:
        return None
    asset, duration, bucket = inferred
    return inferred if asset in assets and duration in durations else None


def load_maker_events(path: Path, assets: set[str], durations: set[int], cutoff_ms: int) -> tuple[list[dict[str, Any]], Counter[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("parentOrdersByMakerHash") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise SystemExit("maker input does not contain parentOrdersByMakerHash")
    events: list[dict[str, Any]] = []
    recognized: Counter[str] = Counter()
    for row in rows:
        if not isinstance(row, dict):
            continue
        event_ms = dt_ms(row.get("firstExecutedAt"))
        title = row.get("marketTitle")
        if event_ms is None:
            continue
        inferred = infer_market_bucket(title, event_ms)
        if inferred is not None:
            recognized[f"{inferred[0]}-{inferred[1]}m"] += 1
        scoped = _scope_for_title(title, event_ms, assets, durations)
        if scoped is None or event_ms < cutoff_ms:
            continue
        side = str(row.get("outcome") or "").upper().strip()
        if side not in {"UP", "DOWN"} or str(row.get("quoteType") or "").upper().strip() != "BID":
            continue
        asset, duration, bucket = scoped
        events.append(
            {
                "role": "MAKER",
                "kind": "MAKER_FIRST_FILL",
                "asset": asset,
                "durationMinutes": duration,
                "marketBucket": bucket,
                "marketId": int(row.get("marketId") or 0),
                "marketTitle": title,
                "side": side,
                "price": finite(row.get("price")),
                "shares": finite(row.get("totalShares")),
                "costUsdtApprox": finite(row.get("totalCostUsdtApprox")),
                "eventMs": event_ms,
                "eventAt": row.get("firstExecutedAt"),
                "lastEventMs": dt_ms(row.get("lastExecutedAt")),
                "orderHash": row.get("makerHash"),
                "fillLegs": int(row.get("fillLegs") or 0),
            }
        )
    return events, recognized


def extract_taker_buy_events(
    matches: list[dict[str, Any]], wallet: str, assets: set[str], durations: set[int]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    events: list[dict[str, Any]] = []
    counts = Counter()
    for match_index, match in enumerate(matches):
        if not isinstance(match, dict):
            continue
        event_ms = dt_ms(match.get("executedAt"))
        market = match.get("market") if isinstance(match.get("market"), dict) else {}
        title = market.get("title") or market.get("question")
        if event_ms is None:
            continue
        scoped = _scope_for_title(title, event_ms, assets, durations)
        if scoped is None:
            counts["outOfScope"] += 1
            continue
        candidates: list[dict[str, Any]] = []
        if isinstance(match.get("taker"), dict):
            candidates.append(match["taker"])
        if isinstance(match.get("takers"), list):
            candidates.extend(row for row in match["takers"] if isinstance(row, dict))
        if isinstance(match.get("order"), dict):
            candidates.append(match["order"])
        asset, duration, bucket = scoped
        for candidate_index, taker in enumerate(candidates):
            if signer(taker) != wallet:
                continue
            counts["walletTakerLegs"] += 1
            if quote_type(taker) != "BID":
                counts["nonBidIgnored"] += 1
                continue
            side = outcome_name(taker)
            if side not in {"UP", "DOWN"}:
                counts["badOutcomeIgnored"] += 1
                continue
            price = dec_wei(taker.get("price"))
            shares = dec_wei(taker.get("amount"))
            events.append(
                {
                    "role": "TAKER",
                    "kind": "TAKER_BUY",
                    "asset": asset,
                    "durationMinutes": duration,
                    "marketBucket": bucket,
                    "marketId": int(market.get("id") or 0),
                    "marketTitle": title,
                    "side": side,
                    "price": price,
                    "shares": shares,
                    "costUsdtApprox": shares * price if shares is not None and price is not None else None,
                    "eventMs": event_ms,
                    "eventAt": match.get("executedAt"),
                    "orderHash": taker.get("hash") or taker.get("orderHash"),
                    "transactionHash": match.get("transactionHash"),
                    "syntheticKey": f"{match_index}:{candidate_index}",
                }
            )
    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in events:
        key = (
            row.get("marketId"), row.get("side"), row.get("eventMs"),
            row.get("orderHash"), row.get("transactionHash"), row.get("price"), row.get("shares"),
        )
        deduped[key] = row
    counts["dedupedTakerBuys"] = len(deduped)
    return list(deduped.values()), dict(counts)


class TrajectoryIndex:
    CANDIDATE_COLUMNS = (
        "seconds_left",
        "poly_up_mid", "poly_down_mid",
        "binance_up_mid", "binance_down_mid",
        "binance_up_ask", "binance_down_ask",
        "mid_gap", "executable_edge_up", "executable_edge_down",
        "poly_source_age_ms", "poly_receipt_age_ms",
    )

    def __init__(self, db_path: Path, assets: set[str]) -> None:
        if not db_path.exists():
            raise SystemExit(f"observer DB not found: {db_path}")
        uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
        self.db = sqlite3.connect(uri, uri=True, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        table = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='multi_prediction_trajectory'"
        ).fetchone()
        if table is None:
            raise SystemExit(f"{db_path} does not contain multi_prediction_trajectory")
        available = {
            str(row[1]) for row in self.db.execute("PRAGMA table_info(multi_prediction_trajectory)").fetchall()
        }
        required = {"asset", "market_bucket", "sampled_at_ms"}
        missing = required - available
        if missing:
            raise SystemExit(f"multi_prediction_trajectory missing required columns: {sorted(missing)}")
        self.available_columns = sorted(available)
        selected = ["asset", "market_bucket", "sampled_at_ms"] + [
            col for col in self.CANDIDATE_COLUMNS if col in available
        ]
        placeholders = ",".join("?" for _ in sorted(assets))
        sql = (
            f"SELECT {','.join(selected)} FROM multi_prediction_trajectory "
            f"WHERE UPPER(asset) IN ({placeholders}) ORDER BY asset,market_bucket,sampled_at_ms"
        )
        grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
        self.min_ms: int | None = None
        self.max_ms: int | None = None
        self.coverage_by_asset: dict[str, dict[str, Any]] = {}
        coverage_raw: dict[str, list[int]] = defaultdict(list)
        for raw in self.db.execute(sql, tuple(sorted(assets))).fetchall():
            row = dict(raw)
            asset = str(row["asset"]).upper()
            bucket = int(row["market_bucket"])
            ms = int(row["sampled_at_ms"])
            grouped[(asset, bucket)].append(row)
            coverage_raw[asset].append(ms)
            self.min_ms = ms if self.min_ms is None else min(self.min_ms, ms)
            self.max_ms = ms if self.max_ms is None else max(self.max_ms, ms)
        for asset, times in coverage_raw.items():
            self.coverage_by_asset[asset] = {
                "rows": len(times),
                "minMs": min(times),
                "maxMs": max(times),
                "markets": sum(1 for key in grouped if key[0] == asset),
            }
        self.by_market: dict[tuple[str, int], tuple[list[int], list[dict[str, Any]]]] = {}
        for key, rows in grouped.items():
            self.by_market[key] = ([int(row["sampled_at_ms"]) for row in rows], rows)

    def close(self) -> None:
        self.db.close()

    def at_or_before(self, asset: str, bucket: int, target_ms: int, max_age_ms: int) -> dict[str, Any] | None:
        block = self.by_market.get((asset, int(bucket)))
        if block is None:
            return None
        times, rows = block
        pos = bisect.bisect_right(times, int(target_ms)) - 1
        if pos < 0:
            return None
        age = int(target_ms) - times[pos]
        return rows[pos] if 0 <= age <= max_age_ms else None

    def at_or_after(self, asset: str, bucket: int, target_ms: int, max_age_ms: int) -> dict[str, Any] | None:
        block = self.by_market.get((asset, int(bucket)))
        if block is None:
            return None
        times, rows = block
        pos = bisect.bisect_left(times, int(target_ms))
        if pos >= len(times):
            return None
        age = times[pos] - int(target_ms)
        return rows[pos] if 0 <= age <= max_age_ms else None


def side_probability(sample: dict[str, Any], side: str, prefix: str) -> float | None:
    direct = finite(sample.get(f"{prefix}_{'up' if side == 'UP' else 'down'}_mid"))
    if direct is not None:
        return direct
    up = finite(sample.get(f"{prefix}_up_mid"))
    if up is not None:
        return up if side == "UP" else 1.0 - up
    return None


def side_ask(sample: dict[str, Any], side: str, prefix: str) -> float | None:
    direct = finite(sample.get(f"{prefix}_{'up' if side == 'UP' else 'down'}_ask"))
    return direct


def snapshot_view(sample: dict[str, Any], event: dict[str, Any], requested_ms: int, direction: str) -> dict[str, Any]:
    side = str(event["side"])
    poly = side_probability(sample, side, "poly")
    target = side_probability(sample, side, "binance")
    target_ask = side_ask(sample, side, "binance")
    price = finite(event.get("price"))
    sampled_ms = int(sample["sampled_at_ms"])
    return {
        "sampledAtMs": sampled_ms,
        "sampleDistanceFromRequestedMs": sampled_ms - requested_ms,
        "direction": direction,
        "secondsLeft": finite(sample.get("seconds_left")),
        "polyProbabilityForSide": poly,
        "binancePredictionProbabilityForSide": target,
        "binancePredictionAskForSide": target_ask,
        "polyMinusBinancePrediction": poly - target if poly is not None and target is not None else None,
        "polyEdgeVsExecutionPrice": poly - price if poly is not None and price is not None else None,
        "binancePredictionMidEdgeVsExecutionPrice": target - price if target is not None and price is not None else None,
        "binancePredictionAskEdgeVsExecutionPrice": target_ask - price if target_ask is not None and price is not None else None,
        "polySourceAgeMs": finite(sample.get("poly_source_age_ms")),
        "polyReceiptAgeMs": finite(sample.get("poly_receipt_age_ms")),
    }


def align_event(
    event: dict[str, Any], index: TrajectoryIndex, *, offsets: tuple[float, ...], max_age_ms: int
) -> dict[str, Any]:
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for offset in offsets:
        key = f"{offset:g}s"
        pre_ms = int(event["eventMs"] - offset * 1000)
        post_ms = int(event["eventMs"] + offset * 1000)
        pre = index.at_or_before(event["asset"], event["marketBucket"], pre_ms, max_age_ms)
        post = index.at_or_after(event["asset"], event["marketBucket"], post_ms, max_age_ms)
        before[key] = snapshot_view(pre, event, pre_ms, "BEFORE") if pre is not None else None
        after[key] = snapshot_view(post, event, post_ms, "AFTER") if post is not None else None
    out = dict(event)
    out["before"] = before
    out["after"] = after
    return out


def summarize_event_edges(events: list[dict[str, Any]], offsets: tuple[float, ...]) -> dict[str, Any]:
    by_offset: dict[str, Any] = {}
    for offset in offsets:
        key = f"{offset:g}s"
        samples = [event.get("before", {}).get(key) for event in events]
        samples = [s for s in samples if isinstance(s, dict)]
        poly_edges = [finite(s.get("polyEdgeVsExecutionPrice")) for s in samples]
        poly_edges = [x for x in poly_edges if x is not None]
        target_edges = [finite(s.get("binancePredictionMidEdgeVsExecutionPrice")) for s in samples]
        target_edges = [x for x in target_edges if x is not None]
        gaps = [finite(s.get("polyMinusBinancePrediction")) for s in samples]
        gaps = [x for x in gaps if x is not None]
        by_offset[key] = {
            "eventsWithObserverSample": len(samples),
            "polyEdgeVsExecutionPrice": {
                "count": len(poly_edges),
                "median": median(poly_edges),
                "positiveShare": sum(x > 0 for x in poly_edges) / len(poly_edges) if poly_edges else None,
                "atLeast1CentShare": sum(x >= 0.01 for x in poly_edges) / len(poly_edges) if poly_edges else None,
                "atLeast2CentShare": sum(x >= 0.02 for x in poly_edges) / len(poly_edges) if poly_edges else None,
                "atLeast5CentShare": sum(x >= 0.05 for x in poly_edges) / len(poly_edges) if poly_edges else None,
            },
            "binancePredictionMidEdgeVsExecutionPrice": {
                "count": len(target_edges),
                "median": median(target_edges),
                "positiveShare": sum(x > 0 for x in target_edges) / len(target_edges) if target_edges else None,
            },
            "polyMinusBinancePrediction": {
                "count": len(gaps),
                "median": median(gaps),
                "medianAbs": median([abs(x) for x in gaps]),
            },
        }
    return {"events": len(events), "byOffset": by_offset}


def maker_repricing(events: list[dict[str, Any]], reference_offset: float) -> dict[str, Any]:
    key = f"{reference_offset:g}s"
    streams: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        streams[(int(event["marketId"]), str(event["side"]))].append(event)
    rows: list[dict[str, Any]] = []
    for stream in streams.values():
        stream.sort(key=lambda row: int(row["eventMs"]))
        for previous, current in zip(stream, stream[1:]):
            p0 = finite(previous.get("price"))
            p1 = finite(current.get("price"))
            s0 = previous.get("before", {}).get(key)
            s1 = current.get("before", {}).get(key)
            if p0 is None or p1 is None or not isinstance(s0, dict) or not isinstance(s1, dict):
                continue
            poly0 = finite(s0.get("polyProbabilityForSide"))
            poly1 = finite(s1.get("polyProbabilityForSide"))
            target0 = finite(s0.get("binancePredictionProbabilityForSide"))
            target1 = finite(s1.get("binancePredictionProbabilityForSide"))
            price_delta = p1 - p0
            poly_delta = poly1 - poly0 if poly0 is not None and poly1 is not None else None
            target_delta = target1 - target0 if target0 is not None and target1 is not None else None
            def sign_match(a: float | None, b: float | None) -> bool | None:
                if a is None or b is None or abs(a) < 1e-12 or abs(b) < 1e-12:
                    return None
                return (a > 0) == (b > 0)
            poly_match = sign_match(price_delta, poly_delta)
            target_match = sign_match(price_delta, target_delta)
            conflict = False
            follows = None
            if poly_delta is not None and target_delta is not None and abs(poly_delta) > 1e-12 and abs(target_delta) > 1e-12:
                conflict = (poly_delta > 0) != (target_delta > 0)
                if conflict and abs(price_delta) > 1e-12:
                    if (price_delta > 0) == (poly_delta > 0):
                        follows = "POLY"
                    elif (price_delta > 0) == (target_delta > 0):
                        follows = "BINANCE_PREDICTION"
            rows.append({
                "asset": current["asset"],
                "durationMinutes": current["durationMinutes"],
                "marketId": current["marketId"],
                "side": current["side"],
                "previousHash": previous.get("orderHash"),
                "nextHash": current.get("orderHash"),
                "gapSeconds": (int(current["eventMs"]) - int(previous["eventMs"])) / 1000.0,
                "priceDelta": price_delta,
                "polyDelta": poly_delta,
                "binancePredictionDelta": target_delta,
                "priceSameSignAsPoly": poly_match,
                "priceSameSignAsBinancePrediction": target_match,
                "polyBinanceMovementConflict": conflict,
                "conflictFollows": follows,
            })
    def source_summary(source: str, match_field: str) -> dict[str, Any]:
        delta_field = "polyDelta" if source == "POLY" else "binancePredictionDelta"
        usable = [r for r in rows if r[match_field] is not None and finite(r.get(delta_field)) is not None]
        matches = [r for r in usable if r[match_field] is True]
        x = [float(r["priceDelta"]) for r in usable]
        y = [float(r[delta_field]) for r in usable]
        residuals = [abs(a - b) for a, b in zip(x, y)]
        return {
            "directionalTransitions": len(usable),
            "sameDirectionCount": len(matches),
            "sameDirectionShare": len(matches) / len(usable) if usable else None,
            "deltaCorrelation": pearson(x, y),
            "medianAbsDeltaDifference": median(residuals),
        }
    conflicts = [r for r in rows if r["polyBinanceMovementConflict"]]
    follow_counts = Counter(str(r.get("conflictFollows") or "NEITHER") for r in conflicts)
    return {
        "referenceOffsetSeconds": reference_offset,
        "transitionsWithSnapshots": len(rows),
        "vsPoly": source_summary("POLY", "priceSameSignAsPoly"),
        "vsBinancePrediction": source_summary("BINANCE_PREDICTION", "priceSameSignAsBinancePrediction"),
        "polyVsBinancePredictionMovementConflicts": {
            "count": len(conflicts),
            "followsPoly": follow_counts["POLY"],
            "followsBinancePrediction": follow_counts["BINANCE_PREDICTION"],
            "neitherOrFlat": follow_counts["NEITHER"],
            "followsPolyShare": follow_counts["POLY"] / len(conflicts) if conflicts else None,
            "followsBinancePredictionShare": follow_counts["BINANCE_PREDICTION"] / len(conflicts) if conflicts else None,
        },
        "examples": rows[:200],
    }


def taker_stale_quote_tests(events: list[dict[str, Any]], reference_offset: float, post_offsets: tuple[float, ...]) -> dict[str, Any]:
    pre_key = f"{reference_offset:g}s"
    by_post: dict[str, Any] = {}
    for post_offset in post_offsets:
        post_key = f"{post_offset:g}s"
        rows: list[dict[str, Any]] = []
        for event in events:
            pre = event.get("before", {}).get(pre_key)
            post = event.get("after", {}).get(post_key)
            if not isinstance(pre, dict) or not isinstance(post, dict):
                continue
            pre_poly = finite(pre.get("polyProbabilityForSide"))
            pre_target = finite(pre.get("binancePredictionProbabilityForSide"))
            post_poly = finite(post.get("polyProbabilityForSide"))
            post_target = finite(post.get("binancePredictionProbabilityForSide"))
            price = finite(event.get("price"))
            if pre_poly is None or pre_target is None or post_target is None or price is None:
                continue
            pre_gap = pre_poly - pre_target
            target_move = post_target - pre_target
            moved_toward_pre_poly = None
            if abs(pre_gap) > 1e-12 and abs(target_move) > 1e-12:
                moved_toward_pre_poly = (pre_gap > 0) == (target_move > 0)
            post_gap = post_poly - post_target if post_poly is not None else None
            catchup_reduced_gap = abs(post_gap) < abs(pre_gap) if post_gap is not None else None
            poly_edge = pre_poly - price
            rows.append({
                "asset": event["asset"],
                "durationMinutes": event["durationMinutes"],
                "marketId": event["marketId"],
                "side": event["side"],
                "eventAt": event["eventAt"],
                "price": price,
                "prePolyProbability": pre_poly,
                "preBinancePredictionProbability": pre_target,
                "prePolyMinusBinancePrediction": pre_gap,
                "prePolyEdgeVsExecutionPrice": poly_edge,
                "postBinancePredictionProbability": post_target,
                "postBinancePredictionMove": target_move,
                "postPolyProbability": post_poly,
                "movedTowardPrePoly": moved_toward_pre_poly,
                "polyBinanceGapReduced": catchup_reduced_gap,
            })
        positive_edge = [r for r in rows if float(r["prePolyEdgeVsExecutionPrice"]) > 0]
        one_cent = [r for r in rows if float(r["prePolyEdgeVsExecutionPrice"]) >= 0.01]
        toward = [r for r in rows if r["movedTowardPrePoly"] is not None]
        toward_true = [r for r in toward if r["movedTowardPrePoly"] is True]
        reduced = [r for r in rows if r["polyBinanceGapReduced"] is not None]
        reduced_true = [r for r in reduced if r["polyBinanceGapReduced"] is True]
        edge_and_toward = [r for r in one_cent if r["movedTowardPrePoly"] is True]
        by_post[post_key] = {
            "eventsWithPreAndPostSamples": len(rows),
            "prePolyPositiveEdgeShare": len(positive_edge) / len(rows) if rows else None,
            "prePolyAtLeast1CentEdgeShare": len(one_cent) / len(rows) if rows else None,
            "binancePredictionMovedTowardPrePolyShare": len(toward_true) / len(toward) if toward else None,
            "polyBinanceGapReducedShare": len(reduced_true) / len(reduced) if reduced else None,
            "atLeast1CentPolyEdgeAndTargetMovedTowardPolyShareOfAll": len(edge_and_toward) / len(rows) if rows else None,
            "medianPostBinancePredictionMove": median([finite(r["postBinancePredictionMove"]) for r in rows]),
            "examples": sorted(rows, key=lambda r: float(r["prePolyEdgeVsExecutionPrice"]), reverse=True)[:50],
        }
    return {"referencePreOffsetSeconds": reference_offset, "byPostOffset": by_post}


def role_transition_summary(maker_events: list[dict[str, Any]], taker_events: list[dict[str, Any]]) -> dict[str, Any]:
    streams: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in maker_events + taker_events:
        streams[int(event["marketId"])].append(event)
    transitions = Counter()
    gaps: dict[str, list[float]] = defaultdict(list)
    examples: list[dict[str, Any]] = []
    for market_id, stream in streams.items():
        stream.sort(key=lambda r: (int(r["eventMs"]), str(r["role"]), str(r.get("orderHash") or "")))
        for prev, cur in zip(stream, stream[1:]):
            label = f"{prev['role']}->{cur['role']}"
            gap = max(0.0, (int(cur["eventMs"]) - int(prev["eventMs"])) / 1000.0)
            transitions[label] += 1
            gaps[label].append(gap)
            if len(examples) < 100 and prev["role"] != cur["role"]:
                examples.append({
                    "marketId": market_id,
                    "asset": cur["asset"],
                    "durationMinutes": cur["durationMinutes"],
                    "fromRole": prev["role"],
                    "toRole": cur["role"],
                    "gapSeconds": gap,
                    "fromSide": prev["side"],
                    "toSide": cur["side"],
                    "fromPrice": prev.get("price"),
                    "toPrice": cur.get("price"),
                })
    summary: dict[str, Any] = {}
    for label in sorted(set(transitions) | set(gaps)):
        vals = gaps[label]
        summary[label] = {
            "count": transitions[label],
            "medianGapSeconds": median(vals),
            "within1sShare": sum(x <= 1 for x in vals) / len(vals) if vals else None,
            "within3sShare": sum(x <= 3 for x in vals) / len(vals) if vals else None,
            "within5sShare": sum(x <= 5 for x in vals) / len(vals) if vals else None,
        }
    return {"transitions": summary, "examples": examples}


def coverage_iso(value: int | None) -> str | None:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat() if value is not None else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only profiler for whether a Predict wallet behaves like a fair-value market maker / stale-quote taker. "
            "Aligns maker first fills and taker BUY fills with local multi_prediction_observer trajectories."
        )
    )
    parser.add_argument("--wallet", default=DEFAULT_WALLET)
    parser.add_argument("--maker-input", default="data/6da6_maker_hash_profile.json")
    parser.add_argument("--observer-db", default="data/multi_prediction_observer.db")
    parser.add_argument("--assets", default="BTC,ETH,BNB")
    parser.add_argument("--durations", default="5,15")
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--pages", type=int, default=80)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--offsets", default="0,0.5,1,2,3,5")
    parser.add_argument("--post-offsets", default="0.5,1,2,3,5")
    parser.add_argument("--reference-offset", type=float, default=1.0)
    parser.add_argument("--max-sample-age-ms", type=int, default=2500)
    parser.add_argument("--skip-takers", action="store_true")
    parser.add_argument("--output", default="data/6da6_fair_value_execution_profile.json")
    args = parser.parse_args()

    wallet = norm_address(args.wallet)
    if not wallet:
        raise SystemExit("--wallet must be a 0x-prefixed 20-byte address")
    assets = parse_assets(args.assets)
    durations = parse_durations(args.durations)
    if not assets:
        raise SystemExit("--assets must include one or more of BTC,ETH,BNB")
    if not durations:
        raise SystemExit("--durations must include at least one positive minute value")
    offsets = parse_offsets(args.offsets)
    post_offsets = parse_offsets(args.post_offsets)
    reference_offset = max(0.0, float(args.reference_offset))
    if reference_offset not in offsets:
        offsets = tuple(sorted(set(offsets + (reference_offset,))))
    max_age_ms = max(100, int(args.max_sample_age_ms))
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=max(0.1, args.hours))).timestamp() * 1000)

    maker_input = Path(args.maker_input)
    maker_events, recognized_scope = load_maker_events(maker_input, assets, durations, cutoff_ms)

    taker_events: list[dict[str, Any]] = []
    taker_parse_counts: dict[str, int] = {}
    taker_fetch_coverage: dict[str, Any] | None = None
    taker_fetch_note: str | None = None
    if not args.skip_takers:
        api_key = str(os.environ.get("PREDICT_FUN_API_KEY") or "").strip()
        if not api_key:
            taker_fetch_note = "PREDICT_FUN_API_KEY unavailable; maker-only analysis produced"
        else:
            matches, taker_fetch_coverage = fetch_taker_matches(
                wallet=wallet, api_key=api_key, hours=args.hours, pages=args.pages, page_size=args.page_size
            )
            taker_events, taker_parse_counts = extract_taker_buy_events(matches, wallet, assets, durations)

    index = TrajectoryIndex(Path(args.observer_db), assets)
    db_min_ms, db_max_ms = index.min_ms, index.max_ms
    db_coverage_by_asset = {
        asset: {
            "rows": meta["rows"],
            "markets": meta["markets"],
            "minUtc": coverage_iso(meta["minMs"]),
            "maxUtc": coverage_iso(meta["maxMs"]),
        }
        for asset, meta in index.coverage_by_asset.items()
    }
    available_columns = index.available_columns
    try:
        maker_aligned = [align_event(e, index, offsets=offsets, max_age_ms=max_age_ms) for e in maker_events]
        taker_aligned = [align_event(e, index, offsets=offsets, max_age_ms=max_age_ms) for e in taker_events]
    finally:
        index.close()

    by_scope: dict[str, Any] = {}
    for asset in sorted(assets):
        for duration in sorted(durations):
            label = f"{asset}-{duration}m"
            makers = [e for e in maker_aligned if e["asset"] == asset and e["durationMinutes"] == duration]
            takers = [e for e in taker_aligned if e["asset"] == asset and e["durationMinutes"] == duration]
            if not makers and not takers:
                continue
            by_scope[label] = {
                "makerFirstFill": summarize_event_edges(makers, offsets),
                "takerBuy": summarize_event_edges(takers, offsets),
                "makerRepricing": maker_repricing(makers, reference_offset),
                "takerStaleQuote": taker_stale_quote_tests(takers, reference_offset, post_offsets),
                "roleTransitions": role_transition_summary(makers, takers),
            }

    summary = {
        "wallet": wallet,
        "makerInput": str(maker_input),
        "observerDb": str(args.observer_db),
        "filter": f"assets={','.join(sorted(assets))}; durations={','.join(str(x) for x in sorted(durations))}m; BUY/BID only",
        "requestedHours": args.hours,
        "recognizedMakerInputScopeParentCounts": dict(sorted(recognized_scope.items())),
        "makerParentsInScope": len(maker_aligned),
        "takerBuysInScope": len(taker_aligned),
        "takerParseCounts": taker_parse_counts,
        "takerFetchCoverage": taker_fetch_coverage,
        "takerFetchNote": taker_fetch_note,
        "observerCoverageUtc": {"min": coverage_iso(db_min_ms), "max": coverage_iso(db_max_ms)},
        "observerCoverageByAsset": db_coverage_by_asset,
        "observerAvailableColumns": available_columns,
        "offsetSeconds": list(offsets),
        "postOffsetSeconds": list(post_offsets),
        "referenceOffsetSeconds": reference_offset,
        "maxObserverSampleAgeMs": max_age_ms,
        "makerFirstFill": summarize_event_edges(maker_aligned, offsets),
        "takerBuy": summarize_event_edges(taker_aligned, offsets),
        "makerRepricing": maker_repricing(maker_aligned, reference_offset),
        "takerStaleQuote": taker_stale_quote_tests(taker_aligned, reference_offset, post_offsets),
        "roleTransitions": role_transition_summary(maker_aligned, taker_aligned),
        "byScope": by_scope,
    }
    report = {
        "summary": summary,
        "interpretationGuide": {
            "makerRepricing": (
                "High maker price-delta correlation / same-direction share with an external fair-value proxy is compatible with "
                "continuous fair-value repricing. firstExecutedAt is a fill timestamp, not placement time, so this cannot prove causality."
            ),
            "takerStaleQuote": (
                "The strongest stale-quote pattern is: positive Poly fair edge before the Taker BUY, followed by the Binance-Prediction "
                "proxy moving toward that pre-trade Poly fair value and/or the Poly-vs-target gap shrinking after the fill."
            ),
            "roleSwitch": (
                "Fast MAKER->TAKER or TAKER->MAKER transitions are compatible with one fair-value engine choosing passive vs aggressive "
                "execution depending on available edge; they do not prove this exact internal architecture."
            ),
            "proxyCaveat": (
                "The local trajectory uses columns actually present in multi_prediction_observer.db. binancePredictionProbabilityForSide "
                "is a target-venue proxy from the recorded Binance Prediction feed; it is not assumed to be a direct Predict.fun websocket snapshot."
            ),
            "coverageCaveat": (
                "No missing observer history is fabricated. Check observerCoverageByAsset and takerFetchCoverage before interpreting null/low sample counts."
            ),
        },
        "makerEvents": maker_aligned,
        "takerEvents": taker_aligned,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"wrote {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
