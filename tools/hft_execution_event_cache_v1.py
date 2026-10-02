from __future__ import annotations

"""Reusable materialized HftBacktest event cache for Execution Tape V1.

Canonical truth remains the raw `.json.xz` Execution Tape. This cache stores the
fully normalized NumPy event array produced by `hftbacktest_execution_tape_feed_v1`
so repeated replay / counterfactual jobs do not repeatedly decompress and normalize
identical tapes.

Validity contract:
- cache key includes market id + trade-offset policy;
- source tape size + mtime and feed-builder SHA256 must match;
- source tape SHA256 is recorded at build time and can be re-verified explicitly;
- no execution semantics are changed by the cache.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path.cwd().resolve() if (Path.cwd() / "tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import hftbacktest_execution_tape_feed_v1 as feed
VERSION = "BTC5M_HFT_EXECUTION_EVENT_CACHE_V1"
DEFAULT_TAPE_DIR = ROOT / "data" / "execution_tape_v1" / "markets"
DEFAULT_CACHE_DIR = ROOT / "data" / "research" / "hft_execution_event_cache_v1"
FEED_BUILDER = ROOT / "tools" / "hftbacktest_execution_tape_feed_v1.py"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_offset(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(value))


class ExecutionEventCache:
    def __init__(self, tape_dir: str | Path = DEFAULT_TAPE_DIR, cache_dir: str | Path = DEFAULT_CACHE_DIR,
                 trade_offset: str = "mid", verify_sha256: bool = False):
        self.tape_dir = Path(tape_dir).resolve()
        self.cache_dir = Path(cache_dir).resolve()
        self.trade_offset = str(trade_offset)
        self.verify_sha256 = bool(verify_sha256)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.feed_builder_sha256 = sha256(FEED_BUILDER)
        self.bundle_tape_sha256: dict[int, str] = {}
        manifest_path = self.tape_dir.parent / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for row in manifest.get("tapes") or []:
                    if isinstance(row, dict) and row.get("marketId") is not None and row.get("sha256"):
                        self.bundle_tape_sha256[int(row["marketId"])] = str(row["sha256"])
            except Exception:
                self.bundle_tape_sha256 = {}
        self.stats = {"hits": 0, "misses": 0, "buildMs": 0.0, "loadMs": 0.0}

    def _paths(self, market_id: int) -> dict[str, Path]:
        stem = f"{int(market_id)}__{safe_offset(self.trade_offset)}"
        return {
            "events": self.cache_dir / f"{stem}.events.npy",
            "times": self.cache_dir / f"{stem}.times.npy",
            "meta": self.cache_dir / f"{stem}.meta.json",
        }

    def _tape(self, market_id: int) -> Path:
        p = self.tape_dir / f"{int(market_id)}.json.xz"
        if not p.exists():
            raise FileNotFoundError(p)
        return p

    def _valid_meta(self, market_id: int, meta: dict[str, Any], tape: Path) -> bool:
        st = tape.stat()
        if meta.get("version") != VERSION:
            return False
        if int(meta.get("marketId") or -1) != int(market_id):
            return False
        if str(meta.get("tradeOffset")) != self.trade_offset:
            return False
        if str(meta.get("feedBuilderSha256")) != self.feed_builder_sha256:
            return False
        if int(meta.get("sourceTapeBytes") or -1) != int(st.st_size):
            return False
        manifest_sha = self.bundle_tape_sha256.get(int(market_id))
        if manifest_sha:
            if str(meta.get("sourceTapeSha256")) != manifest_sha:
                return False
        else:
            if int(meta.get("sourceTapeMtimeNs") or -1) != int(st.st_mtime_ns):
                return False
        if self.verify_sha256 and str(meta.get("sourceTapeSha256")) != sha256(tape):
            return False
        return True

    def has_valid(self, market_id: int) -> bool:
        ps = self._paths(market_id)
        tape = self._tape(market_id)
        if not all(p.exists() for p in ps.values()):
            return False
        try:
            meta = json.loads(ps["meta"].read_text(encoding="utf-8"))
        except Exception:
            return False
        return self._valid_meta(market_id, meta, tape)

    def build(self, market_id: int, *, force: bool = False) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
        mid = int(market_id)
        if not force and self.has_valid(mid):
            return self.load(mid)
        tape = self._tape(mid)
        ps = self._paths(mid)
        t0 = time.perf_counter()
        old_archive = feed.ARCHIVE_DIR
        feed.ARCHIVE_DIR = self.tape_dir
        try:
            events, times, raw_meta = feed.build_archive_events(mid, trade_offset=self.trade_offset)
        finally:
            feed.ARCHIVE_DIR = old_archive
        events = np.asarray(events)
        times_arr = np.asarray(times, dtype=np.int64)
        st = tape.stat()
        meta = {
            "version": VERSION,
            "marketId": mid,
            "tradeOffset": self.trade_offset,
            "feedBuilderSha256": self.feed_builder_sha256,
            "sourceTape": str(tape),
            "sourceTapeBytes": int(st.st_size),
            "sourceTapeMtimeNs": int(st.st_mtime_ns),
            "sourceTapeSha256": self.bundle_tape_sha256.get(mid) or sha256(tape),
            "sourceFingerprintMode": "bundle_manifest_sha256" if mid in self.bundle_tape_sha256 else "size_mtime_with_recorded_sha256",
            "events": int(len(events)),
            "eventBytes": int(events.nbytes),
            "times": int(len(times_arr)),
            "rawFeedMeta": raw_meta,
            "builtAtMs": int(time.time() * 1000),
        }
        tmp_tag = f".tmp-{os.getpid()}-{int(time.time()*1000)}"
        et = ps["events"].with_name(ps["events"].name + tmp_tag)
        tt = ps["times"].with_name(ps["times"].name + tmp_tag)
        mt = ps["meta"].with_name(ps["meta"].name + tmp_tag)
        try:
            with et.open("wb") as f:
                np.save(f, events, allow_pickle=False)
            with tt.open("wb") as f:
                np.save(f, times_arr, allow_pickle=False)
            mt.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            os.replace(et, ps["events"])
            os.replace(tt, ps["times"])
            os.replace(mt, ps["meta"])
        finally:
            for p in (et, tt, mt):
                p.unlink(missing_ok=True)
        self.stats["misses"] += 1
        self.stats["buildMs"] += (time.perf_counter() - t0) * 1000.0
        return events, times_arr, meta

    def load(self, market_id: int) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
        mid = int(market_id)
        ps = self._paths(mid)
        tape = self._tape(mid)
        if not all(p.exists() for p in ps.values()):
            return self.build(mid)
        meta = json.loads(ps["meta"].read_text(encoding="utf-8"))
        if not self._valid_meta(mid, meta, tape):
            return self.build(mid, force=True)
        t0 = time.perf_counter()
        events = np.load(ps["events"], allow_pickle=False)
        times_arr = np.load(ps["times"], allow_pickle=False)
        self.stats["hits"] += 1
        self.stats["loadMs"] += (time.perf_counter() - t0) * 1000.0
        return events, times_arr, meta

    def get(self, market_id: int) -> tuple[np.ndarray, dict[str, Any]]:
        events, _times, meta = self.load(int(market_id)) if self.has_valid(int(market_id)) else self.build(int(market_id))
        return events, dict(meta.get("rawFeedMeta") or {})

    def preload(self, market_ids: list[int]) -> dict[str, Any]:
        built_before = int(self.stats["misses"])
        hits_before = int(self.stats["hits"])
        rows = []
        for i, mid in enumerate(market_ids, 1):
            t0 = time.perf_counter()
            events, _times, meta = self.load(mid) if self.has_valid(mid) else self.build(mid)
            rows.append({"marketId": int(mid), "events": int(len(events)), "ms": round((time.perf_counter()-t0)*1000, 3)})
        return {
            "version": VERSION,
            "markets": len(market_ids),
            "newBuilds": int(self.stats["misses"]) - built_before,
            "cacheHits": int(self.stats["hits"]) - hits_before,
            "stats": self.stats,
            "rows": rows,
        }


def parse_ids(csv_value: str | None, file_value: Path | None) -> list[int]:
    if csv_value and file_value:
        raise ValueError("use only one of --market-ids or --market-ids-file")
    if csv_value:
        return [int(x.strip()) for x in csv_value.split(",") if x.strip()]
    if file_value:
        payload = json.loads(file_value.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("marketIds") or payload.get("market_ids") or payload.get("ids")
        if not isinstance(payload, list):
            raise ValueError("IDs file must be a list or object containing marketIds/market_ids/ids")
        return [int(x) for x in payload]
    raise ValueError("market IDs required")


def main() -> int:
    ap = argparse.ArgumentParser(description=VERSION)
    ap.add_argument("--tape-dir", type=Path, default=DEFAULT_TAPE_DIR)
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    ap.add_argument("--trade-offset", default="mid")
    ap.add_argument("--verify-sha256", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--market-ids")
    b.add_argument("--market-ids-file", type=Path)
    b.add_argument("--force", action="store_true")
    bn = sub.add_parser("bench")
    bn.add_argument("--market-id", type=int, required=True)
    bn.add_argument("--repeats", type=int, default=20)
    ns = ap.parse_args()
    cache = ExecutionEventCache(ns.tape_dir, ns.cache_dir, ns.trade_offset, ns.verify_sha256)
    if ns.cmd == "build":
        ids = parse_ids(ns.market_ids, ns.market_ids_file)
        if ns.force:
            for mid in ids:
                cache.build(mid, force=True)
            rep = {"version": VERSION, "markets": len(ids), "forced": True, "stats": cache.stats}
        else:
            rep = cache.preload(ids)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    mid = int(ns.market_id)
    tape = cache._tape(mid)
    direct = []
    for _ in range(max(1, ns.repeats)):
        old = feed.ARCHIVE_DIR; feed.ARCHIVE_DIR = cache.tape_dir
        try:
            t0 = time.perf_counter(); ev0, t0s, _m = feed.build_archive_events(mid, trade_offset=cache.trade_offset); direct.append((time.perf_counter()-t0)*1000)
        finally:
            feed.ARCHIVE_DIR = old
    cache.build(mid, force=True)
    cached = []
    ev1 = None; t1s = None
    for _ in range(max(1, ns.repeats)):
        t0 = time.perf_counter(); ev1, t1s, _ = cache.load(mid); cached.append((time.perf_counter()-t0)*1000)
    direct_events, direct_times, _ = feed.build_archive_events(mid, trade_offset=cache.trade_offset)
    import statistics
    rep = {
        "version": VERSION,
        "marketId": mid,
        "tapeBytes": tape.stat().st_size,
        "events": len(direct_events),
        "arrayEqual": bool(np.array_equal(np.asarray(direct_events), np.asarray(ev1))),
        "timesEqual": bool(np.array_equal(np.asarray(direct_times, dtype=np.int64), np.asarray(t1s))),
        "directMedianMs": statistics.median(direct),
        "cachedMedianMs": statistics.median(cached),
        "speedup": statistics.median(direct) / statistics.median(cached) if statistics.median(cached) > 0 else None,
        "stats": cache.stats,
    }
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
