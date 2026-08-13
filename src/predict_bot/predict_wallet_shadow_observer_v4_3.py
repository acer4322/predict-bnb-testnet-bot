from __future__ import annotations

import sqlite3
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v3 as v3
from . import predict_wallet_shadow_observer_v4_1 as v4_1
from . import predict_wallet_shadow_observer_v4_2 as v4_2


VERSION = "PREDICT_WALLET_SHADOW_V0_4_4_SPOT_STRIKE_DASHBOARD"
TARGET_SIMILARITY_CACHE_MS = 5_000


def _residual(rows: list[dict[str, Any]], cutoff_ms: int | None = None) -> dict[str, Any]:
    selected = [row for row in rows if cutoff_ms is None or int(row["event_ms"]) <= cutoff_ms]
    up = sum(float(row.get("shares") or 0.0) for row in selected if row.get("side") == "UP")
    down = sum(float(row.get("shares") or 0.0) for row in selected if row.get("side") == "DOWN")
    up_cost = sum(
        float(row.get("shares") or 0.0) * float(row.get("price") or 0.0)
        for row in selected
        if row.get("side") == "UP"
    )
    down_cost = sum(
        float(row.get("shares") or 0.0) * float(row.get("price") or 0.0)
        for row in selected
        if row.get("side") == "DOWN"
    )
    return {
        "side": "UP" if up > down else "DOWN" if down > up else None,
        "capitalSide": "UP" if up_cost > down_cost else "DOWN" if down_cost > up_cost else None,
        "upShares": up,
        "downShares": down,
        "upCostUsdt": up_cost,
        "downCostUsdt": down_cost,
        "legs": len(selected),
    }


