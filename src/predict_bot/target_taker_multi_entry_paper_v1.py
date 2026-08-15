from __future__ import annotations

import json
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_target_taker_public_side_strategy_v1 as public_side


VERSION = "TARGET_TAKER_PUBLIC_SIDE_V1_MULTI_ENTRY_EVERY_SIGNAL"
COHORT = "TARGET_TAKER_PUBLIC_SIDE_V1_MULTI_ENTRY_EVERY_SIGNAL"


def policy() -> dict[str, Any]:
    return {
        "version": VERSION,
        "parentStrategy": public_side.VERSION,
        "paperOnly": True,
        "forwardOnly": True,
        "liveOrdersAffected": False,
        "automaticStrategyPromotion": False,
        "entryRule": (
            "On every new public snapshot, re-run the exact frozen TARGET_TAKER_PUBLIC_SIDE_V1_EBM_FORWARD "
            "SIDE_ONLY decision. If it is TRADE, add one independent paper entry."
        ),
        "oneEntryPerMarket": False,
        "cooldownMs": 0,
        "sideFlipRequired": False,
        "sameSideReentryAllowed": True,
        "dedupe": "at most one paper entry per market_id + public snapshot_timestamp_ns",
        "fixedStakeUsdtPerEntry": public_side.STAKE_USDT,
        "feeRateBps": public_side.FEE_RATE_BPS,
        "maxAsk": public_side.MAX_ASK,
        "minSecondsLeft": public_side.MIN_SECONDS_LEFT,
        "sideProbabilityThreshold": public_side.SIDE_PROBABILITY_THRESHOLD,
        "targetEventsDriveRuntime": False,
    }


