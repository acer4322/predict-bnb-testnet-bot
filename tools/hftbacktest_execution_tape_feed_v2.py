from __future__ import annotations

import argparse
import bisect
import json
import lzma
import statistics
from datetime import datetime, timezone
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
HFT_PATH = ROOT / ".tmp" / "hftbacktest_244"
if str(HFT_PATH) not in sys.path:
    sys.path.append(str(HFT_PATH))

import hftbacktest as hbt  # noqa: E402
from hftbacktest import (  # noqa: E402
    BUY_EVENT,
    DEPTH_EVENT,
    DEPTH_SNAPSHOT_EVENT,
    EXCH_EVENT,
    LOCAL_EVENT,
    SELL_EVENT,
    TRADE_EVENT,
    event_dtype,
)
try:
    from hftbacktest.data.validation import correct_event_order, validate_event_order  # type: ignore  # noqa: E402
    EVENT_ORDER_IMPL = "hftbacktest.data.validation"
except ModuleNotFoundError:
    EVENT_ORDER_IMPL = "embedded_2_4_4_semantics"

    def correct_event_order(
        data: np.ndarray,
        sorted_exch_index: np.ndarray,
        sorted_local_index: np.ndarray,
    ) -> np.ndarray:
        """Fallback with HftBacktest 2.4.4 dual-stream semantics.

        Each raw event represents one exchange occurrence and one later local
        observation. If exchange/local sort orders disagree, emit separate
        EXCH_EVENT and LOCAL_EVENT rows. If the same raw event is next in both
        streams, emit one combined event.
        """
        n = len(data)
        out = np.zeros(n * 2, event_dtype)
        oi = ei = li = 0
        while ei < n or li < n:
            if ei >= n:
                idx = int(sorted_local_index[li])
                out[oi] = data[idx]
                out[oi]["ev"] = int(out[oi]["ev"]) | int(LOCAL_EVENT)
                oi += 1
                li += 1
                continue
            if li >= n:
                idx = int(sorted_exch_index[ei])
                out[oi] = data[idx]
                out[oi]["ev"] = int(out[oi]["ev"]) | int(EXCH_EVENT)
                oi += 1
                ei += 1
                continue

            ex_idx = int(sorted_exch_index[ei])
            loc_idx = int(sorted_local_index[li])
            ex = data[ex_idx]
            loc = data[loc_idx]

            if ex_idx == loc_idx:
                out[oi] = ex
                out[oi]["ev"] = (
                    int(out[oi]["ev"]) | int(EXCH_EVENT) | int(LOCAL_EVENT)
                )
                oi += 1
                ei += 1
                li += 1
                continue

            ex_key = (int(ex["exch_ts"]), int(ex["local_ts"]), ex_idx)
            loc_key = (int(loc["exch_ts"]), int(loc["local_ts"]), loc_idx)
            if ex_key < loc_key:
                out[oi] = ex
                out[oi]["ev"] = int(out[oi]["ev"]) | int(EXCH_EVENT)
                oi += 1
                ei += 1
            else:
                out[oi] = loc
                out[oi]["ev"] = int(out[oi]["ev"]) | int(LOCAL_EVENT)
                oi += 1
                li += 1
        return out[:oi]

    def validate_event_order(data: np.ndarray) -> None:
        exch_mask = data["ev"] & EXCH_EVENT == EXCH_EVENT
        local_mask = data["ev"] & LOCAL_EVENT == LOCAL_EVENT
        if np.any(np.diff(data["exch_ts"][exch_mask]) < 0):
            raise ValueError("exchange events are out of order.")
        if np.any(np.diff(data["local_ts"][local_mask]) < 0):
            raise ValueError("local events are out of order.")

ARCHIVE_DIR = ROOT / "data" / "execution_tape_v1" / "markets"
OUT_DIR = ROOT / "data" / "research" / "hftbacktest_execution_shift_v0"


def _iso_ms(value: str) -> int:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _wei(value: Any) -> float:
    return float(int(str(value))) / 1e18


