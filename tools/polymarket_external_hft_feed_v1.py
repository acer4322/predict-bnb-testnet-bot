from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import zlib
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hftbacktest_execution_shift_audit_v0 as ex

DB_PATH = ROOT / "data" / "polymarket_btc5m_external_hft_v1.db"


def dec(blob: bytes | None) -> Any:
    if not blob:
        return None
    return json.loads(zlib.decompress(blob).decode("utf-8"))


def event_row_dual(ev: int, source_ms: int | None, received_ms: int, px: float, qty: float) -> np.void:
    row = np.zeros(1, ex.event_dtype)[0]
    row["ev"] = int(ev | ex.EXCH_EVENT | ex.LOCAL_EVENT)
    row["exch_ts"] = int(source_ms if source_ms is not None else received_ms) * 1_000_000
    row["local_ts"] = int(received_ms) * 1_000_000
    row["px"] = float(px)
    row["qty"] = float(qty)
    return row


def market_row(con: sqlite3.Connection, *, event_slug: str | None = None) -> sqlite3.Row:
    if event_slug:
        row = con.execute(
            "SELECT * FROM poly_external_markets_v1 WHERE event_slug=?",
            (str(event_slug),),
        ).fetchone()
    else:
        row = con.execute(
            "SELECT * FROM poly_external_markets_v1 ORDER BY last_seen_ms DESC LIMIT 1"
        ).fetchone()
    if row is None:
        raise RuntimeError("no Polymarket external-HFT market found")
    return row


