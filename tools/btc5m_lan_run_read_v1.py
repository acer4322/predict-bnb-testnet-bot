from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import btc5m_lan_dispatch_v1 as d

VERSION = "BTC5M_LAN_RUN_READ_V1_1"
TERMINAL = {"succeeded", "failed", "runner_error", "cancelled"}


def _artifact_payload(local_dir: str, artifact: str, max_bytes: int) -> dict[str, Any]:
    p = Path(local_dir) / artifact
    out: dict[str, Any] = {
        "artifact": artifact,
        "artifactPath": str(p),
        "artifactPresent": p.is_file(),
    }
    if not p.is_file():
        return out
    out["artifactBytes"] = p.stat().st_size
    try:
        raw = p.read_bytes()
        out["artifactTruncated"] = len(raw) > max_bytes
        if p.suffix.lower() == ".json":
            obj = json.loads(raw.decode("utf-8"))
            out["artifactJson"] = obj if len(raw) <= max_bytes else {
                k: obj.get(k) for k in [
                    "ok", "version", "status", "verdict", "kernelPass", "smokeVerified",
                    "aggregate", "gates", "safetyGates", "behaviorGates", "summary",
                    "selectedMarketIds", "marketId", "boundary",
                ] if isinstance(obj, dict) and k in obj
            }
        else:
            out["artifactText"] = raw[:max_bytes].decode("utf-8", errors="replace")
    except Exception as ex:
        out["artifactReadError"] = f"{type(ex).__name__}:{ex}"
    return out


def _tail_safe(host: str, jid: str, stream: str, max_bytes: int = 8192) -> Any:
    try:
        return d.cmd_tail(host, jid, stream, max_bytes)
    except Exception as ex:
        return {"error": f"{type(ex).__name__}:{ex}"}


def _cancel_safe(host: str, jid: str) -> Any:
    try:
        return d.cmd_cancel(host, jid)
    except Exception as ex:
        return {"ok": False, "error": f"{type(ex).__name__}:{ex}"}


