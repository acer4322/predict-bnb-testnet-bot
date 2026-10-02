from __future__ import annotations

import json
import math
import time
from typing import Any

from . import predict_wallet_target_taker_public_side_strategy_v1 as public_side


VERSION = "TARGET_TAKER_PUBLIC_SIDE_V1_EBM_FORWARD_AUTO_BANKROLL"
SOURCE_COHORT = public_side.SIDE_ONLY_COHORT
INITIAL_EQUITY_USDT = 100.0
MIN_RISK_FRACTION = 0.0025
START_RISK_FRACTION = 0.01
MAX_RISK_FRACTION = 0.04
SURPRISE_CONFIDENCE_THRESHOLD = 0.65


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _recent_win_rate(rows: list[dict[str, Any]], count: int) -> float | None:
    recent = rows[-max(1, int(count)) :]
    if not recent:
        return None
    wins = sum(1 for row in recent if str(row.get("status") or "").upper() == "WIN")
    return wins / len(recent)


def is_surprise_loss(row: dict[str, Any]) -> bool:
    """Operational high-confidence loss label, not a calibrated tail probability.

    The Side EBM was trained with class weights and explicitly makes no
    calibration claim.  `selectedProbability` is therefore treated only as a
    confidence/ranking score.  The 0.65 threshold means "high-confidence loss"
    for bankroll diagnostics, not "35% or lower true loss probability".
    """

    confidence = _finite(row.get("signal_confidence"))
    return (
        str(row.get("status") or "").upper() == "LOSS"
        and confidence is not None
        and confidence + 1e-12 >= SURPRISE_CONFIDENCE_THRESHOLD
    )


def performance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        (dict(row) for row in rows),
        key=lambda row: (int(row.get("settled_at_ms") or 0), int(row.get("market_id") or 0)),
    )
    equity = INITIAL_EQUITY_USDT
    high_water = INITIAL_EQUITY_USDT
    max_drawdown_fraction = 0.0
    stake = pnl = 0.0
    wins = losses = 0
    for row in ordered:
        row_pnl = float(_finite(row.get("net_pnl_usdt")) or 0.0)
        row_stake = float(_finite(row.get("stake_usdt")) or 0.0)
        stake += row_stake
        pnl += row_pnl
        status = str(row.get("status") or "").upper()
        wins += int(status == "WIN")
        losses += int(status == "LOSS")
        equity += row_pnl
        high_water = max(high_water, equity)
        if high_water > 0:
            max_drawdown_fraction = max(
                max_drawdown_fraction,
                max(0.0, (high_water - equity) / high_water),
            )

    current_drawdown_fraction = (
        max(0.0, (high_water - equity) / high_water) if high_water > 0 else 1.0
    )
    recent5 = ordered[-5:]
    recent5_surprise_losses = sum(1 for row in recent5 if is_surprise_loss(row))
    consecutive_surprise_losses = 0
    for row in reversed(ordered):
        if is_surprise_loss(row):
            consecutive_surprise_losses += 1
        else:
            break

    return {
        "settledTrades": len(ordered),
        "wins": wins,
        "losses": losses,
        "winRate": wins / len(ordered) if ordered else None,
        "stakeUsdt": stake,
        "netPnlUsdt": pnl,
        "netRoi": pnl / stake if stake > 0 else None,
        "equityUsdt": equity,
        "highWaterUsdt": high_water,
        "maxDrawdownFraction": max_drawdown_fraction,
        "currentDrawdownFraction": current_drawdown_fraction,
        "recent20WinRate": _recent_win_rate(ordered, 20),
        "recent30WinRate": _recent_win_rate(ordered, 30),
        "recent50WinRate": _recent_win_rate(ordered, 50),
        "recent5SurpriseLosses": recent5_surprise_losses,
        "consecutiveSurpriseLosses": consecutive_surprise_losses,
    }


