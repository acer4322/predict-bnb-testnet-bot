from __future__ import annotations

"""Unified fast access layer for Market Capsule research tables.

Downstream research scripts should prefer this module over opening canonical
SQLite stores directly.  Canonical SQLite/tapes remain the source of truth;
this module reads only derived Parquet analytical caches.

Safety contract:
- feature queries exclude `label_*` columns by default;
- market outcome/result is never joined automatically;
- strict-past correctness is inherited from the Capsule/feature manifests;
- Target-history columns are allowed for Target-teacher research but are not
  automatically declared deployable OUR-runtime features.
"""

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAPSULE = ROOT / "data" / "research" / "market_capsule_v1" / "benchmark_50_v1"
DEFAULT_OUR_CAPSULE = ROOT / "data" / "research" / "management_training_v1" / "our_v3b_preaction_h100_v1"
DEFAULT_PHASEB_CAPSULE = ROOT / "data" / "research" / "management_training_v1" / "phaseb_role_switch_h100_v1"
DEFAULT_EXECUTION_CACHE = ROOT / "data" / "research" / "management_training_v1" / "execution_research_cache_v1"

PRESETS = {
    "repair-critical": "seconds_left>180 AND pre_floor < -20 AND pre_abs_share_gap >= 50",
    "near-balanced": "seconds_left>180 AND pre_abs_share_gap <= 20",
    "near-floor": "seconds_left>180 AND pre_floor BETWEEN -5 AND 5",
    "dense-continuation": "seconds_left>180 AND previous_action_age_ms IS NOT NULL AND previous_action_age_ms<=3000",
    "positive-floor": "seconds_left>180 AND pre_floor>=0",
    "fresh-public": "public_age_ms IS NOT NULL AND public_age_ms<=3000",
}

OUR_PRESETS = {
    "repair-debt": "state_total_debt>0",
    "positive-floor": "state_floor>=0",
    "negative-floor": "state_floor<0",
    "open-responsibility": "state_responsibility_count>0",
    "passive-route": "action_route=\'PASSIVE\'",
    "active-route": "action_route=\'ACTIVE\'",
}

PHASEB_PRESETS = {
    "repair": "action_class=\'REPAIR\'",
    "expand": "action_class=\'EXPAND\'",
    "positive-floor": "state_floor>=0",
    "negative-floor": "state_floor<0",
    "partial-service": "state_repair_progress_frac>0 AND state_remaining_debt_qty>0",
    "structural-fill": "label_structural_fill=1",
}


