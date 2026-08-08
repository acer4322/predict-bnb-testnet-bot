from __future__ import annotations

import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Any

from .cross_oracle_strategies import (
    STRATEGY_POLY_LEAD_ENTRY,
    STRATEGY_POLY_LEAD_EXIT,
    probability_direction,
)
from .poly_quote_canary import PolyQuoteCanary, _http_json

LIVE_RULES_URL = "http://127.0.0.1:8766/api/live-rules"


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def configured_live_stake_from_state(
    payload: Any,
    strategy: str,
) -> tuple[float | None, str | None]:
    """Extract the actual configured initial stake for one selected live strategy."""

    if not isinstance(payload, dict):
        return None, None
    rules = payload.get("rules")
    if not isinstance(rules, dict):
        live = payload.get("liveM0W")
        rules = live.get("rules") if isinstance(live, dict) else None
    if not isinstance(rules, dict):
        return None, None

    strategies = rules.get("strategies")
    if not isinstance(strategies, list):
        return None, None
    normalized = str(strategy or "").strip().upper()
    normalized_strategies = [str(value).strip().upper() for value in strategies]
    try:
        index = normalized_strategies.index(normalized)
    except ValueError:
        return None, None

    initial = rules.get("strategyInitialStakesUsdt")
    if isinstance(initial, list) and index < len(initial):
        stake = _positive_float(initial[index])
        if stake is not None:
            return stake, "LIVE_RULE_INITIAL_STAKE"

    stakes = rules.get("strategyStakesUsdt")
    if isinstance(stakes, list) and index < len(stakes):
        stake = _positive_float(stakes[index])
        if stake is not None:
            return stake, "LIVE_RULE_STAKE"

    max_stake = _positive_float(rules.get("maxStakeUsdt"))
    if index == 0 and max_stake is not None:
        return max_stake, "LIVE_RULE_MAX_STAKE"
    return None, None


def _shares_from_wei(value: Any) -> float | None:
    try:
        wei = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not wei.is_finite() or wei <= 0:
        return None
    shares = wei / Decimal(10**18)
    return float(shares) if shares > 0 else None


class LiveSizedPolyQuoteCanary(PolyQuoteCanary):
    """Quote canary sized like the currently configured live strategy.

    A selected live strategy is tested at its configured initial stake instead
    of the paper ledger's default stake.  Unselected strategies remain tested at
    their paper stake.  EXIT quote sizing reuses the ENTRY signed quote output
    when available so the SELL canary represents the simulated BUY quantity.
    """

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            columns = {
                str(row[1])
                for row in self.db.execute(
                    "PRAGMA table_info(poly_quote_canary_attempts)"
                ).fetchall()
            }
            if "simulated_entry_stake_usdt" not in columns:
                self.db.execute(
                    "ALTER TABLE poly_quote_canary_attempts "
                    "ADD COLUMN simulated_entry_stake_usdt REAL"
                )
            if "simulated_amount_source" not in columns:
                self.db.execute(
                    "ALTER TABLE poly_quote_canary_attempts "
                    "ADD COLUMN simulated_amount_source TEXT"
                )
            self.db.commit()

    def _configured_entry_stake(
        self,
        strategy: str,
        paper_stake: float,
    ) -> tuple[float, str]:
        try:
            payload = _http_json(LIVE_RULES_URL, timeout=0.5)
            stake, source = configured_live_stake_from_state(payload, strategy)
        except Exception:
            stake, source = None, None
        if stake is not None and source is not None:
            return float(stake), source
        return float(paper_stake), "PAPER_STAKE_FALLBACK"

    def _entry_quote_shares(self, trade_id: int) -> float | None:
        with self.db_lock:
            row = self.db.execute(
                """SELECT quote_amount_out_wei
                     FROM poly_quote_canary_attempts
                    WHERE trade_id=? AND phase='ENTRY'
                    LIMIT 1""",
                (int(trade_id),),
            ).fetchone()
        return _shares_from_wei(row[0]) if row is not None else None

    def _update_attempt(self, trade_id: int, phase: str, **values: Any) -> None:
        simulated_entry_stake = values.pop("simulated_entry_stake_usdt", None)
        simulated_amount_source = values.pop("simulated_amount_source", None)
        super()._update_attempt(trade_id, phase, **values)
        if simulated_entry_stake is None and simulated_amount_source is None:
            return
        updates: dict[str, Any] = {}
        if simulated_entry_stake is not None:
            updates["simulated_entry_stake_usdt"] = simulated_entry_stake
        if simulated_amount_source is not None:
            updates["simulated_amount_source"] = simulated_amount_source
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self.db_lock:
            self.db.execute(
                f"UPDATE poly_quote_canary_attempts SET {assignments} "
                "WHERE trade_id=? AND phase=?",
                (*updates.values(), int(trade_id), str(phase)),
            )
            self.db.commit()

    def _process_event(self, event: dict[str, Any]) -> None:
        adjusted = dict(event)
        trade_id = int(adjusted["trade_id"])
        phase = str(adjusted["phase"])
        if phase == "ENTRY":
            stake, source = self._configured_entry_stake(
                str(adjusted["strategy"]),
                float(adjusted["stake_usdt"]),
            )
            adjusted["stake_usdt"] = stake
            self._update_attempt(
                trade_id,
                phase,
                simulated_entry_stake_usdt=stake,
                simulated_amount_source=source,
            )
        elif phase == "EXIT":
            quoted_shares = self._entry_quote_shares(trade_id)
            if quoted_shares is not None:
                adjusted["shares"] = quoted_shares
                source = "ENTRY_SIGNED_QUOTE_SHARES"
            else:
                source = "PAPER_SHARES_FALLBACK"
            self._update_attempt(
                trade_id,
                phase,
                simulated_amount_source=source,
            )
        super()._process_event(adjusted)

    def _signal_still_valid(
        self,
        event: dict[str, Any],
        *,
        quote_average: float | None,
        current_binance_up_mid: float | None,
        current_poly_up_mid: float | None,
    ) -> tuple[bool, str, float | None]:
        strategy = str(event.get("strategy") or "")
        phase = str(event.get("phase") or "")
        side = str(event.get("side") or "").upper()
        if (
            phase != "EXIT"
            and strategy in {STRATEGY_POLY_LEAD_ENTRY, STRATEGY_POLY_LEAD_EXIT}
        ):
            if current_poly_up_mid is None:
                return False, "current Polymarket midpoint unavailable at quote response", None
            poly_direction = probability_direction(current_poly_up_mid)
            if poly_direction != side:
                return False, (
                    "Polymarket no longer confidently supports the entry direction "
                    "when the signed quote returned"
                ), None
        return super()._signal_still_valid(
            event,
            quote_average=quote_average,
            current_binance_up_mid=current_binance_up_mid,
            current_poly_up_mid=current_poly_up_mid,
        )

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["sizing"] = {
            "entry": "configured live initial stake when selected; otherwise paper stake",
            "exit": "ENTRY signed quote shares when available; otherwise paper shares",
            "liveRulesUrl": LIVE_RULES_URL,
        }
        return payload
