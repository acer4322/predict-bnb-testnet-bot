from __future__ import annotations

"""Single preferred research-data entry point for BTC5M Lab.

New research scripts should prefer this module instead of directly opening large
SQLite stores or reparsing Execution Tape archives. It composes:
- ResearchData: Parquet/registry analytical cache with label/oracle guards.
- ExecutionEventCache: persistent normalized HftBacktest event cache.
- Per-process event memory cache: repeated same-market forks avoid even `.npy` reloads.

Canonical raw SQLite / Execution Tape remain truth and are rebuilt only by cache
builders or explicit provenance audits.
"""

from pathlib import Path
from typing import Any

from tools.research_data_access_v1 import ResearchData
from tools.hft_execution_event_cache_v1 import ExecutionEventCache, DEFAULT_TAPE_DIR, DEFAULT_CACHE_DIR
from tools.execution_tape_bundle_cache_v1 import ExecutionTapeBundleCache
from tools.build_execution_bundle_market_registry_v1 import check as check_bundle_registry

BUNDLE_REGISTRY_DIR = Path(__file__).resolve().parents[1] / "data" / "research" / "execution_bundle_market_registry_v1"
BUNDLE_REGISTRY = BUNDLE_REGISTRY_DIR / "bundle_market_registry_v1.parquet"


