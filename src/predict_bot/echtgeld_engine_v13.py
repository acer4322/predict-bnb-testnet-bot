from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v6 as v6
from . import echtgeld_engine_v12 as v12

VERSION = "ECHTGELD_ENGINE_V2_4310_POLY_V10_TOKEN_REPAIR_EXPIRED_DETACH"
HOST = v12.HOST
PORT = v12.PORT
EXPIRED_DETACH_GRACE_MS = 2_000


class EchtgeldEngine(v12.EchtgeldEngine):
    """V12 plus durable token repair and expired-round execution detachment.

    V9 legacy repair could overwrite a valid engine_poly_rounds.token_id with an
    empty string when no redeem row existed yet.  Once 4310 order-history sync
    later promoted the BUY to SUBMITTED, the round became OPEN with token_id=''.
    8792 then repeatedly attempted risk-reducing exits that could never query the
    Binance position and, because one-active-round is asset scoped, BTC/ETH new
    markets were blocked indefinitely.

    This layer repairs token_id from the original persisted Poly intent payload
    (the authoritative token selected at signal time), never blanks a non-empty
    token, and detaches already-expired *confirmed SUBMITTED* 5m rounds from the
    active execution lifecycle.  Detached rounds remain in the durable ledger and
    continue through 4310 claim/redeem + PnL/stop-loss settlement accounting.
    No order is placed by this repair path.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.poly_token_repairs = self._repair_poly_token_ids()
        self.expired_round_detaches = 0
        self.last_expired_detach: dict[str, Any] | None = None

    @staticmethod
    def _payload_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
        snapshot = payload.get("snapshot")
        return snapshot if isinstance(snapshot, dict) else {}

    def _intent_payload(self, intent_id: str) -> dict[str, Any]:
        with self.db_lock:
            row = self.db.execute(
                "SELECT payload_json FROM engine_intents WHERE intent_id=?",
                (str(intent_id),),
            ).fetchone()
        if row is None:
            return {}
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _repair_poly_token_ids(self) -> int:
        repaired = 0
        with self.db_lock:
            rows = self.db.execute(
                """SELECT entry_intent_id,token_id
                     FROM engine_poly_rounds
                    WHERE COALESCE(token_id,'')=''"""
            ).fetchall()
            for raw in rows:
                intent_id = str(raw["entry_intent_id"] or "")
                intent = self.db.execute(
                    "SELECT payload_json FROM engine_intents WHERE intent_id=?",
                    (intent_id,),
                ).fetchone()
                if intent is None:
                    continue
                try:
                    payload = json.loads(str(intent["payload_json"] or "{}"))
                except Exception:
                    payload = {}
                snapshot = self._payload_snapshot(payload if isinstance(payload, dict) else {})
                token_id = str(
                    (payload.get("tokenId") if isinstance(payload, dict) else None)
                    or snapshot.get("tokenId")
                    or ""
                ).strip()
                if not token_id:
                    continue
                self.db.execute(
                    """UPDATE engine_poly_rounds
                          SET token_id=?
                        WHERE entry_intent_id=? AND COALESCE(token_id,'')=''""",
                    (token_id, intent_id),
                )
                repaired += 1
            self.db.commit()
        return repaired

    def _round_window_end_ms(self, active: dict[str, Any]) -> int | None:
        payload = self._intent_payload(str(active.get("entry_intent_id") or ""))
        snapshot = self._payload_snapshot(payload)
        for source in (payload, snapshot):
            value = v1._finite(source.get("windowEndMs"))
            if value is not None and value > 0:
                return int(value)
        for source in (payload, snapshot):
            bucket = v1._finite(source.get("bucketStartSec"))
            if bucket is not None and bucket > 0:
                return int(bucket * 1000 + 300_000)
        created = v1._finite(active.get("created_at_ms"))
        if created is not None and created > 0:
            start = (int(created) // 300_000) * 300_000
            return start + 300_000
        return None

    def _poly_active_round(self, asset: str) -> dict[str, Any] | None:
        active = super()._poly_active_round(asset)
        if not active:
            return None

        # V13 never lets an empty-token round enter the executable exit path.
        # A repaired current round is returned normally; an unrecoverable empty
        # token is fail-closed and surfaced as non-executable diagnostics.
        token_id = str(active.get("token_id") or "").strip()
        order_status = str(active.get("order_status") or "").upper()
        end_ms = self._round_window_end_ms(active)
        now_ms = v1._now_ms()

        # Once a BUY is positively reconciled as SUBMITTED/FILLED and its 5m
        # market has expired, it is no longer an active execution round.  Do not
        # send SELLs against a resolved market and do not block the next market.
        # The durable row is intentionally retained for claim/redeem settlement.
        if (
            order_status == "SUBMITTED"
            and end_ms is not None
            and now_ms >= end_ms + EXPIRED_DETACH_GRACE_MS
            and str(active.get("exit_status") or "").upper() not in {"ATTEMPTING", "SUBMITTED", "AMBIGUOUS"}
        ):
            self.expired_round_detaches += 1
            self.last_expired_detach = {
                "asset": str(asset).upper(),
                "roundId": active.get("round_id"),
                "marketId": active.get("market_id"),
                "windowEndMs": end_ms,
                "detachedAtMs": now_ms,
                "orderStatus": order_status,
                "tokenRecovered": bool(token_id),
                "settlementStillTracked": True,
            }
            return None

        if not token_id:
            active = dict(active)
            active["phase"] = "TOKEN_REPAIR_REQUIRED"
            active["executableExit"] = False
            active["tokenRepairReason"] = "persisted Poly round has no tokenId; no position/SELL request is allowed"
        return active

    def submit_poly_exit_intent(self, raw: dict[str, Any]) -> dict[str, Any]:
        asset = str(raw.get("asset") or "").strip().upper()
        active = self._poly_active_round(asset)
        if active and not str(active.get("token_id") or "").strip():
            return {
                "ok": True,
                "accepted": False,
                "status": "BLOCKED_MISSING_TOKEN_ID",
                "roundId": active.get("round_id"),
                "asset": asset,
                "message": "Poly exit blocked before Binance API because durable round tokenId is empty; V13 repair is read-only/fail-closed",
            }
        return super().submit_poly_exit_intent(raw)

    def state(self) -> dict[str, Any]:
        # Repair is idempotent and cheap; rerun so rows introduced by old data
        # migration after startup cannot regress to empty token IDs.
        repaired_now = self._repair_poly_token_ids()
        self.poly_token_repairs += repaired_now
        payload = super().state()
        payload["version"] = VERSION
        payload["polyLifecycleRepairV13"] = {
            "enabled": True,
            "tokenSource": "ORIGINAL_PERSISTED_POLY_INTENT_PAYLOAD",
            "emptyTokenNeverSentToBinance": True,
            "tokenRepairs": int(self.poly_token_repairs),
            "expiredConfirmedRoundDetaches": int(self.expired_round_detaches),
            "expiredDetachGraceMs": EXPIRED_DETACH_GRACE_MS,
            "lastExpiredDetach": self.last_expired_detach,
            "expiredRoundSettlementStillTracked": True,
            "claimRedeemPnlStopLossPreserved": True,
        }
        gateway = payload.get("polyFastGateway")
        if isinstance(gateway, dict):
            gateway.update(
                emptyTokenRoundRepair=True,
                expiredFilledRoundDoesNotBlockNextMarket=True,
                expiredFilledRoundNeverGetsNewSell=True,
                expiredRoundSettlementStillTracked=True,
            )
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["polyEmptyTokenRepair"] = True
        payload["polyExpiredFilledRoundDetach"] = True
        payload["polyExpiredSettlementPreserved"] = True
        return payload


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV13Handler", (v6._Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; "
        "empty Poly token IDs repaired; expired filled rounds detached from execution lifecycle; settlement preserved",
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
