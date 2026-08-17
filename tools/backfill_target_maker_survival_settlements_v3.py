from __future__ import annotations

import argparse
import csv
import json
import os
import time
from pathlib import Path

import httpx

import backfill_target_taker_official_settlements_v1 as base

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_DATASET = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.csv"
DEFAULT_SIM_DB = ROOT / "data" / "simulation.db"
DEFAULT_OUTPUT_DB = ROOT / "data" / "research" / "target_maker_survival_settlements_v3.db"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_survival_settlements_v3_report.json"
VERSION = "TARGET_MAKER_HEAVY_SURVIVAL_SETTLEMENT_BACKFILL_V3"
PREDICT_API_BASE = os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun").rstrip("/")


def _market_ids_from_public_dataset(path: Path) -> list[int]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    market_ids: set[int] = set()
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if "market_id" not in set(reader.fieldnames or []):
            raise RuntimeError("public dataset missing market_id")
        for row in reader:
            try:
                market_ids.add(int(float(row["market_id"])))
            except (TypeError, ValueError):
                continue
    return sorted(market_ids)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill official Predict settlements for every market in the frozen public research cohort."
    )
    parser.add_argument("--public-dataset", type=Path, default=DEFAULT_PUBLIC_DATASET)
    parser.add_argument("--simulation-db", type=Path, default=DEFAULT_SIM_DB)
    parser.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    market_ids = _market_ids_from_public_dataset(args.public_dataset)
    if not market_ids:
        raise SystemExit("no markets in frozen public dataset")

    sim = base._connect_ro(args.simulation_db)
    out = base._create_output(args.output_db)
    headers = {
        "Accept": "application/json",
        "User-Agent": "Target-Maker-Survival-Settlement-Backfill-V3/1.0",
    }
    if os.environ.get("PREDICT_FUN_API_KEY"):
        headers["x-api-key"] = os.environ["PREDICT_FUN_API_KEY"]
    client = httpx.Client(
        timeout=httpx.Timeout(8.0, connect=2.0), trust_env=False, headers=headers
    )

    counts = {
        "requestedMarkets": len(market_ids),
        "existingOutput": 0,
        "simulationDb": 0,
        "predictApi": 0,
        "unresolved": 0,
        "apiErrors": 0,
    }
    unresolved: list[int] = []
    try:
        for index, market_id in enumerate(market_ids, 1):
            existing = out.execute(
                "SELECT status,official_winner FROM market_settlements WHERE market_id=? LIMIT 1",
                (market_id,),
            ).fetchone()
            if (
                existing is not None
                and str(existing["status"] or "").upper() == "OFFICIAL"
                and str(existing["official_winner"] or "").upper() in {"UP", "DOWN"}
            ):
                counts["existingOutput"] += 1
                continue

            copied = base._simulation_lookup(sim, market_id)
            if copied is not None:
                base._upsert(out, copied)
                counts["simulationDb"] += 1
                continue

            resolved = None
            try:
                response = client.get(f"{PREDICT_API_BASE}/v1/markets/{market_id}")
                response.raise_for_status()
                parsed = base._parse_official_detail(response.json())
                if parsed is not None:
                    resolved = {
                        "market_id": market_id,
                        "topic_id": None,
                        **parsed,
                        "source": "PREDICT_MARKET_DETAIL_API",
                    }
            except Exception as exc:
                counts["apiErrors"] += 1
                if len(unresolved) < 20:
                    print(
                        f"API error market {market_id}: {type(exc).__name__}: {exc}",
                        flush=True,
                    )

            if resolved is not None:
                base._upsert(out, resolved)
                counts["predictApi"] += 1
            else:
                counts["unresolved"] += 1
                unresolved.append(market_id)

            if index % 25 == 0 or index == len(market_ids):
                out.commit()
                print(
                    f"settlements {index}/{len(market_ids)} | existing={counts['existingOutput']} "
                    f"sim={counts['simulationDb']} api={counts['predictApi']} unresolved={counts['unresolved']}",
                    flush=True,
                )
        out.commit()
    finally:
        client.close()
        out.close()
        if sim is not None:
            sim.close()

    resolved_count = (
        counts["existingOutput"] + counts["simulationDb"] + counts["predictApi"]
    )
    report = {
        "reportVersion": VERSION,
        "paperResearchOnly": True,
        "sourcePolicy": "simulation.db OFFICIAL first; otherwise Predict market-detail start/end price",
        "winnerRule": "UP iff official endPrice > startPrice, else DOWN",
        "publicDataset": str(args.public_dataset.expanduser().resolve()),
        "counts": counts,
        "resolvedCoverage": resolved_count / len(market_ids),
        "unresolvedMarketIdsFirst50": unresolved[:50],
        "outputDb": str(args.output_db.expanduser().resolve()),
        "generatedAtMs": int(time.time() * 1000),
    }
    resolved_report = args.report.expanduser().resolve()
    resolved_report.parent.mkdir(parents=True, exist_ok=True)
    resolved_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"Resolved official settlements: {resolved_count}/{len(market_ids)} "
        f"({resolved_count / len(market_ids):.1%})",
        flush=True,
    )
    print(f"Report: {resolved_report}", flush=True)
    return 0 if resolved_count else 2


if __name__ == "__main__":
    raise SystemExit(main())