def run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    argv = args.argv[1:] if args.argv and args.argv[0] == "--" else args.argv
    if not argv:
        return {"ok": False, "version": VERSION, "error": "missing command after --"}, 2

    try:
        submitted = d.cmd_submit(
            args.host, argv, args.cwd, args.job_id, args.max_threads,
            args.min_free_ram_gb, args.max_start_cpu_pct, not args.allow_during_copy,
            args.resource, args.gpu_index, args.min_free_vram_mib,
            args.max_start_gpu_pct, args.protect_cancel, False, args.local_root,
        )
    except Exception as ex:
        return {"ok": False, "version": VERSION, "phase": "submit", "error": f"{type(ex).__name__}:{ex}"}, 2

    if not submitted.get("accepted"):
        return {"ok": False, "version": VERSION, "phase": "submit", "submitted": submitted}, 2

    jid = str(submitted.get("job_id") or args.job_id or "").strip()
    if not jid:
        return {"ok": False, "version": VERSION, "phase": "submit", "submitted": submitted, "error": "accepted submit did not return job_id"}, 2

    t0 = time.time()
    last_growth = t0
    last_bytes = -1
    last_status: dict[str, Any] = {}
    watchdog_events: list[dict[str, Any]] = []
    deadline = t0 + max(1, int(args.timeout_seconds))

    while time.time() < deadline:
        try:
            st = d.cmd_status(args.host, jid)
        except Exception as ex:
            return {"ok": False, "version": VERSION, "jobId": jid, "submitted": submitted, "phase": "status", "error": f"{type(ex).__name__}:{ex}"}, 4
        last_status = st
        state = st.get("state")
        out_b = int(st.get("stdout_bytes") or 0)
        err_b = int(st.get("stderr_bytes") or 0)
        total_b = out_b + err_b
        if total_b != last_bytes:
            if last_bytes >= 0:
                last_growth = time.time()
            last_bytes = total_b
        if err_b > int(args.max_stderr_bytes) and state not in TERMINAL:
            watchdog_events.append({"type": "stderr_explosion", "stderrBytes": err_b, "limit": int(args.max_stderr_bytes)})
            cancel = _cancel_safe(args.host, jid)
            return {
                "ok": False, "complete": False, "version": VERSION, "jobId": jid,
                "submitted": submitted, "status": st, "phase": "watchdog_stderr",
                "watchdog": watchdog_events, "cancel": cancel,
                "stdout": _tail_safe(args.host, jid, "stdout"),
                "stderr": _tail_safe(args.host, jid, "stderr", 12000),
                "error": "stderr exceeded watchdog limit; job cancelled to prevent false-running error loop",
            }, 5
        if state in TERMINAL:
            break
        if not args.allow_silent and time.time() - last_growth >= float(args.stall_seconds):
            watchdog_events.append({"type": "no_output_growth", "seconds": round(time.time() - last_growth, 3), "stdoutBytes": out_b, "stderrBytes": err_b})
            cancel = _cancel_safe(args.host, jid)
            return {
                "ok": False, "complete": False, "version": VERSION, "jobId": jid,
                "submitted": submitted, "status": st, "phase": "watchdog_stall",
                "watchdog": watchdog_events, "cancel": cancel,
                "stdout": _tail_safe(args.host, jid, "stdout"),
                "stderr": _tail_safe(args.host, jid, "stderr", 12000),
                "error": "no stdout/stderr growth inside stall window; normal research jobs must emit heartbeat/progress",
            }, 6
        time.sleep(max(0.2, float(args.poll_seconds)))

    state = last_status.get("state")
    base: dict[str, Any] = {
        "version": VERSION,
        "jobId": jid,
        "submitted": submitted,
        "status": last_status,
        "watchdog": watchdog_events,
        "elapsedSeconds": round(time.time() - t0, 3),
    }

    if state not in TERMINAL:
        cancel = _cancel_safe(args.host, jid) if args.cancel_on_timeout else None
        base.update({
            "ok": False, "complete": False, "phase": "timeout", "cancel": cancel,
            "stdout": _tail_safe(args.host, jid, "stdout"),
            "stderr": _tail_safe(args.host, jid, "stderr", 12000),
            "error": "overall timeout before terminal state",
        })
        return base, 7

    if state != "succeeded":
        base.update({
            "ok": False, "complete": False, "phase": "terminal",
            "stdout": _tail_safe(args.host, jid, "stdout"),
            "stderr": _tail_safe(args.host, jid, "stderr", 12000),
            "error": f"job terminal state={state!r}",
        })
        return base, 4

    try:
        collected = d.cmd_collect(args.host, jid, args.local_root)
    except Exception as ex:
        base.update({"ok": False, "complete": False, "phase": "collect", "error": f"{type(ex).__name__}:{ex}"})
        return base, 8

    base["collected"] = collected
    art = _artifact_payload(collected["local_path"], args.artifact, args.artifact_max_bytes)
    base.update(art)
    complete = bool(art.get("artifactPresent")) and not art.get("artifactReadError")
    base["complete"] = complete
    base["ok"] = complete
    if not complete:
        base["phase"] = "artifact"
        base["error"] = "worker succeeded and collected, but required artifact was not readable/present"
        return base, 3
    return base, 0


def main() -> int:
    ap = argparse.ArgumentParser(description=VERSION)
    ap.add_argument("--host", default=d.DEFAULT_HOST)
    ap.add_argument("--cwd", default=".lan_worker_v1/staging")
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--max-threads", type=int, default=4)
    ap.add_argument("--min-free-ram-gb", type=float, default=6.0)
    ap.add_argument("--max-start-cpu-pct", type=float, default=95.0)
    ap.add_argument("--allow-during-copy", action="store_true")
    ap.add_argument("--resource", choices=["cpu", "gpu"], default="cpu")
    ap.add_argument("--gpu-index", type=int, default=0)
    ap.add_argument("--min-free-vram-mib", type=int, default=2048)
    ap.add_argument("--max-start-gpu-pct", type=float, default=95.0)
    ap.add_argument("--protect-cancel", action="store_true")
    ap.add_argument("--timeout-seconds", type=int, default=180)
    ap.add_argument("--poll-seconds", type=float, default=0.5)
    ap.add_argument("--stall-seconds", type=float, default=60.0)
    ap.add_argument("--allow-silent", action="store_true")
    ap.add_argument("--max-stderr-bytes", type=int, default=262144)
    ap.add_argument("--cancel-on-timeout", action="store_true", default=True)
    ap.add_argument("--local-root")
    ap.add_argument("--artifact", default="result.json")
    ap.add_argument("--artifact-max-bytes", type=int, default=65536)
    ap.add_argument("argv", nargs=argparse.REMAINDER)
    ns = ap.parse_args()
    payload, rc = run(ns)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
