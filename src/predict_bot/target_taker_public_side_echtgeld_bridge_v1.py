from __future__ import annotations

import os
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import predict_wallet_target_taker_public_side_strategy_v1 as public_side
from . import target_taker_public_side_reentry_test_v1 as reentry_test
from . import target_taker_public_side_test_v1 as v1


VERSION = "TARGET_TAKER_PUBLIC_SIDE_V1_8782_ECHTGELD_BRIDGE_V1"
PORT = int(os.environ.get("PREDICT_TARGET_TAKER_PUBLIC_SIDE_TEST_PORT", "8782"))
v1.PORT = PORT
ECHTGELD_INTENT_URL = str(
    os.environ.get("PREDICT_TARGET_TAKER_ECHTGELD_INTENT_URL")
    or "http://127.0.0.1:8781/intent"
).strip()
SIDE_ONLY = public_side.SIDE_ONLY_COHORT
BASE_STRATEGY = public_side.VERSION


def _number(value: Any) -> float | None:
    return v1._number(value)


def build_base_echtgeld_intent(
    market_id: int,
    snapshot: dict[str, Any],
    decision: dict[str, Any],
    *,
    created_at_ms: int | None = None,
) -> dict[str, Any]:
    """Build the only live-eligible 8782 intent: BASE EBM entry ordinal #1.

    Re-entry decisions never call this function. The Echtgeld engine still owns
    arming, cohort selection, age checks, durable dedupe and venue execution.
    """

    if str(decision.get("decision") or "") != "TRADE":
        raise ValueError("BASE Echtgeld intent requires a TRADE decision")
    side = str(decision.get("side") or "").upper()
    ask = _number(decision.get("ask"))
    if side not in {"UP", "DOWN"} or ask is None or not 0 < ask <= 1:
        raise ValueError("BASE Echtgeld intent has invalid side/ask")
    snapshot_market_id = int(_number(snapshot.get("marketId") or snapshot.get("market_id")) or 0)
    if int(market_id) <= 0 or snapshot_market_id != int(market_id):
        raise ValueError("BASE Echtgeld snapshot market mismatch")

    now_ms = int(created_at_ms or time.time() * 1000)
    snapshot_ns = int(_number(snapshot.get("timestampNs") or snapshot.get("timestamp_ns")) or time.time_ns())
    signal_id = f"{BASE_STRATEGY}:{SIDE_ONLY}:{int(market_id)}:{snapshot_ns}"
    return {
        "intentId": signal_id,
        "signalId": signal_id,
        "dedupeKey": f"{BASE_STRATEGY}:{SIDE_ONLY}:{int(market_id)}",
        "strategy": BASE_STRATEGY,
        "cohort": SIDE_ONLY,
        "marketId": int(market_id),
        "createdAtMs": now_ms,
        "entryOrdinal": 1,
        "entrySource": "BASE_EBM",
        "producer": "8782",
        "producerVersion": VERSION,
        "reentryLiveEligible": False,
        "decision": dict(decision),
        "snapshot": dict(snapshot),
    }


