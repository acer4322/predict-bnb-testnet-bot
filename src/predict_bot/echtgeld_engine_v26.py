from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v25 as v25

VERSION = "ECHTGELD_ENGINE_V2_4310_CAP100_NEXT_MARKET_ARM_GATE_V1"
HOST = v25.HOST
PORT = v25.PORT
CAP100_SOURCE = "CAP100_8787"
R2_R21_SOURCE = "R2_R21_8789"
CAP100_ALLOWED_SOURCES = {CAP100_SOURCE, R2_R21_SOURCE}
HEARTBEAT_FRESH_MS = 3_500


class EchtgeldEngine(v25.EchtgeldEngine):
    """V25 plus a hard next-market activation gate for CAP100 Echtgeld.

    RESUME does not immediately admit CAP100 entries.  It arms the engine into
    ARMED_WAIT_NEXT_MARKET using the latest fresh 8787 heartbeat market as the
    activation anchor.  Every CAP100 Maker/Taker request for that anchor market is
    rejected before any venue quote/write.  Only the first request belonging to a
    different market unlocks live entry admission.

    The selected controller also implements the same policy; this engine-side gate is intentionally
    redundant so a controller bug/restart cannot create a mid-market live entry.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.next_market_lock = threading.RLock()
        self.cap100_heartbeat_market_id: int | None = None
        self.cap100_heartbeat_bucket_start_sec: int | None = None
        self.cap100_heartbeat_window_end_ms: int | None = None
        self.cap100_heartbeat_market_seen_ms: int | None = None
        self.activation_anchor_market_id: int | None = None
        self.activation_anchor_bucket_start_sec: int | None = None
        self.waiting_next_market = False
        self.live_market_id: int | None = None
        self.live_bucket_start_sec: int | None = None
        super().__init__(*args, **kwargs)

    def cap100_heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = super().cap100_heartbeat(payload)
        market_id = int(v1._finite(payload.get("marketId")) or 0)
        bucket = int(v1._finite(payload.get("bucketStartSec")) or 0)
        window_end = int(v1._finite(payload.get("windowEndMs")) or 0)
        if market_id > 0:
            with self.next_market_lock:
                self.cap100_heartbeat_market_id = market_id
                self.cap100_heartbeat_bucket_start_sec = bucket or None
                self.cap100_heartbeat_window_end_ms = window_end or None
                self.cap100_heartbeat_market_seen_ms = v1._now_ms()
        result["nextMarketArmGate"] = self.next_market_arm_state()
        return result

    def _fresh_cap100_heartbeat_market(self) -> tuple[int, int | None]:
        now = v1._now_ms()
        with self.next_market_lock:
            mid = self.cap100_heartbeat_market_id
            bucket = self.cap100_heartbeat_bucket_start_sec
            seen = self.cap100_heartbeat_market_seen_ms
        if mid is None or seen is None or now - seen > HEARTBEAT_FRESH_MS:
            raise v1.EchtgeldEngineError(
                "Cannot RESUME CAP100 safely: no fresh selected-controller market heartbeat is available"
            )
        return int(mid), int(bucket) if bucket is not None else None

    def resume(self) -> dict[str, Any]:
        selected = self._selected_entry_source()
        if selected in CAP100_ALLOWED_SOURCES:
            anchor_mid, anchor_bucket = self._fresh_cap100_heartbeat_market()
            with self.next_market_lock:
                self.activation_anchor_market_id = anchor_mid
                self.activation_anchor_bucket_start_sec = anchor_bucket
                self.waiting_next_market = True
                self.live_market_id = None
                self.live_bucket_start_sec = None
            state = super().resume()
            self._cap100_event(
                "ARMED_WAIT_NEXT_MARKET",
                detail=f"RESUME anchored at market={anchor_mid}; same market entries blocked until rollover",
                payload={"activationMarketId": anchor_mid, "activationBucketStartSec": anchor_bucket},
            )
            state["nextMarketArmGate"] = self.next_market_arm_state()
            return state
        return super().resume()

    def pause(self, reason: str = "operator") -> dict[str, Any]:
        state = super().pause(reason)
        with self.next_market_lock:
            self.waiting_next_market = False
            self.activation_anchor_market_id = None
            self.activation_anchor_bucket_start_sec = None
            self.live_market_id = None
            self.live_bucket_start_sec = None
        return state

    def _enforce_next_market_gate(self, payload: dict[str, Any]) -> None:
        if str(payload.get("entrySource") or "").strip().upper() not in CAP100_ALLOWED_SOURCES:
            return
        if not self.armed:
            return
        market_id = int(v1._finite(payload.get("marketId")) or 0)
        bucket = int(v1._finite(payload.get("bucketStartSec")) or 0)
        if market_id <= 0:
            raise v1.EchtgeldEngineError("CAP100 next-market gate requires marketId")
        with self.next_market_lock:
            waiting = self.waiting_next_market
            anchor_mid = self.activation_anchor_market_id
            anchor_bucket = self.activation_anchor_bucket_start_sec
            live_mid = self.live_market_id
            live_bucket = self.live_bucket_start_sec
            if waiting:
                same_anchor = market_id == anchor_mid
                if anchor_bucket is not None and bucket > 0:
                    same_anchor = same_anchor or bucket == anchor_bucket
                if same_anchor:
                    raise v1.EchtgeldEngineError(
                        f"ARMED_WAIT_NEXT_MARKET: market {market_id} is the RESUME anchor market; new entries are blocked until the next market"
                    )
                # The first different market request opens exactly that market.
                self.waiting_next_market = False
                self.live_market_id = market_id
                self.live_bucket_start_sec = bucket or None
                self._cap100_event(
                    "NEXT_MARKET_ACTIVATED",
                    detail=f"CAP100 live entry admission opened on next market={market_id}",
                    payload={
                        "activationMarketId": anchor_mid,
                        "liveMarketId": market_id,
                        "liveBucketStartSec": bucket or None,
                    },
                )
                return
            # Once opened, do not accept a stale request from another market without
            # a real rollover. A newer bucket/market may advance the live identity.
            if live_mid is None:
                self.live_market_id = market_id
                self.live_bucket_start_sec = bucket or None
                return
            if market_id == live_mid:
                return
            if live_bucket is not None and bucket > live_bucket:
                self.live_market_id = market_id
                self.live_bucket_start_sec = bucket
                self._cap100_event(
                    "LIVE_MARKET_ROLLOVER",
                    detail=f"CAP100 live market advanced {live_mid}->{market_id}",
                    payload={"previousMarketId": live_mid, "liveMarketId": market_id, "liveBucketStartSec": bucket},
                )
                return
            raise v1.EchtgeldEngineError(
                f"CAP100 stale/out-of-order market request blocked: request={market_id}, live={live_mid}"
            )

    def submit_cap100_maker(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._enforce_next_market_gate(payload)
        return super().submit_cap100_maker(payload)

    def submit_cap100_taker(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._enforce_next_market_gate(payload)
        return super().submit_cap100_taker(payload)

    def next_market_arm_state(self) -> dict[str, Any]:
        with self.next_market_lock:
            return {
                "enabled": True,
                "source": self._selected_entry_source() if self._selected_entry_source() in CAP100_ALLOWED_SOURCES else CAP100_SOURCE,
                "allowedSources": sorted(CAP100_ALLOWED_SOURCES),
                "armed": bool(self.armed),
                "waitingNextMarket": bool(self.waiting_next_market),
                "runtimeStatus": "ARMED_WAIT_NEXT_MARKET" if self.armed and self.waiting_next_market else ("LIVE_ARMED" if self.armed else "PAUSED"),
                "activationMarketId": self.activation_anchor_market_id,
                "activationBucketStartSec": self.activation_anchor_bucket_start_sec,
                "liveMarketId": self.live_market_id,
                "liveBucketStartSec": self.live_bucket_start_sec,
                "heartbeatMarketId": self.cap100_heartbeat_market_id,
                "heartbeatBucketStartSec": self.cap100_heartbeat_bucket_start_sec,
                "heartbeatAgeMs": (
                    v1._now_ms() - self.cap100_heartbeat_market_seen_ms
                    if self.cap100_heartbeat_market_seen_ms is not None else None
                ),
                "sameMarketEntryBlocked": True,
                "engineSideEnforced": True,
                "controllerSideAlsoEnforced": True,
            }

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        gate = self.next_market_arm_state()
        payload["nextMarketArmGate"] = gate
        if gate["runtimeStatus"] == "ARMED_WAIT_NEXT_MARKET":
            payload["runtimeStatus"] = "ARMED_WAIT_NEXT_MARKET"
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        gate = self.next_market_arm_state()
        payload["nextMarketArmGate"] = gate
        payload["cap100ResumeWaitsNextMarket"] = True
        payload["cap100SameMarketEntryBlockedAfterResume"] = True
        payload["cap100NextMarketGateEngineSide"] = True
        return payload


class _Handler(v25._Handler):
    engine: EchtgeldEngine


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV26Handler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; CAP100 RESUME => ARMED_WAIT_NEXT_MARKET; same-market entries hard-blocked at 8781",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown(); server.server_close(); engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