def _normalize_match(row: dict[str, Any]) -> dict[str, Any] | None:
    """Equivalent to hftbacktest_true_match_calibration_v0.normalize_match."""
    taker = row.get("taker") if isinstance(row.get("taker"), dict) else {}
    outcome = taker.get("outcome") if isinstance(taker.get("outcome"), dict) else {}
    name = str(outcome.get("name") or "").strip().upper()
    quote = str(taker.get("quoteType") or "").strip().upper()
    if name not in {"UP", "DOWN", "YES", "NO"} or quote not in {"BID", "ASK"}:
        return None
    px = _wei(row.get("priceExecuted"))
    qty = _wei(row.get("amountFilled"))
    if qty <= 0 or not 0 < px < 1:
        return None
    is_up = name in {"UP", "YES"}
    native_px = px if is_up else 1.0 - px
    if is_up:
        aggressor = "BUY" if quote == "BID" else "SELL"
    else:
        aggressor = "SELL" if quote == "BID" else "BUY"
    return {
        "executedAt": row.get("executedAt"),
        "tsMs": _iso_ms(row.get("executedAt")),
        "qty": qty,
        "outcome": name,
        "quoteType": quote,
        "outcomePrice": px,
        "nativeYesPrice": round(native_px, 12),
        "nativeAggressor": aggressor,
        "transactionHash": row.get("transactionHash"),
        "makerCount": len(row.get("makers") or []),
    }


def _correct_event_order(data: np.ndarray, sorted_exch_index: np.ndarray, sorted_local_index: np.ndarray) -> np.ndarray:
    """Equivalent to HftBacktest 2.4.4 data.validation.correct_event_order."""
    sorted_final = np.zeros(data.shape[0] * 2, event_dtype)
    out_rn = 0
    exch_rn = 0
    local_rn = 0
    n = len(data)
    while True:
        exch_done = exch_rn >= n
        local_done = local_rn >= n
        if exch_done and local_done:
            break
        sorted_exch = None if exch_done else data[sorted_exch_index[exch_rn]]
        sorted_local = None if local_done else data[sorted_local_index[local_rn]]
        if (not exch_done and not local_done and
            sorted_exch["exch_ts"] == sorted_local["exch_ts"] and
            sorted_exch["local_ts"] == sorted_local["local_ts"]):
            if int(sorted_exch["ev"]) != int(sorted_local["ev"]):
                raise AssertionError("event flags mismatch during correction")
            sorted_final[out_rn] = sorted_exch
            sorted_final[out_rn]["ev"] = int(sorted_final[out_rn]["ev"]) | EXCH_EVENT | LOCAL_EVENT
            out_rn += 1; exch_rn += 1; local_rn += 1
        elif (not exch_done and (
            local_done or
            (sorted_exch["exch_ts"] < sorted_local["exch_ts"]) or
            (sorted_exch["exch_ts"] == sorted_local["exch_ts"] and sorted_exch["local_ts"] < sorted_local["local_ts"])
        )):
            sorted_final[out_rn] = sorted_exch
            sorted_final[out_rn]["ev"] = int(sorted_final[out_rn]["ev"]) | EXCH_EVENT
            out_rn += 1; exch_rn += 1
        elif not local_done:
            sorted_final[out_rn] = sorted_local
            sorted_final[out_rn]["ev"] = int(sorted_final[out_rn]["ev"]) | LOCAL_EVENT
            out_rn += 1; local_rn += 1
        else:
            raise AssertionError("unreachable event-order state")
    return sorted_final[:out_rn]


def _validate_event_order(data: np.ndarray) -> None:
    exch_ev = data["ev"] & EXCH_EVENT == EXCH_EVENT
    local_ev = data["ev"] & LOCAL_EVENT == LOCAL_EVENT
    if np.sum(np.diff(data["exch_ts"][exch_ev]) < 0) > 0:
        raise ValueError("exchange events are out of order.")
    if np.sum(np.diff(data["local_ts"][local_ev]) < 0) > 0:
        raise ValueError("local events are out of order.")


def _load_archive(path: Path) -> dict[str, Any]:
    return json.loads(lzma.decompress(path.read_bytes()).decode("utf-8"))


