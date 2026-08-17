from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail fast when a Target Taker special-regime holdout has no positive labels."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--meta", type=Path, default=DEFAULT_META)
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

    markets = int(special["market_id"].nunique())
    print("TARGET_TAKER_SPECIAL_LABEL_PREFLIGHT")
    print(f"special rows:    {len(special)}")
    print(f"special markets: {markets}")
    for horizon in (1, 2, 5):
        key = f"{horizon}s"
        print(
            f"{key} positives:   {counts[key]} "
            f"({rates[key] * 100.0:.4f}%)"
        )

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

    if counts["5s"] <= 0:
        raise SystemExit(
            "TARGET_TAKER_SPECIAL_LABEL_PREFLIGHT FAILED: special holdout has ZERO "
            "Target Taker positives even at 5s. Do not train or interpret EBM on this window. "
            "Public market-state coverage may be valid, but Target event labels are missing/stale "
            "or not joining to these markets. Check targetParentMaxEventTaipei and the Target mirror DB."
        )

    print("TARGET_TAKER_SPECIAL_LABEL_PREFLIGHT OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
