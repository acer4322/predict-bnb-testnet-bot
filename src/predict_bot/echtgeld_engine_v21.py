from __future__ import annotations

import os
import sqlite3
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v2 as v2
from . import echtgeld_engine_v6 as v6
from . import echtgeld_engine_v20 as v20

VERSION = "ECHTGELD_ENGINE_V2_4310_POLY_V18_CURRENT_TARGET_SETTLEMENT_AUTHORITY"
HOST = v20.HOST
PORT = v20.PORT
ROOT = Path(__file__).resolve().parents[2]
TARGET_OFFICIAL_DB_PATH = Path(
    os.environ.get("PREDICT_TARGET_WALLET_OFFICIAL_DB")
    or ROOT / "data" / "target_wallet_official_v1.db"
)
TARGET_SETTLEMENT_SYNC_INTERVAL_MS = 2_000
TARGET_SETTLEMENT_SOURCE = "target_wallet_official_v1.target_markets"


class EchtgeldEngine(v20.EchtgeldEngine):
    """V20 plus current Target Wallet Official settlement authority for live PnL.

    The original V2 stop-loss joined Echtgeld orders against
    predict_wallet_shadow.db.wallet_target_taker_public_side_v1_results.  That
    paper-result table is now historical and stopped advancing while the current
    Target Taker producer continued creating new Predict.fun market ids.  Real
    Binance fills could therefore be durably SUBMITTED yet remain absent from
    Target Taker settled PnL forever.

    The current Target Wallet Official service already owns a durable, continuously
    updated `target_markets` table keyed by the exact Predict.fun source market_id
    carried by Echtgeld Target Taker intents.  This layer imports only SETTLED rows
    with an explicit UP/DOWN winner for market ids that actually exist in the
    Echtgeld Target Taker ledger.  No future/outcome guess is made.  Historical
    engine_settlements rows are preserved, so the old paper source remains a
    durable legacy fallback without being consulted for current risk decisions.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        target_path = kwargs.pop("target_official_db_path", TARGET_OFFICIAL_DB_PATH)
        self.target_official_db_path = Path(target_path)
        self.target_settlement_sync_last_ms = 0
        self.target_settlement_sync_state: dict[str, Any] = {
            "status": "NOT_SYNCED",
            "source": str(self.target_official_db_path),
            "table": "target_markets",
            "asOfMs": None,
            "requestedMarkets": 0,
            "resolvedMarkets": 0,
            "importedRows": 0,
            "latestResolvedAtMs": None,
            "error": None,
        }
        super().__init__(*args, **kwargs)

    @staticmethod
    def _readonly_target_db(path: Path) -> sqlite3.Connection:
        resolved = path.expanduser().resolve().as_posix()
        db = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True, timeout=2.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=2000")
        return db

    def _target_taker_markets(self) -> dict[int, set[str]]:
        """Return exact source market ids/cohorts for non-Poly Echtgeld orders."""
        with self.db_lock:
            rows = self.db.execute(
                """SELECT market_id,cohort
                     FROM engine_orders
                    WHERE intent_id NOT LIKE 'poly-fast:%'
                      AND market_id>0
                    GROUP BY market_id,cohort"""
            ).fetchall()
        result: dict[int, set[str]] = {}
        for row in rows:
            market_id = int(row["market_id"] or 0)
            cohort = str(row["cohort"] or "").strip()
            if market_id > 0 and cohort:
                result.setdefault(market_id, set()).add(cohort)
        return result

    def _sync_settlements(self, *, force: bool = False) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        if (
            not force
            and self.target_settlement_sync_last_ms > 0
            and now_ms - self.target_settlement_sync_last_ms < TARGET_SETTLEMENT_SYNC_INTERVAL_MS
        ):
            # Keep V2's public state field coherent with the current authority.
            self.settlement_sync_state = dict(self.target_settlement_sync_state)
            return dict(self.target_settlement_sync_state)
        self.target_settlement_sync_last_ms = now_ms

        markets = self._target_taker_markets()
        market_ids = sorted(markets)
        if not self.target_official_db_path.exists():
            self.target_settlement_sync_state = {
                "status": "UNAVAILABLE",
                "source": str(self.target_official_db_path),
                "table": "target_markets",
                "asOfMs": now_ms,
                "requestedMarkets": len(market_ids),
                "resolvedMarkets": 0,
                "importedRows": 0,
                "latestResolvedAtMs": None,
                "error": "Target Wallet Official DB does not exist",
            }
            self.settlement_sync_state = dict(self.target_settlement_sync_state)
            return dict(self.target_settlement_sync_state)

        source: sqlite3.Connection | None = None
        resolved_rows: list[sqlite3.Row] = []
        try:
            source = self._readonly_target_db(self.target_official_db_path)
            table = source.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='target_markets'"
            ).fetchone()
            if table is None:
                raise RuntimeError("target_markets table is unavailable")
            columns = {str(row[1]) for row in source.execute("PRAGMA table_info(target_markets)")}
            required = {"market_id", "status", "winner", "resolved_at_ms"}
            missing = sorted(required - columns)
            if missing:
                raise RuntimeError("target_markets missing columns: " + ", ".join(missing))

            # Query only markets ever attempted by the standalone Echtgeld Target
            # Taker path.  Chunking avoids SQLite variable limits as the ledger grows.
            for offset in range(0, len(market_ids), 400):
                chunk = market_ids[offset : offset + 400]
                if not chunk:
                    continue
                placeholders = ",".join("?" for _ in chunk)
                resolved_rows.extend(
                    source.execute(
                        f"""SELECT market_id,winner,resolved_at_ms
                              FROM target_markets
                             WHERE market_id IN ({placeholders})
                               AND status='SETTLED'
                               AND winner IN ('UP','DOWN')""",
                        chunk,
                    ).fetchall()
                )

            inserts: list[tuple[Any, ...]] = []
            latest_resolved: int | None = None
            for row in resolved_rows:
                market_id = int(row["market_id"])
                winner = str(row["winner"] or "").upper()
                resolved_at_ms = int(row["resolved_at_ms"] or 0)
                if winner not in {"UP", "DOWN"} or resolved_at_ms <= 0:
                    continue
                latest_resolved = max(latest_resolved or 0, resolved_at_ms)
                for cohort in sorted(markets.get(market_id, ())):
                    inserts.append(
                        (
                            cohort,
                            market_id,
                            winner,
                            resolved_at_ms,
                            TARGET_SETTLEMENT_SOURCE,
                            now_ms,
                        )
                    )

            with self.db_lock:
                if inserts:
                    # Current official source wins only by exact (cohort,market_id).
                    # It never deletes historical settlements for unrelated markets.
                    self.db.executemany(
                        """INSERT INTO engine_settlements(
                               cohort,market_id,winner,resolved_at_ms,source,synced_at_ms
                           ) VALUES (?,?,?,?,?,?)
                           ON CONFLICT(cohort,market_id) DO UPDATE SET
                               winner=excluded.winner,
                               resolved_at_ms=excluded.resolved_at_ms,
                               source=excluded.source,
                               synced_at_ms=excluded.synced_at_ms""",
                        inserts,
                    )
                    self.db.commit()

            self.target_settlement_sync_state = {
                "status": "OK",
                "source": str(self.target_official_db_path),
                "table": "target_markets",
                "authority": TARGET_SETTLEMENT_SOURCE,
                "asOfMs": now_ms,
                "requestedMarkets": len(market_ids),
                "resolvedMarkets": len({int(row["market_id"]) for row in resolved_rows}),
                "importedRows": len(inserts),
                "latestResolvedAtMs": latest_resolved,
                "error": None,
                "winnerPolicy": "SETTLED + explicit UP/DOWN only",
                "legacySettlementRowsPreserved": True,
            }
        except Exception as exc:
            self.target_settlement_sync_state = {
                "status": "UNAVAILABLE",
                "source": str(self.target_official_db_path),
                "table": "target_markets",
                "authority": TARGET_SETTLEMENT_SOURCE,
                "asOfMs": now_ms,
                "requestedMarkets": len(market_ids),
                "resolvedMarkets": 0,
                "importedRows": 0,
                "latestResolvedAtMs": None,
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                "legacySettlementRowsPreserved": True,
            }
        finally:
            if source is not None:
                source.close()

        self.settlement_sync_state = dict(self.target_settlement_sync_state)
        return dict(self.target_settlement_sync_state)

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        payload["targetTakerSettlementAuthority"] = dict(self.target_settlement_sync_state)
        payload["settlementSync"] = dict(self.target_settlement_sync_state)
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["targetTakerCurrentSettlementAuthority"] = True
        payload["targetTakerSettlementDbPath"] = str(self.target_official_db_path)
        payload["targetTakerSettlementTable"] = "target_markets"
        payload["targetTakerSettlementWinnerRequiresSettled"] = True
        return payload


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV21Handler", (v6._Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; "
        "Target Taker settled PnL uses current target_wallet_official_v1.target_markets; "
        "generic AMBIGUOUS fills reconcile read-only; stop-loss consumes combined durable PnL",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
