from __future__ import annotations

"""Fast freshness registry for derived execution research caches.

Normal checks use only file size + mtime_ns and cached manifest promotion status.
Full SHA256 is captured when the registry is refreshed and can be reverified with
--deep.  This keeps per-research-turn freshness checks cheap while preserving a
strong provenance fingerprint when needed.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
CACHE_ROOT = ROOT / "data" / "research" / "management_training_v1" / "execution_research_cache_v1"
REGISTRY = CACHE_ROOT / "execution_research_cache_registry_v1.json"

DATASETS = {
    "phaseb_decision_micro": {
        "cache": CACHE_ROOT / "phaseb_decision_microstructure_v1.parquet",
        "manifest": CACHE_ROOT / "phaseb_decision_microstructure_v1.manifest.json",
        "sources": [
            ROOT / "data" / "research" / "r4_v0" / "p0_provenance_v1" / "v16_consumed_holdout100_bundle.zip",
            ROOT / "data" / "research" / "management_training_v1" / "phaseb_role_switch_h100_v1" / "phaseb_counterfactual_teacher_v1.parquet",
            ROOT / "tools" / "build_phaseb_execution_microstructure_capsule_v1.py",
        ],
        "semantics": "causal decision-time execution microstructure",
    },
    "phaseb_arrival_oracle": {
        "cache": CACHE_ROOT / "phaseb_arrival_oracle_v1.parquet",
        "manifest": CACHE_ROOT / "phaseb_arrival_oracle_v1.manifest.json",
        "sources": [
            ROOT / "data" / "research" / "r4_v0" / "p0_provenance_v1" / "v16_consumed_holdout100_bundle.zip",
            ROOT / "data" / "research" / "management_training_v1" / "phaseb_role_switch_h100_v1" / "phaseb_counterfactual_teacher_v1.parquet",
            ROOT / "tools" / "build_phaseb_execution_arrival_oracle_v1.py",
            ROOT / "tools" / "build_phaseb_execution_microstructure_capsule_v1.py",
        ],
        "semantics": "offline future arrival-frontier oracle supervision",
    },
    "our_frontier_transition": {
        "cache": CACHE_ROOT / "our_latency_frontier_transition_v1.parquet",
        "manifest": CACHE_ROOT / "our_latency_frontier_transition_v1.manifest.json",
        "sources": [
            ROOT / "data" / "research" / "r4_v0" / "p0_provenance_v1" / "v16_consumed_holdout100_bundle.zip",
            ROOT / "data" / "research" / "management_training_v1" / "our_v3b_preaction_h100_v1" / "our_decision_features_v1.parquet",
            ROOT / "tools" / "build_our_latency_frontier_transition_corpus_v1.py",
            ROOT / "tools" / "build_phaseb_execution_microstructure_capsule_v1.py",
        ],
        "semantics": "native PASSIVE decision micro + future arrival supervision",
    },
}


def rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except Exception:
        return p.resolve().as_posix()


def fast_fp(p: Path) -> dict[str, Any]:
    st = p.stat()
    return {"path": rel(p), "size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def row_count(p: Path) -> int:
    con = duckdb.connect(database=":memory:")
    try:
        q = p.resolve().as_posix().replace("'", "''")
        return int(con.execute(f"select count(*) from read_parquet('{q}')").fetchone()[0])
    finally:
        con.close()


def manifest_pass(p: Path) -> bool | None:
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        gate = d.get("promotionGate") or {}
        return bool(gate.get("pass")) if "pass" in gate else None
    except Exception:
        return None


def build_registry() -> dict[str, Any]:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {
        "version": "EXECUTION_RESEARCH_CACHE_REGISTRY_V1",
        "created_at": time.time(),
        "cache_root": rel(CACHE_ROOT),
        "datasets": {},
    }
    for name, spec in DATASETS.items():
        cache: Path = spec["cache"]
        manifest: Path = spec["manifest"]
        sources: list[Path] = spec["sources"]
        missing = [rel(p) for p in [cache, manifest, *sources] if not p.exists()]
        item: dict[str, Any] = {
            "semantics": spec["semantics"],
            "cache": rel(cache),
            "manifest": rel(manifest),
            "missing": missing,
            "promotion_pass": manifest_pass(manifest),
        }
        if cache.exists():
            item["cache_fast"] = fast_fp(cache)
            item["cache_sha256"] = sha256(cache)
            item["rows"] = row_count(cache)
        src_rows = []
        for p in sources:
            if p.exists():
                fp = fast_fp(p)
                fp["sha256"] = sha256(p)
                src_rows.append(fp)
            else:
                src_rows.append({"path": rel(p), "missing": True})
        item["sources"] = src_rows
        item["fresh_at_build"] = not missing and item.get("promotion_pass") is True
        out["datasets"][name] = item
    out["all_ready"] = all(x.get("fresh_at_build") for x in out["datasets"].values())
    REGISTRY.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def check_registry(deep: bool = False) -> dict[str, Any]:
    if not REGISTRY.exists():
        return {"ok": False, "reason": "registry_missing", "registry": rel(REGISTRY)}
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    result: dict[str, Any] = {
        "ok": True,
        "version": "EXECUTION_RESEARCH_CACHE_REGISTRY_CHECK_V1",
        "registry": rel(REGISTRY),
        "deep": bool(deep),
        "datasets": {},
    }
    for name, old in (reg.get("datasets") or {}).items():
        cache = ROOT / old["cache"]
        manifest = ROOT / old["manifest"]
        changed: list[str] = []
        missing: list[str] = []
        for src in old.get("sources") or []:
            p = ROOT / src["path"]
            if not p.exists():
                missing.append(src["path"])
                continue
            cur = fast_fp(p)
            if int(cur["size"]) != int(src.get("size", -1)) or int(cur["mtime_ns"]) != int(src.get("mtime_ns", -1)):
                changed.append(src["path"])
            elif deep and sha256(p) != src.get("sha256"):
                changed.append(src["path"] + "#sha256")
        cache_ok = cache.exists()
        manifest_ok = manifest_pass(manifest) is True
        cache_changed = False
        if cache_ok and old.get("cache_fast"):
            curc = fast_fp(cache)
            ofp = old["cache_fast"]
            cache_changed = int(curc["size"]) != int(ofp.get("size", -1)) or int(curc["mtime_ns"]) != int(ofp.get("mtime_ns", -1))
            if deep and not cache_changed and sha256(cache) != old.get("cache_sha256"):
                cache_changed = True
        fresh = cache_ok and manifest_ok and not cache_changed and not changed and not missing
        result["datasets"][name] = {
            "fresh": fresh,
            "cache_exists": cache_ok,
            "promotion_pass": manifest_ok,
            "cache_changed": cache_changed,
            "changed_sources": changed,
            "missing": missing,
        }
    result["all_fresh"] = all(x.get("fresh") for x in result["datasets"].values())
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Execution research cache freshness registry")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("refresh")
    ck = sub.add_parser("check")
    ck.add_argument("--deep", action="store_true")
    ns = ap.parse_args()
    if ns.cmd == "refresh":
        out = build_registry()
        print(json.dumps({"ok": True, "registry": rel(REGISTRY), "allReady": out["all_ready"], "datasets": {k: {"rows": v.get("rows"), "promotionPass": v.get("promotion_pass"), "freshAtBuild": v.get("fresh_at_build")} for k, v in out["datasets"].items()}}, indent=2, ensure_ascii=False))
        return 0
    out = check_registry(bool(ns.deep))
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("all_fresh") else 2


if __name__ == "__main__":
    raise SystemExit(main())
