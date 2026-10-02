from __future__ import annotations

"""Export a compact, read-only BTC5M research bundle for Market Capsule V1.

The exporter intentionally runs on the primary node because the canonical SQLite
stores and execution-tape archive live there.  It performs only bounded indexed
reads/copies.  CPU-heavy reconstruction / Parquet conversion is delegated to the
LAN worker by ``build_market_capsule_v1.py``.
"""

import argparse
import hashlib
import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
VERSION = "BTC5M_MARKET_CAPSULE_SOURCE_BUNDLE_V1"
DEFAULT_MAKER_DB = ROOT / "data" / "wallet_maker_book_inference.db"
DEFAULT_PUBLIC_DB = ROOT / "data" / "public_source_snapshot_archive_v2.db"
DEFAULT_TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
DEFAULT_TAPE_DIR = ROOT / "data" / "execution_tape_v1" / "markets"
DEFAULT_OUT = ROOT / "data" / "research" / "market_capsule_v1" / "source_bundle_50"


def _ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=15.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA busy_timeout=15000")
    return con


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> tuple[int, int]:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str))
            fh.write("\n")
            count += 1
    return count, path.stat().st_size


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _select_markets(
    maker: sqlite3.Connection,
    public: sqlite3.Connection,
    target: sqlite3.Connection,
    tape_dir: Path,
    market_count: int,
) -> list[dict[str, Any]]:
    # Target inference is intentionally disabled in the 24/7 book collector, so
    # selection uses the canonical official Target ledger and then requires a
    # COMPLETE_FORWARD execution tape plus public snapshots.
    candidates = target.execute(
        """
        SELECT market_id,COUNT(*) AS target_action_count,
               MIN(event_ms) AS first_action_ms,MAX(event_ms) AS last_action_ms
          FROM wallet_shadow_target_events
         WHERE asset='BTC'
         GROUP BY market_id
         ORDER BY MAX(event_ms) DESC
         LIMIT ?
        """,
        (max(market_count * 8, market_count + 50),),
    ).fetchall()
    selected: list[dict[str, Any]] = []
    for row in candidates:
        d = dict(row)
        market_id = int(d["market_id"])
        quality = maker.execute(
            """SELECT market_id,quality_status,eligible_execution_training,
                      window_start_ms,window_end_ms,first_source_ms,last_source_ms,
                      l2_rows,meta_rows,match_rows,archive_bytes
                 FROM maker_execution_market_quality_v1 WHERE market_id=?""",
            (market_id,),
        ).fetchone()
        if quality is None or int(quality["eligible_execution_training"] or 0) != 1:
            continue
        d.update(dict(quality))
        tape = tape_dir / f"{market_id}.json.xz"
        if not tape.exists():
            continue
        has_public = public.execute(
            "SELECT 1 FROM public_source_snapshots_v2 WHERE market_id=? LIMIT 1",
            (market_id,),
        ).fetchone()
        if has_public is None:
            continue
        d["tape_path"] = str(tape)
        d["tape_bytes"] = tape.stat().st_size
        selected.append(d)
        if len(selected) >= market_count:
            break
    if len(selected) < market_count:
        raise RuntimeError(f"only {len(selected)} eligible markets found; requested {market_count}")
    return selected



