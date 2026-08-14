from __future__ import annotations

from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_maker_grid_strategy as maker_grid
from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v3 as v3
from . import predict_wallet_shadow_observer_v4_2 as spot
from . import predict_wallet_shadow_observer_v4_15 as v4_15
from . import predict_wallet_wide_maker_flow_strategy as wide

VERSION = "PREDICT_WALLET_SHADOW_V0_20_RETIRED_OBSOLETE_LABS"
LEGACY_BASE_COHORT = "LEGACY_SHADOW_PAPER_V0"
RETIRED_COHORTS = (
    LEGACY_BASE_COHORT,
    spot.COHORT,
    *(str(item["cohort"]) for item in maker_grid.COHORTS),
    *(str(item["cohort"]) for item in wide.COHORTS),
)


class WalletShadowObserver(v4_15.WalletShadowObserver):
    """V4.15 with obsolete research cohorts frozen as historical audit only.

    This intentionally keeps the inheritance chain and historical SQLite tables
    intact. Only the four explicitly retired research families are prevented from
    producing new paper events/orders/decisions. Newer Target Core, shared Maker,
    Reconstructed Maker V1/V2 and target-accounting research continue unchanged.
    """

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=None) -> None:
        if simulation_db_path is None:
            simulation_db_path = spot.SIMULATION_DB_PATH
        super().__init__(db_path, simulation_db_path)
        now_ms = base._now_ms()
        with self.db_lock:
            # This table already exists from V4.4; keep one canonical retirement
            # registry so restarts cannot accidentally reactivate an old cohort.
            self.db.executemany(
                """INSERT OR IGNORE INTO wallet_shadow_retired_cohorts(
                       cohort,retired_at_ms,historical_audit_preserved
                   ) VALUES (?,?,1)""",
                [(cohort, now_ms) for cohort in RETIRED_COHORTS],
            )
            self.db.commit()
        self._freeze_retired_runtime()

    def _retired_at(self, cohort: str) -> int | None:
        with self.db_lock:
            row = self.db.execute(
                "SELECT retired_at_ms FROM wallet_shadow_retired_cohorts WHERE cohort=?",
                (cohort,),
            ).fetchone()
        return int(row[0]) if row is not None else None

    # ------------------------------------------------------------------
    # Runtime kill switches. Dynamic dispatch means ancestor _advance_shadow
    # methods still run for newer cohorts, but calls to these retired helpers
    # resolve here and become no-ops.
    # ------------------------------------------------------------------
    def _append_shadow(self, **_kwargs: Any) -> None:
        # Retires the original Shadow Paper Performance event generator while
        # preserving target-wallet ingestion used by current research.
        return

    def _advance_spot_strike(self, _book: dict[str, Any]) -> None:
        return

    def _advance_maker_grids(self) -> None:
        return

    def _advance_wide_cohorts(self) -> None:
        return

    def _freeze_retired_runtime(self) -> None:
        now_ms = base._now_ms()
        if getattr(self, "maker_schema_ready", False):
            for item in maker_grid.COHORTS:
                cohort = str(item["cohort"])
                state = self.maker_states.get(cohort)
                if not isinstance(state, dict):
                    continue
                if state.get("orders"):
                    self._cancel_orders(cohort, "COHORT_RETIRED", now_ms)
                state["active"] = False
                state["retired"] = True
        if getattr(self, "wide_schema_ready", False):
            for item in wide.COHORTS:
                cohort = str(item["cohort"])
                state = self.wide_states.get(cohort)
                if not isinstance(state, dict):
                    continue
                if state.get("orders"):
                    self._cancel_wide_orders(cohort, "COHORT_RETIRED", now_ms)
                state["active"] = False
                state["retired"] = True
        # Do not write a fake Spot/Strike SKIP row. Historical DB rows remain
        # authoritative; runtime state simply stops creating a decision.
        self.forward_event = None
        self.forward_last_block = {
            "decision": "RETIRED",
            "reason": "COHORT_RETIRED_NO_NEW_FORWARD_DATA",
            "retiredAtMs": self._retired_at(spot.COHORT),
        }

    def _prune_empty_retired_market_rows(self) -> None:
        """Remove empty rows ancestor reset hooks create for retired labs.

        If a market already has pre-retirement fills/events, keep it so pending
        settlement can finish. New post-retirement markets have no fills because
        the runtime helpers above are disabled, so their placeholder rows vanish.
        """
        if self.market_id is None:
            return
        market_id = int(self.market_id)
        with self.db_lock:
            for item in maker_grid.COHORTS:
                cohort = str(item["cohort"])
                filled = self.db.execute(
                    "SELECT 1 FROM wallet_maker_grid_v1_orders WHERE cohort=? AND market_id=? AND status='FILLED' LIMIT 1",
                    (cohort, market_id),
                ).fetchone()
                if filled is None:
                    self.db.execute(
                        "DELETE FROM wallet_maker_grid_v1_orders WHERE cohort=? AND market_id=?",
                        (cohort, market_id),
                    )
                    self.db.execute(
                        "DELETE FROM wallet_maker_grid_v1_markets WHERE cohort=? AND market_id=?",
                        (cohort, market_id),
                    )
            for item in wide.COHORTS:
                cohort = str(item["cohort"])
                maker_fill = self.db.execute(
                    "SELECT 1 FROM wallet_wide_maker_flow_v1_orders WHERE cohort=? AND market_id=? AND status='FILLED' LIMIT 1",
                    (cohort, market_id),
                ).fetchone()
                taker_fill = self.db.execute(
                    "SELECT 1 FROM wallet_wide_maker_flow_v1_taker_events WHERE cohort=? AND market_id=? LIMIT 1",
                    (cohort, market_id),
                ).fetchone()
                if maker_fill is None and taker_fill is None:
                    self.db.execute(
                        "DELETE FROM wallet_wide_maker_flow_v1_decisions WHERE cohort=? AND market_id=?",
                        (cohort, market_id),
                    )
                    self.db.execute(
                        "DELETE FROM wallet_wide_maker_flow_v1_orders WHERE cohort=? AND market_id=?",
                        (cohort, market_id),
                    )
                    self.db.execute(
                        "DELETE FROM wallet_wide_maker_flow_v1_markets WHERE cohort=? AND market_id=?",
                        (cohort, market_id),
                    )
            self.db.commit()

    def _reset_market(self, market_id: int, bucket: int | None, title: str | None) -> None:
        super()._reset_market(market_id, bucket, title)
        self._freeze_retired_runtime()
        self._prune_empty_retired_market_rows()

    # These methods are still called while parent snapshots are assembled. Keep
    # them cheap so retired labs no longer contribute large historical scans to
    # the already expensive 8776 full report.
    def _maker_performance(self, cohort: str) -> dict[str, Any]:
        return {
            "status": "RETIRED_NO_NEW_DATA",
            "cohort": cohort,
            "retiredAtMs": self._retired_at(cohort),
            "historicalAuditPreserved": True,
        }

    def _wide_performance(self, cohort: str) -> dict[str, Any]:
        return {
            "status": "RETIRED_NO_NEW_DATA",
            "cohort": cohort,
            "retiredAtMs": self._retired_at(cohort),
            "historicalAuditPreserved": True,
        }

    def _forward_performance(self) -> dict[str, Any]:
        return {
            "status": "RETIRED_NO_NEW_DATA",
            "retired": True,
            "retiredAtMs": self._retired_at(spot.COHORT),
            "historicalAuditPreserved": True,
            "events": 0,
            "decisions": 0,
            "pendingMarkets": 0,
            "storedRows": {},
        }

    def _performance_snapshot(self) -> dict[str, Any]:
        # The original Shadow Paper Performance is historical-only. Do not run
        # its rolling aggregation in every full report anymore.
        return {
            "status": "RETIRED_NO_NEW_DATA",
            "retired": True,
            "retiredAtMs": self._retired_at(LEGACY_BASE_COHORT),
            "historicalAuditPreserved": True,
            "windowDays": self.retention_days,
            "settledMarkets": 0,
            "tradedMarkets": 0,
            "wins": 0,
            "losses": 0,
            "flats": 0,
            "pendingSettlementMarkets": 0,
            "recentMarkets": [],
            "storedRows": {},
            "paperFillModel": "RETIRED: original inferred Maker-fill / divergence-Taker proxy no longer produces forward events",
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        # Remove retired standalone strategy containers from current Research Lab
        # exports. Their SQLite history is intentionally not deleted.
        payload.pop("makerGridDepthLab", None)
        payload.pop("wideMakerFlowTailLab", None)
        payload.pop("spotStrikeForward", None)
        payload["retiredResearchLabs"] = {
            "status": "HISTORICAL_AUDIT_ONLY",
            "historicalDataDeleted": False,
            "newForwardData": False,
            "cohorts": [
                {"cohort": cohort, "retiredAtMs": self._retired_at(cohort)}
                for cohort in RETIRED_COHORTS
            ],
            "families": [
                "Shadow Paper Performance",
                "Maker Grid Depth Lab 3/7/15",
                "Wide Maker + Maker Flow Alpha + Tail Insurance",
                "Spot/Strike T-10s Forward Cohort",
            ],
        }
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        payload["retiredLegacyLabCount"] = len(RETIRED_COHORTS)
        payload["retiredLegacyLabsProduceNewData"] = False
        return payload


class _Handler(v4_15._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_16Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        "obsoleteLabsRetired=true; historicalAuditPreserved=true; paperOnly=true; liveOrdersAffected=false",
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
