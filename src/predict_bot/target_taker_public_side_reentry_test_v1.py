from __future__ import annotations

import json
import os
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import predict_wallet_target_taker_reentry_strategy_v1 as reentry
from . import target_taker_public_side_test_v1 as v1
from . import target_taker_public_side_test_v2 as v2


VERSION = "TARGET_TAKER_PUBLIC_SIDE_V1_EBM_FORWARD_WITH_SAME_SIDE_REENTRY_V1"
PORT = int(os.environ.get("PREDICT_TARGET_TAKER_PUBLIC_SIDE_TEST_PORT", "8782"))
v1.PORT = PORT
STRATEGY = "TARGET_TAKER_PUBLIC_SIDE_V1_EBM_FORWARD+TARGET_TAKER_SAME_SIDE_REENTRY_V1"


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    return v1._number(value)


class TargetTakerPublicSideReentryTest(v2.TargetTakerPublicSideTest):
    """8782 control + paper-only SAME_SIDE re-entry shadow ledger."""

    def __init__(self, db_path: Path = v1.DB_PATH) -> None:
        super().__init__(db_path=db_path)
        self.reentry_model: dict[str, Any] | None = None
        self.reentry_model_error: str | None = None
        self.last_reentry_decision: dict[str, Any] | None = None
        self.last_reentry_decision_key: tuple[str, str, str, int] | None = None
        try:
            self.reentry_model = reentry.load_reentry_model()
        except Exception as exc:
            self.reentry_model_error = str(exc)[:1000]
        self._create_reentry_schema()
        self.reentry_deployed_at_ms, self.reentry_excluded_market_id = self._load_reentry_meta()

    def _create_reentry_schema(self) -> None:
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS strategy_reentry_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id INTEGER NOT NULL,
                    decision_at_ms INTEGER NOT NULL,
                    snapshot_timestamp_ns INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    side TEXT,
                    reentry_probability REAL,
                    raw_reentry_probability REAL,
                    observed_ask REAL,
                    ms_since_prev_entry REAL,
                    entry_count_so_far INTEGER NOT NULL,
                    available_features INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_strategy_reentry_decisions_market_time
                    ON strategy_reentry_decisions(market_id,decision_at_ms);
                CREATE TABLE IF NOT EXISTS strategy_reentry_entries (
                    market_id INTEGER NOT NULL,
                    entry_ordinal INTEGER NOT NULL,
                    decision_at_ms INTEGER NOT NULL,
                    snapshot_timestamp_ns INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    observed_ask REAL NOT NULL,
                    effective_unit_cost REAL NOT NULL,
                    stake_usdt REAL NOT NULL,
                    shares REAL NOT NULL,
                    base_selected_probability REAL,
                    reentry_probability REAL,
                    raw_reentry_probability REAL,
                    entry_source TEXT NOT NULL,
                    ms_since_prev_entry REAL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(market_id,entry_ordinal)
                );
                CREATE INDEX IF NOT EXISTS idx_strategy_reentry_entries_time
                    ON strategy_reentry_entries(decision_at_ms,market_id,entry_ordinal);
                CREATE TABLE IF NOT EXISTS strategy_reentry_entry_results (
                    market_id INTEGER NOT NULL,
                    entry_ordinal INTEGER NOT NULL,
                    title TEXT,
                    winner TEXT NOT NULL,
                    resolved_at_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    side TEXT NOT NULL,
                    observed_ask REAL NOT NULL,
                    stake_usdt REAL NOT NULL,
                    shares REAL NOT NULL,
                    payout_usdt REAL NOT NULL,
                    net_pnl_usdt REAL NOT NULL,
                    net_roi REAL,
                    entry_source TEXT NOT NULL,
                    reentry_probability REAL,
                    PRIMARY KEY(market_id,entry_ordinal)
                );
                """
            )
            self.db.commit()

    def _load_reentry_meta(self) -> tuple[int, int | None]:
        with self.db_lock:
            deployed_row = self.db.execute(
                "SELECT value FROM strategy_test_meta WHERE key='reentry_deployed_at_ms'"
            ).fetchone()
            if deployed_row is None:
                deployed = int(time.time() * 1000)
                self.db.execute(
                    "INSERT INTO strategy_test_meta(key,value) VALUES('reentry_deployed_at_ms',?)",
                    (str(deployed),),
                )
            else:
                deployed = int(deployed_row[0])
            excluded_row = self.db.execute(
                "SELECT value FROM strategy_test_meta WHERE key='reentry_excluded_market_id'"
            ).fetchone()
            excluded = v1._positive_int(excluded_row[0]) if excluded_row else None
            self.db.execute(
                "INSERT INTO strategy_test_meta(key,value) VALUES('reentry_strategy',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (STRATEGY,),
            )
            self.db.execute(
                "INSERT INTO strategy_test_meta(key,value) VALUES('reentry_policy_json',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(reentry.policy(), separators=(",", ":"), default=str),),
            )
            self.db.commit()
        return deployed, excluded

    def _set_reentry_excluded_market(self, market_id: int) -> None:
        if self.reentry_excluded_market_id is not None:
            return
        self.reentry_excluded_market_id = int(market_id)
        with self.db_lock:
            self.db.execute(
                "INSERT INTO strategy_test_meta(key,value) VALUES('reentry_excluded_market_id',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(int(market_id)),),
            )
            self.db.commit()

    def _reentry_market_active(self, market_id: int) -> bool:
        return self.reentry_excluded_market_id is not None and int(market_id) != int(self.reentry_excluded_market_id)

    def _register_market(self, market_id: int, title: str | None) -> bool:
        active = super()._register_market(market_id, title)
        if self.reentry_excluded_market_id is None:
            self._set_reentry_excluded_market(market_id)
        if active and self._reentry_market_active(market_id):
            self._ensure_base_sequence_entry(market_id)
        return active

    def _maybe_trade(self, market_id: int, snapshot: dict[str, Any], decision: dict[str, Any]) -> None:
        had_trade = self.current_trade is not None
        super()._maybe_trade(market_id, snapshot, decision)
        if not had_trade and self.current_trade is not None and self._reentry_market_active(market_id):
            self._ensure_base_sequence_entry(market_id)

    def _ensure_base_sequence_entry(self, market_id: int) -> None:
        if not self._reentry_market_active(market_id) or not isinstance(self.current_trade, dict):
            return
        trade = dict(self.current_trade)
        if int(_number(trade.get("market_id")) or market_id) != int(market_id):
            return
        with self.db_lock:
            existing = self.db.execute(
                "SELECT 1 FROM strategy_reentry_entries WHERE market_id=? AND entry_ordinal=1",
                (int(market_id),),
            ).fetchone()
            if existing is not None:
                return
            decision_at_ms = int(_number(trade.get("decision_at_ms")) or time.time() * 1000)
            payload = {
                "marketId": int(market_id),
                "entryOrdinal": 1,
                "entrySource": "BASE_EBM",
                "side": trade.get("side"),
                "decisionAtMs": decision_at_ms,
                "observedAsk": trade.get("observed_ask"),
                "selectedProbability": trade.get("selected_probability"),
                "paperOnly": True,
                "liveOrdersAffected": False,
                "targetEventsUsed": False,
                "sequenceEntryCountCap": None,
            }
            self.db.execute(
                """INSERT OR IGNORE INTO strategy_reentry_entries(
                     market_id,entry_ordinal,decision_at_ms,snapshot_timestamp_ns,side,observed_ask,effective_unit_cost,
                     stake_usdt,shares,base_selected_probability,reentry_probability,raw_reentry_probability,
                     entry_source,ms_since_prev_entry,payload_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(market_id), 1, decision_at_ms,
                    int(_number(trade.get("snapshot_timestamp_ns")) or time.time_ns()),
                    str(trade.get("side")), float(_number(trade.get("observed_ask")) or 0.0),
                    float(_number(trade.get("effective_unit_cost")) or 0.0),
                    float(_number(trade.get("stake_usdt")) or 0.0), float(_number(trade.get("shares")) or 0.0),
                    _number(trade.get("selected_probability")), None, None, "BASE_EBM", None,
                    json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()

    def _sequence(self, market_id: int) -> list[dict[str, Any]]:
        with self.db_lock:
            return [dict(row) for row in self.db.execute(
                """SELECT market_id,entry_ordinal,decision_at_ms,snapshot_timestamp_ns,side,observed_ask,
                          effective_unit_cost,stake_usdt,shares,base_selected_probability,reentry_probability,
                          raw_reentry_probability,entry_source,ms_since_prev_entry
                     FROM strategy_reentry_entries WHERE market_id=? ORDER BY entry_ordinal""",
                (int(market_id),),
            )]

    def _record_reentry_decision(self, market_id: int, snapshot: dict[str, Any], decision: dict[str, Any]) -> None:
        signal = _record(decision.get("signal"))
        key = (
            str(decision.get("decision") or ""), str(decision.get("reason") or ""),
            str(decision.get("side") or ""), int(decision.get("entryCountSoFar") or 0),
        )
        payload = {
            **decision, "marketId": int(market_id), "strategy": STRATEGY, "paperOnly": True,
            "targetEventsUsed": False, "sameSideOnly": True,
        }
        with self.lock:
            self.last_reentry_decision = payload
            if key == self.last_reentry_decision_key and decision.get("decision") != "TRADE":
                return
            self.last_reentry_decision_key = key
        with self.db_lock:
            self.db.execute(
                """INSERT INTO strategy_reentry_decisions(
                     market_id,decision_at_ms,snapshot_timestamp_ns,decision,reason,side,reentry_probability,
                     raw_reentry_probability,observed_ask,ms_since_prev_entry,entry_count_so_far,
                     available_features,payload_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(market_id), int(time.time() * 1000), int(snapshot.get("timestampNs") or time.time_ns()),
                    str(decision.get("decision") or "SKIP"), str(decision.get("reason") or "UNKNOWN"),
                    decision.get("side"), _number(signal.get("probability")), _number(signal.get("rawProbability")),
                    _number(decision.get("ask")), _number(decision.get("msSincePrevEntry")),
                    int(decision.get("entryCountSoFar") or 0), int(signal.get("availableFeatureCount") or 0),
                    json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()

    def _insert_reentry_entry(
        self, market_id: int, snapshot: dict[str, Any], decision: dict[str, Any],
        previous_entry: dict[str, Any], entry_count_so_far: int,
    ) -> None:
        fill = reentry.execution(decision)
        if fill is None:
            return
        signal = _record(decision.get("signal"))
        ordinal = int(entry_count_so_far) + 1
        decision_at_ms = int(time.time() * 1000)
        payload = {
            "marketId": int(market_id), "entryOrdinal": ordinal, "entrySource": "REENTRY_EBM",
            "side": decision.get("side"), "decisionAtMs": decision_at_ms,
            "snapshotTimestampNs": int(snapshot.get("timestampNs") or time.time_ns()),
            "reentryProbability": signal.get("probability"), "rawReentryProbability": signal.get("rawProbability"),
            "msSincePrevEntry": decision.get("msSincePrevEntry"),
            "previousEntryOrdinal": previous_entry.get("entry_ordinal"),
            "previousObservedAsk": previous_entry.get("observed_ask"), **fill,
            "paperOnly": True, "liveOrdersAffected": False, "targetEventsUsed": False,
            "sameSideOnly": True, "sequenceEntryCountCap": None,
        }
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO strategy_reentry_entries(
                     market_id,entry_ordinal,decision_at_ms,snapshot_timestamp_ns,side,observed_ask,effective_unit_cost,
                     stake_usdt,shares,base_selected_probability,reentry_probability,raw_reentry_probability,
                     entry_source,ms_since_prev_entry,payload_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(market_id), ordinal, decision_at_ms, int(payload["snapshotTimestampNs"]), str(payload["side"]),
                    float(fill["ask"]), float(fill["effectiveUnitCost"]), float(fill["stakeUsdt"]), float(fill["shares"]),
                    None, _number(signal.get("probability")), _number(signal.get("rawProbability")), "REENTRY_EBM",
                    _number(decision.get("msSincePrevEntry")), json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()

    def _maybe_reentry(self, market_id: int, snapshot: dict[str, Any]) -> None:
        if not self._reentry_market_active(market_id):
            return
        self._ensure_base_sequence_entry(market_id)
        sequence = self._sequence(market_id)
        if not sequence:
            return
        previous_entry = sequence[-1]
        decision = reentry.decide_same_side_reentry(
            snapshot, previous_entry, self.reentry_model, entry_count_so_far=len(sequence),
            expected_market_id=int(market_id), now_ms=int(time.time() * 1000),
        )
        self._record_reentry_decision(market_id, snapshot, decision)
        if decision.get("decision") == "TRADE":
            self._insert_reentry_entry(market_id, snapshot, decision, previous_entry, len(sequence))

    def _advance(self) -> None:
        previous_market = self.current_market_id
        super()._advance()
        market_id = self.current_market_id
        if market_id is None:
            return
        if previous_market != market_id:
            with self.lock:
                self.last_reentry_decision = None
                self.last_reentry_decision_key = None
        if market_id == self.excluded_market_id or not self._reentry_market_active(market_id):
            return
        with self.lock:
            snapshot = dict(self.last_public_snapshot) if isinstance(self.last_public_snapshot, dict) else None
        if snapshot:
            self._maybe_reentry(int(market_id), snapshot)

    def _settle_from_official(self, state: dict[str, Any]) -> None:
        super()._settle_from_official(state)
        recent = state.get("targetRecentMarkets")
        if not isinstance(recent, list):
            return
        by_id: dict[int, dict[str, Any]] = {}
        for item in recent:
            row = _record(item)
            market_id = v1._positive_int(row.get("market_id") or row.get("marketId"))
            winner = str(row.get("winner") or "").upper()
            if market_id and winner in {"UP", "DOWN"}:
                by_id[market_id] = row
        if not by_id:
            return
        with self.db_lock:
            pending = [dict(row) for row in self.db.execute(
                """SELECT e.*,m.title FROM strategy_reentry_entries e
                     LEFT JOIN strategy_test_markets m ON m.market_id=e.market_id
                     LEFT JOIN strategy_reentry_entry_results r
                       ON r.market_id=e.market_id AND r.entry_ordinal=e.entry_ordinal
                     WHERE r.market_id IS NULL ORDER BY e.market_id,e.entry_ordinal"""
            )]
            for entry in pending:
                market_id = int(entry["market_id"])
                official = by_id.get(market_id)
                if official is None:
                    continue
                winner = str(official.get("winner") or "").upper()
                side = str(entry.get("side") or "").upper()
                stake = float(entry.get("stake_usdt") or 0.0)
                shares = float(entry.get("shares") or 0.0)
                payout = shares if side == winner else 0.0
                pnl = payout - stake
                roi = pnl / stake if stake > 1e-12 else None
                status = "WIN" if pnl > 1e-9 else "LOSS" if pnl < -1e-9 else "FLAT"
                self.db.execute(
                    """INSERT OR REPLACE INTO strategy_reentry_entry_results(
                         market_id,entry_ordinal,title,winner,resolved_at_ms,status,side,observed_ask,stake_usdt,
                         shares,payout_usdt,net_pnl_usdt,net_roi,entry_source,reentry_probability
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        market_id, int(entry["entry_ordinal"]),
                        str(official.get("title") or entry.get("title") or "") or None, winner,
                        int(official.get("resolved_at_ms") or official.get("resolvedAtMs") or time.time() * 1000),
                        status, side, float(entry.get("observed_ask") or 0.0), stake, shares, payout, pnl, roi,
                        str(entry.get("entry_source") or ""), _number(entry.get("reentry_probability")),
                    ),
                )
            self.db.commit()

    def _reentry_performance(self) -> dict[str, Any]:
        with self.db_lock:
            entry_summary = dict(self.db.execute(
                """SELECT COUNT(*) total_entries,
                          COALESCE(SUM(CASE WHEN entry_source='BASE_EBM' THEN 1 ELSE 0 END),0) base_entries,
                          COALESCE(SUM(CASE WHEN entry_source='REENTRY_EBM' THEN 1 ELSE 0 END),0) reentry_entries,
                          COUNT(DISTINCT market_id) entry_markets FROM strategy_reentry_entries"""
            ).fetchone())
            settled_summary = dict(self.db.execute(
                """SELECT COUNT(*) settled_entries,
                          COALESCE(SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END),0) wins,
                          COALESCE(SUM(CASE WHEN status='LOSS' THEN 1 ELSE 0 END),0) losses,
                          COALESCE(SUM(stake_usdt),0) stake_usdt,COALESCE(SUM(net_pnl_usdt),0) net_pnl_usdt
                     FROM strategy_reentry_entry_results"""
            ).fetchone())
            markets_with_reentry = int(self.db.execute(
                """SELECT COUNT(*) FROM (
                     SELECT market_id FROM strategy_reentry_entries GROUP BY market_id HAVING MAX(entry_ordinal)>1
                   )"""
            ).fetchone()[0])
            ordinal_rows = [dict(row) for row in self.db.execute(
                """SELECT CASE WHEN entry_ordinal>=4 THEN '4+' ELSE CAST(entry_ordinal AS TEXT) END ordinal_bucket,
                          COUNT(*) settled_entries,
                          COALESCE(SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END),0) wins,
                          COALESCE(SUM(CASE WHEN status='LOSS' THEN 1 ELSE 0 END),0) losses,
                          COALESCE(SUM(stake_usdt),0) stake_usdt,COALESCE(SUM(net_pnl_usdt),0) net_pnl_usdt
                     FROM strategy_reentry_entry_results
                     GROUP BY CASE WHEN entry_ordinal>=4 THEN '4+' ELSE CAST(entry_ordinal AS TEXT) END"""
            )]
            recent = [dict(row) for row in self.db.execute(
                """SELECT market_id,entry_ordinal,title,winner,status,side,observed_ask,stake_usdt,shares,
                          payout_usdt,net_pnl_usdt,net_roi,entry_source,reentry_probability,resolved_at_ms
                     FROM strategy_reentry_entry_results
                     ORDER BY resolved_at_ms DESC,market_id DESC,entry_ordinal DESC LIMIT 50"""
            )]
        total_entries = int(entry_summary.get("total_entries") or 0)
        entry_markets = int(entry_summary.get("entry_markets") or 0)
        settled_entries = int(settled_summary.get("settled_entries") or 0)
        wins = int(settled_summary.get("wins") or 0)
        stake = float(settled_summary.get("stake_usdt") or 0.0)
        pnl = float(settled_summary.get("net_pnl_usdt") or 0.0)
        ordinal_stats = []
        for row in ordinal_rows:
            row_stake = float(row.get("stake_usdt") or 0.0)
            row_pnl = float(row.get("net_pnl_usdt") or 0.0)
            row_count = int(row.get("settled_entries") or 0)
            row_wins = int(row.get("wins") or 0)
            ordinal_stats.append({
                **row, "win_rate": row_wins / row_count if row_count else None,
                "net_roi": row_pnl / row_stake if row_stake > 1e-12 else None,
            })
        order = {"1": 1, "2": 2, "3": 3, "4+": 4}
        ordinal_stats.sort(key=lambda row: order.get(str(row.get("ordinal_bucket")), 99))
        return {
            "totalEntries": total_entries, "baseEntries": int(entry_summary.get("base_entries") or 0),
            "reentryEntries": int(entry_summary.get("reentry_entries") or 0), "entryMarkets": entry_markets,
            "marketsWithReentry": markets_with_reentry,
            "avgEntriesPerMarket": total_entries / entry_markets if entry_markets else None,
            "settledEntries": settled_entries, "wins": wins, "losses": int(settled_summary.get("losses") or 0),
            "winRate": wins / settled_entries if settled_entries else None, "stakeUsdt": stake,
            "netPnlUsdt": pnl, "netRoi": pnl / stake if stake > 1e-12 else None,
            "ordinalStats": ordinal_stats, "recentEntries": recent,
        }

    def _recent_reentry_decisions(self) -> list[dict[str, Any]]:
        with self.db_lock:
            return [dict(row) for row in self.db.execute(
                """SELECT market_id,decision_at_ms,decision,reason,side,reentry_probability,
                          raw_reentry_probability,observed_ask,ms_since_prev_entry,entry_count_so_far,
                          available_features FROM strategy_reentry_decisions ORDER BY id DESC LIMIT 40"""
            )]

    def _reentry_diagnostics(self) -> dict[str, Any]:
        with self.lock:
            decision = dict(self.last_reentry_decision) if isinstance(self.last_reentry_decision, dict) else {}
        signal = _record(decision.get("signal"))
        missing = [str(value) for value in signal.get("missingFeatures") or []]
        return {
            "ready": signal.get("status") == "OK" and signal.get("featureInputComplete") is True,
            "signalStatus": signal.get("status") or "NO_SIGNAL", "inferenceAttempted": signal.get("status") == "OK",
            "tradeAllowedNow": decision.get("decision") == "TRADE",
            "requiredFeatureCount": signal.get("requiredFeatureCount") or len(reentry.EXPECTED_FEATURES),
            "availableFeatureCount": signal.get("availableFeatureCount") or 0, "missingFeatures": missing,
            "features": dict(_record(signal.get("features"))), "probability": signal.get("probability"),
            "rawProbability": signal.get("rawProbability"),
            "threshold": signal.get("threshold") or reentry.REENTRY_PROBABILITY_THRESHOLD,
            "decision": decision.get("decision"), "reason": decision.get("reason"), "side": decision.get("side"),
            "ask": decision.get("ask"), "msSincePrevEntry": decision.get("msSincePrevEntry"),
            "entryCountSoFar": decision.get("entryCountSoFar"), "gates": dict(_record(decision.get("gates"))),
        }

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        payload["strategy"] = STRATEGY
        payload["reentryModelLoaded"] = self.reentry_model is not None
        payload["reentryModelError"] = self.reentry_model_error
        payload["reentryPaperOnly"] = True
        payload["reentryTargetEventsUsedForDecision"] = False
        payload["reentryOppositeSideEnabled"] = False
        return payload

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        with self.lock:
            market_id = self.current_market_id
            last_reentry = dict(self.last_reentry_decision) if isinstance(self.last_reentry_decision, dict) else None
        current_sequence = self._sequence(int(market_id)) if market_id is not None else []
        payload["version"] = VERSION
        payload["strategy"] = STRATEGY
        payload["oneEntryPerMarket"] = False
        payload["controlOneEntryPerMarket"] = True
        payload["reentryEntryCountCap"] = None
        payload["sameSideReentryOnly"] = True
        payload["oppositeSideReentryEnabled"] = False
        payload["reentryDeployedAtMs"] = self.reentry_deployed_at_ms
        payload["reentryExcludedDeploymentMarketId"] = self.reentry_excluded_market_id
        payload["reentryModel"] = {
            "loaded": self.reentry_model is not None,
            "path": self.reentry_model.get("path") if self.reentry_model else str(reentry.DEFAULT_MODEL_PATH),
            "reportVersion": self.reentry_model.get("reportVersion") if self.reentry_model else None,
            "datasetVersion": self.reentry_model.get("datasetVersion") if self.reentry_model else None,
            "featureSet": self.reentry_model.get("featureSet") if self.reentry_model else reentry.FEATURE_SET,
            "features": list(reentry.EXPECTED_FEATURES),
            "calibratorLoaded": bool(self.reentry_model and self.reentry_model.get("calibrator") is not None),
            "error": self.reentry_model_error,
        }
        payload["reentryPolicy"] = reentry.policy()
        payload["currentEntrySequence"] = current_sequence
        payload["lastReentryDecision"] = last_reentry
        payload["reentryDiagnostics"] = self._reentry_diagnostics()
        payload["reentryPerformance"] = self._reentry_performance()
        payload["recentReentryDecisions"] = self._recent_reentry_decisions()
        payload["evidenceBoundary"] = (
            str(payload.get("evidenceBoundary") or "")
            + " SAME_SIDE re-entry EBM uses only the strategy's own prior paper entries for actor_* state plus "
              "contemporaneous public market inputs. Live Target fills/parent orders/inventory are never re-entry inputs."
        ).strip()
        return payload


class Handler(v2.Handler):
    test: TargetTakerPublicSideReentryTest


def main() -> int:
    test = TargetTakerPublicSideReentryTest()
    test.start()
    handler = type("TargetTakerPublicSideReentryTestV1Handler", (Handler,), {"test": test})
    server = ThreadingHTTPServer((v1.HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{v1.HOST}:{PORT}/state; strategy={STRATEGY}; "
        "controlOneEntryPerMarket=true; sameSideReentry=true; reentryEntryCountCap=none; "
        "paperOnly=true; targetEventsUsedForDecision=false; liveOrdersAffected=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
        test.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
