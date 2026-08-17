from __future__ import annotations

import time
from typing import Any

from . import multi_prediction_observer as observer_base
from . import poly_fast_live as fast_base
from . import poly_fast_signal_v9 as v9
from . import poly_fast_signal_v15 as v15
from . import poly_fast_signal_v16 as v16

ASSETS = v16.ASSETS
HOST = v16.HOST
PORT = v16.PORT
ROOT = v16.ROOT
DB_PATH = v16.DB_PATH

PAUSED_PRE_VENUE_TERMINALS = frozenset({"IGNORED_PAUSED"})
GATEWAY_ARM_RECHECK_SECONDS = 0.25


class LifecycleAuthoritativePendingEvaluator(v15.EmbeddedPolyStateEvaluator):
    """V16 market-data path plus a lifecycle-authoritative pending-entry latch.

    V9 introduced a local `_pending_entry` latch to prevent duplicate BUYs while
    a newly queued 8781 intent was still being processed. That latch correctly
    protects REJECTED/AMBIGUOUS races, but a successfully SUBMITTED historical
    intent remains SUBMITTED forever in the durable order ledger. After its Poly
    round later becomes FLAT/CLOSED (or expires and detaches), the stale local
    latch therefore used to block every future `_forward_gap_entry()` even though
    `/poly-lifecycle` reported no active round.

    8781 is the durable lifecycle authority. Once it exposes the pending round,
    the local latch is no longer needed because `_active_round()` itself blocks
    duplicate entries. If 8781 later reports no active round while the old entry
    status is still SUBMITTED, that SUBMITTED row is historical fill evidence,
    not an active-position fence, and the stale local latch is released.

    IGNORED_PAUSED is a different terminal: Echtgeld V1 persists it before any
    queue/order/venue write and explicitly never replays it. V19 also never
    attaches a Poly lifecycle round for this status. Therefore the local latch
    may be released once 8781 /health confirms the gateway is armed again. While
    8781 stays PAUSED, a small health cache prevents hot-loop retry spam.

    REJECTED keeps the existing V9 policy (no venue write + >=1s + newer Binance
    book). AMBIGUOUS/ATTEMPTING/QUEUED/PROCESSING remain fail-closed. No venue
    order is placed by this reconciliation; 8792 stays signal-only.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pending_lifecycle_adoptions = 0
        self.stale_submitted_latch_releases = 0
        self.paused_terminal_rearms = 0
        self.paused_terminal_waits = 0
        self._gateway_arm_cache = False
        self._gateway_arm_cache_valid = False
        self._gateway_arm_at_mono = 0.0
        self.last_gateway_arm_check: dict[str, Any] | None = None
        self.last_pending_lifecycle_resolution: dict[str, Any] | None = None

    def _authoritative_lifecycle_row(self) -> tuple[bool, dict[str, Any] | None]:
        """Read 8781 lifecycle directly and return (read_succeeded, asset_row)."""
        try:
            response = self.intent_http.get(f"{v9.ECHTGELD_URL}/poly-lifecycle")
            if response.status_code >= 400:
                return False, None
            data = response.json() if response.content else {}
            lifecycle = data.get("lifecycle") if isinstance(data, dict) else None
            if not isinstance(lifecycle, dict):
                return False, None
            row = lifecycle.get(self.asset)
            active = dict(row) if isinstance(row, dict) else None
            # Keep the inherited lifecycle cache coherent with this authoritative
            # read so the following evaluation does not immediately use stale data.
            self._lifecycle_cache = dict(active) if isinstance(active, dict) else None
            self._lifecycle_at_mono = time.monotonic()
            return True, active
        except Exception as exc:
            self.last_gateway_error = f"lifecycle-authority: {type(exc).__name__}: {str(exc)[:300]}"
            return False, None

    def _gateway_is_armed(self) -> bool:
        """Poll 8781 health at low rate while a confirmed paused terminal is latched."""
        now = time.monotonic()
        if (
            self._gateway_arm_cache_valid
            and now - self._gateway_arm_at_mono < GATEWAY_ARM_RECHECK_SECONDS
        ):
            return bool(self._gateway_arm_cache)

        checked_at_ms = observer_base._now_ms()
        try:
            response = self.intent_http.get(f"{v9.ECHTGELD_URL}/health")
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}")
            data = response.json() if response.content else {}
            if not isinstance(data, dict):
                raise ValueError("health response is not a JSON object")
            runtime_status = str(data.get("runtimeStatus") or "").upper()
            armed = data.get("armed") is True or runtime_status == "LIVE_ARMED"
            self._gateway_arm_cache = bool(armed)
            self._gateway_arm_cache_valid = True
            self._gateway_arm_at_mono = now
            self.last_gateway_arm_check = {
                "atMs": checked_at_ms,
                "armed": bool(armed),
                "runtimeStatus": runtime_status or None,
                "error": None,
            }
            return bool(armed)
        except Exception as exc:
            message = f"{type(exc).__name__}: {str(exc)[:240]}"
            self._gateway_arm_cache = False
            self._gateway_arm_cache_valid = True
            self._gateway_arm_at_mono = now
            self.last_gateway_arm_check = {
                "atMs": checked_at_ms,
                "armed": None,
                "runtimeStatus": None,
                "error": message,
            }
            self.last_gateway_error = f"gateway-health: {message}"
            return False

    @staticmethod
    def _round_id_from_intent(intent_id: str) -> str | None:
        text = str(intent_id or "").strip()
        prefix = "poly-fast:"
        suffix = ":ENTRY"
        if text.startswith(prefix) and text.endswith(suffix):
            value = text[len(prefix) : -len(suffix)]
            return value or None
        return None

    def _release_paused_terminal_latch(
        self,
        *,
        intent_id: str,
        effective: str,
        pending_round_id: str | None,
    ) -> bool:
        self._pending_entry = None
        self.paused_terminal_rearms += 1
        self.last_entry_terminal = {
            "intentId": intent_id,
            "intentStatus": effective,
            "orderStatus": None,
            "effectiveStatus": effective,
            "venueWriteEvidence": False,
            "lifecycleActive": False,
            "gatewayArmed": True,
        }
        self.last_pending_lifecycle_resolution = {
            "atMs": observer_base._now_ms(),
            "asset": self.asset,
            "intentId": intent_id,
            "roundId": pending_round_id or None,
            "resolution": "PAUSED_PRE_VENUE_TERMINAL_REARMED_AFTER_GATEWAY_RESUME",
            "effectiveStatus": effective,
            "lifecycleActive": False,
            "gatewayArmed": True,
            "allowsNewAttemptNow": True,
        }
        self._lifecycle_cache = None
        self._lifecycle_at_mono = 0.0
        return True

    def _pending_entry_allows_new_attempt(self) -> bool:
        pending = self._pending_entry
        if not isinstance(pending, dict):
            return True

        intent_id = str(pending.get("intentId") or "").strip()
        if not intent_id:
            return super()._pending_entry_allows_new_attempt()

        # Once a paused terminal has been proven pre-venue, avoid re-reading the
        # old durable intent on every 50ms strategy tick. Only the lightweight
        # 8781 health state matters until Resume.
        cached_terminal = str(pending.get("preVenueTerminalStatus") or "").upper()
        if cached_terminal in PAUSED_PRE_VENUE_TERMINALS:
            if not self._gateway_is_armed():
                self.paused_terminal_waits += 1
                return False
            return self._release_paused_terminal_latch(
                intent_id=intent_id,
                effective=cached_terminal,
                pending_round_id=self._round_id_from_intent(intent_id),
            )

        lifecycle_ok, active = self._authoritative_lifecycle_row()
        if not lifecycle_ok:
            # Network/schema uncertainty stays fail-closed under the proven V9
            # rules; do not weaken ambiguous-entry protection.
            return super()._pending_entry_allows_new_attempt()

        status = self._intent_status(intent_id)
        round_row = status.get("round") if isinstance(status, dict) and isinstance(status.get("round"), dict) else {}
        pending_round_id = str(round_row.get("round_id") or self._round_id_from_intent(intent_id) or "")

        if isinstance(active, dict):
            active_round_id = str(active.get("round_id") or "")
            # Once 8781's durable lifecycle has adopted this exact round, it is
            # the duplicate-entry fence. Drop the redundant local latch now so
            # it cannot outlive the round after a later FLAT/CLOSED transition.
            if pending_round_id and active_round_id == pending_round_id:
                self._pending_entry = None
                self.pending_lifecycle_adoptions += 1
                self.last_pending_lifecycle_resolution = {
                    "atMs": observer_base._now_ms(),
                    "asset": self.asset,
                    "intentId": intent_id,
                    "roundId": pending_round_id,
                    "resolution": "LIFECYCLE_ADOPTED_ACTIVE_ROUND_LOCAL_LATCH_RELEASED",
                    "activePhase": active.get("phase"),
                    "allowsNewAttemptNow": False,
                }
            # Any authoritative active round blocks new entry regardless of which
            # local pending intent created it.
            return False

        # 8781 successfully reported *no active round*. Check the durable intent
        # terminal before falling back to the original V9 safe-reject policy.
        if not isinstance(status, dict):
            return False
        intent_status = str(status.get("intentStatus") or status.get("status") or "").upper()
        order_status = str(status.get("orderStatus") or "").upper()
        effective = order_status or intent_status
        venue_write = self._venue_write_evidence(status)

        if effective in PAUSED_PRE_VENUE_TERMINALS:
            # Echtgeld V1 writes IGNORED_PAUSED before queue/order execution and
            # explicitly never replays it. V19 does not attach a lifecycle round
            # for that state. It is therefore safe to rearm only after 8781 says
            # it is LIVE_ARMED; until then this pending latch prevents POST spam.
            if venue_write:
                self.unsafe_retry_blocks += 1
                return False
            pending["preVenueTerminalStatus"] = effective
            pending["preVenueTerminalConfirmedAtMs"] = observer_base._now_ms()
            self.last_entry_terminal = {
                "intentId": intent_id,
                "intentStatus": intent_status or None,
                "orderStatus": order_status or None,
                "effectiveStatus": effective,
                "venueWriteEvidence": False,
                "lifecycleActive": False,
            }
            if not self._gateway_is_armed():
                self.paused_terminal_waits += 1
                self.last_pending_lifecycle_resolution = {
                    "atMs": observer_base._now_ms(),
                    "asset": self.asset,
                    "intentId": intent_id,
                    "roundId": pending_round_id or None,
                    "resolution": "PAUSED_PRE_VENUE_TERMINAL_WAITING_FOR_GATEWAY_RESUME",
                    "effectiveStatus": effective,
                    "lifecycleActive": False,
                    "gatewayArmed": False,
                    "allowsNewAttemptNow": False,
                }
                return False
            return self._release_paused_terminal_latch(
                intent_id=intent_id,
                effective=effective,
                pending_round_id=pending_round_id or None,
            )

        if effective == "SUBMITTED" and venue_write:
            # Historical BUY remains SUBMITTED in order history after 8781 has
            # already made the round FLAT/CLOSED/detached. Lifecycle is the
            # active-position authority, so releasing only the local signal latch
            # is safe and performs no venue write.
            self._pending_entry = None
            self.stale_submitted_latch_releases += 1
            self.last_entry_terminal = {
                "intentId": intent_id,
                "intentStatus": intent_status or None,
                "orderStatus": order_status or None,
                "effectiveStatus": effective,
                "venueWriteEvidence": True,
                "lifecycleActive": False,
            }
            self.last_pending_lifecycle_resolution = {
                "atMs": observer_base._now_ms(),
                "asset": self.asset,
                "intentId": intent_id,
                "roundId": pending_round_id or None,
                "resolution": "STALE_HISTORICAL_SUBMITTED_LATCH_RELEASED",
                "lifecycleActive": False,
                "allowsNewAttemptNow": True,
            }
            self._lifecycle_cache = None
            self._lifecycle_at_mono = 0.0
            return True

        # REJECTED still requires no venue-write evidence, >=1s cooldown and a
        # newer Binance book. AMBIGUOUS/ATTEMPTING/QUEUED/PROCESSING remain
        # blocked exactly as V9 defined them.
        return super()._pending_entry_allows_new_attempt()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        pending = payload.get("safeRejectedEntryRearm")
        if isinstance(pending, dict):
            pending.update(
                lifecycleAuthority="8781_/poly-lifecycle",
                localLatchReleasedAfterLifecycleAdoption=True,
                historicalSubmittedDoesNotRemainPermanentLocalFence=True,
                pausedPreVenueTerminalRearm=True,
                pausedPreVenueStatuses=sorted(PAUSED_PRE_VENUE_TERMINALS),
                pausedRetryRequiresGatewayArmed=True,
                gatewayArmRecheckMs=int(GATEWAY_ARM_RECHECK_SECONDS * 1000),
                pendingLifecycleAdoptions=int(self.pending_lifecycle_adoptions),
                staleSubmittedLatchReleases=int(self.stale_submitted_latch_releases),
                pausedTerminalRearms=int(self.paused_terminal_rearms),
                pausedTerminalWaits=int(self.paused_terminal_waits),
                lastGatewayArmCheck=self.last_gateway_arm_check,
                lastLifecycleResolution=self.last_pending_lifecycle_resolution,
                rejectedCooldownPolicyUnchanged=True,
                ambiguousNeverRetried=True,
            )
        return payload


class PolyFastSignalRuntimeV17(v16.PolyFastSignalRuntimeV16):
    def __init__(self) -> None:
        self.observer = v16.SelfHealingBinanceObserver(db_path=DB_PATH)
        self.engines = {
            asset: LifecycleAuthoritativePendingEvaluator(
                self.observer,
                asset=asset,
                db_path=ROOT / "data" / f"poly_fast_signal_{asset.lower()}.db",
            )
            for asset in ASSETS
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_FAST_SIGNAL_V17_PAUSED_RESUME_REARM_FIX"
        payload["architecture"].update(
            pendingEntryLifecycleAuthority=True,
            staleSubmittedLocalLatchCannotBlockFutureRounds=True,
            ignoredPausedLocalLatchCannotBlockAfterResume=True,
            pausedRetryRequires8781Armed=True,
            rejectedEntryCooldownUnchanged=True,
            ambiguousEntryNeverRetried=True,
        )
        return payload

    def diagnostics(self) -> dict[str, Any]:
        payload = super().diagnostics()
        payload["strategyVersion"] = self.snapshot().get("version")
        payload["behavior"]["note"] = (
            "V1.2 diagnostics; V16 market-data behavior preserved. 8792 pending-entry latch yields to "
            "8781 durable /poly-lifecycle; historical SUBMITTED cannot permanently block later rounds; "
            "IGNORED_PAUSED is rearmed only after 8781 /health confirms LIVE_ARMED."
        )
        return payload


class _Handler(v16._Handler):
    runtime: PolyFastSignalRuntimeV17


def main() -> int:
    runtime = PolyFastSignalRuntimeV17()
    runtime.start()
    handler = type("PolyFastSignalV17Handler", (_Handler,), {"runtime": runtime})
    server = fast_base.ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Signal V17 listening on http://{HOST}:{PORT}/state; "
        "V16 market-data path preserved; pending-entry latch authority=8781 lifecycle; "
        "historical SUBMITTED cannot permanently fence later rounds; "
        "IGNORED_PAUSED rearms after 8781 Resume; Echtgeld=8781-only",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.10)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
