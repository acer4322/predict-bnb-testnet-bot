from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from predict_bot.target_maker_taker_link import DEFAULT_SHADOW_DB, TAKER_COHORT

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "data" / "research" / "target_taker_direct_eligibility_special_regime_v1.csv"
DEFAULT_META = ROOT / "data" / "research" / "target_taker_direct_eligibility_special_regime_v1.meta.json"


def _epoch_ms(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        pass
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"time must include timezone offset or be epoch ms, got {value!r}")
    return int(dt.timestamp() * 1000)


def _meta_value(meta: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in meta:
            return meta[key]
    return None


def _shadow_diagnostics(
    shadow_path: Path,
    *,
    start_ms: int,
    end_ms: int | None,
    special_market_ids: set[int],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(shadow_path),
        "exists": shadow_path.exists(),
    }
    if not shadow_path.exists():
        return result
    uri = f"file:{shadow_path.resolve().as_posix()}?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        table = "wallet_target_taker_mirror_parents"
        result["hasParentsTable"] = table in tables
        if table not in tables:
            return result

        clauses = ["cohort=?", "target_event_ms>=?"]
        params: list[Any] = [TAKER_COHORT, int(start_ms)]
        if end_ms is not None:
            clauses.append("target_event_ms<?")
            params.append(int(end_ms))
        where = " AND ".join(clauses)
        rows = list(
            db.execute(
                f"""SELECT market_id,target_event_ms
                       FROM {table}
                      WHERE {where}
                      ORDER BY target_event_ms""",
                tuple(params),
            )
        )
        event_market_ids = {int(row["market_id"]) for row in rows}
        intersection = sorted(event_market_ids & special_market_ids)
        result.update(
            {
                "eventRowsInWindow": len(rows),
                "eventMarketsInWindow": len(event_market_ids),
                "firstEventMs": int(rows[0]["target_event_ms"]) if rows else None,
                "lastEventMs": int(rows[-1]["target_event_ms"]) if rows else None,
                "eventMarketIdsSample": sorted(event_market_ids)[:12],
                "specialMarketIdsSample": sorted(special_market_ids)[:12],
                "marketIdIntersectionCount": len(intersection),
                "marketIdIntersectionSample": intersection[:12],
            }
        )
        return result
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail fast when a Target Taker special-regime holdout has no positive labels, "
            "and diagnose stale Target-mirror coverage versus market-id join mismatch."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--meta", type=Path, default=DEFAULT_META)
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--special-start", required=True)
    parser.add_argument("--special-end", default=None)
    args = parser.parse_args()

    try:
        import pandas as pd
    except ImportError as exc:
        raise SystemExit('Research dependencies missing. Run: pip install -e ".[research]"') from exc

    start_ms = _epoch_ms(args.special_start)
    end_ms = _epoch_ms(args.special_end)
    if start_ms is None:
        raise SystemExit("--special-start is required")
    if end_ms is not None and end_ms <= start_ms:
        raise SystemExit("--special-end must be after --special-start")

    dataset = args.dataset.expanduser().resolve()
    if not dataset.exists():
        raise SystemExit(f"dataset missing: {dataset}")

    needed = [
        "market_id",
        "decision_sampled_at_ms",
        "label_next_target_taker_any_1s",
        "label_next_target_taker_any_2s",
        "label_next_target_taker_any_5s",
    ]
    frame = pd.read_csv(dataset, usecols=needed)
    frame["decision_sampled_at_ms"] = pd.to_numeric(
        frame["decision_sampled_at_ms"], errors="raise"
    ).astype("int64")
    mask = frame["decision_sampled_at_ms"] >= start_ms
    if end_ms is not None:
        mask &= frame["decision_sampled_at_ms"] < end_ms
    special = frame.loc[mask].copy()
    if special.empty:
        raise SystemExit("TARGET_TAKER_SPECIAL_LABEL_PREFLIGHT FAILED: no special-regime rows")

    counts: dict[str, int] = {}
    rates: dict[str, float] = {}
    for horizon in (1, 2, 5):
        column = f"label_next_target_taker_any_{horizon}s"
        values = pd.to_numeric(special[column], errors="raise").astype(int)
        counts[f"{horizon}s"] = int(values.sum())
        rates[f"{horizon}s"] = float(values.mean())

    special_market_ids = {int(value) for value in special["market_id"].unique().tolist()}
    print("TARGET_TAKER_SPECIAL_LABEL_PREFLIGHT")
    print(f"special rows:    {len(special)}")
    print(f"special markets: {len(special_market_ids)}")
    for horizon in (1, 2, 5):
        key = f"{horizon}s"
        print(f"{key} positives:   {counts[key]} ({rates[key] * 100.0:.4f}%)")

    meta_path = args.meta.expanduser().resolve()
    meta: dict[str, Any] = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    if meta:
        print("coverage diagnostics:")
        for label, keys in (
            ("decisionStartTaipei", ("decisionStartTaipei",)),
            ("decisionEndTaipei", ("decisionEndTaipei",)),
            ("targetParentMinEventTaipei", ("targetParentMinEventTaipei",)),
            ("targetParentMaxEventTaipei", ("targetParentMaxEventTaipei",)),
        ):
            value = _meta_value(meta, *keys)
            if value is not None:
                print(f"  {label}: {value}")
        sources = meta.get("signalSources")
        if isinstance(sources, list):
            for source in sources:
                if not isinstance(source, dict):
                    continue
                path = source.get("path") or source.get("db") or "?"
                last = source.get("lastSampleTaipei") or source.get("lastSampleMs")
                print(f"  signalSource: {path} | last={last}")

    shadow_path = args.shadow_db.expanduser().resolve()
    shadow = _shadow_diagnostics(
        shadow_path,
        start_ms=int(start_ms),
        end_ms=end_ms,
        special_market_ids=special_market_ids,
    )
    print("Target mirror diagnostics:")
    print(f"  path: {shadow.get('path')}")
    print(f"  exists: {shadow.get('exists')}")
    if shadow.get("hasParentsTable") is False:
        print("  parents table: MISSING")
    elif shadow.get("hasParentsTable"):
        print(f"  target events in special window: {shadow.get('eventRowsInWindow', 0)}")
        print(f"  target event markets: {shadow.get('eventMarketsInWindow', 0)}")
        print(f"  market-id intersection: {shadow.get('marketIdIntersectionCount', 0)}")
        print(f"  special market IDs sample: {shadow.get('specialMarketIdsSample')}")
        print(f"  target market IDs sample:  {shadow.get('eventMarketIdsSample')}")
        print(f"  intersecting IDs sample:   {shadow.get('marketIdIntersectionSample')}")

    if counts["5s"] <= 0:
        events = int(shadow.get("eventRowsInWindow") or 0)
        overlap = int(shadow.get("marketIdIntersectionCount") or 0)
        if events <= 0:
            diagnosis = "Target mirror has no Taker parent events in the requested special window (stale/missing mirror coverage)."
        elif overlap <= 0:
            diagnosis = "Target mirror has events, but ZERO market-id overlap with the special public archive (market-id namespace/join mismatch)."
        else:
            diagnosis = "Target mirror has events and overlapping markets, so inspect strict future-second bucketing/timestamp alignment next."
        raise SystemExit(
            "TARGET_TAKER_SPECIAL_LABEL_PREFLIGHT FAILED: special holdout has ZERO "
            "Target Taker positives even at 5s. Do not train or interpret EBM on this window. "
            + diagnosis
        )

    print("TARGET_TAKER_SPECIAL_LABEL_PREFLIGHT OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