def build_events(
    *,
    db_path: Path = DB_PATH,
    event_slug: str | None = None,
    include_down_trades: bool = True,
) -> tuple[np.ndarray, list[int], dict[str, Any]]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        market = market_row(con, event_slug=event_slug)
        condition_id = str(market["condition_id"])
        up_token = str(market["up_token_id"])
        down_token = str(market["down_token_id"])

        first = con.execute(
            """SELECT * FROM poly_external_events_v1
               WHERE condition_id=? AND event_type='book_checkpoint' AND outcome='UP'
               ORDER BY received_ms,id LIMIT 1""",
            (condition_id,),
        ).fetchone()
        if first is None:
            raise RuntimeError("no UP full-book checkpoint for external market")
        book = dec(first["payload_blob"]) or {}
        bids = book.get("bids") or []
        asks = book.get("asks") or []

        events: list[np.void] = []
        update_times: list[int] = [int(first["received_ms"])]
        for level in bids:
            events.append(event_row_dual(
                ex.DEPTH_SNAPSHOT_EVENT | ex.BUY_EVENT,
                int(first["source_ms"]) if first["source_ms"] is not None else None,
                int(first["received_ms"]),
                float(level["price"]),
                float(level["size"]),
            ))
        for level in asks:
            events.append(event_row_dual(
                ex.DEPTH_SNAPSHOT_EVENT | ex.SELL_EVENT,
                int(first["source_ms"]) if first["source_ms"] is not None else None,
                int(first["received_ms"]),
                float(level["price"]),
                float(level["size"]),
            ))

        rows = con.execute(
            """SELECT * FROM poly_external_events_v1
               WHERE condition_id=? AND (received_ms>? OR (received_ms=? AND id>?))
               ORDER BY received_ms,id""",
            (condition_id, int(first["received_ms"]), int(first["received_ms"]), int(first["id"])),
        ).fetchall()

        trade_hashes: set[str] = set()
        normalized_trades = 0
        up_trades = 0
        down_trades = 0
        price_changes = 0
        capture_latencies: list[int] = []

        for row in rows:
            typ = str(row["event_type"])
            received = int(row["received_ms"])
            source = int(row["source_ms"]) if row["source_ms"] is not None else None
            if source is not None:
                capture_latencies.append(received - source)

            if typ == "price_change_bundle":
                payload = dec(row["payload_blob"]) or {}
                for bundle_event in payload.get("events") or []:
                    ev_source = bundle_event.get("sourceMs")
                    ev_received = int(bundle_event.get("receivedMs") or received)
                    update_times.append(ev_received)
                    for ch in bundle_event.get("changes") or []:
                        if str(ch.get("outcome")) != "UP":
                            continue
                        side = str(ch.get("side") or "").upper()
                        if side not in {"BUY", "SELL"}:
                            continue
                        px = float(ch["price"])
                        qty = max(0.0, float(ch["size"]))
                        flag = ex.BUY_EVENT if side == "BUY" else ex.SELL_EVENT
                        events.append(event_row_dual(
                            ex.DEPTH_EVENT | flag,
                            int(ev_source) if ev_source is not None else None,
                            ev_received,
                            px,
                            qty,
                        ))
                        price_changes += 1
                continue

            if typ == "last_trade_price":
                tx = str(row["transaction_hash"] or "")
                if tx and tx in trade_hashes:
                    continue
                if tx:
                    trade_hashes.add(tx)
                outcome = str(row["outcome"] or "")
                side = str(row["native_side"] or "").upper()
                px = float(row["price"])
                qty = float(row["size"] or 0.0)
                if qty <= 0 or side not in {"BUY", "SELL"}:
                    continue
                if outcome == "UP":
                    native_px = px
                    native_side = side
                    up_trades += 1
                elif outcome == "DOWN" and include_down_trades:
                    native_px = 1.0 - px
                    native_side = "SELL" if side == "BUY" else "BUY"
                    down_trades += 1
                else:
                    continue
                flag = ex.BUY_EVENT if native_side == "BUY" else ex.SELL_EVENT
                events.append(event_row_dual(
                    ex.TRADE_EVENT | flag,
                    source,
                    received,
                    native_px,
                    qty,
                ))
                update_times.append(received)
                normalized_trades += 1

        arr = np.asarray(events, dtype=ex.event_dtype)
        if len(arr):
            arr.sort(order=["local_ts", "exch_ts"])
        lat_sorted = sorted(capture_latencies)

        def pct(values: list[int], q: float) -> float | None:
            if not values:
                return None
            pos = min(len(values) - 1, max(0, int(round((len(values) - 1) * q))))
            return float(values[pos])

        meta = {
            "version": "POLYMARKET_EXTERNAL_HFT_FEED_V1",
            "eventSlug": str(market["event_slug"]),
            "conditionId": condition_id,
            "marketId": str(market["market_id"]),
            "windowStartMs": int(market["window_start_ms"]),
            "windowEndMs": int(market["window_end_ms"]),
            "upTokenId": up_token,
            "downTokenId": down_token,
            "events": int(len(arr)),
            "priceChangeEvents": int(price_changes),
            "normalizedTrades": int(normalized_trades),
            "upTrades": int(up_trades),
            "downTradesTransformedToNativeUp": int(down_trades),
            "firstReceivedMs": int(first["received_ms"]),
            "lastReceivedMs": max(update_times) if update_times else int(first["received_ms"]),
            "captureLatencyMedianMs": statistics.median(capture_latencies) if capture_latencies else None,
            "captureLatencyP95Ms": pct(lat_sorted, 0.95),
            "timestampBasis": "exchange=Polymarket source timestamp when present; local=BTC5M Lab receipt timestamp",
            "bookMapping": "native asset = UP token; DOWN trade q maps to native UP price 1-q with opposite aggressor",
            "queueEvidence": "real L2 + explicit Polymarket last_trade_price size/side; no depth-depletion-as-trade synthesis",
            "readOnly": True,
        }
        return arr, sorted(set(update_times)), meta
    finally:
        con.close()


def smoke(event_slug: str | None = None) -> dict[str, Any]:
    events, _times, meta = build_events(event_slug=event_slug)
    if len(events) <= 0:
        raise RuntimeError("external feed produced no HftBacktest events")
    bt = ex.new_bt(events, entry_latency_ms=1092, response_latency_ms=273, queue_model="risk")
    try:
        ex.initialize_bt(bt)
        initial_ts = int(bt.current_timestamp)
        rc = int(bt.wait_next_feed(False, 1_000_000_000))
        return {
            "ok": True,
            "feed": meta,
            "hftConfig": {
                "entryLatencyMs": 1092,
                "responseLatencyMs": 273,
                "queueModel": "risk",
                "exchangeModel": "PartialFillExchange",
            },
            "initialTimestampNs": initial_ts,
            "nextFeedRc": rc,
        }
    finally:
        bt.close()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--event-slug")
    p.add_argument("--smoke", action="store_true")
    a = p.parse_args()
    if a.smoke:
        result = smoke(a.event_slug)
    else:
        _ev, _times, meta = build_events(event_slug=a.event_slug)
        result = {"ok": True, "feed": meta}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
