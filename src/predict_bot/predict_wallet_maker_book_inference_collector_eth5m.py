from __future__ import annotations

import copy
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any


# Set the isolated ETH cohort identity and shared official target truth source
# before importing the generic collectors. 8776 is the single target-fill
# producer; 8779 never polls /v1/orders/matches directly.
os.environ["PREDICT_WALLET_MAKER_BOOK_ASSET"] = "ETH"
os.environ["PREDICT_WALLET_MAKER_BOOK_PORT"] = "8779"
root = Path(__file__).resolve().parents[2]
os.environ["PREDICT_WALLET_MAKER_BOOK_DB"] = os.environ.get(
    "PREDICT_WALLET_MAKER_BOOK_ETH5M_DB",
    str(root / "data" / "wallet_maker_book_inference_eth5m.db"),
)
os.environ["PREDICT_WALLET_SHADOW_DB"] = os.environ.get(
    "PREDICT_TARGET_WALLET_OFFICIAL_DB",
    str(root / "data" / "target_wallet_official_v1.db"),
)
os.environ.setdefault(
    "PREDICT_WALLET_MAKER_BOOK_STATE_CACHE_REFRESH_MS",
    os.environ.get("PREDICT_WALLET_MAKER_BOOK_ETH_STATE_CACHE_REFRESH_MS", "30000"),
)

from . import predict_wallet_maker_book_inference_collector as base  # noqa: E402
from . import predict_wallet_maker_book_inference_collector_v2_1_impl as lifecycle  # noqa: E402
from . import predict_wallet_maker_book_inference_collector_v2_1_responsive as responsive  # noqa: E402


VERSION = "TARGET_MAKER_BOOK_INFERENCE_ETH_5M_V2_1_CONSUMABLE_LIFECYCLE_FORWARD_ONLY"