def _event_row(
    ev: int,
    exch_ts_ns: int,
    local_ts_ns: int,
    px: float,
    qty: float,
) -> np.void:
    row = np.zeros(1, event_dtype)[0]
    # IMPORTANT: do not set EXCH_EVENT / LOCAL_EVENT here.
    # correct_event_order() adds them, and splits one raw row into separate
    # exchange/local events when the two clocks imply different orderings.
    row["ev"] = int(ev)
    row["exch_ts"] = int(exch_ts_ns)
    row["local_ts"] = int(local_ts_ns)
    row["px"] = float(px)
    row["qty"] = float(qty)
    return row


def _trade_offset_ns(policy: str, index: int) -> int:
    i = min(int(index), 999)
    if policy == "early":
        return i * 1_000
    if policy == "mid":
        return 500_000_000 + i * 1_000
    if policy == "late":
        return 999_000_000 + i * 1_000
    raise ValueError(policy)


class CausalLagMapper:
    """Align the source clock, then estimate strict-past residual feed latency.

    Predict L2 source timestamps have a stable positive offset versus the
    collector/local UTC clock. Treating the full received-source gap as network
    latency creates an artificial ~2.3s delay. We first align source time by the
    minimum observed nonnegative gap in that market; the remaining
    received-aligned_exchange gap is the replay feed latency.

    For a true-match event, use the latest residual L2 feed latency whose
    aligned exchange timestamp is <= that match timestamp.
    """

    def __init__(self, rows: list[Any]):
        raw_gaps = [int(r[1]) - int(r[0]) for r in rows]
        if not raw_gaps:
            raise RuntimeError("cannot build lag mapper without L2 rows")
        if min(raw_gaps) < 0:
            raise RuntimeError("source timestamp is ahead of received timestamp")
        self.clock_offset_ms = int(min(raw_gaps))
        points = sorted(
            (
                int(r[0]) + self.clock_offset_ms,
                int(r[1]),
                int(r[1]) - (int(r[0]) + self.clock_offset_ms),
            )
            for r in rows
        )
        if any(p[2] < 0 for p in points):
            raise RuntimeError("negative residual feed latency after clock alignment")
        self.sources = [p[0] for p in points]
        self.lags = [p[2] for p in points]

    def aligned_exchange_ms(self, source_ms: int) -> int:
        return int(source_ms) + self.clock_offset_ms

    def lag_ms(self, exchange_ms: int) -> int:
        i = bisect.bisect_right(self.sources, int(exchange_ms)) - 1
        if i < 0:
            return int(self.lags[0])
        return int(self.lags[i])

def _quantile(values: list[int], p: float) -> int | None:
    if not values:
        return None
    vals = sorted(values)
    return int(vals[min(len(vals) - 1, int((len(vals) - 1) * p))])


def _count_reversals(values: np.ndarray) -> int:
    if len(values) < 2:
        return 0
    return int(np.sum(np.diff(values) < 0))


