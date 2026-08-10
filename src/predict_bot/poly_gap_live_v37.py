from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v8 import ENTRY_SLIPPAGE_BPS
from .poly_gap_live_v36 import FastEntryRetryPolyGapLiveEngine

SHOTGUN_DEFAULT_MIN_PRICE = 0.05
SHOTGUN_DEFAULT_MAX_PRICE = 0.40
SHOTGUN_DEFAULT_LEVELS = (0.05, 0.10, 0.20, 0.30, 0.40)
SHOTGUN_MIN_ORDER_USDT = 1.0
SHOTGUN_MAX_ORDER_USDT = 100.0
SHOTGUN_MAX_LEVELS = 5
SHOTGUN_REARM_BLOCK_MS = 300_000


def _parse_shotgun_levels(raw: Any, *, minimum: float, maximum: float) -> tuple[float, ...]:
    if isinstance(raw, str):
        raw_values = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, (list, tuple)):
        raw_values = list(raw)
    else:
        raise ValueError("shotgunLevels must be a comma-separated string or number list")
    levels: list[float] = []
    for raw_value in raw_values:
        try:
            value = round(float(raw_value), 6)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid shotgun level: {raw_value}") from exc
        if not math.isfinite(value) or not 0.01 <= value <= 0.99:
            raise ValueError("every shotgun level must be between 0.01 and 0.99")
        if value < minimum - 1e-12 or value > maximum + 1e-12:
            raise ValueError("every shotgun level must stay inside shotgunMinPrice/shotgunMaxPrice")
        if value not in levels:
            levels.append(value)
    if not levels:
        raise ValueError("shotgunLevels must contain at least one price")
    if len(levels) > SHOTGUN_MAX_LEVELS:
        raise ValueError(f"shotgunLevels supports at most {SHOTGUN_MAX_LEVELS} prices")
    return tuple(sorted(levels, reverse=True))