class ResearchData:
    def __init__(self, capsule: str | Path = DEFAULT_CAPSULE, our_capsule: str | Path = DEFAULT_OUR_CAPSULE, phaseb_capsule: str | Path = DEFAULT_PHASEB_CAPSULE, execution_cache: str | Path = DEFAULT_EXECUTION_CACHE):
        self.capsule = Path(capsule).resolve()
        self.our_capsule = Path(our_capsule).resolve()
        self.phaseb_capsule = Path(phaseb_capsule).resolve()
        self.execution_cache = Path(execution_cache).resolve()
        self.con = duckdb.connect(database=":memory:")
        self._execution_cache_freshness = None
        self.paths = {
            "features": self.capsule / "decision_features_v1.parquet",
            "seams": self.capsule / "decision_seams.parquet",
            "markets": self.capsule / "markets.parquet",
            "target_actions": self.capsule / "target_actions.parquet",
            "target_parents": self.capsule / "target_parents.parquet",
            "public_snapshots": self.capsule / "public_snapshots.parquet",
            "market_results": self.capsule / "market_results.parquet",
            "book_updates": self.capsule / "book_updates.parquet",
            "registry": self.capsule.parent / "research_market_registry_v1.parquet",
            "our_features": self.our_capsule / "our_decision_features_v1.parquet",
            "phaseb_teacher": self.phaseb_capsule / "phaseb_counterfactual_teacher_v1.parquet",
            "phaseb_decision_micro": self.execution_cache / "phaseb_decision_microstructure_v1.parquet",
            "phaseb_arrival_oracle": self.execution_cache / "phaseb_arrival_oracle_v1.parquet",
            "our_frontier_transition": self.execution_cache / "our_latency_frontier_transition_v1.parquet",
        }

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "ResearchData":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @staticmethod
    def _q(path: Path) -> str:
        return path.as_posix().replace("'", "''")

    @staticmethod
    def _ident(name: str) -> str:
        return '"' + str(name).replace('"', '""') + '"'

    def require(self, table: str) -> Path:
        if table not in self.paths:
            raise KeyError(f"unknown table: {table}")
        p = self.paths[table]
        if not p.exists():
            raise FileNotFoundError(p)
        return p

    def columns(self, table: str) -> list[str]:
        p = self.require(table)
        return [r[0] for r in self.con.execute(f"DESCRIBE SELECT * FROM read_parquet('{self._q(p)}')").fetchall()]

    def query(self, table: str, *, where: str | None = None, columns: list[str] | None = None,
              limit: int | None = None, order_by: str | None = None,
              include_labels: bool = False, include_oracles: bool = False) -> Any:
        p = self.require(table)
        guarded = table in {"features", "our_features", "phaseb_teacher"}
        if guarded and not include_labels and where and "label_" in where.lower():
            raise ValueError("label columns in filters require include_labels=True")
        if not include_oracles and where and "oracle_" in where.lower():
            raise ValueError("oracle columns in filters require include_oracles=True")
        available = self.columns(table)
        if columns is None:
            columns = available
            if table in {"features", "our_features", "phaseb_teacher"} and not include_labels:
                columns = [c for c in columns if not c.startswith("label_")]
            if not include_oracles:
                columns = [c for c in columns if not c.startswith("oracle_")]
        else:
            missing = [c for c in columns if c not in available]
            if missing:
                raise KeyError(f"missing columns in {table}: {missing}")
            if table in {"features", "our_features", "phaseb_teacher"} and not include_labels:
                bad = [c for c in columns if c.startswith("label_")]
                if bad:
                    raise ValueError(f"label columns require include_labels=True: {bad}")
            if not include_oracles:
                bad_oracle = [c for c in columns if c.startswith("oracle_")]
                if bad_oracle:
                    raise ValueError(f"oracle columns require include_oracles=True: {bad_oracle}")
        sql = f"SELECT {','.join(self._ident(c) for c in columns)} FROM read_parquet('{self._q(p)}')"
        if where:
            sql += f" WHERE ({where})"
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit is not None:
            sql += f" LIMIT {max(1, int(limit))}"
        return self.con.sql(sql)

    def feature_rows(self, *, preset: str | None = None, where: str | None = None,
                     columns: list[str] | None = None, limit: int | None = None,
                     include_labels: bool = False) -> Any:
        clauses = []
        if preset:
            if preset not in PRESETS:
                raise KeyError(f"unknown preset {preset}; choices={sorted(PRESETS)}")
            clauses.append(PRESETS[preset])
        if where:
            clauses.append(where)
        return self.query("features", where=" AND ".join(f"({x})" for x in clauses) if clauses else None,
                          columns=columns, limit=limit, order_by="market_id,decision_ms,seam_id",
                          include_labels=include_labels)

    def our_feature_rows(self, *, preset: str | None = None, where: str | None = None,
                         columns: list[str] | None = None, limit: int | None = None,
                         include_labels: bool = False, state_only: bool = False) -> Any:
        clauses = []
        if preset:
            if preset not in OUR_PRESETS:
                raise KeyError(f"unknown OUR preset {preset}; choices={sorted(OUR_PRESETS)}")
            clauses.append(OUR_PRESETS[preset])
        if where:
            clauses.append(where)
        if columns is None and state_only:
            ids = {"market_id", "decision_ms", "carrier_key", "window_end_ms", "state_timing", "candidate_already_in_state"}
            columns = [c for c in self.columns("our_features") if c in ids or c.startswith("state_")]
        return self.query("our_features", where=" AND ".join(f"({x})" for x in clauses) if clauses else None,
                          columns=columns, limit=limit, order_by="market_id,decision_ms,carrier_key",
                          include_labels=include_labels)

    def phaseb_rows(self, *, preset: str | None = None, where: str | None = None,
                    columns: list[str] | None = None, limit: int | None = None,
                    include_labels: bool = False, state_only: bool = False) -> Any:
        clauses = []
        if preset:
            if preset not in PHASEB_PRESETS:
                raise KeyError(f"unknown Phase-B preset {preset}; choices={sorted(PHASEB_PRESETS)}")
            clauses.append(PHASEB_PRESETS[preset])
        if where:
            clauses.append(where)
        if columns is None and state_only:
            ids = {"market_id", "decision_ms", "pair_id", "prefix_digest", "window_end_ms", "state_timing", "candidate_already_in_state"}
            columns = [c for c in self.columns("phaseb_teacher") if c in ids or c.startswith("state_")]
        return self.query("phaseb_teacher", where=" AND ".join(f"({x})" for x in clauses) if clauses else None,
                          columns=columns, limit=limit, order_by="market_id,decision_ms,action_branch",
                          include_labels=include_labels)


    def execution_cache_status(self, *, deep: bool = False) -> dict[str, Any]:
        if deep or self._execution_cache_freshness is None:
            try:
                from tools.execution_research_cache_registry_v1 import check_registry
            except ImportError:
                from execution_research_cache_registry_v1 import check_registry
            self._execution_cache_freshness = check_registry(bool(deep))
        return self._execution_cache_freshness

    def _assert_execution_cache_fresh(self, registry_name: str, *, allow_stale: bool = False) -> None:
        if allow_stale:
            return
        status = self.execution_cache_status(deep=False)
        item = (status.get("datasets") or {}).get(registry_name) or {}
        if not item.get("fresh"):
            raise RuntimeError(f"execution cache is stale or unavailable for {registry_name}: {item}")

    def execution_rows(self, *, dataset: str = "phaseb-decision", where: str | None = None,
                       columns: list[str] | None = None, limit: int | None = None,
                       include_oracles: bool = False, allow_stale: bool = False) -> Any:
        table_map = {
            "phaseb-decision": "phaseb_decision_micro",
            "phaseb-arrival": "phaseb_arrival_oracle",
            "our-frontier": "our_frontier_transition",
        }
        if dataset not in table_map:
            raise KeyError(f"unknown execution dataset {dataset}; choices={sorted(table_map)}")
        table = table_map[dataset]
        registry_map = {"phaseb-decision": "phaseb_decision_micro", "phaseb-arrival": "phaseb_arrival_oracle", "our-frontier": "our_frontier_transition"}
        self._assert_execution_cache_fresh(registry_map[dataset], allow_stale=allow_stale)
        return self.query(table, where=where, columns=columns, limit=limit,
                          order_by="market_id,decision_ms", include_oracles=include_oracles)

    def parent_rows(self, *, market_id: int | None = None, role: str | None = None,
                    side: str | None = None, quote_type: str | None = None,
                    where: str | None = None, columns: list[str] | None = None,
                    limit: int | None = None) -> Any:
        clauses: list[str] = []
        if market_id is not None:
            clauses.append(f"market_id={int(market_id)}")
        if role:
            clauses.append("role='" + str(role).upper().replace("'", "''") + "'")
        if side:
            clauses.append("side='" + str(side).upper().replace("'", "''") + "'")
        if quote_type:
            clauses.append("quote_type='" + str(quote_type).upper().replace("'", "''") + "'")
        if where:
            clauses.append(where)
        return self.query("target_parents", where=" AND ".join(f"({x})" for x in clauses) if clauses else None,
                          columns=columns, limit=limit, order_by="market_id,first_event_ms,parent_id")

    def registry_rows(self, *, where: str | None = None, columns: list[str] | None = None,
                      limit: int | None = None, newest_first: bool = True) -> Any:
        order = "window_end_ms DESC NULLS LAST,market_id DESC" if newest_first else "window_end_ms ASC NULLS LAST,market_id ASC"
        return self.query("registry", where=where, columns=columns, limit=limit, order_by=order)

    def cohort_ids(self, *, ready: bool = True, require_parents: bool = False,
                   where: str | None = None, limit: int = 50, newest_first: bool = True) -> list[int]:
        clauses: list[str] = []
        if ready:
            clauses.append("ready_market_capsule=1")
        if require_parents:
            clauses.append("has_target_parents=1")
        if where:
            clauses.append(where)
        rel = self.registry_rows(where=" AND ".join(f"({x})" for x in clauses) if clauses else None,
                                 columns=["market_id"], limit=max(1, int(limit)), newest_first=newest_first)
        return [int(r[0]) for r in rel.fetchall()]

    def market_ids(self, *, preset: str | None = None, where: str | None = None) -> list[int]:
        rel = self.feature_rows(preset=preset, where=where, columns=["market_id"], limit=None)
        return sorted({int(r[0]) for r in rel.fetchall()})

    def our_market_ids(self, *, preset: str | None = None, where: str | None = None) -> list[int]:
        rel = self.our_feature_rows(preset=preset, where=where, columns=["market_id"], limit=None)
        return sorted({int(r[0]) for r in rel.fetchall()})

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"capsule": str(self.capsule), "ourCapsule": str(self.our_capsule), "phasebCapsule": str(self.phaseb_capsule), "executionCache": str(self.execution_cache)}
        for name in ("markets", "seams", "features", "target_parents", "registry", "our_features", "phaseb_teacher", "phaseb_decision_micro", "phaseb_arrival_oracle", "our_frontier_transition"):
            if not self.paths[name].exists():
                continue
            p = self.require(name)
            out[name] = int(self.con.execute(f"SELECT count(*) FROM read_parquet('{self._q(p)}')").fetchone()[0])
        m = self.capsule / "decision_features_v1.manifest.json"
        if m.exists():
            manifest = json.loads(m.read_text(encoding="utf-8"))
            out["featureStrictPast"] = manifest.get("strictPast")
            out["featureCoverage"] = manifest.get("coverage")
        om = self.our_capsule / "our_decision_features_v1.manifest.json"
        if om.exists():
            manifest = json.loads(om.read_text(encoding="utf-8"))
            out["ourFeatureStateSemantics"] = manifest.get("stateSemantics")
            out["ourFeatureChecks"] = manifest.get("checks")
            out["ourFeaturePromotionGate"] = manifest.get("promotionGate")
        pm = self.phaseb_capsule / "phaseb_counterfactual_teacher_v1.manifest.json"
        if pm.exists():
            manifest = json.loads(pm.read_text(encoding="utf-8"))
            out["phasebChecks"] = manifest.get("checks")
            out["phasebPromotionGate"] = manifest.get("promotionGate")
            out["phasebStateSemantics"] = manifest.get("stateSemantics")
        try:
            out["executionCacheFreshness"] = self.execution_cache_status(deep=False)
        except Exception as exc:
            out["executionCacheFreshness"] = {"all_fresh": False, "error": f"{type(exc).__name__}: {exc}"}
        return out


