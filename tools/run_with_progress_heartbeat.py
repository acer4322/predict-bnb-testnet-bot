from __future__ import annotations

import subprocess
import sys
import time


def _duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:d}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes:d}m{secs:02d}s"
    return f"{secs:d}s"


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: run_with_progress_heartbeat.py <python-script> [args ...]")

    command = [sys.executable, *sys.argv[1:]]
    started = time.perf_counter()
    print(
        "PROGRESS_HEARTBEAT: child process started; heartbeat every 30s while a fit is busy.",
        flush=True,
    )
    process = subprocess.Popen(command)
    while True:
        try:
            return_code = process.wait(timeout=30.0)
            elapsed = time.perf_counter() - started
            print(
                f"PROGRESS_HEARTBEAT: child finished exit={return_code} elapsed={_duration(elapsed)}",
                flush=True,
            )
            return int(return_code)
        except subprocess.TimeoutExpired:
            elapsed = time.perf_counter() - started
            print(
                f"PROGRESS_HEARTBEAT: still running | elapsed={_duration(elapsed)}",
                flush=True,
            )


if __name__ == "__main__":
    raise SystemExit(main())