class TargetTakerPublicSideEchtgeldBridge(reentry_test.TargetTakerPublicSideReentryTest):
    """8782 EBM paper/re-entry service plus fail-closed BASE-only Echtgeld handoff."""

    def __init__(self, db_path: Path = v1.DB_PATH) -> None:
        super().__init__(db_path=db_path)
        self.echtgeld_excluded_market_id: int | None = None
        self.echtgeld_handoff_attempts = 0
        self.echtgeld_handoff_sent = 0
        self.echtgeld_handoff_errors = 0
        self.last_echtgeld_handoff: dict[str, Any] | None = None

    def _register_market(self, market_id: int, title: str | None) -> bool:
        active = super()._register_market(market_id, title)
        # Every 8782 process start excludes the market first observed by this
        # bridge. This prevents a mid-market restart from replaying or creating
        # a live order from a partially observed market.
        if self.echtgeld_excluded_market_id is None:
            self.echtgeld_excluded_market_id = int(market_id)
        return active

    def _maybe_trade(self, market_id: int, snapshot: dict[str, Any], decision: dict[str, Any]) -> None:
        had_trade = self.current_trade is not None
        super()._maybe_trade(market_id, snapshot, decision)
        created_base_trade = not had_trade and self.current_trade is not None
        if not created_base_trade:
            return
        if int(market_id) == self.echtgeld_excluded_market_id:
            self.last_echtgeld_handoff = {
                "status": "SKIPPED_DEPLOYMENT_MARKET",
                "marketId": int(market_id),
                "entryOrdinal": 1,
                "entrySource": "BASE_EBM",
                "atMs": int(time.time() * 1000),
                "message": "First market observed after 8782 process start is never live-forwarded",
            }
            return
        self._handoff_base_trade(int(market_id), snapshot, decision)

    def _handoff_base_trade(self, market_id: int, snapshot: dict[str, Any], decision: dict[str, Any]) -> None:
        self.echtgeld_handoff_attempts += 1
        attempted_at_ms = int(time.time() * 1000)
        try:
            intent = build_base_echtgeld_intent(
                market_id,
                snapshot,
                decision,
                created_at_ms=attempted_at_ms,
            )
            response = self.client.post(ECHTGELD_INTENT_URL, json=intent)
            try:
                body = response.json()
            except Exception:
                body = {"raw": response.text[:500]}
            accepted = bool(isinstance(body, dict) and body.get("accepted"))
            if response.is_success:
                self.echtgeld_handoff_sent += 1
                status = str(body.get("status") if isinstance(body, dict) else "HTTP_OK")
                self.last_echtgeld_handoff = {
                    "status": status or "HTTP_OK",
                    "accepted": accepted,
                    "httpStatus": int(response.status_code),
                    "marketId": market_id,
                    "side": decision.get("side"),
                    "ask": decision.get("ask"),
                    "entryOrdinal": 1,
                    "entrySource": "BASE_EBM",
                    "intentId": intent["intentId"],
                    "atMs": attempted_at_ms,
                    "response": body,
                }
                return
            raise RuntimeError(f"HTTP {response.status_code}: {body}")
        except Exception as exc:
            # Intent delivery is deliberately single-shot. 8782 never retries a
            # failed handoff and never replays an existing paper trade after a
            # process restart. Missing Echtgeld is safer than a duplicate order.
            self.echtgeld_handoff_errors += 1
            self.last_echtgeld_handoff = {
                "status": "HANDOFF_ERROR_NO_RETRY",
                "accepted": False,
                "marketId": market_id,
                "side": decision.get("side"),
                "ask": decision.get("ask"),
                "entryOrdinal": 1,
                "entrySource": "BASE_EBM",
                "atMs": attempted_at_ms,
                "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            }

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        payload["echtgeldBaseHandoffEnabled"] = True
        payload["echtgeldEntryPolicy"] = "BASE_EBM_ENTRY_1_ONLY_NO_RETRY"
        payload["echtgeldExcludedDeploymentMarketId"] = self.echtgeld_excluded_market_id
        return payload

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        payload["echtgeldHandoff"] = {
            "enabled": True,
            "sourcePort": PORT,
            "destination": ECHTGELD_INTENT_URL,
            "strategy": BASE_STRATEGY,
            "cohort": SIDE_ONLY,
            "entryPolicy": "BASE_EBM_ENTRY_1_ONLY",
            "reentryForwarding": False,
            "restartReplay": False,
            "automaticRetry": False,
            "excludedDeploymentMarketId": self.echtgeld_excluded_market_id,
            "attempts": self.echtgeld_handoff_attempts,
            "sent": self.echtgeld_handoff_sent,
            "errors": self.echtgeld_handoff_errors,
            "last": dict(self.last_echtgeld_handoff) if isinstance(self.last_echtgeld_handoff, dict) else None,
        }
        return payload


class Handler(reentry_test.Handler):
    test: TargetTakerPublicSideEchtgeldBridge


def main() -> int:
    test = TargetTakerPublicSideEchtgeldBridge()
    test.start()
    handler = type("TargetTakerPublicSideEchtgeldBridgeV1Handler", (Handler,), {"test": test})
    server = ThreadingHTTPServer((v1.HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{v1.HOST}:{PORT}/state; "
        f"baseStrategy={BASE_STRATEGY}; cohort={SIDE_ONLY}; "
        "baseEntry1EchtgeldHandoff=true; reentryEchtgeldHandoff=false; "
        "restartReplay=false; automaticRetry=false; 8781OwnsArmingAndExecution=true",
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
