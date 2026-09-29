from __future__ import annotations

"""Build Market Capsule + Decision Seam V1 from a compact source bundle.

This tool is intentionally worker-friendly: source SQLite databases are not
required.  It reconstructs each execution tape once, materializes strict-past
Target decision seams, then converts the reusable analytical tables to Parquet
with DuckDB.  Canonical raw tape JSON.XZ remains the source of truth.
"""

import argparse
import bisect
import hashlib
import json
import lzma
import os
import shutil
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

VERSION = "BTC5M_MARKET_CAPSULE_DECISION_SEAM_V1"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> tuple[int, int]:
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str))
            fh.write("\n")
            n += 1
    return n, path.stat().st_size


def _levels(value: Any) -> dict[float, float]:
    out: dict[float, float] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            try:
                p, q = float(k), float(v)
            except (TypeError, ValueError):
                continue
            if q > 1e-12:
                out[p] = q
    elif isinstance(value, list):
        for item in value:
            try:
                if isinstance(item, dict):
                    p = float(item.get("price"))
                    q = float(item.get("size") if item.get("size") is not None else item.get("quantity"))
                else:
                    p, q = float(item[0]), float(item[1])
            except (TypeError, ValueError, IndexError):
                continue
            if q > 1e-12:
                out[p] = q
    return out


def _top_json(book: dict[float, float], *, reverse: bool, n: int = 5) -> str:
    rows = [[p, book[p]] for p in sorted(book, reverse=reverse)[:n]]
    return json.dumps(rows, separators=(",", ":"))


def _book_metrics(market_id: int, update: list[Any], bids: dict[float, float], asks: dict[float, float]) -> dict[str, Any]:
    source_ms, received_ms, order_count, is_checkpoint = int(update[0]), int(update[1]), int(update[2]), int(update[3])
    best_bid = max(bids) if bids else None
    best_ask = min(asks) if asks else None
    spread = (best_ask - best_bid) if best_bid is not None and best_ask is not None else None
    return {
        "market_id": market_id,
        "source_ms": source_ms,
        "received_ms": received_ms,
        "order_count": order_count,
        "is_checkpoint": is_checkpoint,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "bid_depth_total": sum(bids.values()),
        "ask_depth_total": sum(asks.values()),
        "bid_level_count": len(bids),
        "ask_level_count": len(asks),
        "top5_bids_json": _top_json(bids, reverse=True),
        "top5_asks_json": _top_json(asks, reverse=False),
        "changes_json": json.dumps(update[6] if len(update) > 6 else {}, separators=(",", ":")),
    }


