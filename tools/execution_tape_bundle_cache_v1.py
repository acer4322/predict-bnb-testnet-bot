from __future__ import annotations

"""Persistent materialization cache for Execution-Tape bundles.

Many research scripts receive the same H100 ZIP bundle and historically repeat:
ZIP extract -> XZ decompress -> normalize HFT events -> rebuild update clocks.
This module makes those steps reusable across research turns.

Layers:
1) ZIP tape materialization: each market `.json.xz` extracted once into a stable cache.
2) Tape SHA manifest: lets ExecutionEventCache validate by content rather than copy mtime.
3) Update-clock sidecar: received_ms + order_count cached as compressed NumPy arrays.
4) Process-memory decoded tape cache for analyses that genuinely need raw updates.

Canonical ZIP / raw Execution Tape remain truth. This is a derived research cache.
"""

import hashlib
import json
import lzma
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable

import numpy as np

try:
    from tools.hft_execution_event_cache_v1 import ExecutionEventCache, DEFAULT_CACHE_DIR
except ImportError:
    from hft_execution_event_cache_v1 import ExecutionEventCache, DEFAULT_CACHE_DIR

ROOT = Path(__file__).resolve().parents[1]
VERSION = "EXECUTION_TAPE_BUNDLE_CACHE_V1"
DEFAULT_ROOT = ROOT / "data" / "research" / "execution_tape_bundle_cache_v1"


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("._") or "bundle"


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except Exception:
        return p.resolve().as_posix()


class ExecutionTapeBundleCache:
    def __init__(self, bundle: str | Path, cache_root: str | Path = DEFAULT_ROOT):
        self.bundle = Path(bundle).resolve()
        if not self.bundle.exists():
            raise FileNotFoundError(self.bundle)
        st = self.bundle.stat()
        # Fast source identity. Content SHA is additionally recorded per extracted tape.
        self.bundle_fast_id = f"{_safe(self.bundle.stem)}__{int(st.st_size)}__{int(st.st_mtime_ns)}"
        self.root = Path(cache_root).resolve() / self.bundle_fast_id
        self.tape_dir = self.root / "tapes"
        self.meta_dir = self.root / "update_meta"
        self.manifest_path = self.root / "manifest.json"
        self.tape_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        self._decoded: dict[int, dict[str, Any]] = {}
        self._manifest = self._load_manifest()
        self.stats = {
            "tapeHits": 0,
            "tapeExtracts": 0,
            "updateMetaHits": 0,
            "updateMetaBuilds": 0,
            "decodedMemoryHits": 0,
            "decodedLoads": 0,
        }

    def _load_manifest(self) -> dict[str, Any]:
        if self.manifest_path.exists():
            try:
                d = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                if d.get("version") == VERSION:
                    return d
            except Exception:
                pass
        return {
            "version": VERSION,
            "sourceBundle": _rel(self.bundle),
            "sourceBundleBytes": int(self.bundle.stat().st_size),
            "sourceBundleMtimeNs": int(self.bundle.stat().st_mtime_ns),
            "bundleFastId": self.bundle_fast_id,
            "tapes": [],
            "createdAtMs": int(time.time() * 1000),
        }

    def _save_manifest(self) -> None:
        self._manifest["updatedAtMs"] = int(time.time() * 1000)
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.manifest_path)

    def _manifest_map(self) -> dict[int, dict[str, Any]]:
        return {int(r["marketId"]): r for r in self._manifest.get("tapes") or [] if r.get("marketId") is not None}

    def tape_path(self, market_id: int) -> Path:
        return self.tape_dir / f"{int(market_id)}.json.xz"

    def ensure_tapes(self, market_ids: Iterable[int]) -> dict[str, Any]:
        ids = list(dict.fromkeys(int(x) for x in market_ids))
        mmap = self._manifest_map()
        missing = [mid for mid in ids if not self.tape_path(mid).exists() or mid not in mmap]
        if missing:
            with zipfile.ZipFile(self.bundle) as zf:
                names = set(zf.namelist())
                for mid in missing:
                    name = f"tapes/{mid}.json.xz"
                    if name not in names:
                        raise FileNotFoundError(f"{name} missing in {self.bundle}")
                    raw = zf.read(name)
                    dest = self.tape_path(mid)
                    tmp = dest.with_suffix(dest.suffix + f".tmp-{os.getpid()}")
                    tmp.write_bytes(raw)
                    os.replace(tmp, dest)
                    mmap[mid] = {
                        "marketId": mid,
                        "path": f"tapes/{mid}.json.xz",
                        "bytes": len(raw),
                        "sha256": _sha_bytes(raw),
                    }
                    self.stats["tapeExtracts"] += 1
            self._manifest["tapes"] = [mmap[k] for k in sorted(mmap)]
            self._save_manifest()
        self.stats["tapeHits"] += len(ids) - len(missing)
        return {
            "markets": len(ids),
            "extracted": len(missing),
            "hits": len(ids) - len(missing),
            "tapeDir": str(self.tape_dir),
            "manifest": str(self.manifest_path),
        }

    def load_tape(self, market_id: int) -> dict[str, Any]:
        mid = int(market_id)
        if mid in self._decoded:
            self.stats["decodedMemoryHits"] += 1
            return self._decoded[mid]
        self.ensure_tapes([mid])
        payload = json.loads(lzma.decompress(self.tape_path(mid).read_bytes()).decode("utf-8"))
        self._decoded[mid] = payload
        self.stats["decodedLoads"] += 1
        return payload

    def update_clock(self, market_id: int) -> tuple[np.ndarray, np.ndarray]:
        """Return sorted receipt timestamps and order-count series, cached persistently."""
        mid = int(market_id)
        npz = self.meta_dir / f"{mid}.update_clock.npz"
        if npz.exists():
            with np.load(npz, allow_pickle=False) as d:
                ts = np.asarray(d["received_ms"], dtype=np.int64)
                oc = np.asarray(d["order_count"], dtype=np.float64)
            self.stats["updateMetaHits"] += 1
            return ts, oc
        tape = self.load_tape(mid)
        rows = sorted(tape.get("updates") or [], key=lambda u: (int(u[1]), int(u[0])))
        ts = np.asarray([int(u[1]) for u in rows], dtype=np.int64)
        oc = np.asarray([float(u[2]) for u in rows], dtype=np.float64)
        tmp = npz.with_name(npz.name + f".tmp-{os.getpid()}.npz")
        np.savez_compressed(tmp, received_ms=ts, order_count=oc)
        os.replace(tmp, npz)
        self.stats["updateMetaBuilds"] += 1
        return ts, oc

    def event_cache(self, *, trade_offset: str = "mid", verify_sha256: bool = False,
                    event_cache_dir: str | Path = DEFAULT_CACHE_DIR) -> ExecutionEventCache:
        return ExecutionEventCache(
            tape_dir=self.tape_dir,
            cache_dir=event_cache_dir,
            trade_offset=trade_offset,
            verify_sha256=verify_sha256,
        )

    def summary(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "bundle": _rel(self.bundle),
            "bundleFastId": self.bundle_fast_id,
            "tapesMaterialized": len(self._manifest.get("tapes") or []),
            "root": _rel(self.root),
            "stats": dict(self.stats),
        }


__all__ = ["ExecutionTapeBundleCache"]
