from __future__ import annotations

"""Static guard for new BTC5M research scripts.

The goal is not to rewrite historical tools. It flags new/edited analysis scripts
that bypass the fast research-data path and would repeatedly scan canonical DBs or
reparse raw Execution Tape.
"""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RULES = [
    ("DIRECT_SQLITE", re.compile(r"\bsqlite3\.connect\s*\("), "Prefer ResearchFastPath / ResearchData Parquet cache; direct SQLite only for cache builders/provenance audits."),
    ("DIRECT_ARCHIVE_BUILD", re.compile(r"\bbuild_archive_events\s*\("), "Prefer ResearchFastPath.hft_events() / ExecutionEventCache."),
    ("DIRECT_ARCHIVE_LOAD", re.compile(r"\bload_archive\s*\("), "Prefer a derived cache unless this file is itself a cache builder/provenance audit."),
    ("DIRECT_XZ_PARSE", re.compile(r"(?:lzma\.decompress|json\.xz|\.json\.xz)"), "Repeated raw tape parsing should be moved behind a cache builder or ExecutionEventCache."),
    ("DIRECT_ZIP_SCAN", re.compile(r"\bzipfile\.ZipFile\s*\("), "Prefer ExecutionTapeBundleCache / bundle-market registry; direct ZIP access only for cache builders/provenance audits."),
    ("TARGET_PARENT_RAW", re.compile(r"\btarget_parent_orders\b"), "Prefer target_parents.parquet via ResearchData/ResearchFastPath."),
    ("TARGET_EVENTS_RAW", re.compile(r"\bwallet_shadow_target_events\b"), "Prefer Target Decision/Feature Capsule when the needed field is already materialized."),
]

ALLOW_NAMES = {
    "research_fast_path_v1.py",
    "research_data_access_v1.py",
    "execution_research_cache_registry_v1.py",
    "hft_execution_event_cache_v1.py",
    "execution_tape_bundle_cache_v1.py",
    "build_execution_bundle_market_registry_v1.py",
    "hftbacktest_execution_tape_feed_v1.py",
    "build_phaseb_execution_microstructure_capsule_v1.py",
    "build_phaseb_execution_arrival_oracle_v1.py",
    "build_our_latency_frontier_transition_corpus_v1.py",
    "export_market_capsule_source_bundle_v1.py",
    "build_market_capsule_v1.py",
    "build_decision_feature_matrix_v1.py",
    "build_research_market_registry_v1.py",
}


def scan(path: Path, allow_builder: bool = False) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    allowed = allow_builder or path.name in ALLOW_NAMES
    hits = []
    for rule, rx, advice in RULES:
        for m in rx.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            hits.append({"rule": rule, "line": line, "match": m.group(0), "advice": advice})
    imports_fast = "ResearchFastPath" in text or "ResearchData" in text or "read_parquet" in text
    return {
        "path": path.resolve().relative_to(ROOT).as_posix() if ROOT in path.resolve().parents else str(path),
        "allowedBuilderOrProvenance": allowed,
        "usesFastOrParquetPath": imports_fast,
        "violations": [] if allowed else hits,
        "informationalHits": hits if allowed else [],
        "pass": allowed or not hits,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="BTC5M research fast-path compliance check")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--allow-builder", action="store_true")
    ns = ap.parse_args()
    rows = []
    for name in ns.files:
        p = Path(name)
        if not p.is_absolute():
            p = ROOT / p
        if not p.exists():
            rows.append({"path": str(p), "pass": False, "error": "missing"})
        else:
            rows.append(scan(p, ns.allow_builder))
    out = {
        "version": "BTC5M_RESEARCH_FAST_PATH_COMPLIANCE_V1",
        "files": len(rows),
        "pass": all(r.get("pass") for r in rows),
        "rows": rows,
        "policy": "New analysis/model/fork scripts should read derived Parquet through ResearchFastPath/ResearchData and normalized HFT events through ExecutionEventCache. Raw DB/tape access is reserved for cache builders and explicit provenance audits.",
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
