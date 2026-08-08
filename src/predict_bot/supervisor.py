from __future__ import annotations

import os
import subprocess
import sys
import time

from .pair_arb_live_minimum import install_pair_arb_minimum
from .poly_confidence_restart_guard import invalidate_unfinished_confidence_shadows


install_pair_arb_minimum()

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


def _enabled(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _stop_child(child: subprocess.Popen[bytes] | None) -> None:
    if child is None or child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def _start_cross_oracle() -> subprocess.Popen[bytes] | None:
    if not _enabled("PREDICT_CROSS_ORACLE_ENABLED", True):
        return None
    print(
        "API supervisor: starting resilient Chainlink/Polymarket cross-oracle collector "
        "with restricted public-readonly TLS fallback",
        flush=True,
    )
    return subprocess.Popen(
        [sys.executable, "-m", "predict_bot.cross_oracle_tls_fallback"]
    )


def _start_cross_oracle_strategies() -> subprocess.Popen[bytes] | None:
    if not _enabled("PREDICT_CROSS_ORACLE_ENABLED", True):
        return None
    if not _enabled("PREDICT_CROSS_ORACLE_STRATEGIES_ENABLED", True):
        return None
    try:
        excluded = invalidate_unfinished_confidence_shadows()
        if excluded:
            print(
                "API supervisor: Poly confidence restart guard excluded "
                f"{excluded} unfinished shadow(s)",
                flush=True,
            )
    except Exception as exc:
        # A restart guard failure must not silently validate a discontinuous
        # shadow. The strategy sidecar still starts, but the error is visible.
        print(
            f"API supervisor: Poly confidence restart guard failed: {exc}",
            file=sys.stderr,
            flush=True,
        )
    print(
        "API supervisor: starting gap-aware Polymarket lead/gap Paper strategies "
        "with live-sized signed ENTRY quote canary and simulated EXIT scenarios",
        flush=True,
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "predict_bot.cross_oracle_strategy_entry_quote_exit_sim",
        ]
    )


def main() -> int:
    max_restarts = _positive_int("PREDICT_API_MAX_RESTARTS", 3)
    restart_window = _positive_float(
        "PREDICT_API_RESTART_WINDOW_SECONDS", 600.0
    )
    restart_delay = _positive_float("PREDICT_API_RESTART_DELAY_SECONDS", 5.0)
    cross_oracle_restart_delay = _positive_float(
        "PREDICT_CROSS_ORACLE_RESTART_DELAY_SECONDS", 5.0
    )
    restart_times: list[float] = []
    cross_oracle = _start_cross_oracle()
    cross_oracle_strategies = _start_cross_oracle_strategies()
    next_cross_oracle_restart_at = 0.0
    next_strategy_restart_at = 0.0

    try:
        while True:
            child = subprocess.Popen([sys.executable, "-m", "predict_bot.server"])
            try:
                while True:
                    exit_code = child.poll()
                    if exit_code is not None:
                        break
                    now = time.monotonic()
                    if (
                        _enabled("PREDICT_CROSS_ORACLE_ENABLED", True)
                        and cross_oracle is not None
                        and cross_oracle.poll() is not None
                        and now >= next_cross_oracle_restart_at
                    ):
                        print(
                            "API supervisor: cross-oracle collector exited; restarting it",
                            file=sys.stderr,
                            flush=True,
                        )
                        next_cross_oracle_restart_at = now + cross_oracle_restart_delay
                        cross_oracle = _start_cross_oracle()
                    if (
                        _enabled("PREDICT_CROSS_ORACLE_ENABLED", True)
                        and _enabled("PREDICT_CROSS_ORACLE_STRATEGIES_ENABLED", True)
                        and cross_oracle_strategies is not None
                        and cross_oracle_strategies.poll() is not None
                        and now >= next_strategy_restart_at
                    ):
                        print(
                            "API supervisor: cross-oracle Paper strategy sidecar exited; restarting it",
                            file=sys.stderr,
                            flush=True,
                        )
                        next_strategy_restart_at = now + cross_oracle_restart_delay
                        cross_oracle_strategies = _start_cross_oracle_strategies()
                    time.sleep(0.5)
            except KeyboardInterrupt:
                _stop_child(child)
                return 130

            if exit_code != API_RESTART_EXIT_CODE:
                return int(exit_code)

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
    finally:
        _stop_child(cross_oracle_strategies)
        _stop_child(cross_oracle)


if __name__ == "__main__":
    raise SystemExit(main())
