from __future__ import annotations

import os
import threading
import time
from typing import Any

import httpx

from . import poly_gap_live as base
from .poly_gap_live_v32 import EXIT_NO_FILL_RETRY_COOLDOWN_MS
from .poly_gap_live_v37_guarded import (
    GuardedShotgunEntryPolyGapLiveEngine,
    SHOTGUN_ZERO_SHARE_EXIT_RECHECK_MS,
)


LEADER_GUARD_OFF = "OFF"
LEADER_GUARD_ENTRY_ONLY = "POLY_ONLY_ENTRY"
LEADER_GUARD_ENTRY_AND_KILL = "POLY_ONLY_ENTRY_AND_KILL"
LEADER_GUARD_MODES = {
    LEADER_GUARD_OFF,
    LEADER_GUARD_ENTRY_ONLY,
    LEADER_GUARD_ENTRY_AND_KILL,
}
POLY_LEADING = "POLY_LEADING"
BINANCE_LEADING = "BINANCE_LEADING_RISK"
MIXED = "MIXED"
EXPLICIT_KILL_REGIMES = {BINANCE_LEADING, MIXED}

LEADER_STATE_URL = os.environ.get(
    "PREDICT_POLY_GAP_LIVE_LEADER_STATE_URL",
    "http://127.0.0.1:8768/state",
)
LEADER_POLL_SECONDS = max(
    0.50,
    float(os.environ.get("PREDICT_POLY_GAP_LIVE_LEADER_POLL_SECONDS", "1.0")),
)
LEADER_REQUEST_TIMEOUT_SECONDS = max(
    0.50,
    float(os.environ.get("PREDICT_POLY_GAP_LIVE_LEADER_REQUEST_TIMEOUT_SECONDS", "2.5")),
)
LEADER_MAX_LOCAL_AGE_MS = max(
    1_000,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_LEADER_MAX_LOCAL_AGE_MS", "5000")),
)
LEADER_GENERIC_EXIT_RETRY_MS = max(
    EXIT_NO_FILL_RETRY_COOLDOWN_MS,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_LEADER_EXIT_RETRY_MS", "250")),
)


def _normalize_mode(value: Any) -> str:
    mode = str(value or LEADER_GUARD_OFF).strip().upper()
    if mode not in LEADER_GUARD_MODES:
        raise ValueError(
            "leaderGuardMode must be OFF, POLY_ONLY_ENTRY, or POLY_ONLY_ENTRY_AND_KILL"
        )
    return mode


def leader_guard_policy(
    *,
    mode: str,
    regime: str | None,
    fresh: bool,
    market_locked: bool,
    active_open: bool,
) -> str:
    """Pure V38 policy used by the engine and regression tests."""
    mode = _normalize_mode(mode)
    regime = str(regime or "")
    if mode == LEADER_GUARD_OFF:
        return "ALLOW"
    if market_locked:
        return "EXIT_LOCKED" if active_open and mode == LEADER_GUARD_ENTRY_AND_KILL else "BLOCK_LOCKED"
    if not fresh:
        return "BLOCK_UNAVAILABLE"
    if regime == POLY_LEADING:
        return "ALLOW"
    if mode == LEADER_GUARD_ENTRY_AND_KILL and regime in EXPLICIT_KILL_REGIMES:
        return "LOCK_AND_EXIT" if active_open else "LOCK_MARKET"
    return "BLOCK_NOT_POLY"


