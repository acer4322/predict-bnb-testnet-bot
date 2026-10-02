from __future__ import annotations

from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v4 as v4
from . import echtgeld_engine_v5 as v5
from . import echtgeld_engine_v10 as v10
from . import echtgeld_engine_v18 as v18

VERSION = "ECHTGELD_ENGINE_V2_4310_POLY_V16_GHOST_ENTRY_PENDING_FIX"
HOST = v18.HOST
PORT = v18.PORT


class EchtgeldEngine(v18.EchtgeldEngine):
    """V18 4310 state machine with correct multi-strategy entry sequencing.

    V18 deliberately bypassed V6's inconsistent ACTIVE_ROUND_EXISTS SQL fence,
    but it also bypassed V10's Poly GAP/PINNED strategy adapter and inserted the
    engine_poly_rounds row before the durable intent was accepted. A GAP intent
    could therefore fail V4 validation after leaving a row with no engine_intent
    and no engine_order. The lifecycle correctly rendered that orphan as
    ENTRY_PENDING, which then fenced the asset forever.

    V19 keeps the restored V18/4310 exit behavior unchanged. It only restores the
    V10 strategy adapter around the V5/V4 mature execution path and creates the
    Poly round *after* the durable intent has actually been accepted/queued.
    Startup also removes only impossible orphan rows that have neither an intent
    nor an order, which by construction cannot represent a venue write.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.ghost_poly_rounds_pruned = self._prune_ghost_poly_rounds()

    def _prune_ghost_poly_rounds(self) -> int:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT r.entry_intent_id,r.round_id,r.asset,r.market_id
                     FROM engine_poly_rounds r
                     LEFT JOIN engine_intents i ON i.intent_id=r.entry_intent_id
                     LEFT JOIN engine_orders o ON o.intent_id=r.entry_intent_id
                    WHERE i.intent_id IS NULL AND o.intent_id IS NULL"""
            ).fetchall()
            if rows:
                self.db.execute(
                    """DELETE FROM engine_poly_rounds
                         WHERE entry_intent_id IN (
                             SELECT r.entry_intent_id
                               FROM engine_poly_rounds r
                               LEFT JOIN engine_intents i ON i.intent_id=r.entry_intent_id
                               LEFT JOIN engine_orders o ON o.intent_id=r.entry_intent_id
                              WHERE i.intent_id IS NULL AND o.intent_id IS NULL
                         )"""
                )
                self.db.commit()
        return len(rows)

    def submit_poly_fast_intent(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise v1.EchtgeldEngineError("Poly Fast intent must be a JSON object")

        # Remove only impossible no-intent/no-order rows before consulting the
        # authoritative V18 lifecycle. This performs no venue read or write.
        self.ghost_poly_rounds_pruned += self._prune_ghost_poly_rounds()

        round_id = str(raw.get("roundId") or "").strip()
        token_id = str(raw.get("tokenId") or "").strip()
        asset = str(raw.get("asset") or "").strip().upper()
        side = str(raw.get("side") or "").strip().upper()
        market_id = int(v1._finite(raw.get("marketId")) or 0)
        created_at_ms = int(v1._finite(raw.get("createdAtMs")) or 0)
        fee_rate_bps = int(v1._finite(raw.get("feeRateBps")) or 200)
        supplied = str(raw.get("strategy") or v10.GAP_STRATEGY).strip()

        if asset == "BNB":
            return {
                "ok": True,
                "accepted": False,
                "queued": False,
                "status": "ASSET_DISABLED",
                "asset": asset,
                "message": "BNB new Poly entries are temporarily disabled in 8781; existing BNB exits remain allowed",
            }
        if asset not in {"BTC", "ETH"}:
            raise v1.EchtgeldEngineError("Poly Fast asset must be BTC or ETH for new entry")
        if side not in {"UP", "DOWN"}:
            raise v1.EchtgeldEngineError("Poly Fast side must be UP or DOWN")
        if supplied not in v10.POLY_STRATEGIES:
            raise v1.EchtgeldEngineError(f"unsupported Poly Fast strategy: {supplied}")
        if not round_id or not token_id or market_id <= 0 or created_at_ms <= 0:
            raise v1.EchtgeldEngineError("Poly Fast roundId, tokenId, marketId and createdAtMs are required")

        active = self._poly_active_round(asset)
        if active is not None and str(active.get("round_id") or "") != round_id:
            return {
                "ok": True,
                "accepted": False,
                "queued": False,
                "status": "ACTIVE_ROUND_EXISTS",
                "message": "Poly Fast has a genuinely active/potential round; wait for confirmed flat before re-entry",
                "activeRoundId": str(active.get("round_id") or ""),
                "activePhase": active.get("phase"),
            }

        # Restore V10's mature strategy adaptation, while intentionally calling
        # V5 directly so V6's old same-market SQL admission fence stays bypassed.
        # Nothing is inserted into engine_poly_rounds until V1 has durably
        # accepted and queued the intent.
        with v10.EchtgeldEngine._poly_strategy_lock:
            previous = v4.POLY_FAST_STRATEGY
            v4.POLY_FAST_STRATEGY = supplied
            try:
                result = v5.EchtgeldEngine.submit_poly_fast_intent(self, raw)
            finally:
                v4.POLY_FAST_STRATEGY = previous

        status = str(result.get("status") or "").upper()
        accepted = result.get("accepted") is True
        durable_intent_exists = False
        intent_id = str(raw.get("intentId") or "").strip()
        if intent_id:
            with self.db_lock:
                durable_intent_exists = (
                    self.db.execute(
                        "SELECT 1 FROM engine_intents WHERE intent_id=?",
                        (intent_id,),
                    ).fetchone()
                    is not None
                )

        # Normally this is QUEUED. The durable existence check also makes an
        # exact idempotent duplicate safe if the HTTP response was lost after the
        # intent was persisted. Ignored/invalid requests never create a round.
        should_attach_round = bool(
            durable_intent_exists
            and (
                accepted
                or status in {
                    "QUEUED",
                    "PROCESSING",
                    "ATTEMPTING",
                    "SUBMITTED",
                    "AMBIGUOUS",
                    "REJECTED",
                    "DUPLICATE_FENCE",
                }
            )
        )
        if should_attach_round:
            with self.db_lock:
                self.db.execute(
                    """INSERT OR IGNORE INTO engine_poly_rounds(
                           entry_intent_id,round_id,asset,market_id,side,token_id,fee_rate_bps,created_at_ms
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (intent_id, round_id, asset, market_id, side, token_id, fee_rate_bps, created_at_ms),
                )
                self.db.commit()

        result["roundId"] = round_id
        result["sourceStrategy"] = supplied
        result["poly4310RoundStateMachine"] = True
        result["ghostEntryPendingFix"] = True
        result["roundAttached"] = should_attach_round
        return result

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        gateway = payload.get("polyFastGateway")
        if isinstance(gateway, dict):
            gateway.update(
                ghostEntryPendingFix=True,
                polyRoundCreatedAfterDurableIntentAcceptance=True,
                v10MultiStrategyAdapterRestored=True,
                ghostRoundsPruned=int(self.ghost_poly_rounds_pruned),
            )
        payload["polyGhostEntryPendingV19"] = {
            "enabled": True,
            "ghostRoundsPruned": int(self.ghost_poly_rounds_pruned),
            "roundCreationOrder": "DURABLE_INTENT_ACCEPTED_THEN_POLY_ROUND",
            "strategyAdapter": "V10_GAP_PLUS_PINNED",
            "v18ExitStateMachineUnchanged": True,
        }
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["polyGhostEntryPendingFix"] = True
        payload["polyRoundCreatedAfterDurableIntentAcceptance"] = True
        payload["polyV10MultiStrategyAdapterRestored"] = True
        payload["ghostPolyRoundsPruned"] = int(self.ghost_poly_rounds_pruned)
        return payload


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV19Handler", (v18.v6._Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; "
        "V18 4310 exit state machine unchanged; GAP/PINNED V10 adapter restored; "
        "Poly round is created only after durable intent acceptance; ghost ENTRY_PENDING rows pruned",
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