class WalletShadowObserver(v4_2.WalletShadowObserver):
    """Dashboard-ready Spot/Strike forward cohort with bounded storage and target diagnostics."""

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=v4_2.SIMULATION_DB_PATH) -> None:
        self.forward_event: dict[str, Any] | None = None
        self.forward_decision: dict[str, Any] | None = None
        self.forward_last_block: dict[str, Any] | None = None
        self.forward_last_cleanup_deleted = {"events": 0, "decisions": 0, "results": 0}
        self.forward_similarity_cache: dict[str, Any] | None = None
        self.forward_similarity_cache_at_ms = 0
        self.simulation_db: sqlite3.Connection | None = None
        self.simulation_db_path = Path(simulation_db_path)
        self.simulation_db_error: str | None = None

        # Bypass v4_2.__init__: its read-only simulation DB open is fatal when the
        # source DB is temporarily unavailable. The rest of Wallet Shadow must stay up.
        v4_1.WalletShadowObserver.__init__(self, db_path)

        try:
            self.simulation_db = sqlite3.connect(
                f"file:{self.simulation_db_path.as_posix()}?mode=ro",
                uri=True,
                check_same_thread=False,
                timeout=2.0,
            )
            self.simulation_db.row_factory = sqlite3.Row
        except (sqlite3.Error, OSError) as exc:
            self.simulation_db = None
            self.simulation_db_error = str(exc)[:500]

        with self.db_lock:
            row = self.db.execute(
                "SELECT deployed_at_ms FROM wallet_spot_strike_forward_meta WHERE cohort=?",
                (v4_2.COHORT,),
            ).fetchone()
            if row is None:
                self.forward_deployed_at_ms = base._now_ms()
                self.db.execute(
                    "INSERT INTO wallet_spot_strike_forward_meta(cohort,deployed_at_ms,policy_json) VALUES (?,?,?)",
                    (
                        v4_2.COHORT,
                        self.forward_deployed_at_ms,
                        base.json.dumps(self._forward_config(), separators=(",", ":")),
                    ),
                )
                self.db.commit()
            else:
                self.forward_deployed_at_ms = int(row[0])

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            self.db.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_wallet_spot_strike_forward_events_time
                    ON wallet_spot_strike_forward_events(cohort, decision_at_ms);
                CREATE INDEX IF NOT EXISTS idx_wallet_spot_strike_forward_decisions_time
                    ON wallet_spot_strike_forward_decisions(cohort, decision_at_ms);
                """
            )
            self.db.commit()

    def stop(self) -> None:
        if self.simulation_db is not None:
            self.simulation_db.close()
            self.simulation_db = None
        v4_1.WalletShadowObserver.stop(self)

    def _current_spot_snapshot(self, bucket: int) -> dict[str, Any] | None:
        if self.simulation_db is None:
            return None
        return v4_2.WalletShadowObserver._current_spot_snapshot(self, bucket)

    def _advance_spot_strike(self, book: dict[str, Any]) -> None:
        if self.simulation_db is None:
            if self.forward_decision is not None:
                return
            seconds_left = base._finite(book.get("secondsLeft"))
            if seconds_left is not None and 0 < seconds_left <= v4_2.DECISION_SECONDS:
                self._block_forward(
                    "SIMULATION_DB_UNAVAILABLE",
                    seconds_left,
                    simulationDbError=self.simulation_db_error,
                )
            return
        super()._advance_spot_strike(book)

    def _seed_pending_settlements(self) -> None:
        super()._seed_pending_settlements()
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            rows = self.db.execute(
                """
                SELECT e.market_id
                  FROM wallet_spot_strike_forward_events e
                  LEFT JOIN wallet_spot_strike_forward_results r
                    ON r.cohort=e.cohort AND r.market_id=e.market_id
                 WHERE e.cohort=? AND e.decision_at_ms>=? AND r.market_id IS NULL
                """,
                (v4_2.COHORT, cutoff),
            ).fetchall()
        self.pending_settlement_ids.update(int(row[0]) for row in rows)

    def _cleanup_retention(self, *, force: bool = False) -> None:
        super()._cleanup_retention(force=force)
        cutoff = base._now_ms() - self.retention_ms
        deleted: dict[str, int] = {}
        with self.db_lock:
            cur = self.db.execute(
                "DELETE FROM wallet_spot_strike_forward_events WHERE cohort=? AND decision_at_ms<?",
                (v4_2.COHORT, cutoff),
            )
            deleted["events"] = max(0, cur.rowcount)
            cur = self.db.execute(
                "DELETE FROM wallet_spot_strike_forward_decisions WHERE cohort=? AND decision_at_ms<?",
                (v4_2.COHORT, cutoff),
            )
            deleted["decisions"] = max(0, cur.rowcount)
            cur = self.db.execute(
                "DELETE FROM wallet_spot_strike_forward_results WHERE cohort=? AND resolved_at_ms<?",
                (v4_2.COHORT, cutoff),
            )
            deleted["results"] = max(0, cur.rowcount)
            self.db.commit()
        self.forward_last_cleanup_deleted = deleted

    def _forward_performance(self) -> dict[str, Any]:
        payload = v4_2.WalletShadowObserver._forward_performance(self)
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            recent_decisions = [
                dict(row)
                for row in self.db.execute(
                    """
                    SELECT market_id,market_bucket,decision_at_ms,seconds_left,decision,reason,
                           side,observed_ask,start_price,spot_price,displacement_bps,
                           spot_age_ms,prediction_receipt_age_ms
                      FROM wallet_spot_strike_forward_decisions
                     WHERE cohort=? AND decision_at_ms>=?
                     ORDER BY decision_at_ms DESC LIMIT 30
                    """,
                    (v4_2.COHORT, cutoff),
                )
            ]
            stored = {
                "events": int(
                    self.db.execute(
                        "SELECT COUNT(*) FROM wallet_spot_strike_forward_events WHERE cohort=? AND decision_at_ms>=?",
                        (v4_2.COHORT, cutoff),
                    ).fetchone()[0]
                ),
                "decisions": int(
                    self.db.execute(
                        "SELECT COUNT(*) FROM wallet_spot_strike_forward_decisions WHERE cohort=? AND decision_at_ms>=?",
                        (v4_2.COHORT, cutoff),
                    ).fetchone()[0]
                ),
                "results": int(
                    self.db.execute(
                        "SELECT COUNT(*) FROM wallet_spot_strike_forward_results WHERE cohort=? AND resolved_at_ms>=?",
                        (v4_2.COHORT, cutoff),
                    ).fetchone()[0]
                ),
            }
        decisions = int(payload.get("decisions") or 0)
        trades = int(payload.get("events") or 0)
        payload.update(
            {
                "windowDays": self.retention_days,
                "tradeRate": trades / decisions if decisions else None,
                "skips": max(0, decisions - trades),
                "storedRows": stored,
                "recentDecisions": recent_decisions,
            }
        )
        return payload

    def _forward_target_similarity(self) -> dict[str, Any]:
        now_ms = base._now_ms()
        if (
            self.forward_similarity_cache is not None
            and now_ms - self.forward_similarity_cache_at_ms < TARGET_SIMILARITY_CACHE_MS
        ):
            return dict(self.forward_similarity_cache)

        cutoff = now_ms - self.retention_ms
        with self.db_lock:
            decisions = [
                dict(row)
                for row in self.db.execute(
                    """
                    SELECT market_id,decision_at_ms,decision,side
                      FROM wallet_spot_strike_forward_decisions
                     WHERE cohort=? AND decision_at_ms>=? AND side IN ('UP','DOWN')
                     ORDER BY decision_at_ms
                    """,
                    (v4_2.COHORT, cutoff),
                )
            ]
            target_rows = [
                dict(row)
                for row in self.db.execute(
                    """
                    SELECT market_id,event_ms,side,shares,price
                      FROM wallet_shadow_target_events
                     WHERE wallet=? AND event_ms>=? AND role='TAKER' AND quote_type='BID'
                       AND side IN ('UP','DOWN') AND shares IS NOT NULL
                     ORDER BY market_id,event_ms
                    """,
                    (self.wallet, cutoff),
                )
            ]

        by_market: dict[int, list[dict[str, Any]]] = {}
        for row in target_rows:
            by_market.setdefault(int(row["market_id"]), []).append(row)

        side_at_matches = side_at_total = 0
        side_final_matches = side_final_total = 0
        capital_at_matches = capital_at_total = 0
        capital_final_matches = capital_final_total = 0
        trade_decisions = skip_signals = 0
        latest: dict[str, Any] | None = None

        for decision in decisions:
            side = str(decision.get("side") or "")
            market_id = int(decision["market_id"])
            at_ms = int(decision["decision_at_ms"])
            target = by_market.get(market_id, [])
            at_decision = _residual(target, at_ms)
            final = _residual(target)
            trade_decisions += int(decision.get("decision") == "TRADE")
            skip_signals += int(decision.get("decision") == "SKIP")

            if at_decision["side"] in {"UP", "DOWN"}:
                side_at_total += 1
                side_at_matches += int(side == at_decision["side"])
            if final["side"] in {"UP", "DOWN"}:
                side_final_total += 1
                side_final_matches += int(side == final["side"])
            if at_decision["capitalSide"] in {"UP", "DOWN"}:
                capital_at_total += 1
                capital_at_matches += int(side == at_decision["capitalSide"])
            if final["capitalSide"] in {"UP", "DOWN"}:
                capital_final_total += 1
                capital_final_matches += int(side == final["capitalSide"])

            latest = {
                "marketId": market_id,
                "decisionAtMs": at_ms,
                "decision": decision.get("decision"),
                "side": side,
                "targetAtDecision": at_decision,
                "targetFinalSoFar": final,
            }

        result = {
            "windowDays": self.retention_days,
            "signalDecisions": len(decisions),
            "tradeSignals": trade_decisions,
            "skipSignals": skip_signals,
            "atDecisionComparable": side_at_total,
            "atDecisionMatches": side_at_matches,
            "atDecisionMatchRate": side_at_matches / side_at_total if side_at_total else None,
            "finalComparable": side_final_total,
            "finalMatches": side_final_matches,
            "finalMatchRate": side_final_matches / side_final_total if side_final_total else None,
            "capitalAtDecisionComparable": capital_at_total,
            "capitalAtDecisionMatches": capital_at_matches,
            "capitalAtDecisionMatchRate": capital_at_matches / capital_at_total if capital_at_total else None,
            "capitalFinalComparable": capital_final_total,
            "capitalFinalMatches": capital_final_matches,
            "capitalFinalMatchRate": capital_final_matches / capital_final_total if capital_final_total else None,
            "latest": latest,
            "note": "target Taker fills are diagnostic only and never drive the Spot/Strike decision",
        }
        self.forward_similarity_cache = dict(result)
        self.forward_similarity_cache_at_ms = now_ms
        return result

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        forward = payload.get("spotStrikeForward") if isinstance(payload.get("spotStrikeForward"), dict) else {}
        forward["availability"] = {
            "simulationDbAvailable": self.simulation_db is not None,
            "simulationDbPath": str(self.simulation_db_path),
            "simulationDbError": self.simulation_db_error,
        }
        forward["retention"] = {
            "days": self.retention_days,
            "bounded": True,
            "lastCleanupDeleted": dict(self.forward_last_cleanup_deleted),
            "policy": "Spot/Strike decisions, events, and settled results use the same rolling retention window as Wallet Shadow",
        }
        forward["targetSimilarity"] = self._forward_target_similarity()
        payload["spotStrikeForward"] = forward
        return payload


class _Handler(base._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_3Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"target={observer.wallet}; retention={v3.RETENTION_DAYS}d; "
        f"spotStrikeForward={v4_2.COHORT}; simulationDbAvailable={observer.simulation_db is not None}; "
        f"paper only; apiKeyConfigured={bool(observer.api_key)}; db={base.DB_PATH}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        observer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