def _write_relation(rel: Any, output: Path, fmt: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    q = output.resolve().as_posix().replace("'", "''")
    if fmt == "parquet":
        rel.query("x", f"COPY (SELECT * FROM x) TO '{q}' (FORMAT PARQUET, COMPRESSION ZSTD)").execute()
    elif fmt == "csv":
        rel.df().to_csv(output, index=False)
    elif fmt == "jsonl":
        with output.open("w", encoding="utf-8", newline="\n") as fh:
            for rec in rel.df().to_dict(orient="records"):
                fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    else:
        raise ValueError(fmt)


def main() -> int:
    ap = argparse.ArgumentParser(description="BTC5M Research Data Access V1")
    ap.add_argument("--capsule", type=Path, default=DEFAULT_CAPSULE)
    ap.add_argument("--our-capsule", type=Path, default=DEFAULT_OUR_CAPSULE)
    ap.add_argument("--phaseb-capsule", type=Path, default=DEFAULT_PHASEB_CAPSULE)
    ap.add_argument("--execution-cache", type=Path, default=DEFAULT_EXECUTION_CACHE)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("summary")
    q = sub.add_parser("features")
    q.add_argument("--preset", choices=sorted(PRESETS))
    q.add_argument("--where")
    q.add_argument("--columns", help="comma-separated columns")
    q.add_argument("--limit", type=int, default=50)
    q.add_argument("--include-labels", action="store_true")
    q.add_argument("--output", type=Path)
    q.add_argument("--format", choices=["parquet", "csv", "jsonl"], default="parquet")
    oq = sub.add_parser("our-features")
    oq.add_argument("--preset", choices=sorted(OUR_PRESETS))
    oq.add_argument("--where")
    oq.add_argument("--columns", help="comma-separated columns")
    oq.add_argument("--limit", type=int, default=50)
    oq.add_argument("--include-labels", action="store_true")
    oq.add_argument("--state-only", action="store_true")
    oq.add_argument("--output", type=Path)
    oq.add_argument("--format", choices=["parquet", "csv", "jsonl"], default="parquet")
    pb = sub.add_parser("phaseb")
    pb.add_argument("--preset", choices=sorted(PHASEB_PRESETS))
    pb.add_argument("--where")
    pb.add_argument("--columns", help="comma-separated columns")
    pb.add_argument("--limit", type=int, default=50)
    pb.add_argument("--include-labels", action="store_true")
    pb.add_argument("--state-only", action="store_true")
    pb.add_argument("--output", type=Path)
    pb.add_argument("--format", choices=["parquet", "csv", "jsonl"], default="parquet")
    exq = sub.add_parser("execution")
    exq.add_argument("--dataset", choices=["phaseb-decision", "phaseb-arrival", "our-frontier"], default="phaseb-decision")
    exq.add_argument("--where")
    exq.add_argument("--columns", help="comma-separated columns")
    exq.add_argument("--limit", type=int, default=50)
    exq.add_argument("--include-oracles", action="store_true")
    exq.add_argument("--allow-stale", action="store_true")
    exq.add_argument("--output", type=Path)
    exq.add_argument("--format", choices=["parquet", "csv", "jsonl"], default="parquet")
    rg = sub.add_parser("registry")
    rg.add_argument("--where")
    rg.add_argument("--columns", help="comma-separated columns")
    rg.add_argument("--limit", type=int, default=50)
    rg.add_argument("--oldest-first", action="store_true")
    rg.add_argument("--output", type=Path)
    rg.add_argument("--format", choices=["parquet", "csv", "jsonl"], default="parquet")
    ch = sub.add_parser("cohort")
    ch.add_argument("--latest", type=int, default=50)
    ch.add_argument("--oldest", action="store_true")
    ch.add_argument("--allow-not-ready", action="store_true")
    ch.add_argument("--require-parents", action="store_true")
    ch.add_argument("--where")
    ch.add_argument("--output", type=Path)
    pr = sub.add_parser("parents")
    pr.add_argument("--market-id", type=int)
    pr.add_argument("--role", choices=["MAKER", "TAKER"])
    pr.add_argument("--side", choices=["UP", "DOWN"])
    pr.add_argument("--quote-type", choices=["BID", "ASK"])
    pr.add_argument("--where")
    pr.add_argument("--columns", help="comma-separated columns")
    pr.add_argument("--limit", type=int, default=50)
    pr.add_argument("--output", type=Path)
    pr.add_argument("--format", choices=["parquet", "csv", "jsonl"], default="parquet")
    oids = sub.add_parser("our-ids")
    oids.add_argument("--preset", choices=sorted(OUR_PRESETS))
    oids.add_argument("--where")
    ids = sub.add_parser("ids")
    ids.add_argument("--preset", choices=sorted(PRESETS))
    ids.add_argument("--where")
    ns = ap.parse_args()

    with ResearchData(ns.capsule, ns.our_capsule, ns.phaseb_capsule, ns.execution_cache) as data:
        if ns.cmd == "summary":
            print(json.dumps(data.summary(), indent=2, ensure_ascii=False))
            return 0
        if ns.cmd == "ids":
            vals = data.market_ids(preset=ns.preset, where=ns.where)
            print(json.dumps({"marketIds": vals, "count": len(vals)}, indent=2))
            return 0
        if ns.cmd == "our-ids":
            vals = data.our_market_ids(preset=ns.preset, where=ns.where)
            print(json.dumps({"marketIds": vals, "count": len(vals)}, indent=2))
            return 0
        if ns.cmd == "our-features":
            cols = [x.strip() for x in ns.columns.split(",") if x.strip()] if ns.columns else None
            rel = data.our_feature_rows(preset=ns.preset, where=ns.where, columns=cols, limit=ns.limit,
                                        include_labels=ns.include_labels, state_only=ns.state_only)
            if ns.output:
                _write_relation(rel, ns.output, ns.format)
                print(json.dumps({"ok": True, "output": str(ns.output), "rows": int(rel.count('*').fetchone()[0]), "labelsIncluded": bool(ns.include_labels), "stateOnly": bool(ns.state_only)}, indent=2))
            else:
                print(rel.df().to_string(index=False))
            return 0
        if ns.cmd == "phaseb":
            cols = [x.strip() for x in ns.columns.split(",") if x.strip()] if ns.columns else None
            rel = data.phaseb_rows(preset=ns.preset, where=ns.where, columns=cols, limit=ns.limit,
                                   include_labels=ns.include_labels, state_only=ns.state_only)
            if ns.output:
                _write_relation(rel, ns.output, ns.format)
                print(json.dumps({"ok": True, "output": str(ns.output), "rows": int(rel.count('*').fetchone()[0]), "labelsIncluded": bool(ns.include_labels), "stateOnly": bool(ns.state_only)}, indent=2))
            else:
                print(rel.df().to_string(index=False))
            return 0
        if ns.cmd == "execution":
            cols = [x.strip() for x in ns.columns.split(",") if x.strip()] if ns.columns else None
            rel = data.execution_rows(dataset=ns.dataset, where=ns.where, columns=cols, limit=ns.limit,
                                      include_oracles=ns.include_oracles, allow_stale=ns.allow_stale)
            if ns.output:
                _write_relation(rel, ns.output, ns.format)
                print(json.dumps({"ok": True, "output": str(ns.output), "rows": int(rel.count('*').fetchone()[0]), "dataset": ns.dataset, "oraclesIncluded": bool(ns.include_oracles)}, indent=2))
            else:
                print(rel.df().to_string(index=False))
            return 0
        if ns.cmd == "registry":
            cols = [x.strip() for x in ns.columns.split(",") if x.strip()] if ns.columns else None
            rel = data.registry_rows(where=ns.where, columns=cols, limit=ns.limit, newest_first=not ns.oldest_first)
            if ns.output:
                _write_relation(rel, ns.output, ns.format)
                print(json.dumps({"ok": True, "output": str(ns.output), "rows": int(rel.count('*').fetchone()[0])}, indent=2))
            else:
                print(rel.df().to_string(index=False))
            return 0
        if ns.cmd == "cohort":
            vals = data.cohort_ids(ready=not ns.allow_not_ready, require_parents=ns.require_parents,
                                   where=ns.where, limit=ns.latest, newest_first=not ns.oldest)
            payload = {"marketIds": vals, "count": len(vals), "selection": {
                "readyRequired": not ns.allow_not_ready, "parentsRequired": bool(ns.require_parents),
                "where": ns.where, "order": "oldest" if ns.oldest else "newest"}}
            if ns.output:
                ns.output.parent.mkdir(parents=True, exist_ok=True)
                ns.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                print(json.dumps({"ok": True, "output": str(ns.output), "count": len(vals)}, indent=2))
            else:
                print(json.dumps(payload, indent=2))
            return 0
        if ns.cmd == "parents":
            cols = [x.strip() for x in ns.columns.split(",") if x.strip()] if ns.columns else None
            rel = data.parent_rows(market_id=ns.market_id, role=ns.role, side=ns.side, quote_type=ns.quote_type,
                                   where=ns.where, columns=cols, limit=ns.limit)
            if ns.output:
                _write_relation(rel, ns.output, ns.format)
                print(json.dumps({"ok": True, "output": str(ns.output), "rows": int(rel.count('*').fetchone()[0])}, indent=2))
            else:
                print(rel.df().to_string(index=False))
            return 0
        cols = [x.strip() for x in ns.columns.split(",") if x.strip()] if ns.columns else None
        rel = data.feature_rows(preset=ns.preset, where=ns.where, columns=cols, limit=ns.limit,
                                include_labels=ns.include_labels)
        if ns.output:
            _write_relation(rel, ns.output, ns.format)
            print(json.dumps({"ok": True, "output": str(ns.output), "rows": int(rel.count('*').fetchone()[0]), "labelsIncluded": bool(ns.include_labels)}, indent=2))
        else:
            print(rel.df().to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
