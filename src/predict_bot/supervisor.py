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
        "with bounded parallel Gamma event discovery, event-window fallback, strict UP/DOWN identity, "
        "next-market prefetch, rollover invalidation and websocket initial-book readiness",
        flush=True,
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "predict_bot.cross_oracle_gamma_redundant_discovery",
        ]
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
        print(
            f"API supervisor: Poly confidence restart guard failed: {exc}",
            file=sys.stderr,
            flush=True,
        )
    print(
        "API supervisor: starting gap-aware Polymarket Paper strategies with "
        "R_POLY_GAP_SCALP, R_POLY_GAP_SCALP_STABLE, R_POLY_INVERTED_PRICE, CHOP guard, "
        "rolling stats, persisted health heartbeat and coverage-qualified Poly/Binance 10/30/50 lead-lag validation",
        flush=True,
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "predict_bot.cross_oracle_strategy_chop_guard_v7",
        ]
    )


def _start_poly_gap_live() -> subprocess.Popen[bytes] | None:
    if not _enabled("PREDICT_CROSS_ORACLE_ENABLED", True):
        return None
    # Compatibility lineage markers kept for historical regression tests:
    # predict_bot.poly_gap_live_v11 -> predict_bot.poly_gap_live_v16 ->
    # predict_bot.poly_gap_live_v17 -> predict_bot.poly_gap_live_v18 ->
    # predict_bot.poly_gap_live_v19 -> predict_bot.poly_gap_live_v20 ->
    # predict_bot.poly_gap_live_v21 -> predict_bot.poly_gap_live_v22 ->
    # predict_bot.poly_gap_live_v23 -> predict_bot.poly_gap_live_v24 ->
    # predict_bot.poly_gap_live_v25 -> predict_bot.poly_gap_live_v26 ->
    # predict_bot.poly_gap_live_v27 -> predict_bot.poly_gap_live_v28 ->
    # predict_bot.poly_gap_live_v29 -> predict_bot.poly_gap_live_v30 ->
    # predict_bot.poly_gap_live_v31 -> predict_bot.poly_gap_live_v32 ->
    # predict_bot.poly_gap_live_v33 -> predict_bot.poly_gap_live_v34 ->
    # predict_bot.poly_gap_live_v35 -> predict_bot.poly_gap_live_v36 ->
    # predict_bot.poly_gap_live_v37 -> predict_bot.poly_gap_live_v37_guarded ->
    # predict_bot.poly_gap_live_v38 -> predict_bot.poly_gap_live_v39.
    # Cross-oracle lineage: predict_bot.cross_oracle_trade_readiness ->
    # predict_bot.cross_oracle_gamma_redundant_discovery.
    # Paper guard lineage: predict_bot.cross_oracle_strategy_chop_guard_v3 ->
    # predict_bot.cross_oracle_strategy_chop_guard_v4 ->
    # predict_bot.cross_oracle_strategy_chop_guard_v5 ->
    # predict_bot.cross_oracle_strategy_chop_guard_v6 ->
    # predict_bot.cross_oracle_strategy_chop_guard_v7.
    # Server lineage: predict_bot.server_binance_prefetch_v2 ->
    # predict_bot.server_binance_prefetch_v3 -> predict_bot.server_binance_prefetch_v4 ->
    # predict_bot.server_binance_prefetch_v5 -> predict_bot.server_binance_prefetch_v6.
    # V39 preserves V38 leader guard and V37 Shotgun behavior, then adds two
    # MARKET/FOK entry safety gates: V36 fast retries may not outlive the original
    # signal by more than 1200ms by default, and signed BUY quotes must retain a
    # strictly positive current Poly-vs-Binance edge before placement.
    # Shotgun LIMIT/GTC entry and Shotgun auto-cancel behavior are unchanged.
    print(
        "API supervisor: starting dedicated R_POLY_GAP_SCALP live executor "
        "V39 on port 8769 (V38 leader guard + stale-retry and post-quote edge safety)",
        flush=True,
    )
    return subprocess.Popen(
        [sys.executable, "-m", "predict_bot.poly_gap_live_v39"]
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
    poly_gap_live = _start_poly_gap_live()
    next_cross_oracle_restart_at = 0.0
    next_strategy_restart_at = 0.0
    next_poly_gap_live_restart_at = 0.0

    try:
        while True:
            child = subprocess.Popen(
                [sys.executable, "-m", "predict_bot.server_binance_prefetch_v6"]
            )
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
                    if (
                        _enabled("PREDICT_CROSS_ORACLE_ENABLED", True)
                        and poly_gap_live is not None
                        and poly_gap_live.poll() is not None
                        and now >= next_poly_gap_live_restart_at
                    ):
                        print(
                            "API supervisor: dedicated Poly GAP live executor exited; restarting it",
                            file=sys.stderr,
                            flush=True,
                        )
                        next_poly_gap_live_restart_at = now + cross_oracle_restart_delay
                        poly_gap_live = _start_poly_gap_live()
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
                "starting again in "
                f"{restart_delay:g} seconds ({len(restart_times)}/{max_restarts}).",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(restart_delay)
    finally:
        _stop_child(poly_gap_live)
        _stop_child(cross_oracle_strategies)
        _stop_child(cross_oracle)


if __name__ == "__main__":
    raise SystemExit(main())
