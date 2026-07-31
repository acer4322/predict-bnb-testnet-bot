from __future__ import annotations

import os
import subprocess
import sys
import time


API_RESTART_EXIT_CODE = 75
SUPERVISOR_GIVE_UP_EXIT_CODE = 76


def _positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def _positive_float(name: str, default: float) -> float:
    try:
        return max(0.1, float(os.environ.get(name, str(default))))
    except ValueError:
        return default


def main() -> int:
    max_restarts = _positive_int("PREDICT_API_MAX_RESTARTS", 3)
    restart_window = _positive_float(
        "PREDICT_API_RESTART_WINDOW_SECONDS", 600.0
    )
    restart_delay = _positive_float("PREDICT_API_RESTART_DELAY_SECONDS", 5.0)
    restart_times: list[float] = []

    while True:
        child = subprocess.Popen([sys.executable, "-m", "predict_bot.server"])
        try:
            exit_code = child.wait()
        except KeyboardInterrupt:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
            return 130

        if exit_code != API_RESTART_EXIT_CODE:
            return exit_code

        now = time.monotonic()
        restart_times = [
            started
            for started in restart_times
            if now - started <= restart_window
        ]
        if len(restart_times) >= max_restarts:
            print(
                "API supervisor stopped: automatic restart limit reached "
                f"({max_restarts} restarts in {restart_window:g} seconds).",
                file=sys.stderr,
                flush=True,
            )
            return SUPERVISOR_GIVE_UP_EXIT_CODE
        restart_times.append(now)
        print(
            "API supervisor: watchdog requested a restart; "
            f"starting again in {restart_delay:g} seconds "
            f"({len(restart_times)}/{max_restarts}).",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(restart_delay)


if __name__ == "__main__":
    raise SystemExit(main())
