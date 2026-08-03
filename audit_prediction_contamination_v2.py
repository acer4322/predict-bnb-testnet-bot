from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


STRICT_SOURCE_KEYS = {
    "predictionmarketid",
    "predictioneventmarketid",
    "eventmarketid",
    "bookmarketid",
    "subscriptionmarketid",
    "latestmarketid",
    "sourcemarketid",
    "wssmarketid",
}

CONTEXT_KEYS = {
    "mrealtimemarketid",
    "signalmarketid",
    "executionmarketid",
    "settlementmarketid",
    "currentmarketid",
    "gatecurrentmarketid",
}

PREDICTION_DEPENDENT_PREFIXES = (
    "R_MICROPRICE",
    "R_CALIBRATED_VALUE",
    "R_OFI",
    "R_CONSENSUS",
    "R_FUTURES_LEAD",
)

KNOWN_DIRECT_REST_PREFIXES = (
    "PAIR_ARB_",
)


def normalize_key(key: str) -> str:
    return "".join(char.lower() for char in key if char.isalnum())


def walk(value: Any, path: str = "$") -> Iterable[tuple[str, str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            yield child_path, str(key), child
            yield from walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{path}[{index}]")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def extract_ids(payload: dict[str, Any], trade_market_id: int) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for path, key, value in walk(payload):
        normalized = normalize_key(key)
        path_lower = path.lower()
        generic_market_id_in_source_path = (
            normalized == "marketid"
            and any(
                marker in path_lower
                for marker in ("prediction", "subscription", "orderbook", "wss")
            )
            and not any(
                marker in path_lower
                for marker in ("observer", "gate")
            )
        )
        if normalized in STRICT_SOURCE_KEYS or generic_market_id_in_source_path:
            evidence = "STRICT_SOURCE"
        elif normalized in CONTEXT_KEYS:
            evidence = "CONTEXT_ONLY"
        else:
            continue
        try:
            market_id = int(value)
        except (TypeError, ValueError):
            continue
        found.append(
            {
                "path": path,
                "key": key,
                "value": market_id,
                "evidence": evidence,
                "matches_trade": market_id == trade_market_id,
            }
        )
    return found


def string_value(payload: dict[str, Any], wanted: set[str]) -> str | None:
    for _path, key, value in walk(payload):
        if normalize_key(key) in wanted and value is not None:
            return str(value)
    return None


def classify(strategy: str, ids: list[dict[str, Any]]) -> str:
    if strategy.startswith(KNOWN_DIRECT_REST_PREFIXES):
        return "DIRECT_REST_SEPARATE_PATH"
    if not strategy.startswith(PREDICTION_DEPENDENT_PREFIXES):
        return "NOT_PRIMARY_PREDICTION_DEPENDENT"
    strict = [item for item in ids if item["evidence"] == "STRICT_SOURCE"]
    context = [item for item in ids if item["evidence"] == "CONTEXT_ONLY"]
    if any(not item["matches_trade"] for item in strict):
        return "STRICT_SOURCE_MARKET_ID_MISMATCH"
    if strict:
        return "STRICT_SOURCE_MARKET_ID_MATCH"
    if context:
        return "CONTEXT_ID_MATCH_ONLY"
    return "NO_SOURCE_MARKET_ID_EVIDENCE"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit Prediction market-ID contamination in simulation trades."
    )
    parser.add_argument("--db", default="data/simulation.db")
    parser.add_argument("--from-time", default=None)
    parser.add_argument("--to-time", default=None)
    parser.add_argument(
        "--output",
        default="prediction_contamination_audit.csv",
    )
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    from_time = parse_time(args.from_time)
    to_time = parse_time(args.to_time)

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    columns = {
        row["name"]
        for row in db.execute("PRAGMA table_info(trades)").fetchall()
    }
    required = {"id", "strategy", "market_id", "opened_at", "diagnostics_json"}
    missing = required - columns
    if missing:
        raise SystemExit(f"trades table missing columns: {sorted(missing)}")

    select_columns = [
        "id",
        "strategy",
        "market_id",
        "opened_at",
        "status",
        "pnl",
        "diagnostics_json",
    ]
    rows = db.execute(
        f"SELECT {', '.join(select_columns)} FROM trades ORDER BY id ASC"
    ).fetchall()

    records: list[dict[str, Any]] = []
    by_verdict: Counter[str] = Counter()
    by_strategy: dict[str, Counter[str]] = defaultdict(Counter)

    for row in rows:
        opened = parse_time(str(row["opened_at"]))
        if from_time is not None and (opened is None or opened < from_time):
            continue
        if to_time is not None and (opened is None or opened > to_time):
            continue

        raw = row["diagnostics_json"]
        try:
            payload = json.loads(str(raw or "{}"))
        except json.JSONDecodeError:
            payload = {}

        trade_market_id = int(row["market_id"])
        strategy = str(row["strategy"])
        ids = extract_ids(payload, trade_market_id)
        verdict = classify(strategy, ids)

        mismatches = [
            f"{item['evidence']}:{item['path']}={item['value']}"
            for item in ids
            if not item["matches_trade"]
        ]
        matches = [
            f"{item['evidence']}:{item['path']}={item['value']}"
            for item in ids
            if item["matches_trade"]
        ]

        record = {
            "trade_id": int(row["id"]),
            "opened_at": str(row["opened_at"]),
            "strategy": strategy,
            "trade_market_id": trade_market_id,
            "status": str(row["status"]),
            "pnl": row["pnl"],
            "verdict": verdict,
            "mismatched_ids": " | ".join(mismatches),
            "matching_ids": " | ".join(matches),
            "signal_event_type": string_value(
                payload, {"signaleventtype"}
            ),
            "prediction_data_source": string_value(
                payload, {"predictiondatasource"}
            ),
            "prediction_orientation": string_value(
                payload, {"predictionorientation", "orientation"}
            ),
        }
        records.append(record)
        by_verdict[verdict] += 1
        by_strategy[strategy][verdict] += 1

    output_path = Path(args.output).resolve()
    with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(records[0].keys()) if records else [
                "trade_id",
                "opened_at",
                "strategy",
                "trade_market_id",
                "status",
                "pnl",
                "verdict",
                "mismatched_ids",
                "matching_ids",
                "signal_event_type",
                "prediction_data_source",
                "prediction_orientation",
            ],
        )
        writer.writeheader()
        writer.writerows(records)

    mismatched = [
        row for row in records
        if row["verdict"] == "STRICT_SOURCE_MARKET_ID_MISMATCH"
    ]
    unverifiable = [
        row for row in records
        if row["verdict"] in {
            "CONTEXT_ID_MATCH_ONLY",
            "NO_SOURCE_MARKET_ID_EVIDENCE",
        }
    ]

    summary = {
        "database": str(db_path),
        "filterFrom": args.from_time,
        "filterTo": args.to_time,
        "totalRows": len(records),
        "verdictCounts": dict(by_verdict),
        "firstStrictSourceMismatch": mismatched[0] if mismatched else None,
        "lastStrictSourceMismatch": mismatched[-1] if mismatched else None,
        "firstWeakOrUnverifiablePredictionTrade": (
            unverifiable[0] if unverifiable else None
        ),
        "lastWeakOrUnverifiablePredictionTrade": (
            unverifiable[-1] if unverifiable else None
        ),
        "strategyCounts": {
            strategy: dict(counts)
            for strategy, counts in sorted(by_strategy.items())
        },
        "csv": str(output_path),
        "limitations": [
            (
                "STRICT mismatch requires an explicit Prediction event/book/subscription/"
                "source market ID saved inside diagnostics_json."
            ),
            (
                "Rows containing only current/signal/execution market IDs are context-only, "
                "not proof that the Prediction source contract matched."
            ),
            (
                "A stale Prediction contract may have been copied under the new "
                "trade market ID; that cannot be proven from rows that saved only "
                "the destination market ID."
            ),
        ],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