def maturity(metrics: dict[str, Any]) -> tuple[str, float]:
    trades = int(metrics.get("settledTrades") or 0)
    pnl = float(metrics.get("netPnlUsdt") or 0.0)
    roi = _finite(metrics.get("netRoi"))
    max_dd = float(metrics.get("maxDrawdownFraction") or 0.0)
    recent20 = _finite(metrics.get("recent20WinRate"))
    recent30 = _finite(metrics.get("recent30WinRate"))
    recent50 = _finite(metrics.get("recent50WinRate"))

    if (
        trades >= 150
        and roi is not None
        and roi >= 0.25
        and max_dd < 0.12
        and recent50 is not None
        and recent50 >= 0.60
    ):
        return "MATURE", 0.04
    if (
        trades >= 80
        and roi is not None
        and roi >= 0.15
        and max_dd < 0.10
        and recent30 is not None
        and recent30 >= 0.60
    ):
        return "STRONG", 0.03
    if (
        trades >= 40
        and roi is not None
        and roi >= 0.08
        and recent30 is not None
        and recent30 >= 0.62
    ):
        return "PROVEN_2", 0.02
    if trades >= 20 and pnl > 0 and recent20 is not None and recent20 >= 0.60:
        return "PROVEN_1", 0.015
    return "START", START_RISK_FRACTION


def signal_multiplier(confidence: float | None, settled_trades: int) -> float:
    # The first twenty settled trades deliberately match the original 1%/$1
    # control exposure.  Only after there is bankroll evidence does EBM
    # confidence modulate the allowed ceiling.
    if settled_trades < 20:
        return 1.0
    value = float(confidence or 0.0)
    if value >= 0.78:
        return 1.0
    if value >= 0.70:
        return 0.90
    if value >= 0.65:
        return 0.80
    return 0.67


def short_term_multiplier(metrics: dict[str, Any]) -> float:
    recent = int(metrics.get("recent5SurpriseLosses") or 0)
    consecutive = int(metrics.get("consecutiveSurpriseLosses") or 0)
    multiplier = 1.0
    if recent >= 1:
        multiplier = min(multiplier, 0.75)
    if recent >= 2:
        multiplier = min(multiplier, 0.40)
    if consecutive >= 2:
        multiplier = min(multiplier, 0.30)
    if consecutive >= 3:
        multiplier = min(multiplier, 0.15)
    if consecutive >= 4:
        multiplier = min(multiplier, 0.10)
    return multiplier


def drawdown_multiplier(current_drawdown_fraction: float) -> float:
    drawdown = max(0.0, float(current_drawdown_fraction))
    if drawdown < 0.03:
        return 1.0
    if drawdown < 0.06:
        return 0.80
    if drawdown < 0.10:
        return 0.60
    if drawdown < 0.15:
        return 0.35
    return 0.20


def size_trade(
    settled_rows: list[dict[str, Any]],
    *,
    signal_confidence: float | None,
) -> dict[str, Any]:
    metrics = performance(settled_rows)
    state, ceiling = maturity(metrics)
    sig_mult = signal_multiplier(signal_confidence, int(metrics["settledTrades"]))
    short_mult = short_term_multiplier(metrics)
    dd_mult = drawdown_multiplier(float(metrics["currentDrawdownFraction"]))
    raw_risk = ceiling * sig_mult * short_mult * dd_mult
    final_risk = max(MIN_RISK_FRACTION, min(ceiling, raw_risk))
    equity = max(0.0, float(metrics["equityUsdt"]))
    stake = min(equity, equity * final_risk) if equity > 0 else 0.0
    stake = round(stake, 6)
    reason = (
        f"{state}: ceiling={ceiling:.4f} x signal={sig_mult:.2f} x "
        f"surprise={short_mult:.2f} x drawdown={dd_mult:.2f} -> risk={final_risk:.4f}"
    )
    return {
        "walletVersion": VERSION,
        "equityBeforeUsdt": equity,
        "maturityState": state,
        "riskCeilingFraction": ceiling,
        "signalMultiplier": sig_mult,
        "shortTermMultiplier": short_mult,
        "drawdownMultiplier": dd_mult,
        "finalRiskFraction": final_risk,
        "stakeUsdt": stake,
        "signalConfidence": signal_confidence,
        "sizingReason": reason,
        "metricsBefore": metrics,
    }


def policy() -> dict[str, Any]:
    return {
        "version": VERSION,
        "paperOnly": True,
        "sourceStrategy": public_side.VERSION,
        "sourceCohort": SOURCE_COHORT,
        "sameTradeSignalsAsControl": True,
        "targetEventsDriveSizing": False,
        "initialEquityUsdt": INITIAL_EQUITY_USDT,
        "minimumRiskFraction": MIN_RISK_FRACTION,
        "startRiskFraction": START_RISK_FRACTION,
        "maximumRiskFraction": MAX_RISK_FRACTION,
        "surpriseConfidenceThreshold": SURPRISE_CONFIDENCE_THRESHOLD,
        "surpriseDefinition": (
            "LOSS with Side EBM selectedProbability >= 0.65; operational high-confidence loss only, "
            "not a calibrated tail probability"
        ),
        "maturityCeilings": {
            "START": 0.01,
            "PROVEN_1": 0.015,
            "PROVEN_2": 0.02,
            "STRONG": 0.03,
            "MATURE": 0.04,
        },
        "fastDeRiskSlowReRisk": True,
        "unsettledProfitCountsTowardSizing": False,
    }


