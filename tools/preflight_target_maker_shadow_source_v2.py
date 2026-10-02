from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BEHAVIOR = ROOT / "data" / "research" / "target_maker_direct_behavior_v1.csv"
DEFAULT_SHADOW_DB = ROOT / "data" / "predict_wallet_shadow.db"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_shadow_source_v2_preflight.json"

TIME_NAMES = (
    "event_ms", "first_event_ms", "last_event_ms", "sampled_at_ms", "created_at_ms",
    "updated_at_ms", "timestamp_ms", "observed_at_ms", "captured_at_ms", "filled_at_ms",
    "placement_first_ms", "first_fill_ms",
)
MARKET_NAMES = ("market_id", "marketId", "topic_id")


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _read_behavior(path: Path) -> tuple[set[int], int, int, int]:
    markets: set[int] = set()
    first: int | None = None
    last: int | None = None
    rows = 0
    with path.expanduser().resolve().open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        if not {"market_id", "placement_first_ms"}.issubset(fields):
            raise RuntimeError("behavior CSV must contain market_id and placement_first_ms")
        for row in reader:
            try:
                market = int(float(row["market_id"]))
                at_ms = int(float(row["placement_first_ms"]))
            except (TypeError, ValueError):
                continue
            rows += 1
            markets.add(market)
            first = at_ms if first is None else min(first, at_ms)
            last = at_ms if last is None else max(last, at_ms)
    if first is None or last is None:
        raise RuntimeError("behavior CSV has no usable rows")
    return markets, first, last, rows


def _columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in db.execute(f"PRAGMA table_info({_q(table)})")]


