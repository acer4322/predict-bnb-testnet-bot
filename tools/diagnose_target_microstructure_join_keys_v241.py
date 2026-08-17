from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BURSTS = ROOT / "data" / "research" / "target_controller_hazard_v21_directional_bursts.csv"
STRESS = "STRESS_2026_08_16"
WINDOW_START = "2026-08-16T05:40:05.009737+08:00"
WINDOW_END = "2026-08-16T06:00:02.210840+08:00"
EXPECTED = {6991509, 6991528, 6991536, 6991538}


def iso_ms(value: str) -> int:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone required")
    return int(dt.timestamp() * 1000)


def fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().isoformat(timespec="milliseconds")


def load() -> list[dict]:
    rows = []
    with BURSTS.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if str(r.get("regime") or "") != STRESS:
                continue
            try:
                r["market_id"] = int(float(r.get("market_id") or 0))
                r["first_event_ms"] = int(float(r.get("first_event_ms") or 0))
                r["last_event_ms"] = int(float(r.get("last_event_ms") or 0))
            except (TypeError, ValueError):
                continue
            rows.append(r)
    return sorted(rows, key=lambda r: r["first_event_ms"])


def summarize_window(rows: list[dict], start: int, end: int, label: str) -> None:
    hit = [r for r in rows if start <= r["first_event_ms"] <= end]
    ids = Counter(r["market_id"] for r in hit)
    print(f"\n{label}")
    print("time-overlap bursts:", len(hit))
    print("market ids:", ids.most_common(20))
    if hit:
        print("first:", fmt(hit[0]["first_event_ms"]), "market", hit[0]["market_id"])
        print("last :", fmt(hit[-1]["first_event_ms"]), "market", hit[-1]["market_id"])
        print("expected-id overlap:", sum(1 for r in hit if r["market_id"] in EXPECTED))


def main() -> None:
    rows = load()
    start, end = iso_ms(WINDOW_START), iso_ms(WINDOW_END)
    print("TARGET_MICROSTRUCTURE_JOIN_KEYS_V241")
    print("bursts:", len(rows))
    if rows:
        print("global first:", fmt(rows[0]["first_event_ms"]), "market", rows[0]["market_id"])
        print("global last :", fmt(rows[-1]["first_event_ms"]), "market", rows[-1]["market_id"])

    summarize_window(rows, start, end, "REQUESTED WINDOW")
    summarize_window(rows, start - 8 * 3600_000, end - 8 * 3600_000, "REQUESTED WINDOW - 8H")
    summarize_window(rows, start + 8 * 3600_000, end + 8 * 3600_000, "REQUESTED WINDOW + 8H")

    print("\nEXPECTED MARKET IDS ANY TIME")
    for market_id in sorted(EXPECTED):
        hit = [r for r in rows if r["market_id"] == market_id]
        if not hit:
            print(market_id, "NONE")
            continue
        print(market_id, "bursts=", len(hit), "first=", fmt(hit[0]["first_event_ms"]), "last=", fmt(hit[-1]["first_event_ms"]))

    print("\nNEAREST BURSTS TO WINDOW START (by timestamp, ignoring market id)")
    nearest = sorted(rows, key=lambda r: abs(r["first_event_ms"] - start))[:12]
    for r in nearest:
        delta = r["first_event_ms"] - start
        print(fmt(r["first_event_ms"]), "delta_ms=", delta, "market=", r["market_id"], "purpose=", r.get("purpose"), "side=", r.get("side"))


if __name__ == "__main__":
    main()