class AutoBankrollMixin:
    """Paper-only bankroll overlay for the exact SIDE_ONLY EBM trade stream."""

    auto_bankroll_schema_ready: bool

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.auto_bankroll_schema_ready = False
        self.auto_bankroll_last_sizing: dict[str, Any] | None = None
        super().__init__(*args, **kwargs)
        now_ms = int(time.time() * 1000)
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_target_taker_auto_bankroll_v1_meta (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    version TEXT NOT NULL,
                    deployed_at_ms INTEGER NOT NULL,
                    initial_equity_usdt REAL NOT NULL,
                    source_cohort TEXT NOT NULL,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_target_taker_auto_bankroll_v1_trades (
                    market_id INTEGER PRIMARY KEY,
                    source_event_id TEXT NOT NULL UNIQUE,
                    decision_at_ms INTEGER NOT NULL,
                    settled_at_ms INTEGER,
                    side TEXT NOT NULL,
                    observed_ask REAL NOT NULL,
                    effective_unit_cost REAL NOT NULL,
                    signal_confidence REAL,
                    equity_before_usdt REAL NOT NULL,
                    maturity_state TEXT NOT NULL,
                    risk_ceiling_fraction REAL NOT NULL,
                    signal_multiplier REAL NOT NULL,
                    short_term_multiplier REAL NOT NULL,
                    drawdown_multiplier REAL NOT NULL,
                    final_risk_fraction REAL NOT NULL,
                    stake_usdt REAL NOT NULL,
                    shares REAL NOT NULL,
                    status TEXT NOT NULL,
                    winner TEXT,
                    payout_usdt REAL,
                    net_pnl_usdt REAL,
                    equity_after_usdt REAL,
                    surprise_loss INTEGER,
                    sizing_reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_target_taker_auto_bankroll_v1_settled
                    ON wallet_target_taker_auto_bankroll_v1_trades(settled_at_ms,market_id);
                """
            )
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_target_taker_auto_bankroll_v1_meta(
                       id,version,deployed_at_ms,initial_equity_usdt,source_cohort,policy_json
                   ) VALUES (1,?,?,?,?,?)""",
                (
                    VERSION,
                    now_ms,
                    INITIAL_EQUITY_USDT,
                    SOURCE_COHORT,
                    json.dumps(policy(), separators=(",", ":"), default=str),
                ),
            )
            self.db.execute(
                "UPDATE wallet_target_taker_auto_bankroll_v1_meta SET version=?,policy_json=? WHERE id=1",
                (VERSION, json.dumps(policy(), separators=(",", ":"), default=str)),
            )
            self.db.commit()
        self.auto_bankroll_schema_ready = True
        self._reconcile_auto_bankroll_settlements()
        state = getattr(self, "public_side_states", {}).get(SOURCE_COHORT)
        current_event = state.get("event") if isinstance(state, dict) else None
        if isinstance(current_event, dict):
            self._mirror_auto_bankroll_event(current_event)

    def _auto_bankroll_deployed_at_ms(self) -> int:
        with self.db_lock:
            row = self.db.execute(
                "SELECT deployed_at_ms FROM wallet_target_taker_auto_bankroll_v1_meta WHERE id=1"
            ).fetchone()
        return int(row[0]) if row else 0

    def _auto_bankroll_settled_rows(self) -> list[dict[str, Any]]:
        with self.db_lock:
            return [
                dict(row)
                for row in self.db.execute(
                    """SELECT market_id,settled_at_ms,status,stake_usdt,net_pnl_usdt,signal_confidence
                         FROM wallet_target_taker_auto_bankroll_v1_trades
                        WHERE settled_at_ms IS NOT NULL
                        ORDER BY settled_at_ms,market_id"""
                )
            ]

    def _mirror_auto_bankroll_event(self, event: dict[str, Any]) -> None:
        if not self.auto_bankroll_schema_ready:
            return
        market_id = int(event.get("marketId") or getattr(self, "market_id", 0) or 0)
        decision_at_ms = int(event.get("decisionAtMs") or 0)
        if market_id <= 0 or decision_at_ms < self._auto_bankroll_deployed_at_ms():
            return
        with self.db_lock:
            exists = self.db.execute(
                "SELECT 1 FROM wallet_target_taker_auto_bankroll_v1_trades WHERE market_id=?",
                (market_id,),
            ).fetchone()
        if exists is not None:
            return

        side = str(event.get("side") or "").upper()
        ask = _finite(event.get("ask") or event.get("observedAsk"))
        unit_cost = _finite(event.get("effectiveUnitCost"))
        confidence = _finite(event.get("sideSelectedProbability"))
        if side not in {"UP", "DOWN"} or ask is None or unit_cost is None or unit_cost <= 0:
            return
        sizing = size_trade(
            self._auto_bankroll_settled_rows(),
            signal_confidence=confidence,
        )
        stake = float(sizing["stakeUsdt"])
        shares = stake / unit_cost if stake > 0 else 0.0
        status = "OPEN" if stake > 0 else "BANKRUPT_NO_STAKE"
        payload = {
            **sizing,
            "marketId": market_id,
            "sourceEventId": event.get("id"),
            "sourceCohort": SOURCE_COHORT,
            "side": side,
            "observedAsk": ask,
            "effectiveUnitCost": unit_cost,
            "shares": shares,
            "status": status,
            "paperOnly": True,
            "targetEventsUsed": False,
            "sameSignalAsFixedControl": True,
        }
        with self.db_lock:
            cursor = self.db.execute(
                """INSERT OR IGNORE INTO wallet_target_taker_auto_bankroll_v1_trades(
                       market_id,source_event_id,decision_at_ms,side,observed_ask,effective_unit_cost,
                       signal_confidence,equity_before_usdt,maturity_state,risk_ceiling_fraction,
                       signal_multiplier,short_term_multiplier,drawdown_multiplier,final_risk_fraction,
                       stake_usdt,shares,status,sizing_reason,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    market_id,
                    str(event.get("id") or f"{SOURCE_COHORT}:{market_id}"),
                    decision_at_ms,
                    side,
                    ask,
                    unit_cost,
                    confidence,
                    sizing["equityBeforeUsdt"],
                    sizing["maturityState"],
                    sizing["riskCeilingFraction"],
                    sizing["signalMultiplier"],
                    sizing["shortTermMultiplier"],
                    sizing["drawdownMultiplier"],
                    sizing["finalRiskFraction"],
                    stake,
                    shares,
                    status,
                    sizing["sizingReason"],
                    json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()
        if cursor.rowcount > 0:
            self.auto_bankroll_last_sizing = payload

    def _execute_public_side(
        self,
        cohort: str,
        decision: dict[str, Any],
        hazard: dict[str, Any] | None,
        *,
        snapshot_ns: int,
        now_ms: int,
    ) -> None:
        state = getattr(self, "public_side_states", {}).get(cohort)
        before = state.get("event") if isinstance(state, dict) else None
        super()._execute_public_side(
            cohort, decision, hazard, snapshot_ns=snapshot_ns, now_ms=now_ms
        )
        if cohort != SOURCE_COHORT or not isinstance(state, dict) or before is not None:
            return
        event = state.get("event")
        if isinstance(event, dict):
            self._mirror_auto_bankroll_event(event)

    def _settle_auto_bankroll_trade(
        self,
        market_id: int,
        winner: str,
        resolved_at_ms: int,
    ) -> None:
        with self.db_lock:
            row = self.db.execute(
                "SELECT * FROM wallet_target_taker_auto_bankroll_v1_trades WHERE market_id=?",
                (int(market_id),),
            ).fetchone()
        if row is None or row["settled_at_ms"] is not None:
            return
        side = str(row["side"])
        stake = float(row["stake_usdt"])
        shares = float(row["shares"])
        payout = shares if side == winner else 0.0
        pnl = payout - stake
        if stake <= 0:
            status = "BANKRUPT_NO_STAKE"
        else:
            status = "WIN" if pnl > 1e-9 else "LOSS" if pnl < -1e-9 else "FLAT"
        confidence = _finite(row["signal_confidence"])
        surprise = int(
            status == "LOSS"
            and confidence is not None
            and confidence + 1e-12 >= SURPRISE_CONFIDENCE_THRESHOLD
        )
        equity_after = float(row["equity_before_usdt"]) + pnl
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        payload.update(
            {
                "settledAtMs": int(resolved_at_ms),
                "winner": winner,
                "status": status,
                "payoutUsdt": payout,
                "netPnlUsdt": pnl,
                "equityAfterUsdt": equity_after,
                "surpriseLoss": bool(surprise),
            }
        )
        with self.db_lock:
            self.db.execute(
                """UPDATE wallet_target_taker_auto_bankroll_v1_trades
                      SET settled_at_ms=?,status=?,winner=?,payout_usdt=?,net_pnl_usdt=?,
                          equity_after_usdt=?,surprise_loss=?,payload_json=?
                    WHERE market_id=? AND settled_at_ms IS NULL""",
                (
                    int(resolved_at_ms),
                    status,
                    winner,
                    payout,
                    pnl,
                    equity_after,
                    surprise,
                    json.dumps(payload, separators=(",", ":"), default=str),
                    int(market_id),
                ),
            )
            self.db.commit()

    def _reconcile_auto_bankroll_settlements(self) -> None:
        if not self.auto_bankroll_schema_ready:
            return
        with self.db_lock:
            rows = [
                dict(row)
                for row in self.db.execute(
                    """SELECT a.market_id,r.winner,r.resolved_at_ms
                         FROM wallet_target_taker_auto_bankroll_v1_trades a
                         JOIN wallet_target_taker_public_side_v1_results r
                           ON r.cohort=? AND r.market_id=a.market_id
                        WHERE a.settled_at_ms IS NULL""",
                    (SOURCE_COHORT,),
                )
            ]
        for row in rows:
            self._settle_auto_bankroll_trade(
                int(row["market_id"]),
                str(row["winner"]),
                int(row["resolved_at_ms"]),
            )

    def _store_market_result(self, market_id: int, market: dict[str, Any], winner: str) -> None:
        super()._store_market_result(market_id, market, winner)
        if self.auto_bankroll_schema_ready:
            self._settle_auto_bankroll_trade(
                int(market_id), str(winner), int(time.time() * 1000)
            )

    def _auto_bankroll_snapshot(self) -> dict[str, Any]:
        settled = self._auto_bankroll_settled_rows()
        metrics = performance(settled)
        with self.db_lock:
            recent = [
                dict(row)
                for row in self.db.execute(
                    """SELECT market_id,decision_at_ms,settled_at_ms,side,observed_ask,signal_confidence,
                              maturity_state,risk_ceiling_fraction,signal_multiplier,short_term_multiplier,
                              drawdown_multiplier,final_risk_fraction,stake_usdt,status,winner,net_pnl_usdt,
                              equity_before_usdt,equity_after_usdt,surprise_loss,sizing_reason
                         FROM wallet_target_taker_auto_bankroll_v1_trades
                        ORDER BY decision_at_ms DESC LIMIT 20"""
                )
            ]
            meta = self.db.execute(
                "SELECT deployed_at_ms FROM wallet_target_taker_auto_bankroll_v1_meta WHERE id=1"
            ).fetchone()
        state, ceiling = maturity(metrics)
        return {
            "version": VERSION,
            "paperOnly": True,
            "sourceStrategy": public_side.VERSION,
            "sourceCohort": SOURCE_COHORT,
            "sameTradeSignalsAsFixedControl": True,
            "targetEventsDriveSizing": False,
            "deploymentBoundaryMs": int(meta[0]) if meta else None,
            "initialEquityUsdt": INITIAL_EQUITY_USDT,
            "equityUsdt": metrics["equityUsdt"],
            "highWaterUsdt": metrics["highWaterUsdt"],
            "currentMaturityState": state,
            "currentRiskCeilingFraction": ceiling,
            "performance": metrics,
            "lastSizing": self.auto_bankroll_last_sizing,
            "recentTrades": recent,
            "policy": policy(),
            "researchQuestion": (
                "Does autonomous stake sizing improve the exact same frozen EBM trade stream, and do "
                "the resulting size changes resemble the target Taker's variable entry amounts?"
            ),
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        if self.auto_bankroll_schema_ready:
            payload["targetTakerAutoBankrollV1"] = self._auto_bankroll_snapshot()
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        if self.auto_bankroll_schema_ready:
            metrics = performance(self._auto_bankroll_settled_rows())
            state, ceiling = maturity(metrics)
            payload["targetTakerAutoBankrollV1"] = {
                "version": VERSION,
                "paperOnly": True,
                "equityUsdt": metrics["equityUsdt"],
                "settledTrades": metrics["settledTrades"],
                "maturityState": state,
                "riskCeilingFraction": ceiling,
                "lastSizing": self.auto_bankroll_last_sizing,
            }
        return payload