class MultiEntryPaperMixin:
    """Forward-only paper cohort that removes the one-entry-per-market cap.

    The control SIDE_ONLY cohort remains untouched. This experiment deliberately
    adds no cooldown or flip requirement: every distinct public snapshot that
    still satisfies the frozen Side EBM is one $1 paper entry. It never feeds the
    live executor and is intentionally absent from the live cohort allowlist.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.target_taker_multi_entry_schema_ready = False
        self.target_taker_multi_entry_state: dict[str, Any] = {
            "active": False,
            "deployedAtMs": 0,
            "excludedMarketId": None,
            "lastProcessedSnapshotNs": 0,
            "entryCount": 0,
            "lastEvent": None,
        }
        super().__init__(*args, **kwargs)

        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_target_taker_multi_entry_v1_meta (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    deployed_at_ms INTEGER NOT NULL,
                    excluded_market_id INTEGER,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_target_taker_multi_entry_v1_markets (
                    market_id INTEGER PRIMARY KEY,
                    title TEXT,
                    started_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_target_taker_multi_entry_v1_events (
                    id TEXT PRIMARY KEY,
                    market_id INTEGER NOT NULL,
                    snapshot_timestamp_ns INTEGER NOT NULL,
                    decision_at_ms INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    observed_ask REAL NOT NULL,
                    effective_unit_cost REAL NOT NULL,
                    stake_usdt REAL NOT NULL,
                    shares REAL NOT NULL,
                    side_score REAL NOT NULL,
                    selected_probability REAL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(market_id, snapshot_timestamp_ns)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_target_taker_multi_entry_v1_events_market
                    ON wallet_target_taker_multi_entry_v1_events(market_id,decision_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_target_taker_multi_entry_v1_results (
                    market_id INTEGER PRIMARY KEY,
                    title TEXT,
                    winner TEXT NOT NULL,
                    resolved_at_ms INTEGER NOT NULL,
                    traded INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    entry_count INTEGER NOT NULL,
                    winning_entries INTEGER NOT NULL,
                    losing_entries INTEGER NOT NULL,
                    up_entries INTEGER NOT NULL,
                    down_entries INTEGER NOT NULL,
                    avg_observed_ask REAL,
                    stake_usdt REAL NOT NULL,
                    payout_usdt REAL NOT NULL,
                    net_pnl_usdt REAL NOT NULL,
                    net_roi REAL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_target_taker_multi_entry_v1_results_time
                    ON wallet_target_taker_multi_entry_v1_results(resolved_at_ms);
                """
            )
            now_ms = base._now_ms()
            row = self.db.execute(
                "SELECT deployed_at_ms,excluded_market_id FROM wallet_target_taker_multi_entry_v1_meta WHERE id=1"
            ).fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO wallet_target_taker_multi_entry_v1_meta(id,deployed_at_ms,excluded_market_id,policy_json) VALUES (1,?,?,?)",
                    (now_ms, None, json.dumps(policy(), separators=(",", ":"), default=str)),
                )
                deployed_at_ms = now_ms
                excluded_market_id = None
            else:
                deployed_at_ms = int(row["deployed_at_ms"])
                excluded_market_id = (
                    int(row["excluded_market_id"])
                    if row["excluded_market_id"] is not None
                    else None
                )
                self.db.execute(
                    "UPDATE wallet_target_taker_multi_entry_v1_meta SET policy_json=? WHERE id=1",
                    (json.dumps(policy(), separators=(",", ":"), default=str),),
                )
            self.db.commit()

        self.target_taker_multi_entry_state.update(
            {
                "deployedAtMs": deployed_at_ms,
                "excludedMarketId": excluded_market_id,
            }
        )
        self.target_taker_multi_entry_schema_ready = True
        self._seed_target_taker_multi_entry_pending_settlements()

    def _restore_target_taker_multi_entry_market(
        self, market_id: int, title: str | None, now_ms: int
    ) -> None:
        state = self.target_taker_multi_entry_state
        with self.db_lock:
            if state["excludedMarketId"] is None:
                state["excludedMarketId"] = int(market_id)
                self.db.execute(
                    "UPDATE wallet_target_taker_multi_entry_v1_meta SET excluded_market_id=? WHERE id=1",
                    (int(market_id),),
                )
            registered = self.db.execute(
                "SELECT 1 FROM wallet_target_taker_multi_entry_v1_markets WHERE market_id=?",
                (int(market_id),),
            ).fetchone()
            active = bool(registered) or int(market_id) != int(state["excludedMarketId"])
            state.update(
                {
                    "active": active,
                    "lastProcessedSnapshotNs": 0,
                    "entryCount": 0,
                    "lastEvent": None,
                }
            )
            if not active:
                self.db.commit()
                return
            self.db.execute(
                "INSERT OR IGNORE INTO wallet_target_taker_multi_entry_v1_markets(market_id,title,started_at_ms) VALUES (?,?,?)",
                (int(market_id), title, int(now_ms)),
            )
            row = self.db.execute(
                """SELECT COUNT(*) entry_count,MAX(snapshot_timestamp_ns) last_snapshot_ns
                     FROM wallet_target_taker_multi_entry_v1_events WHERE market_id=?""",
                (int(market_id),),
            ).fetchone()
            state["entryCount"] = int(row["entry_count"] or 0)
            state["lastProcessedSnapshotNs"] = int(row["last_snapshot_ns"] or 0)
            last = self.db.execute(
                """SELECT payload_json FROM wallet_target_taker_multi_entry_v1_events
                    WHERE market_id=? ORDER BY decision_at_ms DESC,id DESC LIMIT 1""",
                (int(market_id),),
            ).fetchone()
            if last is not None:
                try:
                    state["lastEvent"] = json.loads(last["payload_json"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    state["lastEvent"] = None
            self.db.commit()
        self.pending_settlement_ids.add(int(market_id))

    def _reset_market(self, market_id: int, bucket: int | None, title: str | None) -> None:
        super()._reset_market(market_id, bucket, title)
        if not self.target_taker_multi_entry_schema_ready:
            return
        self._restore_target_taker_multi_entry_market(
            int(market_id), title, base._now_ms()
        )

    def _advance_target_taker_multi_entry(
        self,
        snapshot: dict[str, Any],
        *,
        snapshot_ns: int,
        now_ms: int,
    ) -> None:
        state = self.target_taker_multi_entry_state
        if not state.get("active") or self.market_id is None:
            return
        if int(snapshot_ns) <= int(state.get("lastProcessedSnapshotNs") or 0):
            return
        state["lastProcessedSnapshotNs"] = int(snapshot_ns)

        decision = public_side.decide_side(
            snapshot,
            self.public_side_model,
            expected_market_id=int(self.market_id),
            now_ms=int(now_ms),
        )
        if decision.get("decision") != "TRADE":
            return
        fill = public_side.execution(decision)
        if fill is None:
            return
        signal = decision.get("signal") if isinstance(decision.get("signal"), dict) else {}
        event_id = f"{COHORT}:{int(self.market_id)}:{int(snapshot_ns)}"
        payload = {
            "id": event_id,
            "cohort": COHORT,
            "strategy": public_side.VERSION,
            "experimentVersion": VERSION,
            "marketId": int(self.market_id),
            "snapshotTimestampNs": int(snapshot_ns),
            "decisionAtMs": int(now_ms),
            "side": str(decision["side"]),
            **fill,
            "sideScore": signal.get("score"),
            "sideProbabilityUp": signal.get("probabilityUp"),
            "sideSelectedProbability": signal.get("selectedProbability"),
            "secondsLeft": decision.get("secondsLeft"),
            "paperOnly": True,
            "targetEventsUsed": False,
            "oneEntryPerMarket": False,
            "reentryPolicy": "EVERY_NEW_PUBLIC_SNAPSHOT_THAT_IS_TRADE",
        }
        with self.db_lock:
            cursor = self.db.execute(
                """INSERT OR IGNORE INTO wallet_target_taker_multi_entry_v1_events(
                       id,market_id,snapshot_timestamp_ns,decision_at_ms,side,observed_ask,
                       effective_unit_cost,stake_usdt,shares,side_score,selected_probability,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    int(self.market_id),
                    int(snapshot_ns),
                    int(now_ms),
                    str(decision["side"]),
                    float(fill["ask"]),
                    float(fill["effectiveUnitCost"]),
                    float(fill["stakeUsdt"]),
                    float(fill["shares"]),
                    float(signal.get("score") or 0.0),
                    signal.get("selectedProbability"),
                    json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()
        if cursor.rowcount > 0:
            state["entryCount"] = int(state.get("entryCount") or 0) + 1
            state["lastEvent"] = payload
            self.pending_settlement_ids.add(int(self.market_id))

    def _advance_shadow(self, book: dict[str, Any], core: dict[str, Any]) -> None:
        super()._advance_shadow(book, core)
        if not self.target_taker_multi_entry_schema_ready or self.market_id is None:
            return
        snapshot = self.latest_public_signal_snapshot
        if not isinstance(snapshot, dict):
            return
        snapshot_ns = int(float(snapshot.get("timestamp_ns") or 0))
        if snapshot_ns <= 0:
            return
        self._advance_target_taker_multi_entry(
            snapshot,
            snapshot_ns=snapshot_ns,
            now_ms=base._now_ms(),
        )

    def _seed_pending_settlements(self) -> None:
        super()._seed_pending_settlements()
        if self.target_taker_multi_entry_schema_ready:
            self._seed_target_taker_multi_entry_pending_settlements()

    def _seed_target_taker_multi_entry_pending_settlements(self) -> None:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            rows = self.db.execute(
                """SELECT m.market_id FROM wallet_target_taker_multi_entry_v1_markets m
                     LEFT JOIN wallet_target_taker_multi_entry_v1_results r ON r.market_id=m.market_id
                    WHERE m.started_at_ms>=? AND r.market_id IS NULL""",
                (cutoff,),
            ).fetchall()
        self.pending_settlement_ids.update(int(row[0]) for row in rows)

    def _store_market_result(self, market_id: int, market: dict[str, Any], winner: str) -> None:
        super()._store_market_result(market_id, market, winner)
        if not self.target_taker_multi_entry_schema_ready:
            return
        winner = str(winner or "").upper()
        if winner not in {"UP", "DOWN"}:
            return
        resolved_at_ms = base._now_ms()
        with self.db_lock:
            registered = self.db.execute(
                "SELECT title FROM wallet_target_taker_multi_entry_v1_markets WHERE market_id=?",
                (int(market_id),),
            ).fetchone()
            if registered is None:
                return
            events = [
                dict(row)
                for row in self.db.execute(
                    "SELECT * FROM wallet_target_taker_multi_entry_v1_events WHERE market_id=? ORDER BY decision_at_ms,id",
                    (int(market_id),),
                )
            ]
            title = str(
                market.get("title") or market.get("question") or registered["title"] or ""
            ) or None
            entry_count = len(events)
            winning_entries = sum(str(row["side"]).upper() == winner for row in events)
            losing_entries = entry_count - winning_entries
            up_entries = sum(str(row["side"]).upper() == "UP" for row in events)
            down_entries = sum(str(row["side"]).upper() == "DOWN" for row in events)
            stake = sum(float(row["stake_usdt"] or 0.0) for row in events)
            payout = sum(
                float(row["shares"] or 0.0)
                for row in events
                if str(row["side"]).upper() == winner
            )
            pnl = payout - stake
            roi = pnl / stake if stake > 0 else None
            avg_ask = (
                sum(float(row["observed_ask"] or 0.0) for row in events) / entry_count
                if entry_count
                else None
            )
            traded = int(entry_count > 0)
            status = (
                "NO_TRADE"
                if not traded
                else "NET_WIN"
                if pnl > 1e-9
                else "NET_LOSS"
                if pnl < -1e-9
                else "FLAT"
            )
            self.db.execute(
                """INSERT INTO wallet_target_taker_multi_entry_v1_results(
                       market_id,title,winner,resolved_at_ms,traded,status,entry_count,winning_entries,
                       losing_entries,up_entries,down_entries,avg_observed_ask,stake_usdt,payout_usdt,
                       net_pnl_usdt,net_roi
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(market_id) DO UPDATE SET
                       title=excluded.title,winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,
                       traded=excluded.traded,status=excluded.status,entry_count=excluded.entry_count,
                       winning_entries=excluded.winning_entries,losing_entries=excluded.losing_entries,
                       up_entries=excluded.up_entries,down_entries=excluded.down_entries,
                       avg_observed_ask=excluded.avg_observed_ask,stake_usdt=excluded.stake_usdt,
                       payout_usdt=excluded.payout_usdt,net_pnl_usdt=excluded.net_pnl_usdt,
                       net_roi=excluded.net_roi""",
                (
                    int(market_id),
                    title,
                    winner,
                    int(resolved_at_ms),
                    traded,
                    status,
                    entry_count,
                    winning_entries,
                    losing_entries,
                    up_entries,
                    down_entries,
                    avg_ask,
                    stake,
                    payout,
                    pnl,
                    roi,
                ),
            )
            self.db.commit()

    def _target_taker_multi_entry_performance(self) -> dict[str, Any]:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            row = dict(
                self.db.execute(
                    """SELECT COUNT(*) settled,
                              COALESCE(SUM(traded),0) traded_markets,
                              COALESCE(SUM(entry_count),0) entries,
                              COALESCE(SUM(winning_entries),0) winning_entries,
                              COALESCE(SUM(losing_entries),0) losing_entries,
                              COALESCE(SUM(CASE WHEN traded=1 AND status='NET_WIN' THEN 1 ELSE 0 END),0) positive_markets,
                              COALESCE(SUM(CASE WHEN traded=1 AND status='NET_LOSS' THEN 1 ELSE 0 END),0) negative_markets,
                              COALESCE(SUM(stake_usdt),0) stake,
                              COALESCE(SUM(net_pnl_usdt),0) pnl
                         FROM wallet_target_taker_multi_entry_v1_results
                        WHERE resolved_at_ms>=?""",
                    (cutoff,),
                ).fetchone()
            )
            markets = int(
                self.db.execute(
                    "SELECT COUNT(*) FROM wallet_target_taker_multi_entry_v1_markets WHERE started_at_ms>=?",
                    (cutoff,),
                ).fetchone()[0]
            )
            ordered = [
                dict(item)
                for item in self.db.execute(
                    """SELECT status,net_pnl_usdt FROM wallet_target_taker_multi_entry_v1_results
                        WHERE resolved_at_ms>=? AND traded=1 ORDER BY resolved_at_ms,market_id""",
                    (cutoff,),
                )
            ]
            recent = [
                dict(item)
                for item in self.db.execute(
                    """SELECT market_id,winner,status,entry_count,winning_entries,losing_entries,
                              up_entries,down_entries,avg_observed_ask,stake_usdt,net_pnl_usdt,net_roi,resolved_at_ms
                         FROM wallet_target_taker_multi_entry_v1_results
                        WHERE resolved_at_ms>=? ORDER BY resolved_at_ms DESC LIMIT 20""",
                    (cutoff,),
                )
            ]
        settled = int(row["settled"])
        traded_markets = int(row["traded_markets"])
        entries = int(row["entries"])
        winning_entries = int(row["winning_entries"])
        losing_entries = int(row["losing_entries"])
        positive_markets = int(row["positive_markets"])
        negative_markets = int(row["negative_markets"])
        stake = float(row["stake"])
        pnl = float(row["pnl"])
        equity = peak = max_drawdown = 0.0
        for item in ordered:
            equity += float(item["net_pnl_usdt"] or 0.0)
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
        return {
            "markets": markets,
            "settledMarkets": settled,
            "pendingMarkets": max(0, markets - settled),
            "tradedMarkets": traded_markets,
            "tradeRate": traded_markets / settled if settled else None,
            "entries": entries,
            "averageEntriesPerTradedMarket": entries / traded_markets if traded_markets else None,
            "winningEntries": winning_entries,
            "losingEntries": losing_entries,
            "entryWinRate": winning_entries / entries if entries else None,
            "positivePnlMarkets": positive_markets,
            "negativePnlMarkets": negative_markets,
            "positiveMarketRate": positive_markets / traded_markets if traded_markets else None,
            "stakeUsdt": stake,
            "netPnlUsdt": pnl,
            "netRoi": pnl / stake if stake > 0 else None,
            "maxDrawdownUsdt": max_drawdown,
            "recentMarkets": recent,
        }

    def _cleanup_retention(self, *, force: bool = False) -> None:
        super()._cleanup_retention(force=force)
        if not self.target_taker_multi_entry_schema_ready:
            return
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            self.db.execute(
                "DELETE FROM wallet_target_taker_multi_entry_v1_events WHERE decision_at_ms<?",
                (cutoff,),
            )
            self.db.execute(
                "DELETE FROM wallet_target_taker_multi_entry_v1_results WHERE resolved_at_ms<?",
                (cutoff,),
            )
            self.db.execute(
                "DELETE FROM wallet_target_taker_multi_entry_v1_markets WHERE started_at_ms<? AND market_id!=?",
                (cutoff, int(self.market_id or -1)),
            )
            self.db.commit()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        lab = payload.get("targetTakerPublicSideV1Lab")
        if not isinstance(lab, dict):
            lab = {}
            payload["targetTakerPublicSideV1Lab"] = lab
        state = self.target_taker_multi_entry_state
        lab["multiEntryExperiment"] = {
            "version": VERSION,
            "cohort": COHORT,
            "status": "ACTIVE" if state.get("active") else "WAITING_NEXT_COMPLETE_MARKET",
            "deploymentBoundaryMs": state.get("deployedAtMs"),
            "excludedDeploymentMarketId": state.get("excludedMarketId"),
            "currentMarketEntryCount": int(state.get("entryCount") or 0),
            "lastEvent": state.get("lastEvent"),
            "performance": self._target_taker_multi_entry_performance(),
            "policy": policy(),
        }
        ab = lab.get("abTest")
        if isinstance(ab, dict):
            ab["multiEntryEverySignal"] = COHORT
            ab["multiEntryQuestion"] = (
                "Does removing the first-entry-only lock improve or degrade forward PnL when every later valid frozen Side EBM snapshot also enters?"
            )
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["targetTakerMultiEntryPaperV1"] = {
            "version": VERSION,
            "paperOnly": True,
            "liveOrdersAffected": False,
            "active": bool(self.target_taker_multi_entry_state.get("active")),
            "currentMarketEntryCount": int(self.target_taker_multi_entry_state.get("entryCount") or 0),
        }
        return payload