def _validate_shotgun_order_amount(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("shotgunOrderUsdt must be a number") from exc
    if not math.isfinite(value) or not SHOTGUN_MIN_ORDER_USDT <= value <= SHOTGUN_MAX_ORDER_USDT:
        raise ValueError(
            f"shotgunOrderUsdt must be between {SHOTGUN_MIN_ORDER_USDT:.2f} and "
            f"{SHOTGUN_MAX_ORDER_USDT:.2f} USDT"
        )
    return value


class ShotgunEntryPolyGapLiveEngine(FastEntryRetryPolyGapLiveEngine):
    """V37: optional parallel downward LIMIT/GTC ladder for a valid GAP signal."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v37_generations_armed = 0
        self._v37_levels_submitted = 0
        self._v37_levels_rejected = 0
        self._v37_levels_ambiguous = 0
        self._v37_last_generation: dict[str, Any] | None = None
        super().__init__(*args, **kwargs)

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS poly_gap_live_shotgun_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    round_id INTEGER NOT NULL,
                    market_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    level_price REAL NOT NULL,
                    amount_usdt REAL NOT NULL,
                    state TEXT NOT NULL,
                    quote_started_at_ms INTEGER,
                    quote_completed_at_ms INTEGER,
                    quote_rtt_ms REAL,
                    quote_id TEXT,
                    quote_average REAL,
                    quote_amount_out_wei TEXT,
                    quote_expire_at_ms INTEGER,
                    place_started_at_ms INTEGER,
                    place_completed_at_ms INTEGER,
                    place_rtt_ms REAL,
                    order_id TEXT,
                    order_status TEXT,
                    error_kind TEXT,
                    error_message TEXT,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    UNIQUE(round_id, level_price)
                );
                CREATE INDEX IF NOT EXISTS idx_poly_gap_live_shotgun_market
                    ON poly_gap_live_shotgun_orders(market_id, side, round_id, level_price);
                """
            )
            self.db.commit()

    def _ensure_defaults(self) -> None:
        super()._ensure_defaults()
        defaults = {
            "shotgun_enabled": "0",
            "shotgun_min_price": f"{SHOTGUN_DEFAULT_MIN_PRICE:.8f}",
            "shotgun_max_price": f"{SHOTGUN_DEFAULT_MAX_PRICE:.8f}",
            "shotgun_levels": ",".join(f"{x:.6f}" for x in SHOTGUN_DEFAULT_LEVELS),
            "shotgun_order_usdt": f"{SHOTGUN_MIN_ORDER_USDT:.8f}",
        }
        now = base._now_ms()
        with self.db_lock:
            for key, value in defaults.items():
                self.db.execute(
                    "INSERT OR IGNORE INTO poly_gap_live_settings(key,value,updated_at_ms) VALUES(?,?,?)",
                    (key, value, now),
                )
            self.db.commit()

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        minimum = float(self._setting("shotgun_min_price", str(SHOTGUN_DEFAULT_MIN_PRICE)))
        maximum = float(self._setting("shotgun_max_price", str(SHOTGUN_DEFAULT_MAX_PRICE)))
        try:
            levels = _parse_shotgun_levels(
                self._setting("shotgun_levels", ",".join(str(x) for x in SHOTGUN_DEFAULT_LEVELS)),
                minimum=minimum,
                maximum=maximum,
            )
        except ValueError:
            levels = tuple(sorted(SHOTGUN_DEFAULT_LEVELS, reverse=True))
        amount = float(self._setting("shotgun_order_usdt", str(SHOTGUN_MIN_ORDER_USDT)))
        settings.update(
            shotgunEnabled=self._setting("shotgun_enabled", "0") == "1",
            shotgunMinPrice=minimum,
            shotgunMaxPrice=maximum,
            shotgunLevels=list(levels),
            shotgunOrderUsdt=amount,
            shotgunMaximumExposureUsdt=amount * len(levels),
            shotgunMaximumLevels=SHOTGUN_MAX_LEVELS,
            shotgunMinimumOrderUsdt=SHOTGUN_MIN_ORDER_USDT,
        )
        return settings

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        extra = {"shotgunEnabled", "shotgunMinPrice", "shotgunMaxPrice", "shotgunLevels", "shotgunOrderUsdt"}
        current = self._settings()
        minimum = float(values.get("shotgunMinPrice", current["shotgunMinPrice"]))
        maximum = float(values.get("shotgunMaxPrice", current["shotgunMaxPrice"]))
        if not math.isfinite(minimum) or not 0.01 <= minimum <= 0.99:
            raise ValueError("shotgunMinPrice must be between 0.01 and 0.99")
        if not math.isfinite(maximum) or not 0.01 <= maximum <= 0.99:
            raise ValueError("shotgunMaxPrice must be between 0.01 and 0.99")
        if minimum > maximum + 1e-12:
            raise ValueError("shotgunMinPrice must be <= shotgunMaxPrice")
        max_entry = float(values.get("maxEntryPrice", current.get("maxEntryPrice", 0.99)))
        if maximum > max_entry + 1e-12:
            raise ValueError("shotgunMaxPrice must be <= maxEntryPrice")
        levels = _parse_shotgun_levels(
            values.get("shotgunLevels", current["shotgunLevels"]), minimum=minimum, maximum=maximum
        )
        amount = _validate_shotgun_order_amount(values.get("shotgunOrderUsdt", current["shotgunOrderUsdt"]))
        forwarded = {key: value for key, value in values.items() if key not in extra}
        if forwarded:
            super().update_settings(forwarded)
        if "shotgunEnabled" in values:
            self._set_setting("shotgun_enabled", "1" if bool(values["shotgunEnabled"]) else "0")
        if "shotgunMinPrice" in values:
            self._set_setting("shotgun_min_price", f"{minimum:.8f}")
        if "shotgunMaxPrice" in values:
            self._set_setting("shotgun_max_price", f"{maximum:.8f}")
        if "shotgunLevels" in values:
            self._set_setting("shotgun_levels", ",".join(f"{x:.6f}" for x in reversed(levels)))
        if "shotgunOrderUsdt" in values:
            self._set_setting("shotgun_order_usdt", f"{amount:.8f}")
        if set(values) & extra:
            enabled = bool(values.get("shotgunEnabled", current["shotgunEnabled"]))
            self._event(
                "WARN" if enabled else "INFO",
                "SHOTGUN_ENTRY_SETTINGS_UPDATED_V37",
                None,
                None,
                f"shotgun={'ON' if enabled else 'OFF'}; levels={','.join(f'{x:.3f}' for x in levels)}; "
                f"perLevel={amount:.2f} USDT; maxExposure={amount * len(levels):.2f} USDT",
            )
        return self.snapshot()

    @staticmethod
    def _failure_cooldown_ms(row: dict[str, Any]) -> int:
        reason = str(row.get("error_kind") or row.get("close_reason") or "")
        if reason == "SHOTGUN_ALREADY_ARMED_V37":
            return SHOTGUN_REARM_BLOCK_MS
        return FastEntryRetryPolyGapLiveEngine._failure_cooldown_ms(row)

    def _direct_book(self, market: dict[str, Any], side: str) -> tuple[float | None, float | None, float]:
        ask, ask_size, rtt_ms = super()._direct_book(market, side)
        settings = self._settings()
        if not settings.get("shotgunEnabled"):
            return ask, ask_size, rtt_ms
        snapshot = getattr(self, "_last_entry_depth_preflight", None)
        candidate = ask
        if candidate is None and isinstance(snapshot, dict):
            candidate = base._finite(snapshot.get("bestAsk"))
            if ask_size is None:
                ask_size = base._finite(snapshot.get("bestAskSize"))
        if candidate is None:
            return None, ask_size, rtt_ms
        if candidate > float(settings["maxEntryPrice"]) + 1e-12:
            return None, ask_size, rtt_ms
        if candidate > float(settings["shotgunMaxPrice"]) + 1e-12:
            return None, ask_size, rtt_ms
        self._entry_price_blocked_this_tick = False
        self._entry_depth_blocked_this_tick = False
        return candidate, ask_size, rtt_ms

    def _generation_exists(self, market_id: int, side: str) -> bool:
        with self.db_lock:
            row = self.db.execute(
                "SELECT 1 FROM poly_gap_live_shotgun_orders WHERE market_id=? AND side=? LIMIT 1",
                (int(market_id), str(side)),
            ).fetchone()
        return row is not None

    def _is_shotgun_round(self, round_id: int) -> bool:
        with self.db_lock:
            row = self.db.execute(
                "SELECT 1 FROM poly_gap_live_shotgun_orders WHERE round_id=? LIMIT 1", (int(round_id),)
            ).fetchone()
        return row is not None

    def _submitted_levels(self, round_id: int) -> list[tuple[float, float]]:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT level_price,amount_usdt FROM poly_gap_live_shotgun_orders
                    WHERE round_id=? AND state='SUBMITTED' ORDER BY level_price DESC""",
                (int(round_id),),
            ).fetchall()
        return [(float(row[0]), float(row[1])) for row in rows if float(row[0] or 0) > 0]

    def _estimated_cost(self, round_id: int, shares: float) -> float:
        levels = self._submitted_levels(round_id)
        remaining = max(0.0, float(shares))
        cost = 0.0
        for level, amount in levels:
            take = min(remaining, amount / level)
            cost += take * level
            remaining -= take
            if remaining <= 1e-9:
                break
        if remaining > 1e-6:
            return sum(amount for _level, amount in levels)
        return cost

    def _position_shares(self, token_id: str) -> float | None:
        shares = super()._position_shares(token_id)
        active = self._current_active_round()
        if not isinstance(active, dict) or str(active.get("token_id") or "") != str(token_id):
            return shares
        if not self._is_shotgun_round(int(active["id"])) or shares is None:
            return shares
        actual = max(0.0, float(shares))
        cost = self._estimated_cost(int(active["id"]), actual) if actual > 0 else 0.0
        self._update_round(
            int(active["id"]),
            shares=actual,
            entry_cost_usdt=cost,
            entry_quote_average=(cost / actual if actual > 1e-12 else None),
        )
        if actual <= 1e-9 and isinstance(getattr(self, "_v35_exit_context", None), dict):
            return None
        return actual

    def _insert_levels(self, round_id: int, market_id: int, side: str, levels: tuple[float, ...], amount: float) -> None:
        now = base._now_ms()
        with self.db_lock:
            for level in levels:
                self.db.execute(
                    """INSERT OR IGNORE INTO poly_gap_live_shotgun_orders(
                           round_id,market_id,side,level_price,amount_usdt,state,created_at_ms,updated_at_ms
                       ) VALUES(?,?,?,?,?,'PENDING',?,?)""",
                    (round_id, market_id, side, level, amount, now, now),
                )
            self.db.commit()

    def _save_level(self, round_id: int, level: float, result: dict[str, Any]) -> None:
        columns = (
            "state", "quote_started_at_ms", "quote_completed_at_ms", "quote_rtt_ms", "quote_id",
            "quote_average", "quote_amount_out_wei", "quote_expire_at_ms", "place_started_at_ms",
            "place_completed_at_ms", "place_rtt_ms", "order_id", "order_status", "error_kind", "error_message",
        )
        values = [result.get(column) for column in columns]
        assignments = ",".join(f"{column}=?" for column in columns) + ",updated_at_ms=?"
        with self.db_lock:
            self.db.execute(
                f"UPDATE poly_gap_live_shotgun_orders SET {assignments} WHERE round_id=? AND level_price=?",
                (*values, base._now_ms(), int(round_id), float(level)),
            )
            self.db.commit()

    def _level_pipeline(
        self, *, client: Any, wallet_address: str, wallet_id: str, token_id: str,
        level: float, amount: float, fee_rate_bps: int,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {"state": "REJECTED"}
        q0_ms, q0 = base._now_ms(), time.monotonic()
        result["quote_started_at_ms"] = q0_ms
        try:
            quote = client.get_quote(
                wallet_address=wallet_address, token_id=token_id,
                amount_in_wei=base._to_wei(amount), price_limit=f"{level:.6f}",
                slippage_bps=ENTRY_SLIPPAGE_BPS, fee_rate_bps=fee_rate_bps,
                funding_source="MPC", side="BUY", order_type="LIMIT",
            )
        except Exception as exc:
            result.update(quote_completed_at_ms=base._now_ms(), quote_rtt_ms=(time.monotonic()-q0)*1000,
                          error_kind="SHOTGUN_QUOTE_REJECTED_V37", error_message=str(exc)[:500])
            return result
        quote_id = str(quote.get("quoteId") or "")
        result.update(
            quote_completed_at_ms=base._now_ms(), quote_rtt_ms=(time.monotonic()-q0)*1000,
            quote_id=quote_id or None, quote_average=base._finite(quote.get("averagePrice")),
            quote_amount_out_wei=str(quote.get("amountOut") or "") or None,
            quote_expire_at_ms=int(quote.get("expireAt") or 0) or None,
        )
        if not quote_id or not self._quote_expiry_safe(quote, client):
            result.update(error_kind="SHOTGUN_QUOTE_INVALID_V37", error_message="LIMIT quote id/expiry invalid")
            return result
        p0_ms, p0 = base._now_ms(), time.monotonic()
        result["place_started_at_ms"] = p0_ms
        try:
            placed = client.place_limit_order(
                wallet_address=wallet_address, wallet_id=wallet_id, quote_id=quote_id,
                price_limit=f"{level:.6f}", slippage_bps=ENTRY_SLIPPAGE_BPS,
                account_type=base.ACCOUNT_TYPE, funding_source="MPC",
            )
        except base.ApiTransportError as exc:
            result.update(state="AMBIGUOUS", place_completed_at_ms=base._now_ms(), place_rtt_ms=(time.monotonic()-p0)*1000,
                          error_kind="SHOTGUN_PLACE_AMBIGUOUS_V37", error_message=str(exc)[:500])
            return result
        except Exception as exc:
            result.update(place_completed_at_ms=base._now_ms(), place_rtt_ms=(time.monotonic()-p0)*1000,
                          error_kind="SHOTGUN_PLACE_REJECTED_V37", error_message=str(exc)[:500])
            return result
        order_id = base._first_text(placed, ("orderId", "order_id", "id"))
        if not order_id:
            result.update(state="AMBIGUOUS", place_completed_at_ms=base._now_ms(), place_rtt_ms=(time.monotonic()-p0)*1000,
                          error_kind="SHOTGUN_PLACE_RESPONSE_MISSING_ORDER_ID_V37",
                          error_message="LIMIT place response returned without orderId")
            return result
        result.update(
            state="SUBMITTED", place_completed_at_ms=base._now_ms(), place_rtt_ms=(time.monotonic()-p0)*1000,
            order_id=order_id, order_status=base._first_text(placed, ("status", "orderStatus", "order_status")),
            error_kind=None, error_message=None,
        )
        return result

    def _open_round(self, row: dict[str, Any], poly: dict[str, Any]) -> None:
        settings = self._settings()
        if not settings.get("shotgunEnabled"):
            return super()._open_round(row, poly)
        if not self._entry_allowed():
            self._update_round(int(row["id"]), state="REJECTED", close_reason="ENTRY_DISABLED")
            return
        market_id, round_id, side = int(row["market_id"]), int(row["id"]), str(row["side"])
        if self._generation_exists(market_id, side):
            self._update_round(round_id, state="REJECTED", close_reason="SHOTGUN_ALREADY_ARMED_V37",
                               error_kind="SHOTGUN_ALREADY_ARMED_V37",
                               error_message="same market+direction already has a Shotgun generation")
            return
        trigger_ask = base._finite(row.get("entry_binance_ask"))
        if trigger_ask is None or trigger_ask > float(settings["shotgunMaxPrice"]) + 1e-12:
            self._update_round(round_id, state="REJECTED", close_reason="SHOTGUN_WAITING_TRIGGER_PRICE_V37",
                               error_kind="SHOTGUN_WAITING_TRIGGER_PRICE_V37")
            return
        current_poly = self._poly_state()
        if (current_poly or {}).get("direction") != side:
            self._update_round(round_id, state="REJECTED", close_reason="POLY_SIGNAL_GONE",
                               error_kind="POLY_SIGNAL_GONE",
                               error_message="Polymarket direction changed before Shotgun placement")
            return
        with self.lock:
            client, wallet_address, wallet_id = self.client, self.wallet_address, self.wallet_id
        if client is None or not wallet_address or not wallet_id:
            self._update_round(round_id, state="FAILED", error_kind="PREFLIGHT", error_message="live client unavailable")
            return
        levels = tuple(float(x) for x in settings["shotgunLevels"])
        amount = _validate_shotgun_order_amount(settings["shotgunOrderUsdt"])
        self._insert_levels(round_id, market_id, side, levels, amount)
        self._v37_generations_armed += 1
        started_ms = base._now_ms()
        self._event(
            "WARN", "SHOTGUN_ENTRY_ARMED_V37", market_id, round_id,
            f"side={side}; triggerAsk={trigger_ask:.6f}; levels={','.join(f'{x:.3f}' for x in levels)}; "
            f"perLevel={amount:.2f} USDT; maxExposure={amount*len(levels):.2f} USDT",
        )
        fee_rate_bps = int((self.market_cache or {}).get("fee_rate_bps") or 200)
        token_id = str(row["token_id"])
        results: dict[float, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=len(levels), thread_name_prefix="poly-shotgun") as pool:
            futures = {
                pool.submit(
                    self._level_pipeline, client=client, wallet_address=wallet_address, wallet_id=wallet_id,
                    token_id=token_id, level=level, amount=amount, fee_rate_bps=fee_rate_bps,
                ): level for level in levels
            }
            for future in as_completed(futures):
                level = futures[future]
                try:
                    results[level] = future.result()
                except Exception as exc:
                    results[level] = {"state": "REJECTED", "error_kind": "SHOTGUN_WORKER_FAILED_V37", "error_message": str(exc)[:500]}
        for level in levels:
            self._save_level(round_id, level, results.get(level) or {"state": "REJECTED", "error_kind": "SHOTGUN_WORKER_MISSING_RESULT_V37"})
        submitted = [level for level, result in results.items() if result.get("state") == "SUBMITTED"]
        ambiguous = [level for level, result in results.items() if result.get("state") == "AMBIGUOUS"]
        rejected = [level for level, result in results.items() if result.get("state") not in {"SUBMITTED", "AMBIGUOUS"}]
        self._v37_levels_submitted += len(submitted)
        self._v37_levels_ambiguous += len(ambiguous)
        self._v37_levels_rejected += len(rejected)
        self._v37_last_generation = {
            "marketId": market_id, "roundId": round_id, "side": side, "triggerAsk": trigger_ask,
            "startedAtMs": started_ms, "completedAtMs": base._now_ms(), "levels": list(levels),
            "submittedLevels": sorted(submitted, reverse=True), "ambiguousLevels": sorted(ambiguous, reverse=True),
            "rejectedLevels": sorted(rejected, reverse=True), "perLevelUsdt": amount,
            "maximumExposureUsdt": amount * len(levels), "submittedExposureUsdt": amount * len(submitted),
        }
        if ambiguous:
            self._halt_market(market_id, "Shotgun LIMIT placement ambiguity; manual reconciliation required", round_id)
            self._update_round(round_id, state="AMBIGUOUS", error_kind="SHOTGUN_PLACE_AMBIGUOUS_V37",
                               error_message=f"ambiguous levels: {','.join(f'{x:.3f}' for x in ambiguous)}")
            return
        if not submitted:
            self._update_round(round_id, state="FAILED", close_reason="SHOTGUN_NO_ORDERS_SUBMITTED_V37",
                               error_kind="SHOTGUN_NO_ORDERS_SUBMITTED_V37",
                               error_message="all Shotgun LIMIT levels were definitively rejected")
            return
        committed = amount * len(submitted)
        planned_shares = sum(amount / level for level in submitted)
        planned_average = committed / planned_shares if planned_shares > 0 else None
        first_order = next((str(results[level].get("order_id")) for level in sorted(submitted, reverse=True)
                            if results[level].get("order_id")), None)
        placed_ms = max(int(results[level].get("place_completed_at_ms") or 0) for level in submitted) or base._now_ms()
        with self.db_lock:
            self.db.execute("UPDATE poly_gap_live_rounds SET stake_usdt=?,updated_at_ms=? WHERE id=?",
                            (committed, base._now_ms(), round_id))
            self.db.commit()
        self._update_round(
            round_id, state="OPEN", entry_order_id=first_order, entry_placed_at_ms=placed_ms,
            entry_sync_started_at_ms=None, entry_cost_usdt=0.0, entry_quote_average=planned_average,
            shares=0.0, error_kind=None, error_message=None,
        )
        self.last_entry_latency = {
            "signalToQuoteStartMs": max(0, started_ms-int(row.get("entry_signal_at_ms") or started_ms)),
            "signalToQuoteResponseMs": max(0, base._now_ms()-int(row.get("entry_signal_at_ms") or started_ms)),
            "quoteRttMs": max((float(results[level].get("quote_rtt_ms") or 0) for level in submitted), default=0.0),
            "edgeAfterQuote": None, "shotgun": True,
        }
        self._event("WARN", "SHOTGUN_ENTRY_SUBMITTED_V37", market_id, round_id,
                    f"submitted {len(submitted)}/{len(levels)} LIMIT/GTC levels in parallel; "
                    f"submittedExposure={committed:.2f} USDT; rejected={len(rejected)}")
        try:
            actual_shares = self._position_shares(token_id)
        except Exception as exc:
            self.last_error = f"Shotgun immediate position sync: {str(exc)[:300]}"
            actual_shares = None
        if actual_shares is not None and actual_shares > 0:
            self._event("INFO", "SHOTGUN_ENTRY_CONFIRMED_V37", market_id, round_id,
                        f"wallet position confirmed {actual_shares:.8f} shares after ladder placement")
        elif actual_shares is not None:
            self.status = "SHOTGUN_RESTING_WAITING_FILL_V37"

    def _settle_hold(self, row: dict[str, Any]) -> None:
        if self._is_shotgun_round(int(row["id"])):
            try:
                shares = self._position_shares(str(row["token_id"]))
            except Exception as exc:
                self.last_error = f"Shotgun settlement position read: {str(exc)[:300]}"
                shares = None
            if shares is not None and shares > 0:
                refreshed = self._round_state(int(row["id"]))
                if refreshed is not None:
                    row = refreshed
            elif shares is not None and shares <= 1e-9:
                winner = self._official_winner(int(row["market_id"]))
                if winner is None:
                    return
                self._update_round(
                    int(row["id"]), state="SETTLED", official_winner=winner, entry_cost_usdt=0.0,
                    shares=0.0, exit_proceeds_usdt=0.0, pnl_usdt=0.0,
                    close_reason="SHOTGUN_NO_CONFIRMED_FILL_AT_SETTLEMENT_V37", error_kind=None, error_message=None,
                )
                self._event("INFO", "SHOTGUN_NO_FILL_SETTLED_V37", int(row["market_id"]), int(row["id"]),
                            "Shotgun reached settlement without confirmed wallet shares; PnL recorded as 0")
                return
        return super()._settle_hold(row)

    def _recent_orders(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT round_id,market_id,side,level_price,amount_usdt,state,quote_rtt_ms,quote_average,
                          place_rtt_ms,order_id,order_status,error_kind,error_message,updated_at_ms
                     FROM poly_gap_live_shotgun_orders ORDER BY id DESC LIMIT ?""",
                (max(1, min(100, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        settings = payload.get("settings") or self._settings()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V37"
        payload["shotgunEntryV37"] = {
            "enabled": bool(settings.get("shotgunEnabled")),
            "executionMode": "PARALLEL_LIMIT_GTC_QUOTE_THEN_PLACE_PER_LEVEL",
            "replacesNormalMarketEntryWhenEnabled": True,
            "minimumOrderUsdt": SHOTGUN_MIN_ORDER_USDT,
            "maximumLevels": SHOTGUN_MAX_LEVELS,
            "levels": list(settings.get("shotgunLevels") or []),
            "perLevelUsdt": settings.get("shotgunOrderUsdt"),
            "maximumExposureUsdt": settings.get("shotgunMaximumExposureUsdt"),
            "triggerMaxPrice": settings.get("shotgunMaxPrice"),
            "minimumPrice": settings.get("shotgunMinPrice"),
            "oneGenerationPerMarketDirection": True,
            "batchPlaceApiAvailable": False,
            "automaticBatchCancelEnabled": False,
            "restingLimitOrdersAreGtc": True,
            "existingReversalAndTakeProfitExitUsedForFilledShares": True,
            "zeroFilledSharesDoNotFalseCloseOnExitSignal": True,
            "filledCostEstimatedFromConfirmedSharesAndDescendingLevels": True,
            "generationsArmed": self._v37_generations_armed,
            "levelsSubmitted": self._v37_levels_submitted,
            "levelsRejected": self._v37_levels_rejected,
            "levelsAmbiguous": self._v37_levels_ambiguous,
            "lastGeneration": dict(self._v37_last_generation or {}),
            "recentOrders": self._recent_orders(),
        }
        rules = payload.setdefault("rules", {})
        rules.update(
            shotgunEntryV37=True,
            shotgunEntryOptional=True,
            shotgunMinOrderUsdt=SHOTGUN_MIN_ORDER_USDT,
            shotgunMaxLevels=SHOTGUN_MAX_LEVELS,
            shotgunParallelPerLevelPipeline=True,
            shotgunLimitTimeInForce="GTC",
            shotgunAutomaticBatchCancel=False,
            shotgunRestingOrdersCanOutliveAZeroShareReversal=True,
        )
        if settings.get("shotgunEnabled"):
            rules["entryExecution"] = "parallel LIMIT/GTC shotgun ladder (V37)"
        return payload


base.PolyGapLiveEngine = ShotgunEntryPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