def build_archive_events(
    market_id: int,
    *,
    trade_offset: str = "mid",
    archive_dir: Path | None = None,
    trade_local_policy: str = "causal_l2_lag",
) -> tuple[np.ndarray, list[int], dict[str, Any]]:
    archive_root = Path(archive_dir) if archive_dir is not None else ARCHIVE_DIR
    path = archive_root / f"{int(market_id)}.json.xz"
    if not path.exists():
        raise RuntimeError(f"execution tape archive missing for market {market_id}")

    tape = _load_archive(path)
    rows = list(tape.get("updates") or [])
    if not rows:
        raise RuntimeError("archive has no L2 updates")

    # Archive rows are originally persisted in source/exchange order. Keep that
    # semantic for book-delta construction; local arrival ordering is handled
    # later by HftBacktest's correct_event_order().
    rows.sort(key=lambda r: (int(r[0]), int(r[1])))
    first = next(
        (
            r
            for r in rows
            if int(r[3]) == 1 and r[4] is not None and r[5] is not None
        ),
        None,
    )
    if first is None:
        raise RuntimeError("archive has no full L2 checkpoint")

    lag_mapper = CausalLagMapper(rows)
    raw_events: list[np.void] = []

    first_ex_ns = lag_mapper.aligned_exchange_ms(int(first[0])) * 1_000_000
    first_loc_ns = int(first[1]) * 1_000_000
    for p, q in (first[4] or {}).items():
        raw_events.append(
            _event_row(
                DEPTH_SNAPSHOT_EVENT | BUY_EVENT,
                first_ex_ns,
                first_loc_ns,
                float(p),
                float(q),
            )
        )
    for p, q in (first[5] or {}).items():
        raw_events.append(
            _event_row(
                DEPTH_SNAPSHOT_EVENT | SELL_EVENT,
                first_ex_ns,
                first_loc_ns,
                float(p),
                float(q),
            )
        )

    local_update_times = [int(first[1])]
    negative_depth = 0.0
    passed = False
    for r in rows:
        if not passed:
            if r is first:
                passed = True
            continue
        ex_ms = lag_mapper.aligned_exchange_ms(int(r[0]))
        loc_ms = int(r[1])
        local_update_times.append(loc_ms)
        changes = r[6] or {}
        for side, flag in (("bids", BUY_EVENT), ("asks", SELL_EVENT)):
            for item in changes.get(side, []) or []:
                p, before, after, delta = map(float, item)
                negative_depth += max(0.0, -delta)
                raw_events.append(
                    _event_row(
                        DEPTH_EVENT | flag,
                        ex_ms * 1_000_000,
                        loc_ms * 1_000_000,
                        p,
                        max(0.0, after),
                    )
                )

    trades: list[dict[str, Any]] = []
    for raw in tape.get("matches") or []:
        n = _normalize_match(raw)
        if n is not None:
            trades.append(n)
    trades.sort(
        key=lambda x: (
            int(x["tsMs"]),
            str(x.get("transactionHash") or ""),
            float(x["nativeYesPrice"]),
            float(x["qty"]),
        )
    )

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        grouped[int(t["tsMs"])].append(t)

    trade_lags: list[int] = []
    trade_local_times: list[int] = []
    for ts_ms, vals in grouped.items():
        for i, t in enumerate(vals):
            offset_ns = _trade_offset_ns(trade_offset, i)
            exch_ns = int(ts_ms) * 1_000_000 + offset_ns
            exch_ms_for_lag = int(exch_ns // 1_000_000)
            if trade_local_policy != "causal_l2_lag":
                raise ValueError(trade_local_policy)
            lag_ms = lag_mapper.lag_ms(exch_ms_for_lag)
            local_ns = exch_ns + int(lag_ms) * 1_000_000
            trade_lags.append(lag_ms)
            trade_local_times.append(int(local_ns // 1_000_000))
            raw_events.append(
                _event_row(
                    TRADE_EVENT
                    | (BUY_EVENT if t["nativeAggressor"] == "BUY" else SELL_EVENT),
                    exch_ns,
                    local_ns,
                    float(t["nativeYesPrice"]),
                    float(t["qty"]),
                )
            )

    raw_arr = np.asarray(raw_events, dtype=event_dtype)
    if len(raw_arr) == 0:
        raise RuntimeError("no replay events")

    feed_latencies_ns = raw_arr["local_ts"] - raw_arr["exch_ts"]
    if np.any(feed_latencies_ns < 0):
        raise RuntimeError("negative feed latency in raw dual-clock tape")

    exch_idx = np.argsort(raw_arr["exch_ts"], kind="mergesort")
    local_idx = np.argsort(raw_arr["local_ts"], kind="mergesort")

    # Diagnostics before correction: these are expected to be non-zero when
    # exchange and local streams experience different arrival ordering.
    local_sorted_exchange_reversals = _count_reversals(
        raw_arr["exch_ts"][local_idx]
    )
    exchange_sorted_local_reversals = _count_reversals(
        raw_arr["local_ts"][exch_idx]
    )

    corrected = _correct_event_order(raw_arr, exch_idx, local_idx)
    _validate_event_order(corrected)

    exch_mask = corrected["ev"] & EXCH_EVENT == EXCH_EVENT
    local_mask = corrected["ev"] & LOCAL_EVENT == LOCAL_EVENT
    both_mask = exch_mask & local_mask

    corrected_exchange_reversals = _count_reversals(
        corrected["exch_ts"][exch_mask]
    )
    corrected_local_reversals = _count_reversals(
        corrected["local_ts"][local_mask]
    )
    if corrected_exchange_reversals or corrected_local_reversals:
        raise RuntimeError(
            "correct_event_order returned a non-monotonic stream: "
            f"exchange={corrected_exchange_reversals}, "
            f"local={corrected_local_reversals}"
        )

    raw_source_gaps = [int(r[1]) - int(r[0]) for r in rows]
    l2_lags = [
        int(r[1]) - lag_mapper.aligned_exchange_ms(int(r[0]))
        for r in rows
    ]
    all_local_times = sorted(
        set(local_update_times + trade_local_times)
    )

    meta = {
        "version": "HFTBACKTEST_EXECUTION_TAPE_FEED_V2",
        "eventOrderImplementation": EVENT_ORDER_IMPL,
        "marketId": int(market_id),
        "archivePath": str(path),
        "archiveVersion": tape.get("version"),
        "updates": len(rows),
        "executionMetaRows": len(tape.get("executionMeta") or []),
        "rawMatchRows": len(tape.get("matches") or []),
        "normalizedTrades": len(trades),
        "trueMatchQty": sum(float(t["qty"]) for t in trades),
        "rawEvents": int(len(raw_arr)),
        "correctedEvents": int(len(corrected)),
        "bothClockEvents": int(np.sum(both_mask)),
        "exchangeOnlyEvents": int(np.sum(exch_mask & ~local_mask)),
        "localOnlyEvents": int(np.sum(local_mask & ~exch_mask)),
        "firstSourceMs": int(first[0]),
        "firstAlignedExchangeMs": lag_mapper.aligned_exchange_ms(int(first[0])),
        "firstReceivedMs": int(first[1]),
        "lastSourceMs": max(int(r[0]) for r in rows),
        "lastAlignedExchangeMs": max(
            lag_mapper.aligned_exchange_ms(int(r[0])) for r in rows
        ),
        "lastReceivedMs": max(int(r[1]) for r in rows),
        "negativeDepthQty": negative_depth,
        "timestampBasis": (
            "L2 exch_ts=source_timestamp_ms+market clock offset; "
            "L2 local_ts=received_at_ms; clock offset=min(received-source) "
            "within the market; true-match exch_ts=executedAt+trade offset; "
            "true-match local_ts=exchange timestamp plus latest strict-past "
            "residual aligned L2 feed latency"
        ),
        "tradeOffsetPolicy": trade_offset,
        "tradeLocalPolicy": trade_local_policy,
        "pendingMetadataUsedAsDepth": False,
        "settlementMetadataUsedForTradeTiming": False,
        "clockAudit": {
            "sourceClockOffsetMs": lag_mapper.clock_offset_ms,
            "rawSourceReceiptGapMedianMs": int(statistics.median(raw_source_gaps)),
            "rawSourceReceiptGapP05Ms": _quantile(raw_source_gaps, 0.05),
            "rawSourceReceiptGapP95Ms": _quantile(raw_source_gaps, 0.95),
            "rawNegativeLatencyEvents": int(np.sum(feed_latencies_ns < 0)),
            "localSortedExchangeReversalsBeforeCorrection": local_sorted_exchange_reversals,
            "exchangeSortedLocalReversalsBeforeCorrection": exchange_sorted_local_reversals,
            "correctedExchangeReversals": corrected_exchange_reversals,
            "correctedLocalReversals": corrected_local_reversals,
            "l2LagMedianMs": int(statistics.median(l2_lags)),
            "l2LagP05Ms": _quantile(l2_lags, 0.05),
            "l2LagP95Ms": _quantile(l2_lags, 0.95),
            "l2LagMinMs": min(l2_lags),
            "l2LagMaxMs": max(l2_lags),
            "tradeLagMedianMs": (
                int(statistics.median(trade_lags)) if trade_lags else None
            ),
            "tradeLagP05Ms": _quantile(trade_lags, 0.05),
            "tradeLagP95Ms": _quantile(trade_lags, 0.95),
            "eventOrderValidation": "PASS",
        },
    }
    return corrected, all_local_times, meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-id", type=int, required=True)
    ap.add_argument(
        "--trade-offset",
        choices=["early", "mid", "late"],
        default="mid",
    )
    ap.add_argument(
        "--archive-dir",
        type=Path,
        default=None,
    )
    args = ap.parse_args()

    events, _, meta = build_archive_events(
        args.market_id,
        trade_offset=args.trade_offset,
        archive_dir=args.archive_dir,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "marketId": args.market_id,
                "events": len(events),
                "feed": meta,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