class PolyLeaderGuardLiveEngine(GuardedShotgunEntryPolyGapLiveEngine):
    """V38: gate Live entry by the existing 8768 Poly/Binance leader regime.

    Mode A (POLY_ONLY_ENTRY):
      - new BUY is allowed only while the existing lead-validation regime is
        POLY_LEADING.
      - BINANCE_LEADING_RISK, MIXED, INSUFFICIENT_DATA, stale, or unavailable
        leader state fail closed for new entry only.

    Mode B (POLY_ONLY_ENTRY_AND_KILL):
      - same entry gate as Mode A.
      - an explicit BINANCE_LEADING_RISK or MIXED regime locks the current Binance
        market for the remainder of that market.
      - if a Live round is already OPEN, the existing signed SELL/FOK exit path is
        used immediately and retried while the market remains locked.

    Shotgun GTC cancellation is deliberately NOT changed in V38. A leader lock
    prevents new strategy BUY/Shotgun generations, but existing resting Shotgun
    LIMIT orders are left untouched as requested.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v38_leader_lock = threading.RLock()
        self._v38_leader_thread: threading.Thread | None = None
        self._v38_leader_http = httpx.Client(
            timeout=httpx.Timeout(
                LEADER_REQUEST_TIMEOUT_SECONDS,
                connect=min(0.75, LEADER_REQUEST_TIMEOUT_SECONDS),
            )
        )
        self._v38_leader_regime: str | None = None
        self._v38_leader_received_at_ms = 0
        self._v38_leader_error: str | None = "leader state not received yet"
        self._v38_leader_source_sample_rows: int | None = None
        self._v38_leader_source_markets: int | None = None
        self._v38_entry_blocks = 0
        self._v38_market_locks = 0
        self._v38_forced_exit_attempts = 0
        self._v38_forced_exit_retry_at_ms: dict[int, int] = {}
        super().__init__(*args, **kwargs)

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS poly_gap_live_leader_guard_locks (
                    market_id INTEGER PRIMARY KEY,
                    regime TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    locked_at_ms INTEGER NOT NULL
                );
                """
            )
            self.db.commit()

    def _ensure_defaults(self) -> None:
        super()._ensure_defaults()
        now = base._now_ms()
        with self.db_lock:
            self.db.execute(
                "INSERT OR IGNORE INTO poly_gap_live_settings(key,value,updated_at_ms) VALUES(?,?,?)",
                ("leader_guard_mode", LEADER_GUARD_OFF, now),
            )
            self.db.commit()

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        try:
            mode = _normalize_mode(self._setting("leader_guard_mode", LEADER_GUARD_OFF))
        except ValueError:
            mode = LEADER_GUARD_OFF
        settings["leaderGuardMode"] = mode
        return settings

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        current = self._settings()
        forwarded = {key: value for key, value in values.items() if key != "leaderGuardMode"}
        if forwarded:
            super().update_settings(forwarded)
        if "leaderGuardMode" in values:
            mode = _normalize_mode(values["leaderGuardMode"])
            self._set_setting("leader_guard_mode", mode)
            self._event(
                "WARN" if mode == LEADER_GUARD_ENTRY_AND_KILL else "INFO",
                "LEADER_GUARD_MODE_UPDATED_V38",
                None,
                None,
                (
                    f"leader guard mode={mode}; entry requires {POLY_LEADING} when enabled; "
                    "Shotgun GTC cancellation unchanged"
                ),
            )
        elif current.get("leaderGuardMode") not in LEADER_GUARD_MODES:
            self._set_setting("leader_guard_mode", LEADER_GUARD_OFF)
        return self.snapshot()

    def start(self) -> None:
        if self._v38_leader_thread is None or not self._v38_leader_thread.is_alive():
            self._v38_leader_thread = threading.Thread(
                target=self._leader_poll_loop,
                name="poly-gap-live-leader-guard",
                daemon=True,
            )
            self._v38_leader_thread.start()
        super().start()

    def stop(self) -> None:
        self.stop_event.set()
        if self._v38_leader_thread:
            self._v38_leader_thread.join(timeout=3.0)
        try:
            self._v38_leader_http.close()
        except Exception:
            pass
        super().stop()

    def _leader_poll_loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                response = self._v38_leader_http.get(
                    LEADER_STATE_URL,
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("8768 leader state returned a non-object payload")
                validation = payload.get("polyBinanceLeadValidation")
                if not isinstance(validation, dict):
                    raise RuntimeError("8768 state has no polyBinanceLeadValidation")
                regime = str(validation.get("currentRegime") or "").strip().upper()
                if regime not in {POLY_LEADING, BINANCE_LEADING, MIXED, "INSUFFICIENT_DATA"}:
                    raise RuntimeError(f"8768 returned unknown leader regime: {regime or 'EMPTY'}")
                with self._v38_leader_lock:
                    self._v38_leader_regime = regime
                    self._v38_leader_received_at_ms = base._now_ms()
                    self._v38_leader_error = None
                    try:
                        self._v38_leader_source_sample_rows = int(validation.get("sampleRows") or 0)
                    except (TypeError, ValueError):
                        self._v38_leader_source_sample_rows = None
                    try:
                        self._v38_leader_source_markets = int(validation.get("sampledMarketsTotal") or 0)
                    except (TypeError, ValueError):
                        self._v38_leader_source_markets = None
            except Exception as exc:
                with self._v38_leader_lock:
                    self._v38_leader_error = str(exc)[:400]
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.05, LEADER_POLL_SECONDS - elapsed))

    def _leader_state(self) -> dict[str, Any]:
        with self._v38_leader_lock:
            regime = self._v38_leader_regime
            received_at_ms = int(self._v38_leader_received_at_ms or 0)
            error = self._v38_leader_error
            rows = self._v38_leader_source_sample_rows
            markets = self._v38_leader_source_markets
        age_ms = max(0, base._now_ms() - received_at_ms) if received_at_ms > 0 else None
        fresh = bool(age_ms is not None and age_ms <= LEADER_MAX_LOCAL_AGE_MS)
        return {
            "regime": regime,
            "receivedAtMs": received_at_ms or None,
            "ageMs": age_ms,
            "fresh": fresh,
            "error": error,
            "sampleRows": rows,
            "sampledMarketsTotal": markets,
        }

    def _market_lock(self, market_id: int) -> dict[str, Any] | None:
        with self.db_lock:
            row = self.db.execute(
                "SELECT * FROM poly_gap_live_leader_guard_locks WHERE market_id=?",
                (int(market_id),),
            ).fetchone()
        return dict(row) if row else None

    def _lock_market(self, market_id: int, regime: str, reason: str, round_id: int | None = None) -> dict[str, Any]:
        now_ms = base._now_ms()
        with self.db_lock:
            existing = self.db.execute(
                "SELECT * FROM poly_gap_live_leader_guard_locks WHERE market_id=?",
                (int(market_id),),
            ).fetchone()
            if existing is None:
                self.db.execute(
                    """INSERT INTO poly_gap_live_leader_guard_locks(
                           market_id,regime,reason,locked_at_ms
                       ) VALUES(?,?,?,?)""",
                    (int(market_id), str(regime), str(reason)[:500], now_ms),
                )
                self.db.commit()
                created = True
            else:
                created = False
        if created:
            self._v38_market_locks += 1
            self._event(
                "WARN",
                "LEADER_GUARD_MARKET_LOCKED_V38",
                int(market_id),
                round_id,
                f"regime={regime}; market locked for remainder of this market; {reason}",
            )
        return self._market_lock(market_id) or {
            "market_id": int(market_id),
            "regime": str(regime),
            "reason": str(reason),
            "locked_at_ms": now_ms,
        }

    def _forced_leader_exit(self, active: dict[str, Any], lock_row: dict[str, Any]) -> None:
        round_id = int(active["id"])
        now_ms = base._now_ms()
        retry_at = int(self._v38_forced_exit_retry_at_ms.get(round_id, 0))
        if now_ms < retry_at:
            self.status = "LEADER_GUARD_EXIT_RETRY_COOLDOWN_V38"
            return

        self._v38_forced_exit_attempts += 1
        if int(active.get("exit_signal_at_ms") or 0) <= 0:
            self._event(
                "WARN",
                "LEADER_GUARD_FORCED_EXIT_V38",
                int(active["market_id"]),
                round_id,
                (
                    f"leader regime {lock_row.get('regime')} triggered Mode B; "
                    "using existing signed SELL/FOK path and keeping market locked"
                ),
            )
        self.status = "LEADER_GUARD_EXITING_V38"
        self._exit_round(active, int(lock_row.get("locked_at_ms") or now_ms))

        refreshed = self._round_state(round_id)
        if not isinstance(refreshed, dict) or str(refreshed.get("state") or "") != "OPEN":
            self._v38_forced_exit_retry_at_ms.pop(round_id, None)
            return
        shares = base._finite(refreshed.get("shares"))
        error_kind = str(refreshed.get("error_kind") or "")
        if shares is None or shares <= 1e-9:
            delay_ms = SHOTGUN_ZERO_SHARE_EXIT_RECHECK_MS
        elif error_kind == "EXIT_ORDER_NOT_FILLED":
            delay_ms = EXIT_NO_FILL_RETRY_COOLDOWN_MS
        else:
            delay_ms = LEADER_GENERIC_EXIT_RETRY_MS
        self._v38_forced_exit_retry_at_ms[round_id] = base._now_ms() + int(delay_ms)

    def _tick(self) -> None:
        settings = self._settings()
        mode = _normalize_mode(settings.get("leaderGuardMode"))
        if mode == LEADER_GUARD_OFF:
            return super()._tick()

        leader = self._leader_state()
        regime = str(leader.get("regime") or "")
        fresh = leader.get("fresh") is True
        active = self._current_active_round()

        if isinstance(active, dict):
            market_id = int(active["market_id"])
            lock_row = self._market_lock(market_id)
            if (
                mode == LEADER_GUARD_ENTRY_AND_KILL
                and lock_row is None
                and fresh
                and regime in EXPLICIT_KILL_REGIMES
            ):
                lock_row = self._lock_market(
                    market_id,
                    regime,
                    "explicit non-Poly leader regime observed while Live round existed",
                    int(active["id"]),
                )

            if mode == LEADER_GUARD_ENTRY_AND_KILL and lock_row is not None:
                state = str(active.get("state") or "")
                if state == "OPEN":
                    self._forced_leader_exit(active, lock_row)
                    return
                # ENTRY_SYNC/EXIT_SYNC must reconcile normally. If an in-flight BUY
                # becomes OPEN after reconciliation, the next tick immediately exits.
                return super()._tick()

            # Mode A never forces an existing position out. Mode B also leaves an
            # existing position alone unless an explicit BINANCE/MIXED regime was
            # actually observed and persisted as a market lock.
            return super()._tick()

        market = self._prime_market()
        market_id = int((market or {}).get("market_id") or 0)
        lock_row = self._market_lock(market_id) if market_id > 0 else None
        decision = leader_guard_policy(
            mode=mode,
            regime=regime,
            fresh=fresh,
            market_locked=lock_row is not None,
            active_open=False,
        )

        if decision == "ALLOW":
            return super()._tick()

        if decision == "LOCK_MARKET" and market_id > 0:
            lock_row = self._lock_market(
                market_id,
                regime,
                "explicit non-Poly leader regime observed before entry",
                None,
            )
            self.status = "LEADER_GUARD_MARKET_LOCKED_V38"
            self.last_error = (
                f"leader regime {regime}; Mode B locked market {market_id}; no new Live BUY this market"
            )
            return

        self._v38_entry_blocks += 1
        if decision == "BLOCK_LOCKED":
            self.status = "LEADER_GUARD_MARKET_LOCKED_V38"
            self.last_error = f"market {market_id} is locked by V38 leader guard; no new Live BUY"
        elif decision == "BLOCK_UNAVAILABLE":
            self.status = "LEADER_GUARD_WAITING_FRESH_STATE_V38"
            self.last_error = (
                f"leader state unavailable/stale; entry fails closed; ageMs={leader.get('ageMs')}; "
                f"error={leader.get('error') or 'none'}"
            )
        else:
            self.status = "LEADER_GUARD_BLOCKED_NOT_POLY_V38"
            self.last_error = f"leader regime={regime or 'UNAVAILABLE'}; new entry requires {POLY_LEADING}"
        return

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        settings = payload.get("settings") or self._settings()
        mode = _normalize_mode(settings.get("leaderGuardMode"))
        leader = self._leader_state()
        current_market_id = int((payload.get("market") or {}).get("market_id") or 0)
        current_lock = self._market_lock(current_market_id) if current_market_id > 0 else None
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V38"
        payload["leaderGuardV38"] = {
            "mode": mode,
            "enabled": mode != LEADER_GUARD_OFF,
            "entryRequiresPolyLeading": mode != LEADER_GUARD_OFF,
            "exitAndLockOnBinanceOrMixed": mode == LEADER_GUARD_ENTRY_AND_KILL,
            "explicitKillRegimes": sorted(EXPLICIT_KILL_REGIMES),
            "insufficientOrUnavailableBlocksEntry": True,
            "insufficientOrUnavailableForcesExit": False,
            "leaderSource": "8768 polyBinanceLeadValidation.currentRegime",
            "leaderSourceUsesCompletedMarkets": True,
            "currentMarketExcludedFromLeaderWindow": True,
            "pollIntervalMs": int(LEADER_POLL_SECONDS * 1000),
            "maxLocalAgeMs": LEADER_MAX_LOCAL_AGE_MS,
            "leader": leader,
            "currentMarketLock": current_lock,
            "entryBlocks": int(self._v38_entry_blocks),
            "marketLocks": int(self._v38_market_locks),
            "forcedExitAttempts": int(self._v38_forced_exit_attempts),
            "shotgunAutomaticCancelChangedByV38": False,
            "restingShotgunGtcLeftUntouched": True,
        }
        rules = payload.setdefault("rules", {})
        rules.update(
            leaderGuardV38=True,
            leaderGuardMode=mode,
            leaderGuardEntryRequires="POLY_LEADING" if mode != LEADER_GUARD_OFF else "DISABLED",
            leaderGuardBinanceOrMixedExit=(mode == LEADER_GUARD_ENTRY_AND_KILL),
            leaderGuardBinanceOrMixedLocksCurrentMarket=(mode == LEADER_GUARD_ENTRY_AND_KILL),
            leaderGuardUnavailableEntryPolicy="FAIL_CLOSED" if mode != LEADER_GUARD_OFF else "DISABLED",
            leaderGuardUnavailableExitPolicy="DO_NOT_FORCE_EXIT",
            leaderGuardShotgunCancelPolicy="UNCHANGED_V37_NO_AUTO_CANCEL",
        )
        return payload


base.PolyGapLiveEngine = PolyLeaderGuardLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