def _explicit_market_ids(csv_value: str | None, file_value: Path | None) -> list[int] | None:
    if csv_value and file_value:
        raise ValueError("use only one of --market-ids or --market-ids-file")
    raw: list[Any] = []
    if csv_value:
        raw = [x.strip() for x in csv_value.split(",") if x.strip()]
    elif file_value:
        payload = json.loads(file_value.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("marketIds") or payload.get("market_ids") or payload.get("ids")
        if not isinstance(payload, list):
            raise ValueError("market ids file must be a JSON list or an object containing marketIds/market_ids/ids")
        raw = payload
    if not raw:
        return None
    ids = [int(x) for x in raw]
    if len(ids) != len(set(ids)):
        raise ValueError("explicit market ids must be unique")
    return ids


def _select_explicit_markets(
    maker: sqlite3.Connection,
    public: sqlite3.Connection,
    target: sqlite3.Connection,
    tape_dir: Path,
    market_ids: list[int],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    for market_id in market_ids:
        act = target.execute(
            "SELECT COUNT(*) n,MIN(event_ms) first_action_ms,MAX(event_ms) last_action_ms FROM wallet_shadow_target_events WHERE asset='BTC' AND market_id=?",
            (int(market_id),),
        ).fetchone()
        quality = maker.execute(
            """SELECT market_id,quality_status,eligible_execution_training,
                      window_start_ms,window_end_ms,first_source_ms,last_source_ms,
                      l2_rows,meta_rows,match_rows,archive_bytes
                 FROM maker_execution_market_quality_v1 WHERE market_id=?""",
            (int(market_id),),
        ).fetchone()
        pub_n = int(public.execute("SELECT COUNT(*) FROM public_source_snapshots_v2 WHERE market_id=?", (int(market_id),)).fetchone()[0])
        tape = tape_dir / f"{int(market_id)}.json.xz"
        issue = []
        if act is None or int(act["n"] or 0) <= 0: issue.append("no_target_actions")
        if quality is None: issue.append("no_execution_quality")
        elif int(quality["eligible_execution_training"] or 0) != 1: issue.append("not_execution_training_eligible")
        if pub_n <= 0: issue.append("no_public_snapshots")
        if not tape.exists(): issue.append("missing_execution_tape")
        if issue:
            problems.append({"marketId": int(market_id), "issues": issue})
            continue
        d = {
            "market_id": int(market_id),
            "target_action_count": int(act["n"]),
            "first_action_ms": int(act["first_action_ms"]),
            "last_action_ms": int(act["last_action_ms"]),
            **dict(quality),
            "tape_path": str(tape),
            "tape_bytes": tape.stat().st_size,
        }
        selected.append(d)
    if problems:
        raise RuntimeError(f"explicit cohort failed source gates: {json.dumps(problems, ensure_ascii=False)}")
    return selected

def main() -> int:
    ap = argparse.ArgumentParser(description=VERSION)
    ap.add_argument("--market-count", type=int, default=50)
    ap.add_argument("--market-ids", help="comma-separated explicit market IDs; preserves supplied order")
    ap.add_argument("--market-ids-file", type=Path, help="JSON list or object containing marketIds/market_ids/ids")
    ap.add_argument("--maker-db", type=Path, default=DEFAULT_MAKER_DB)
    ap.add_argument("--public-db", type=Path, default=DEFAULT_PUBLIC_DB)
    ap.add_argument("--target-db", type=Path, default=DEFAULT_TARGET_DB)
    ap.add_argument("--tape-dir", type=Path, default=DEFAULT_TAPE_DIR)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--overwrite", action="store_true")
    ns = ap.parse_args()

    explicit_ids = _explicit_market_ids(ns.market_ids, ns.market_ids_file)
    out = ns.output if ns.output.is_absolute() else ROOT / ns.output
    out = out.resolve()
    if out.exists():
        if not ns.overwrite:
            raise FileExistsError(f"output exists: {out}; use --overwrite")
        shutil.rmtree(out)
    tape_out = out / "tapes"
    tape_out.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    maker = _ro(ns.maker_db if ns.maker_db.is_absolute() else ROOT / ns.maker_db)
    public = _ro(ns.public_db if ns.public_db.is_absolute() else ROOT / ns.public_db)
    target = _ro(ns.target_db if ns.target_db.is_absolute() else ROOT / ns.target_db)
    try:
        t = time.perf_counter()
        resolved_tape_dir = ns.tape_dir if ns.tape_dir.is_absolute() else ROOT / ns.tape_dir
        markets = (_select_explicit_markets(maker, public, target, resolved_tape_dir, explicit_ids) if explicit_ids is not None else _select_markets(maker, public, target, resolved_tape_dir, max(1, ns.market_count)))
        timings["selectMarketsSeconds"] = time.perf_counter() - t
        market_ids = [int(x["market_id"]) for x in markets]

        t = time.perf_counter()
        target_rows: list[dict[str, Any]] = []
        target_parent_rows: list[dict[str, Any]] = []
        public_rows: list[dict[str, Any]] = []
        result_rows: list[dict[str, Any]] = []
        source_counts: dict[str, dict[str, int]] = {}
        for market_id in market_ids:
            actions = [dict(r) for r in target.execute(
                """SELECT leg_id AS source_leg_id,wallet,market_id,role,quote_type,side,order_hash,
                          event_ms,observed_at_ms,price,shares
                     FROM wallet_shadow_target_events
                    WHERE asset='BTC' AND market_id=? ORDER BY event_ms,leg_id""",
                (market_id,),
            )]
            target_rows.extend(actions)

            parents = [dict(r) for r in target.execute(
                """SELECT parent_id,wallet,asset,market_id,role,side,quote_type,order_hash,
                          first_event_ms,last_event_ms,average_price,shares,fill_legs,updated_at_ms
                     FROM target_parent_orders
                    WHERE asset='BTC' AND market_id=? ORDER BY first_event_ms,parent_id""",
                (market_id,),
            )]
            target_parent_rows.extend(parents)

            snapshots = [dict(r) for r in public.execute(
                """SELECT id,market_id,sampled_at_ms,timestamp_ns,seconds_left,snapshot_json,archived_at_ms
                     FROM public_source_snapshots_v2
                    WHERE market_id=? ORDER BY sampled_at_ms,id""",
                (market_id,),
            )]
            public_rows.extend(snapshots)

            result = target.execute(
                "SELECT * FROM target_market_results WHERE market_id=?",
                (market_id,),
            ).fetchone()
            if result is not None:
                result_rows.append(dict(result))
            source_counts[str(market_id)] = {
                "targetActions": len(actions),
                "targetParents": len(parents),
                "publicSnapshots": len(snapshots),
                "targetResultRows": int(result is not None),
            }
        timings["extractRowsSeconds"] = time.perf_counter() - t

        t = time.perf_counter()
        target_n, target_bytes = _write_jsonl(out / "target_actions.jsonl", target_rows)
        target_parent_n, target_parent_bytes = _write_jsonl(out / "target_parents.jsonl", target_parent_rows)
        public_n, public_bytes = _write_jsonl(out / "public_snapshots.jsonl", public_rows)
        result_n, result_bytes = _write_jsonl(out / "market_results.jsonl", result_rows)
        timings["writeJsonlSeconds"] = time.perf_counter() - t

        t = time.perf_counter()
        tape_manifest: list[dict[str, Any]] = []
        for m in markets:
            src = Path(str(m["tape_path"]))
            dst = tape_out / src.name
            shutil.copy2(src, dst)
            tape_manifest.append({
                "marketId": int(m["market_id"]),
                "file": f"tapes/{dst.name}",
                "bytes": dst.stat().st_size,
                "sha256": _sha256(dst),
            })
        timings["copyAndHashTapesSeconds"] = time.perf_counter() - t

        manifest = {
            "version": VERSION,
            "createdAtMs": int(time.time() * 1000),
            "marketCount": len(markets),
            "marketIds": market_ids,
            "selection": ("explicit market IDs with all source gates satisfied; supplied order preserved" if explicit_ids is not None else "most recent execution-training-eligible BTC5M markets with Target wallet actions, complete execution tape and public snapshots; no special cohort opened"),
            "explicitMarketIds": explicit_ids,
            "sources": {
                "makerDb": str(ns.maker_db),
                "publicDb": str(ns.public_db),
                "targetDb": str(ns.target_db),
                "executionTapeDir": str(ns.tape_dir),
            },
            "rows": {
                "targetActions": target_n,
                "targetParents": target_parent_n,
                "publicSnapshots": public_n,
                "marketResults": result_n,
            },
            "bytes": {
                "targetActionsJsonl": target_bytes,
                "targetParentsJsonl": target_parent_bytes,
                "publicSnapshotsJsonl": public_bytes,
                "marketResultsJsonl": result_bytes,
                "executionTapes": sum(int(x["bytes"]) for x in tape_manifest),
            },
            "sourceCountsByMarket": source_counts,
            "markets": markets,
            "tapes": tape_manifest,
            "timings": timings,
        }
        manifest["totalBundleBytesBeforeManifest"] = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
        manifest["elapsedSeconds"] = time.perf_counter() - t0
        manifest_path = out / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["manifestSha256"] = _sha256(manifest_path)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "ok": True,
            "version": VERSION,
            "output": str(out),
            "marketCount": len(markets),
            "rows": manifest["rows"],
            "bundleBytes": sum(p.stat().st_size for p in out.rglob("*") if p.is_file()),
            "elapsedSeconds": round(manifest["elapsedSeconds"], 3),
            "marketIds": market_ids,
        }, ensure_ascii=False, indent=2))
    finally:
        maker.close()
        public.close()
        target.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
