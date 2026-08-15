from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from .poly_feed_latency_probe import (
    DEFAULT_POLL_MS,
    DEFAULT_REPORT_SECONDS,
    ROOT,
    PolyFeedLatencyProbe,
)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_label(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value.strip())
    return cleaned[:80] or "route"


def _pick(payload: dict[str, Any], *path: str) -> float | None:
    value: Any = payload
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return _finite(value)


def _delta(current: dict[str, Any], baseline: dict[str, Any], *path: str) -> dict[str, float | None]:
    now = _pick(current, *path)
    before = _pick(baseline, *path)
    delta = now - before if now is not None and before is not None else None
    pct = (delta / before * 100.0) if delta is not None and before not in (None, 0.0) else None
    return {"baselineMs": before, "currentMs": now, "deltaMs": delta, "deltaPct": pct}


def compare_summaries(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    return {
        "baselineLabel": baseline.get("label"),
        "currentLabel": current.get("label"),
        "lowerIsBetter": {
            "wsSourceAge": True,
            "restSourceAge": True,
            "restRtt": True,
        },
        "higherMeansWsObservedSameHashEarlier": {
            "restMinusWs": True,
        },
        "median": {
            "wsSourceAge": _delta(current, baseline, "wsSourceAgeMs", "median"),
            "restSourceAge": _delta(current, baseline, "restSourceAgeMs", "median"),
            "restRtt": _delta(current, baseline, "restRttMs", "median"),
            "restMinusWs": _delta(current, baseline, "restMinusWsMs", "median"),
        },
        "p95": {
            "wsSourceAge": _delta(current, baseline, "wsSourceAgeMs", "p95"),
            "restSourceAge": _delta(current, baseline, "restSourceAgeMs", "p95"),
            "restRtt": _delta(current, baseline, "restRttMs", "p95"),
            "restMinusWs": _delta(current, baseline, "restMinusWsMs", "p95"),
        },
        "notes": [
            "wsSourceAgeMs/restSourceAgeMs include Polymarket source timestamp semantics and are not pure network RTT.",
            "restMinusWsMs matches identical order-book hashes; positive means the WebSocket observed that state first.",
            "Use equal durations and similar market conditions for route comparisons.",
        ],
    }


def _decorate(payload: dict[str, Any], label: str) -> dict[str, Any]:
    payload = dict(payload)
    payload["version"] = "poly_route_latency_probe_v1"
    payload["label"] = label
    payload["safety"] = {
        "passiveOnly": True,
        "ordersPossible": False,
        "tradeEndpointsCalled": False,
        "sources": [
            "public Polymarket market WebSocket",
            "public Polymarket POST /books",
            "local 8767 state for current token IDs only",
        ],
    }
    payload["measurementMeaning"] = {
        "wsSourceAgeMs": "local receive time minus Polymarket event timestamp when the feed exposes a usable timestamp; lower is fresher but not pure network RTT",
        "restSourceAgeMs": "local receive time minus Polymarket REST book timestamp when exposed; lower is fresher but includes polling/source semantics",
        "restRttMs": "local round-trip time for public POST /books",
        "restMinusWsMs": "arrival-time difference for the identical order-book hash; positive means WS saw it first",
    }
    if not payload.get("wsSourceAgeMs", {}).get("samples"):
        payload.setdefault("measurementWarnings", []).append(
            "No usable Polymarket source timestamps were observed on matched WS events, so absolute WS source-to-receive latency is unavailable in this run. Use identical-hash arrival lead and route-level RTT for A/B comparisons."
        )
    return payload


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Route-labeled passive Polymarket WebSocket/REST feed latency benchmark"
    )
    parser.add_argument("--label", default="DIRECT", help="route label, e.g. DIRECT, WARP, TOKYO_VPS")
    parser.add_argument("--minutes", type=float, default=3.0, help="duration; 0 runs until Ctrl-C")
    parser.add_argument("--poll-ms", type=int, default=DEFAULT_POLL_MS, help="REST /books polling interval")
    parser.add_argument("--report-seconds", type=float, default=DEFAULT_REPORT_SECONDS)
    parser.add_argument("--compare-to", help="previous Poly route JSON to compare against")
    parser.add_argument("--output")
    args = parser.parse_args()

    label = _safe_label(args.label)
    output = Path(args.output) if args.output else ROOT / "data" / f"poly_route_latency_{label}.json"
    duration = None if args.minutes <= 0 else max(1.0, float(args.minutes) * 60.0)

    probe = PolyFeedLatencyProbe(args.poll_ms, args.report_seconds, output)
    exit_code = probe.run(duration)

    payload = json.loads(output.read_text(encoding="utf-8"))
    payload = _decorate(payload, label)

    # Persist the completed current run before attempting any optional comparison.
    # A missing/invalid baseline must never discard a valid latency measurement.
    _write_payload(output, payload)

    if args.compare_to:
        baseline_path = Path(args.compare_to)
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            payload["comparison"] = compare_summaries(payload, baseline)
        except FileNotFoundError:
            payload["comparison"] = None
            payload.setdefault("comparisonWarnings", []).append(
                f"Baseline file not found: {baseline_path}. Current run was saved normally; generate the baseline first and rerun with --compare-to."
            )
        except (OSError, json.JSONDecodeError) as exc:
            payload["comparison"] = None
            payload.setdefault("comparisonWarnings", []).append(
                f"Baseline could not be read: {baseline_path}: {type(exc).__name__}: {exc}. Current run was saved normally."
            )
        _write_payload(output, payload)

    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    print(f"saved: {output}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