class OfficialLedgerEthMakerCollector(responsive.ResponsiveMakerBookConsumableLifecycleCollector):
    """ETH target activity plus BTC-parity consumable Maker lifecycle inference.

    All retained ETH MAKER/TAKER legs from 8776 are recorded in targetActivity.
    Only MAKER BID entry legs are projected onto the anonymous public order book
    and are therefore eligible for Maker lifecycle/depth inference.
    """

    def __init__(
        self,
        db_path: Path = base.DB_PATH,
        target_db_path: Path = base.TARGET_DB_PATH,
    ) -> None:
        super().__init__(db_path, target_db_path)
        self.version = VERSION

    def _target_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._ingest_new_target_events()
                self._match_pending_events()
            except Exception as exc:
                self.last_error = f"target-ledger: {str(exc)[:350]}"
            self.stop_event.wait(0.5)

    def _ingest_new_target_events(self) -> None:
        """Retain both roles from 8776 while routing only Maker BID to inference."""

        if not self.target_db_path.exists():
            return

        meta = self._meta()
        cursor_rowid = int(meta.get("last_target_rowid") or 0)
        deployed_at = int(meta.get("deployed_at_ms") or self.started_at_ms)
        excluded = base.positive_int(meta.get("excluded_market_id"))

        con = sqlite3.connect(
            f"file:{self.target_db_path.resolve()}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                """SELECT rowid target_rowid,leg_id,wallet,market_id,order_hash,event_ms,
                          role,quote_type,side,price,shares
                     FROM wallet_shadow_target_events
                    WHERE rowid>? ORDER BY rowid LIMIT 2000""",
                (cursor_rowid,),
            ).fetchall()
            if not rows:
                return

            context: dict[str, int] = {}
            leg_ids = [str(row["leg_id"]) for row in rows]
            for start in range(0, len(leg_ids), 500):
                batch = leg_ids[start : start + 500]
                placeholders = ",".join("?" for _ in batch)
                try:
                    context_rows = con.execute(
                        f"""SELECT leg_id,observed_at_ms
                              FROM wallet_shadow_target_event_context
                             WHERE leg_id IN ({placeholders})""",
                        batch,
                    )
                    for context_row in context_rows:
                        if context_row["observed_at_ms"] is not None:
                            context[str(context_row["leg_id"])] = int(
                                context_row["observed_at_ms"]
                            )
                except sqlite3.OperationalError:
                    break
        finally:
            con.close()

        with self.db_lock:
            precision_by_market = {
                int(row["market_id"]): int(row["decimal_precision"])
                for row in self.db.execute(
                    "SELECT market_id,decimal_precision FROM maker_book_inference_markets"
                )
            }

        maximum_rowid = cursor_rowid
        wallet_events: list[tuple[Any, ...]] = []
        maker_sources: list[tuple[str, int, str, int]] = []
        grouped: dict[tuple[int, str, int, str, float], dict[str, Any]] = {}

        for row in rows:
            maximum_rowid = max(maximum_rowid, int(row["target_rowid"]))
            leg_id = str(row["leg_id"] or "")
            wallet = str(row["wallet"] or "").lower()
            market_id = int(row["market_id"] or 0)
            event_ms = int(row["event_ms"] or 0)
            role = str(row["role"] or "").upper()
            quote_type = str(row["quote_type"] or "").upper()
            side = str(row["side"] or "").upper()
            price = base.finite(row["price"])
            shares = base.finite(row["shares"])

            if (
                not leg_id
                or wallet != base.TARGET_WALLET
                or event_ms < deployed_at
                or market_id == excluded
                or market_id not in precision_by_market
                or role not in {"MAKER", "TAKER"}
                or quote_type not in {"BID", "ASK"}
                or side not in {"UP", "DOWN"}
                or price is None
                or shares is None
                or shares <= 0
            ):
                continue

            observed_ms = int(context.get(leg_id) or event_ms)
            wallet_events.append(
                (
                    leg_id,
                    base.TARGET_WALLET,
                    market_id,
                    role,
                    quote_type,
                    side,
                    row["order_hash"],
                    event_ms,
                    observed_ms,
                    float(price),
                    float(shares),
                )
            )

            # Taker activity and Maker ASK/reduction activity remain visible in
            # targetActivity, but never become anonymous resting-order matches.
            if role != "MAKER" or quote_type != "BID":
                continue

            parent = str(row["order_hash"] or leg_id)
            key = (market_id, parent, event_ms, side, float(price))
            group = grouped.setdefault(
                key,
                {
                    "targetRowId": int(row["target_rowid"]),
                    "orderHash": str(row["order_hash"] or "") or None,
                    "observedMs": observed_ms,
                    "shares": 0.0,
                    "sourceLegIds": [],
                },
            )
            group["targetRowId"] = max(
                int(group["targetRowId"]),
                int(row["target_rowid"]),
            )
            group["observedMs"] = max(
                int(group.get("observedMs") or 0),
                observed_ms,
            )
            group["shares"] = float(group["shares"]) + float(shares)
            group["sourceLegIds"].append(leg_id)

        maker_events: list[tuple[Any, ...]] = []
        for (market_id, parent, event_ms, side, price), group in grouped.items():
            native_side, native_price = base.native_level_for_target(
                side,
                price,
                precision_by_market[market_id],
            )
            event_id = f"{parent}:{event_ms}:{side}:{price:.12g}"
            maker_events.append(
                (
                    event_id,
                    int(group["targetRowId"]),
                    base.TARGET_WALLET,
                    market_id,
                    group["orderHash"],
                    event_ms,
                    int(group["observedMs"]),
                    side,
                    price,
                    float(group["shares"]),
                    native_side,
                    native_price,
                    "PENDING",
                )
            )
            for source_leg_id in group["sourceLegIds"]:
                maker_sources.append(
                    (
                        str(source_leg_id),
                        market_id,
                        event_id,
                        int(group["observedMs"]),
                    )
                )

        with self.db_lock:
            if wallet_events:
                self.db.executemany(
                    """INSERT OR IGNORE INTO maker_book_inference_wallet_events(
                           source_leg_id,wallet,market_id,role,quote_type,side,order_hash,
                           event_ms,observed_at_ms,price,shares
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    wallet_events,
                )
            if maker_sources:
                self.db.executemany(
                    """INSERT OR IGNORE INTO maker_book_inference_source_legs(
                           source_leg_id,market_id,target_event_id,observed_at_ms
                       ) VALUES (?,?,?,?)""",
                    maker_sources,
                )
            if maker_events:
                self.db.executemany(
                    """INSERT INTO maker_book_inference_target_events(
                           leg_id,target_rowid,wallet,market_id,order_hash,target_event_ms,target_observed_ms,
                           side,target_price,target_shares,native_book_side,native_price,status
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(leg_id) DO UPDATE SET
                           target_rowid=MAX(target_rowid,excluded.target_rowid),
                           target_observed_ms=CASE
                               WHEN target_observed_ms IS NULL THEN excluded.target_observed_ms
                               WHEN excluded.target_observed_ms IS NULL THEN target_observed_ms
                               ELSE MAX(target_observed_ms,excluded.target_observed_ms)
                           END,
                           target_shares=target_shares+excluded.target_shares""",
                    maker_events,
                )
            self.db.execute(
                "UPDATE maker_book_inference_meta SET last_target_rowid=? WHERE cohort=?",
                (maximum_rowid, self.cohort),
            )
            self.db.commit()

        self.target_events_seen_run += len(wallet_events)

    def _advance_v21(self) -> None:
        """Run the same consumable lifecycle reconciliation used by BTC 8778."""

        if self.asset != "ETH":
            return
        now = base.now_ms()
        current = int(self.current_market_id) if self.current_market_id is not None else None
        if (
            current is not None
            and now - self.v21_last_current_ms >= lifecycle.RECONCILE_CURRENT_MS
        ):
            self.v21_last_current_ms = now
            self._reconcile_market(current)

        if now - self.v21_last_backlog_ms < lifecycle.RECONCILE_BACKLOG_MS:
            return
        self.v21_last_backlog_ms = now
        backlog = [market for market in self._backlog_markets() if market != current]
        if backlog:
            self._reconcile_market(backlog[0])

    @staticmethod
    def _decorate_eth_snapshot(reader: Any, payload: dict[str, Any]) -> dict[str, Any]:
        payload["version"] = VERSION
        payload["roleSeparation"] = {
            "MAKER": "retained; MAKER BID entries feed public-book lifecycle/depth inference",
            "TAKER": "retained separately in targetActivity; excluded from Maker lifecycle/depth inference",
        }
        lifecycle_snapshot = lifecycle.MakerBookConsumableLifecycleCollector._v21_snapshot(reader)
        lifecycle_snapshot["version"] = VERSION
        lifecycle_snapshot["asset"] = "ETH"
        payload["lifecycleInference"] = lifecycle_snapshot
        payload["lifecycleInferenceV2Retired"] = {
            "status": "RETIRED_MANY_TO_ONE_EVIDENCE_REUSE",
            "historicalTablesPreserved": True,
        }
        return payload

    def _build_snapshot_from_reader(self) -> dict[str, Any]:
        """Build ETH summaries off the writer lock and cache them for HTTP polls."""
        uri = f"file:{self.db_path.resolve()}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=1.0, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA query_only=ON")
            con.execute("PRAGMA busy_timeout=1000")
            reader = copy.copy(self)
            reader.db = con
            reader.db_lock = threading.RLock()
            reader.v21_cache = None
            reader.v21_cache_at = 0
            payload = base.MakerBookInferenceCollector.snapshot(reader)
            return self._decorate_eth_snapshot(reader, payload)
        finally:
            con.close()

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        payload["ordersSupported"] = False
        payload["liveOrdersAffected"] = False
        payload["targetEventsDriveStrategy"] = False
        payload["roleSeparation"] = {
            "MAKER": "retained; MAKER BID entries feed public-book lifecycle/depth inference",
            "TAKER": "retained separately in targetActivity; excluded from Maker lifecycle/depth inference",
        }
        lifecycle_state = payload.get("lifecycleInference")
        if isinstance(lifecycle_state, dict):
            lifecycle_state["version"] = VERSION
            lifecycle_state["asset"] = "ETH"
        return payload

    def snapshot(self) -> dict[str, Any]:
        return responsive.ResponsiveMakerBookConsumableLifecycleCollector.snapshot(self)


def main() -> int:
    collector = OfficialLedgerEthMakerCollector(base.DB_PATH, base.TARGET_DB_PATH)
    collector.start()
    handler = type(
        "MakerBookInferenceEth5mV21Handler",
        (responsive.ResponsiveHandler,),
        {"collector": collector},
    )
    server = base.ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"{VERSION} listening on http://{base.HOST}:{base.PORT}/state; asset=ETH; "
        f"targetSource={base.TARGET_DB_PATH}; makerTakerSeparated=true; "
        "consumableQuantityAllocation=true; responsiveHealth=true; cachedState=true; "
        "readOnly=true; liveOrdersAffected=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        collector.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
