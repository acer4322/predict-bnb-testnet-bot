from __future__ import annotations

import time
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v2 import RolloverSafePolyGapLiveEngine

GENERAL_LIVE_RULES_URL = "http://127.0.0.1:8766/api/live-rules"
GENERAL_LIVE_CONFLICT_REFRESH_SECONDS = 1.0


class SignalGenerationPolyGapLiveEngine(RolloverSafePolyGapLiveEngine):
    """V3: multiple rounds, but never repeated attempts for one persistent gap."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.entry_signal_latch: tuple[int, str] | None = None
        self.general_live_conflict = False
        self.general_live_conflict_detail: str | None = None
        self.next_general_live_conflict_refresh = 0.0

    def _refresh_general_live_conflict(self) -> bool:
        now = time.monotonic()
        if now < self.next_general_live_conflict_refresh:
            return self.general_live_conflict
        self.next_general_live_conflict_refresh = now + GENERAL_LIVE_CONFLICT_REFRESH_SECONDS
        try:
            response = self.http.get(GENERAL_LIVE_RULES_URL)
            response.raise_for_status()
            payload = response.json()
            rules = payload.get("rules") if isinstance(payload, dict) else None
            strategies = rules.get("strategies") if isinstance(rules, dict) else None
            normalized = {
                str(value).strip().upper()
                for value in (strategies if isinstance(strategies, list) else [])
            }
            self.general_live_conflict = "R_POLY_GAP_SCALP" in normalized
            self.general_live_conflict_detail = (
                "R_POLY_GAP_SCALP is selected in general LiveM0WEngine; remove it there "
                "before enabling the dedicated executor"
                if self.general_live_conflict else None
            )
        except Exception as exc:
            # Fail closed for new entries when ownership cannot be verified.
            self.general_live_conflict = True
            self.general_live_conflict_detail = (
                "could not verify general-live strategy ownership: " + str(exc)[:240]
            )
        return self.general_live_conflict

    def _tick(self) -> None:
        active = self._current_active_round()
        if active is not None:
            return super()._tick()

        settings = self._settings()
        if not base.MASTER_ENABLED:
            self.status = "MASTER_DISABLED"
            return
        if not settings["runtimeEnabled"] or settings["lossTripped"]:
            self.status = "MAX_LOSS_TRIPPED" if settings["lossTripped"] else "PAUSED"
            return
        if self._refresh_general_live_conflict():
            self.status = "BLOCKED_GENERAL_LIVE_CONFLICT"
            self.last_error = self.general_live_conflict_detail
            return
        if not self._ensure_clients():
            return
        market = self._prime_market()
        poly = self._poly_state()
        if market is None or poly is None:
            self.status = "WAITING_DATA"
            return
        market_id = int(market["market_id"])
        if self.halted_market_id == market_id:
            self.status = "MARKET_HALTED"
            return
        poly_end = int(poly.get("windowEndMs") or 0)
        if poly_end <= 0 or abs(poly_end - int(market["end_ms"])) > base.MAX_MARKET_END_SKEW_MS:
            self.status = "MARKET_MISMATCH"
            self.last_error = f"Poly/Binance end-time mismatch: {poly_end} vs {market['end_ms']}"
            return

        direction = poly.get("direction")
        self.last_poly_direction = str(direction) if direction else None
        if direction not in {"UP", "DOWN"}:
            self.entry_signal_latch = None
            self.status = "ARMED_WAITING_GAP"
            return
        selected_mid = base._finite(poly.get("selectedMid"))
        if selected_mid is None:
            return
        ask, ask_size, _book_rtt_ms = self._direct_book(market, str(direction))
        if ask is None:
            self.status = "WAITING_BINANCE_BOOK"
            return
        edge = selected_mid - ask
        key = (market_id, str(direction))
        if edge + 1e-12 < base.SCALP_MIN_EDGE:
            if self.entry_signal_latch == key:
                self.entry_signal_latch = None
            self.status = "ARMED_WAITING_GAP"
            return
        if self.entry_signal_latch == key:
            self.status = "GAP_SIGNAL_LATCHED"
            return

        # One persistent above-threshold gap is one signal generation. A fresh
        # attempt requires the same-direction edge to disarm below threshold,
        # a confident direction change, or a new market.
        self.entry_signal_latch = key
        stake = float(settings["stakeUsdt"])
        if ask_size is not None and ask_size > 0 and ask_size * ask < min(stake, 0.01):
            self.status = "GAP_SIGNAL_LATCHED_NO_VISIBLE_DEPTH"
            return
        token_id = str(market[f"{str(direction).lower()}_token_id"])
        row = self._insert_round(
            market=market,
            side=str(direction),
            token_id=token_id,
            stake=stake,
            poly_selected=selected_mid,
            ask=ask,
            edge=edge,
        )
        self._open_round(row, poly)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V3"
        payload["signalGeneration"] = {
            "latched": self.entry_signal_latch is not None,
            "key": list(self.entry_signal_latch) if self.entry_signal_latch else None,
            "rearm": "edge below threshold, Poly confident direction change, or market rollover",
        }
        payload["generalLiveConflict"] = {
            "blocked": self.general_live_conflict,
            "detail": self.general_live_conflict_detail,
            "singleRealMoneyOwner": True,
        }
        return payload


base.PolyGapLiveEngine = SignalGenerationPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