def _pick(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    lower = {name.lower(): name for name in columns}
    for candidate in candidates:
        if candidate.lower() in lower:
            return lower[candidate.lower()]
    return None


def _candidate_score(table: str, columns: list[str]) -> int:
    text = (table + " " + " ".join(columns)).lower()
    score = 0
    for token, weight in (("target", 5), ("wallet", 4), ("shadow", 4), ("maker", 4), ("taker", 2), ("event", 2), ("parent", 2), ("role", 2), ("side", 1), ("market", 1)):
        if token in text:
            score += weight
    return score


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only preflight for the legacy predict_wallet_shadow.db Maker source")
    parser.add_argument("--behavior-dataset", type=Path, default=DEFAULT_BEHAVIOR)
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    behavior_path = args.behavior_dataset.expanduser().resolve()
    shadow_path = args.shadow_db.expanduser().resolve()
    if not behavior_path.exists():
        raise SystemExit(f"missing behavior dataset: {behavior_path}")
    if not shadow_path.exists():
        raise SystemExit(f"missing legacy shadow DB: {shadow_path}")

    behavior_markets, behavior_first, behavior_last, behavior_rows = _read_behavior(behavior_path)
    db = sqlite3.connect(f"file:{shadow_path.as_posix()}?mode=ro", uri=True, timeout=10.0)
    db.row_factory = sqlite3.Row
    try:
        tables = [str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        results: list[dict[str, Any]] = []
        for table in tables:
            cols = _columns(db, table)
            market_col = _pick(cols, MARKET_NAMES)
            time_col = _pick(cols, TIME_NAMES)
            score = _candidate_score(table, cols)
            item: dict[str, Any] = {
                "table": table,
                "score": score,
                "columns": cols,
                "marketColumn": market_col,
                "timeColumn": time_col,
            }
            try:
                item["rows"] = int(db.execute(f"SELECT COUNT(*) FROM {_q(table)}").fetchone()[0])
            except sqlite3.Error as exc:
                item["countError"] = type(exc).__name__

            if market_col is not None:
                try:
                    table_markets = {
                        int(row[0])
                        for row in db.execute(
                            f"SELECT DISTINCT {_q(market_col)} FROM {_q(table)} WHERE {_q(market_col)} IS NOT NULL"
                        )
                        if _finite(row[0]) is not None
                    }
                    overlap = sorted(behavior_markets & table_markets)
                    item["distinctMarkets"] = len(table_markets)
                    item["exactMarketOverlap"] = len(overlap)
                    item["exactMarketOverlapSample"] = overlap[:20]
                except (sqlite3.Error, TypeError, ValueError) as exc:
                    item["marketAuditError"] = type(exc).__name__

            if time_col is not None:
                try:
                    row = db.execute(
                        f"SELECT MIN({_q(time_col)}), MAX({_q(time_col)}) FROM {_q(table)} WHERE {_q(time_col)} IS NOT NULL"
                    ).fetchone()
                    tmin = int(float(row[0])) if row and _finite(row[0]) is not None else None
                    tmax = int(float(row[1])) if row and _finite(row[1]) is not None else None
                    item["timeRange"] = {"firstMs": tmin, "lastMs": tmax}
                    if tmin is not None and tmax is not None:
                        item["timeRangesOverlapBehavior"] = not (tmax < behavior_first or tmin > behavior_last)
                        try:
                            item["rowsInsideBehaviorTimeRange"] = int(
                                db.execute(
                                    f"SELECT COUNT(*) FROM {_q(table)} WHERE {_q(time_col)}>=? AND {_q(time_col)}<=?",
                                    (behavior_first, behavior_last),
                                ).fetchone()[0]
                            )
                        except sqlite3.Error:
                            pass
                except (sqlite3.Error, TypeError, ValueError) as exc:
                    item["timeAuditError"] = type(exc).__name__

            if market_col is not None and time_col is not None and item.get("exactMarketOverlap", 0):
                overlap_ids = item.get("exactMarketOverlapSample", [])
                # Full overlap row count uses a temporary in-memory parameter list only when manageable.
                full_overlap = sorted(behavior_markets)
                if len(full_overlap) <= 999:
                    placeholders = ",".join("?" for _ in full_overlap)
                    try:
                        item["rowsOnBehaviorMarketsInsideBehaviorTimeRange"] = int(
                            db.execute(
                                f"SELECT COUNT(*) FROM {_q(table)} WHERE {_q(market_col)} IN ({placeholders}) AND {_q(time_col)}>=? AND {_q(time_col)}<=?",
                                [*full_overlap, behavior_first, behavior_last],
                            ).fetchone()[0]
                        )
                    except sqlite3.Error as exc:
                        item["jointAuditError"] = type(exc).__name__

            results.append(item)

        ranked = sorted(
            results,
            key=lambda x: (
                int(x.get("rowsOnBehaviorMarketsInsideBehaviorTimeRange") or 0),
                int(x.get("exactMarketOverlap") or 0),
                int(bool(x.get("timeRangesOverlapBehavior"))),
                int(x.get("score") or 0),
                int(x.get("rows") or 0),
            ),
            reverse=True,
        )
        likely = [
            row for row in ranked
            if int(row.get("exactMarketOverlap") or 0) > 0
            and bool(row.get("timeRangesOverlapBehavior"))
        ]
        payload = {
            "reportVersion": "TARGET_MAKER_LEGACY_SHADOW_SOURCE_V2_PREFLIGHT",
            "paperResearchOnly": True,
            "noModelTraining": True,
            "behavior": {
                "path": str(behavior_path),
                "rows": behavior_rows,
                "markets": len(behavior_markets),
                "firstPlacementMs": behavior_first,
                "lastPlacementMs": behavior_last,
            },
            "shadowDb": str(shadow_path),
            "tables": len(tables),
            "likelySourceTables": likely[:10],
            "topRankedTables": ranked[:25],
            "interpretation": "Choose a source table only if it has both exact market-id overlap and time overlap with the Maker V1 behavior dataset. Prefer tables whose rowsOnBehaviorMarketsInsideBehaviorTimeRange is materially positive and whose schema exposes role/side/event identity. No EBM should run before this source gate passes.",
        }
        report = args.report.expanduser().resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("TARGET MAKER LEGACY SHADOW SOURCE V2 PREFLIGHT", flush=True)
        print(f"behavior rows={behavior_rows} markets={len(behavior_markets)} range={behavior_first}..{behavior_last}", flush=True)
        print(f"shadow tables={len(tables)}", flush=True)
        for index, row in enumerate(likely[:10], 1):
            print(
                f"  candidate {index}: {row['table']} overlapMarkets={row.get('exactMarketOverlap')} "
                f"timeOverlap={row.get('timeRangesOverlapBehavior')} rowsInJointWindow={row.get('rowsOnBehaviorMarketsInsideBehaviorTimeRange')} "
                f"marketCol={row.get('marketColumn')} timeCol={row.get('timeColumn')}",
                flush=True,
            )
        if not likely:
            print("  NO TABLE PASSED market+time overlap gate", flush=True)
        print(f"Report: {report}", flush=True)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
