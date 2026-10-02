from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _stop(child: subprocess.Popen[Any] | None) -> None:
    if child is None or child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=3.0)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Polymarket and Binance Prediction passive latency probes in the same time window"
    )
    parser.add_argument("--minutes", type=float, default=15.0)
    parser.add_argument("--poly-poll-ms", type=int, default=250)
    parser.add_argument("--binance-poll-ms", type=int, default=500)
    parser.add_argument("--collector-poll-ms", type=int, default=50)
    parser.add_argument("--report-seconds", type=float, default=10.0)
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "feed_latency_ab_summary.json"),
    )
    args = parser.parse_args()

    minutes = max(0.05, float(args.minutes))
    output = Path(args.output)
    poly_output = output.with_name("poly_feed_latency_probe_summary.json")
    binance_output = output.with_name("binance_feed_latency_probe_summary.json")
    started_at_ms = time.time_ns() // 1_000_000

    poly_cmd = [
        sys.executable,
        "-m",
        "predict_bot.poly_feed_latency_probe",
        "--minutes",
        str(minutes),
        "--poll-ms",
        str(max(100, int(args.poly_poll_ms))),
        "--report-seconds",
        str(max(2.0, float(args.report_seconds))),
        "--output",
        str(poly_output),
    ]
    binance_cmd = [
        sys.executable,
        "-m",
        "predict_bot.binance_feed_latency_probe",
        "--minutes",
        str(minutes),
        "--poll-ms",
        str(max(200, int(args.binance_poll_ms))),
        "--collector-poll-ms",
        str(max(20, int(args.collector_poll_ms))),
        "--report-seconds",
        str(max(2.0, float(args.report_seconds))),
        "--output",
        str(binance_output),
    ]

    print(
        json.dumps(
            {
                "status": "STARTING",
                "startedAtMs": started_at_ms,
                "minutes": minutes,
                "polyPollMs": max(100, int(args.poly_poll_ms)),
                "binanceDirectPollMs": max(200, int(args.binance_poll_ms)),
                "binanceCollectorPollMs": max(20, int(args.collector_poll_ms)),
                "productionFeedsModified": False,
                "ordersPossible": False,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )

    poly: subprocess.Popen[Any] | None = None
    binance: subprocess.Popen[Any] | None = None
    interrupted = False
    try:
        # Start both children back-to-back so they share effectively the same DNS,
        # routing and market time window.  Their periodic JSON reports are left on
        # the parent console for live inspection.
        poly = subprocess.Popen(poly_cmd)
        binance = subprocess.Popen(binance_cmd)
        poly_code = poly.wait()
        binance_code = binance.wait()
    except KeyboardInterrupt:
        interrupted = True
        poly_code = 130
        binance_code = 130
    finally:
        _stop(poly)
        _stop(binance)

    ended_at_ms = time.time_ns() // 1_000_000
    combined = {
        "probe": "SYNCHRONIZED_POLY_BINANCE_FEED_LATENCY_AB",
        "startedAtMs": started_at_ms,
        "endedAtMs": ended_at_ms,
        "durationMs": max(0, ended_at_ms - started_at_ms),
        "interrupted": interrupted,
        "polyExitCode": poly_code,
        "binanceExitCode": binance_code,
        "parameters": {
            "minutes": minutes,
            "polyRestPollMs": max(100, int(args.poly_poll_ms)),
            "binanceDirectRestPollMs": max(200, int(args.binance_poll_ms)),
            "binanceCollectorPollMs": max(20, int(args.collector_poll_ms)),
            "reportSeconds": max(2.0, float(args.report_seconds)),
        },
        "poly": _read_json(poly_output),
        "binance": _read_json(binance_output),
        "files": {
            "poly": str(poly_output),
            "binance": str(binance_output),
            "combined": str(output),
        },
        "productionFeedsModified": False,
        "ordersPossible": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(combined, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(combined, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return 130 if interrupted else (0 if poly_code == 0 and binance_code == 0 else 1)


if __name__ == "__main__":
    raise SystemExit(main())