def _reconstruct_tape(path: Path, market_id: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(lzma.decompress(path.read_bytes()).decode("utf-8"))
    updates = payload.get("updates") if isinstance(payload.get("updates"), list) else []
    bids: dict[float, float] = {}
    asks: dict[float, float] = {}
    rows: list[dict[str, Any]] = []
    checkpoints = 0
    reset_failures = 0
    for raw in updates:
        if not isinstance(raw, list) or len(raw) < 7:
            continue
        checkpoint = bool(int(raw[3]))
        if checkpoint:
            nb, na = _levels(raw[4]), _levels(raw[5])
            if nb or na:
                bids, asks = nb, na
                checkpoints += 1
            elif not rows:
                reset_failures += 1
        if not checkpoint or (not bids and not asks):
            changes = raw[6] if isinstance(raw[6], dict) else {}
            for key, book in (("bids", bids), ("asks", asks)):
                for change in changes.get(key, []) if isinstance(changes.get(key), list) else []:
                    if not isinstance(change, list) or len(change) < 3:
                        continue
                    try:
                        price = float(change[0])
                        after = float(change[2])
                    except (TypeError, ValueError):
                        continue
                    if after <= 1e-12:
                        book.pop(price, None)
                    else:
                        book[price] = after
        rows.append(_book_metrics(market_id, raw, bids, asks))
    info = {
        "marketId": market_id,
        "updates": len(rows),
        "checkpoints": checkpoints,
        "resetFailures": reset_failures,
        "executionMetaRows": len(payload.get("executionMeta") or []),
        "matchRows": len(payload.get("matches") or []),
    }
    return rows, info


def _prior_row(rows: list[dict[str, Any]], times: list[int], boundary_ms: int, field: str) -> dict[str, Any] | None:
    i = bisect.bisect_left(times, int(boundary_ms)) - 1
    if i < 0:
        return None
    row = rows[i]
    if int(row[field]) >= int(boundary_ms):
        raise AssertionError(f"strict-past violation {field}: {row[field]} >= {boundary_ms}")
    return row


def _portfolio(state: dict[str, Any]) -> dict[str, float]:
    net_cost = float(state["buy_notional"]) - float(state["sell_proceeds"])
    pnl_up = float(state["up_shares"]) - net_cost
    pnl_down = float(state["down_shares"]) - net_cost
    return {
        "up_shares": float(state["up_shares"]),
        "down_shares": float(state["down_shares"]),
        "net_cost": net_cost,
        "pnl_if_up": pnl_up,
        "pnl_if_down": pnl_down,
        "floor": min(pnl_up, pnl_down),
        "upside": max(pnl_up, pnl_down),
        "surplus": abs(pnl_up - pnl_down),
        "share_gap": float(state["up_shares"]) - float(state["down_shares"]),
        "abs_share_gap": abs(float(state["up_shares"]) - float(state["down_shares"])),
    }


def _apply_action(state: dict[str, Any], action: dict[str, Any]) -> None:
    side = str(action.get("side") or "").upper()
    quote = str(action.get("quote_type") or "").upper()
    shares = float(action.get("shares") or 0.0)
    price = float(action.get("price") or 0.0)
    if side not in {"UP", "DOWN"} or shares <= 0:
        return
    key = "up_shares" if side == "UP" else "down_shares"
    if quote == "BID":
        state[key] += shares
        state["buy_notional"] += shares * price
    elif quote == "ASK":
        state[key] -= shares
        state["sell_proceeds"] += shares * price


def _copy_label_fields(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "action_source_leg_id": action.get("source_leg_id"),
        "action_role": action.get("role"),
        "action_quote_type": action.get("quote_type"),
        "action_side": action.get("side"),
        "action_order_hash": action.get("order_hash"),
        "action_event_ms": int(action.get("event_ms") or 0),
        "action_observed_at_ms": int(action.get("observed_at_ms") or 0),
        "action_price": float(action.get("price") or 0.0),
        "action_shares": float(action.get("shares") or 0.0),
    }


def _book_fields(prefix: str, row: dict[str, Any] | None, event_ms: int) -> dict[str, Any]:
    if row is None:
        return {
            f"{prefix}_book_source_ms": None,
            f"{prefix}_book_received_ms": None,
            f"{prefix}_book_age_ms": None,
            f"{prefix}_book_received_age_ms": None,
            f"{prefix}_best_bid": None,
            f"{prefix}_best_ask": None,
            f"{prefix}_spread": None,
            f"{prefix}_bid_depth_total": None,
            f"{prefix}_ask_depth_total": None,
            f"{prefix}_order_count": None,
        }
    return {
        f"{prefix}_book_source_ms": int(row["source_ms"]),
        f"{prefix}_book_received_ms": int(row["received_ms"]),
        f"{prefix}_book_age_ms": int(event_ms) - int(row["source_ms"]),
        f"{prefix}_book_received_age_ms": int(event_ms) - int(row["received_ms"]),
        f"{prefix}_best_bid": row.get("best_bid"),
        f"{prefix}_best_ask": row.get("best_ask"),
        f"{prefix}_spread": row.get("spread"),
        f"{prefix}_bid_depth_total": row.get("bid_depth_total"),
        f"{prefix}_ask_depth_total": row.get("ask_depth_total"),
        f"{prefix}_order_count": row.get("order_count"),
    }


def _make_seams(
    market_id: int,
    actions: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
    public_rows: list[dict[str, Any]],
    window_end_ms: int | None,
) -> list[dict[str, Any]]:
    source_rows = sorted(book_rows, key=lambda r: (int(r["source_ms"]), int(r["received_ms"])))
    source_times = [int(r["source_ms"]) for r in source_rows]
    receipt_rows = sorted(book_rows, key=lambda r: (int(r["received_ms"]), int(r["source_ms"])))
    receipt_times = [int(r["received_ms"]) for r in receipt_rows]
    public_rows = sorted(public_rows, key=lambda r: (int(r.get("sampled_at_ms") or 0), int(r.get("id") or 0)))
    public_times = [int(r.get("sampled_at_ms") or 0) for r in public_rows]
    actions = sorted(actions, key=lambda r: (int(r.get("event_ms") or 0), str(r.get("source_leg_id") or "")))

    state: dict[str, Any] = {
        "up_shares": 0.0,
        "down_shares": 0.0,
        "buy_notional": 0.0,
        "sell_proceeds": 0.0,
        "prior_fills": 0,
        "prior_parent_ids": set(),
        "last_action": None,
    }
    seams: list[dict[str, Any]] = []
    i = 0
    while i < len(actions):
        event_ms = int(actions[i].get("event_ms") or 0)
        j = i + 1
        while j < len(actions) and int(actions[j].get("event_ms") or 0) == event_ms:
            j += 1
        group = actions[i:j]
        port = _portfolio(state)
        src = _prior_row(source_rows, source_times, event_ms, "source_ms")
        rec = _prior_row(receipt_rows, receipt_times, event_ms, "received_ms")
        pub = _prior_row(public_rows, public_times, event_ms, "sampled_at_ms")
        last = state.get("last_action") if isinstance(state.get("last_action"), dict) else None
        for action in group:
            seam: dict[str, Any] = {
                "market_id": market_id,
                "seam_id": f"{market_id}:{action.get('source_leg_id')}",
                "same_timestamp_action_count": len(group),
                "seconds_left": ((int(window_end_ms) - event_ms) / 1000.0) if window_end_ms else None,
                "target_prior_fill_legs": int(state["prior_fills"]),
                "target_prior_parent_count": len(state["prior_parent_ids"]),
                "previous_action_event_ms": int(last.get("event_ms")) if last else None,
                "previous_action_role": last.get("role") if last else None,
                "previous_action_side": last.get("side") if last else None,
                "previous_action_quote_type": last.get("quote_type") if last else None,
                "previous_action_age_ms": event_ms - int(last.get("event_ms")) if last else None,
                **{f"pre_{k}": v for k, v in port.items()},
                **_copy_label_fields(action),
                **_book_fields("source_strict", src, event_ms),
                **_book_fields("receipt_strict", rec, event_ms),
                "public_sample_id": int(pub.get("id")) if pub else None,
                "public_sampled_at_ms": int(pub.get("sampled_at_ms")) if pub else None,
                "public_age_ms": event_ms - int(pub.get("sampled_at_ms")) if pub else None,
                "public_timestamp_ns": int(pub.get("timestamp_ns")) if pub and pub.get("timestamp_ns") is not None else None,
                "public_seconds_left": float(pub.get("seconds_left")) if pub and pub.get("seconds_left") is not None else None,
            }
            seams.append(seam)
        for action in group:
            _apply_action(state, action)
            state["prior_fills"] += 1
            state["prior_parent_ids"].add(str(action.get("order_hash") or action.get("source_leg_id") or ""))
        state["last_action"] = group[-1]
        i = j
    return seams


def _duckdb() -> Any:
    try:
        import duckdb  # type: ignore
    except ImportError as exc:
        raise RuntimeError("duckdb is required on the worker: python -m pip install duckdb") from exc
    return duckdb


def _sql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def _jsonl_to_parquet(con: Any, src: Path, dst: Path) -> None:
    if not src.exists() or src.stat().st_size == 0:
        return
    con.execute(
        f"COPY (SELECT * FROM read_json_auto('{_sql_path(src)}', format='newline_delimited')) "
        f"TO '{_sql_path(dst)}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )


def _query_benchmark(con: Any, parquet: Path, jsonl: Path, repeats: int = 25) -> dict[str, Any]:
    pq = _sql_path(parquet)
    js = _sql_path(jsonl)
    query_pq = f"SELECT action_role,action_side,count(*) n,avg(source_strict_book_age_ms) age FROM read_parquet('{pq}') GROUP BY 1,2 ORDER BY 1,2"
    query_js = f"SELECT action_role,action_side,count(*) n,avg(source_strict_book_age_ms) age FROM read_json_auto('{js}', format='newline_delimited') GROUP BY 1,2 ORDER BY 1,2"
    con.execute(query_pq).fetchall()
    con.execute(query_js).fetchall()
    t = time.perf_counter()
    for _ in range(repeats):
        con.execute(query_pq).fetchall()
    pq_s = time.perf_counter() - t
    t = time.perf_counter()
    for _ in range(repeats):
        con.execute(query_js).fetchall()
    js_s = time.perf_counter() - t
    return {
        "repeats": repeats,
        "parquetTotalSeconds": pq_s,
        "jsonlTotalSeconds": js_s,
        "parquetMsPerQuery": pq_s * 1000 / repeats,
        "jsonlMsPerQuery": js_s * 1000 / repeats,
        "speedupJsonlOverParquet": (js_s / pq_s) if pq_s > 0 else None,
        "sampleResult": [list(r) for r in con.execute(query_pq).fetchall()],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=VERSION)
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--benchmark-repeats", type=int, default=25)
    ap.add_argument("--keep-jsonl", action="store_true", help="retain auditable JSONL intermediates after benchmarking")
    ap.add_argument("--overwrite", action="store_true")
    ns = ap.parse_args()
    bundle = ns.bundle.resolve()
    if not (bundle / "manifest.json").exists():
        raise FileNotFoundError(f"bundle manifest missing: {bundle}")
    result_env = os.environ.get("BTC5M_LAN_RESULT_DIR")
    out = ns.output.resolve() if ns.output else (Path(result_env).resolve() if result_env else bundle.parent / "capsule_output")
    if out.exists() and any(out.iterdir()):
        if not ns.overwrite and not result_env:
            raise FileExistsError(f"output exists: {out}; use --overwrite")
        if ns.overwrite and not result_env:
            shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    work = out / "_jsonl"
    work.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    markets = manifest.get("markets") if isinstance(manifest.get("markets"), list) else []
    market_by_id = {int(m["market_id"]): m for m in markets if isinstance(m, dict) and m.get("market_id") is not None}
    target_actions = _load_jsonl(bundle / "target_actions.jsonl")
    target_parents = _load_jsonl(bundle / "target_parents.jsonl")
    public_snapshots = _load_jsonl(bundle / "public_snapshots.jsonl")
    market_results = _load_jsonl(bundle / "market_results.jsonl")
    actions_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    public_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in target_actions:
        actions_by_market[int(row["market_id"])].append(row)
    for row in public_snapshots:
        public_by_market[int(row["market_id"])].append(row)

    reconstruct_t = time.perf_counter()
    all_books: list[dict[str, Any]] = []
    all_seams: list[dict[str, Any]] = []
    market_summaries: list[dict[str, Any]] = []
    reconstruction: list[dict[str, Any]] = []
    for market_id in [int(x) for x in manifest.get("marketIds", [])]:
        tape = bundle / "tapes" / f"{market_id}.json.xz"
        books, info = _reconstruct_tape(tape, market_id)
        all_books.extend(books)
        m = market_by_id.get(market_id, {})
        seams = _make_seams(
            market_id,
            actions_by_market.get(market_id, []),
            books,
            public_by_market.get(market_id, []),
            int(m["window_end_ms"]) if m.get("window_end_ms") is not None else None,
        )
        all_seams.extend(seams)
        reconstruction.append(info)
        market_summaries.append({
            "market_id": market_id,
            "window_start_ms": m.get("window_start_ms"),
            "window_end_ms": m.get("window_end_ms"),
            "quality_status": m.get("quality_status"),
            "target_actions": len(actions_by_market.get(market_id, [])),
            "public_snapshots": len(public_by_market.get(market_id, [])),
            "book_updates": len(books),
            "decision_seams": len(seams),
            "tape_bytes": tape.stat().st_size,
        })
    reconstruction_seconds = time.perf_counter() - reconstruct_t

    # JSONL is retained as an auditable intermediate and as the baseline for the
    # analytical query benchmark. Parquet is the intended reusable research layer.
    jsonl_stats: dict[str, dict[str, int]] = {}
    tables: dict[str, list[dict[str, Any]]] = {
        "markets": market_summaries,
        "book_updates": all_books,
        "target_actions": target_actions,
        "target_parents": target_parents,
        "public_snapshots": public_snapshots,
        "market_results": market_results,
        "decision_seams": all_seams,
    }
    for name, rows in tables.items():
        n, b = _write_jsonl(work / f"{name}.jsonl", rows)
        jsonl_stats[name] = {"rows": n, "bytes": b}

    duckdb = _duckdb()
    con = duckdb.connect(database=":memory:")
    parquet_stats: dict[str, dict[str, int]] = {}
    parquet_t = time.perf_counter()
    try:
        for name in tables:
            src = work / f"{name}.jsonl"
            dst = out / f"{name}.parquet"
            _jsonl_to_parquet(con, src, dst)
            if dst.exists():
                parquet_stats[name] = {"bytes": dst.stat().st_size}
        parquet_seconds = time.perf_counter() - parquet_t
        bench = _query_benchmark(
            con,
            out / "decision_seams.parquet",
            work / "decision_seams.jsonl",
            max(3, int(ns.benchmark_repeats)),
        )
    finally:
        con.close()

    def cov(field: str) -> float | None:
        if not all_seams:
            return None
        return sum(1 for r in all_seams if r.get(field) is not None) / len(all_seams)

    violations = {
        "sourceStrictLeak": sum(1 for r in all_seams if r.get("source_strict_book_source_ms") is not None and int(r["source_strict_book_source_ms"]) >= int(r["action_event_ms"])),
        "receiptStrictLeak": sum(1 for r in all_seams if r.get("receipt_strict_book_received_ms") is not None and int(r["receipt_strict_book_received_ms"]) >= int(r["action_event_ms"])),
        "publicStrictLeak": sum(1 for r in all_seams if r.get("public_sampled_at_ms") is not None and int(r["public_sampled_at_ms"]) >= int(r["action_event_ms"])),
        "negativeSourceBookAge": sum(1 for r in all_seams if r.get("source_strict_book_age_ms") is not None and float(r["source_strict_book_age_ms"]) < 0),
        "negativeReceiptBookAge": sum(1 for r in all_seams if r.get("receipt_strict_book_received_age_ms") is not None and float(r["receipt_strict_book_received_age_ms"]) < 0),
        "negativePublicAge": sum(1 for r in all_seams if r.get("public_age_ms") is not None and float(r["public_age_ms"]) < 0),
    }
    strict_pass = all(v == 0 for v in violations.values())
    source_ages = [float(r["source_strict_book_age_ms"]) for r in all_seams if r.get("source_strict_book_age_ms") is not None]
    public_ages = [float(r["public_age_ms"]) for r in all_seams if r.get("public_age_ms") is not None]
    result = {
        "version": VERSION,
        "createdAtMs": int(time.time() * 1000),
        "sourceBundleVersion": manifest.get("version"),
        "sourceBundleManifestSha256": _sha256(bundle / "manifest.json"),
        "builderSha256": _sha256(Path(__file__)),
        "marketCount": len(market_summaries),
        "marketIds": [x["market_id"] for x in market_summaries],
        "rows": {name: stat["rows"] for name, stat in jsonl_stats.items()},
        "coverage": {
            "sourceStrictBook": cov("source_strict_book_source_ms"),
            "receiptStrictBook": cov("receipt_strict_book_received_ms"),
            "publicStrict": cov("public_sampled_at_ms"),
        },
        "strictPast": {"pass": strict_pass, "violations": violations},
        "freshnessMs": {
            "sourceBookMedian": statistics.median(source_ages) if source_ages else None,
            "sourceBookP95": sorted(source_ages)[min(len(source_ages) - 1, int(len(source_ages) * 0.95))] if source_ages else None,
            "publicMedian": statistics.median(public_ages) if public_ages else None,
            "publicP95": sorted(public_ages)[min(len(public_ages) - 1, int(len(public_ages) * 0.95))] if public_ages else None,
        },
        "storage": {
            "sourceBundleBytes": sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file()),
            "jsonlBytes": sum(x["bytes"] for x in jsonl_stats.values()),
            "parquetBytes": sum(x["bytes"] for x in parquet_stats.values()),
            "parquetByTable": parquet_stats,
            "jsonlRetained": bool(ns.keep_jsonl),
        },
        "timings": {
            "reconstructionSeconds": reconstruction_seconds,
            "parquetConversionSeconds": parquet_seconds,
            "totalSeconds": time.perf_counter() - t0,
        },
        "queryBenchmark": bench,
        "reconstruction": reconstruction,
        "promotionGate": {
            "strictPastZeroLeak": strict_pass,
            "marketCountAtLeast50": len(market_summaries) >= 50,
            "sourceStrictCoverageAtLeast95pct": (cov("source_strict_book_source_ms") or 0.0) >= 0.95,
            "publicStrictCoverageAtLeast90pct": (cov("public_sampled_at_ms") or 0.0) >= 0.90,
            "parquetRepeatQueryFasterThanJsonl": (bench.get("speedupJsonlOverParquet") or 0.0) > 1.0,
        },
    }
    result["promotionGate"]["pass"] = all(bool(v) for k, v in result["promotionGate"].items() if k != "pass")
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "README.md").write_text(
        "# BTC5M Market Capsule + Decision Seam V1\n\n"
        "Canonical raw truth remains the execution tape.  These Parquet tables are a reusable analytical cache.\n\n"
        "- `book_updates.parquet`: reconstructed strict chronology + top-book/depth metrics.\n"
        "- `target_actions.parquet`: raw Target wallet fill legs from the existing forward collector.\n"
        "- `target_parents.parquet`: Target parent-order lifecycle rows for Repair/Expand/cadence research.\n"
        "- `public_snapshots.parquet`: raw public-source snapshots for the selected markets.\n"
        "- `decision_seams.parquet`: Target action labels joined only to strict-past Target portfolio/book/public state.\n"
        "- `markets.parquet` / `market_results.parquet`: cohort metadata and official outcomes.\n"
        "- `result.json`: correctness, coverage, storage and repeat-query benchmark.\n",
        encoding="utf-8",
    )
    if not ns.keep_jsonl:
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps({
        "ok": True,
        "output": str(out),
        "marketCount": result["marketCount"],
        "decisionSeams": result["rows"]["decision_seams"],
        "strictPast": result["strictPast"],
        "coverage": result["coverage"],
        "storage": result["storage"],
        "queryBenchmark": result["queryBenchmark"],
        "promotionGate": result["promotionGate"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