class ResearchFastPath:
    def __init__(
        self,
        *,
        tape_dir: str | Path = DEFAULT_TAPE_DIR,
        event_cache_dir: str | Path = DEFAULT_CACHE_DIR,
        trade_offset: str = "mid",
        verify_event_sha256: bool = False,
    ) -> None:
        self.data = ResearchData()
        self.events = ExecutionEventCache(
            tape_dir=tape_dir,
            cache_dir=event_cache_dir,
            trade_offset=trade_offset,
            verify_sha256=verify_event_sha256,
        )
        self._event_memory: dict[int, tuple[Any, Any, dict[str, Any]]] = {}
        self.stats = {
            "eventMemoryHits": 0,
            "eventDiskOrBuildLoads": 0,
        }
        self._bundle_registry_status: dict[str, Any] | None = None
        self._bundle_market_map: dict[int, list[dict[str, Any]]] | None = None

    def close(self) -> None:
        self.data.close()

    def __enter__(self) -> "ResearchFastPath":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def cache_status(self, *, deep: bool = False) -> dict[str, Any]:
        return self.data.execution_cache_status(deep=deep)

    def cohort_ids(self, **kwargs: Any) -> list[int]:
        return self.data.cohort_ids(**kwargs)

    def rows(
        self,
        dataset: str,
        *,
        where: str | None = None,
        columns: list[str] | None = None,
        limit: int | None = None,
        include_labels: bool = False,
        include_oracles: bool = False,
        allow_stale: bool = False,
    ) -> Any:
        """Query a named fast-path dataset.

        Dataset names:
        - target-features
        - our-features
        - phaseb-teacher
        - phaseb-decision-micro
        - phaseb-arrival-oracle
        - our-frontier
        - target-parents
        - registry
        """
        if dataset == "target-features":
            return self.data.feature_rows(
                where=where, columns=columns, limit=limit, include_labels=include_labels
            )
        if dataset == "our-features":
            return self.data.our_feature_rows(
                where=where, columns=columns, limit=limit, include_labels=include_labels
            )
        if dataset == "phaseb-teacher":
            return self.data.phaseb_rows(
                where=where, columns=columns, limit=limit, include_labels=include_labels
            )
        if dataset == "phaseb-decision-micro":
            return self.data.execution_rows(
                dataset="phaseb-decision", where=where, columns=columns, limit=limit,
                include_oracles=include_oracles, allow_stale=allow_stale,
            )
        if dataset == "phaseb-arrival-oracle":
            return self.data.execution_rows(
                dataset="phaseb-arrival", where=where, columns=columns, limit=limit,
                include_oracles=include_oracles, allow_stale=allow_stale,
            )
        if dataset == "our-frontier":
            return self.data.execution_rows(
                dataset="our-frontier", where=where, columns=columns, limit=limit,
                include_oracles=include_oracles, allow_stale=allow_stale,
            )
        if dataset == "target-parents":
            return self.data.query("target_parents", where=where, columns=columns, limit=limit,
                                   order_by="market_id,first_event_ms,parent_id")
        if dataset == "registry":
            return self.data.registry_rows(where=where, columns=columns, limit=limit)
        raise KeyError(f"unknown fast-path dataset: {dataset}")


    def bundle_registry_status(self, *, force: bool = False) -> dict[str, Any]:
        if force or self._bundle_registry_status is None:
            self._bundle_registry_status = check_bundle_registry(BUNDLE_REGISTRY_DIR)
        return dict(self._bundle_registry_status)

    def _require_bundle_registry_fresh(self) -> None:
        st = self.bundle_registry_status()
        if not st.get("fresh"):
            raise RuntimeError(f"bundle registry stale: {st}")

    def _load_bundle_market_map(self) -> dict[int, list[dict[str, Any]]]:
        if self._bundle_market_map is not None:
            return self._bundle_market_map
        self._require_bundle_registry_fresh()
        state_path = BUNDLE_REGISTRY_DIR / "registry_state_v1.json"
        if not state_path.exists():
            raise FileNotFoundError(state_path)
        import json
        state = json.loads(state_path.read_text(encoding="utf-8"))
        out: dict[int, list[dict[str, Any]]] = {}
        for bp, meta in (state.get("bundles") or {}).items():
            canonical = "/p0_provenance_v1/" in ("/" + str(bp).replace("\\", "/") + "/") and "DO_NOT_EXECUTE" not in str(bp).upper()
            dne = "DO_NOT_EXECUTE" in str(bp).upper()
            for t in meta.get("tapes") or []:
                mid = int(t["marketId"])
                out.setdefault(mid, []).append({
                    "bundlePath": str(bp),
                    "memberBytes": int(t["memberBytes"]),
                    "compressedBytes": int(t["compressedBytes"]),
                    "crc32": int(t["crc32"]),
                    "canonicalHint": bool(canonical),
                    "doNotExecuteName": bool(dne),
                })
        for rows in out.values():
            rows.sort(key=lambda r: (not r["canonicalHint"], r["doNotExecuteName"], r["bundlePath"]))
        self._bundle_market_map = out
        return out

    def bundle_candidates(self, market_id: int) -> list[dict[str, Any]]:
        """Resolve cached research ZIP candidates from the in-memory registry map."""
        return list(self._load_bundle_market_map().get(int(market_id), []))

    def preferred_bundle(self, market_id: int, *, allow_conflicting_copies: bool = False) -> Path:
        """Return preferred bundle; refuse silent choice when duplicate tape copies disagree."""
        rows = self.bundle_candidates(int(market_id))
        if not rows:
            raise KeyError(f"market {int(market_id)} not found in bundle registry")
        fingerprints = {(r["memberBytes"], r["crc32"]) for r in rows}
        if len(fingerprints) > 1 and not allow_conflicting_copies:
            raise RuntimeError(f"conflicting tape copies for market {int(market_id)}: {sorted(fingerprints)}")
        p = Path(rows[0]["bundlePath"])
        if not p.is_absolute():
            p = Path(__file__).resolve().parents[1] / p
        if not p.exists():
            raise FileNotFoundError(p)
        return p.resolve()

    def bundle_cache_for_market(self, market_id: int, *, cache_root: str | Path | None = None) -> ExecutionTapeBundleCache:
        bundle = self.preferred_bundle(int(market_id))
        return ExecutionTapeBundleCache(bundle, cache_root=cache_root) if cache_root else ExecutionTapeBundleCache(bundle)

    def hft_events(self, market_id: int) -> tuple[Any, Any, dict[str, Any]]:
        """Load normalized HFT events for one market via memory -> disk cache -> raw tape."""
        mid = int(market_id)
        cached = self._event_memory.get(mid)
        if cached is not None:
            self.stats["eventMemoryHits"] += 1
            return cached
        events, times, meta = self.events.load(mid) if self.events.has_valid(mid) else self.events.build(mid)
        out = (events, times, meta)
        self._event_memory[mid] = out
        self.stats["eventDiskOrBuildLoads"] += 1
        return out

    def preload_hft_events(self, market_ids: list[int]) -> dict[str, Any]:
        before_mem = int(self.stats["eventMemoryHits"])
        before_load = int(self.stats["eventDiskOrBuildLoads"])
        for mid in market_ids:
            self.hft_events(int(mid))
        return {
            "markets": len(market_ids),
            "memoryHits": int(self.stats["eventMemoryHits"]) - before_mem,
            "diskOrBuildLoads": int(self.stats["eventDiskOrBuildLoads"]) - before_load,
            "eventCacheStats": dict(self.events.stats),
        }


__all__ = ["ResearchFastPath"]
