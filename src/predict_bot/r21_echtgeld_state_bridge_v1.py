from __future__ import annotations

import copy
from collections import deque
from typing import Any


VERSION = "R21_ECHTGELD_STATE_BRIDGE_V33_CONTEXT_SEQUENCE"
INBOX_FIELD = "r21ExecutionIncidentInbox"
EPS = 1e-9
MAKER_TERMINAL_DUST_SHARES = 0.011

TERMINAL_STATES = {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "FAILED"}
LIVE_STATES = {
    "PLANNED",
    "QUOTING",
    "QUOTE_READY",
    "PLACING",
    "RESTING",
    "PARTIAL_FILL",
    "CANCEL_PENDING",
    "CANCEL_UNKNOWN",
    "UNKNOWN_SUBMISSION",
}
UNKNOWN_STATES = {"CANCEL_UNKNOWN", "UNKNOWN_SUBMISSION"}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result else default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class R21EchtgeldStateBridgeV1:
    """Translate confirmed Echtgeld lifecycle facts into an information-only R2.1 inbox.

    The bridge has no executor callback and never returns an action.  It keeps
    child ownership separate from terminal residual obligations so one terminal
    event cannot incorrectly release other live children.
    """

    def __init__(self, source_id: str, *, max_incidents: int = 32) -> None:
        self.source_id = str(source_id).strip().upper()
        self.max_incidents = max(1, int(max_incidents))
        self.market_id: int | None = None
        self.children: dict[str, dict[str, Any]] = {}
        self.obligations: dict[str, dict[str, Any]] = {}
        self.target_revision = {"UP": 0, "DOWN": 0}
        self.incidents: deque[dict[str, Any]] = deque(maxlen=self.max_incidents)
        self.seen_event_ids: set[str] = set()
        self.last_event_seq = 0
        self.filtered_foreign_events = 0
        self.previous_summary: dict[str, Any] | None = None

    def reset_market(self, market_id: int) -> None:
        self.market_id = int(market_id)
        self.children.clear()
        self.obligations.clear()
        self.target_revision = {"UP": 0, "DOWN": 0}
        self.incidents.clear()
        self.seen_event_ids.clear()
        self.last_event_seq = 0
        self.previous_summary = None

    def belongs_to_source(self, event: dict[str, Any]) -> bool:
        event_source = str(event.get("source_id") or "").strip().upper()
        cid = str(event.get("client_order_id") or event.get("clientOrderId") or "")
        if event_source:
            return event_source == self.source_id
        # Backward-compatible recovery for events written before source_id was
        # projected by 8781.  Never admit the old CAP100 controller prefix.
        return cid.startswith("UNIFIED_R2_R21_")

    def register_intent(
        self,
        *,
        client_order_id: str,
        market_id: int,
        role: str,
        side: str,
        requested_shares: float,
        created_at_ms: int,
        reason: str | None = None,
        requested_price: float | None = None,
    ) -> None:
        cid = str(client_order_id)
        if not cid or cid in self.children:
            return
        side_key = str(side).upper()
        role_key = str(role).upper()
        if side_key not in {"UP", "DOWN"} or role_key not in {"MAKER", "TAKER"}:
            return
        if self.market_id is None:
            self.market_id = int(market_id)
        if int(market_id) != int(self.market_id):
            return
        if role_key == "MAKER":
            self.target_revision[side_key] += 1
        self.children[cid] = {
            "clientOrderId": cid,
            "marketId": int(market_id),
            "role": role_key,
            "side": side_key,
            "requestedShares": max(0.0, _number(requested_shares)),
            "requestedPrice": None if requested_price is None else max(0.0, _number(requested_price)),
            "confirmedFilledShares": 0.0,
            "state": "PLANNED",
            "createdAtMs": int(created_at_ms),
            "lastEventMs": int(created_at_ms),
            "targetRevision": int(self.target_revision[side_key]),
            "reason": str(reason or ""),
            "cancelPending": False,
            "terminal": False,
            "errorKind": None,
        }

    def _event_id(self, event: dict[str, Any]) -> str:
        seq = _integer(event.get("seq"))
        if seq > 0:
            return f"8781:{seq}"
        return ":".join(
            (
                str(event.get("client_order_id") or "GLOBAL"),
                str(event.get("event_type") or "EVENT"),
                str(event.get("occurred_at_ms") or 0),
                str(event.get("state") or ""),
            )
        )

    def _emit(
        self,
        *,
        event: dict[str, Any],
        incident_type: str,
        child: dict[str, Any] | None,
        reason: str | None = None,
    ) -> None:
        base_id = self._event_id(event)
        event_id = f"{base_id}:{incident_type}"
        if event_id in self.seen_event_ids:
            return
        self.seen_event_ids.add(event_id)
        row = child or {}
        requested = max(0.0, _number(row.get("requestedShares"), _number(event.get("requested_shares"))))
        confirmed = max(
            0.0,
            _number(row.get("confirmedFilledShares"), _number(event.get("filled_share_qty"))),
        )
        self.incidents.append(
            {
                "eventId": event_id,
                "incidentType": str(incident_type),
                "atMs": _integer(event.get("occurred_at_ms")),
                "orderKey": row.get("clientOrderId") or event.get("client_order_id"),
                "role": row.get("role") or event.get("role"),
                "side": row.get("side") or event.get("side"),
                "venueState": row.get("state") or event.get("state"),
                "stateCertainty": "TERMINAL_CONFIRMED" if bool(row.get("terminal")) else "OBSERVED_NONTERMINAL",
                "requestedQty": requested,
                "confirmedFilledQty": confirmed,
                "fillDeltaQty": max(0.0, _number(event.get("delta_shares"))),
                "unresolvedQty": max(0.0, requested - confirmed),
                "submittedAtMs": row.get("createdAtMs"),
                "orderAgeMs": max(
                    0,
                    _integer(event.get("occurred_at_ms")) - _integer(row.get("createdAtMs")),
                ),
                "reason": reason or row.get("errorKind") or event.get("detail"),
            }
        )

    def _open_terminal_obligation(self, child: dict[str, Any], event: dict[str, Any]) -> None:
        if child.get("role") != "MAKER":
            return
        requested = max(0.0, _number(child.get("requestedShares")))
        confirmed = max(0.0, _number(child.get("confirmedFilledShares")))
        residual = max(0.0, requested - confirmed)
        # Venue/precision dust must not become a portfolio-blocking repair obligation.
        if residual <= MAKER_TERMINAL_DUST_SHARES:
            return
        cid = str(child["clientOrderId"])
        existing = self.obligations.get(cid)
        if existing is not None:
            existing["residualQty"] = min(_number(existing.get("residualQty")), residual)
            return
        self.obligations[cid] = {
            "obligationId": cid,
            "faultAtMs": _integer(event.get("occurred_at_ms")),
            "faultType": str(child.get("state") or "TERMINAL_REMAINDER"),
            "side": child.get("side"),
            "originalQty": residual,
            "progressQty": 0.0,
            "residualQty": residual,
            "targetRevisionAtFault": _integer(child.get("targetRevision")),
            "completedAtMs": None,
        }

    def _allocate_recovery_progress(self, *, side: str, shares: float, cid: str, at_ms: int) -> None:
        remaining = max(0.0, shares)
        if remaining <= EPS:
            return
        for obligation in sorted(
            self.obligations.values(),
            key=lambda row: (_integer(row.get("faultAtMs")), str(row.get("obligationId") or "")),
        ):
            if obligation.get("side") != side or obligation.get("obligationId") == cid:
                continue
            residual = max(0.0, _number(obligation.get("residualQty")))
            if residual <= EPS:
                continue
            applied = min(remaining, residual)
            obligation["progressQty"] = _number(obligation.get("progressQty")) + applied
            obligation["residualQty"] = max(0.0, residual - applied)
            if obligation["residualQty"] <= EPS:
                obligation["completedAtMs"] = int(at_ms)
            remaining -= applied
            if remaining <= EPS:
                break

    def observe_event(self, event: dict[str, Any]) -> bool:
        if not self.belongs_to_source(event):
            self.filtered_foreign_events += 1
            return False
        market_id = _integer(event.get("source_market_id"))
        if self.market_id is not None and market_id and market_id != self.market_id:
            return False
        cid = str(event.get("client_order_id") or "")
        if not cid:
            return False
        side = str(event.get("side") or "").upper()
        role = str(event.get("role") or "").upper()
        occurred_at = _integer(event.get("occurred_at_ms"))
        state = str(event.get("state") or "").upper()
        event_type = str(event.get("event_type") or "").upper()
        child = self.children.get(cid)
        if child is None:
            self.register_intent(
                client_order_id=cid,
                market_id=market_id or int(self.market_id or 0),
                role=role,
                side=side,
                requested_shares=_number(event.get("requested_shares")),
                created_at_ms=_integer(event.get("created_at_ms"), occurred_at),
                reason=str(event.get("intent_reason") or ""),
                requested_price=_number(event.get("requested_price"), 0.0),
            )
            child = self.children.get(cid)
        if child is None:
            return False

        self.last_event_seq = max(self.last_event_seq, _integer(event.get("seq")))
        was_cancel_pending = bool(child.get("cancelPending")) or _integer(event.get("cancel_requested_at_ms")) > 0
        requested = max(_number(child.get("requestedShares")), _number(event.get("requested_shares")))
        cumulative = max(
            _number(child.get("confirmedFilledShares")),
            _number(event.get("filled_share_qty")),
        )
        delta = max(0.0, _number(event.get("delta_shares")))
        if delta > EPS and _number(event.get("filled_share_qty")) <= EPS:
            cumulative = min(requested, cumulative + delta) if requested > EPS else cumulative + delta
        child["requestedShares"] = requested
        child["confirmedFilledShares"] = cumulative
        child["lastEventMs"] = occurred_at
        child["errorKind"] = event.get("error_kind") or child.get("errorKind")
        if state:
            child["state"] = state
        child["terminal"] = child["state"] in TERMINAL_STATES
        child["cancelPending"] = child["state"] in {"CANCEL_PENDING", "CANCEL_UNKNOWN"}

        if event_type == "FILL_DELTA" and delta > EPS:
            if was_cancel_pending:
                incident = "FILL_DURING_CANCEL"
            elif occurred_at - _integer(child.get("createdAtMs")) >= 5_000:
                incident = "LATE_FILL_AFTER_DELAY"
            else:
                incident = "FILL_CONFIRMED"
            self._emit(event=event, incident_type=incident, child=child)
            if role == "MAKER":
                self._allocate_recovery_progress(side=side, shares=delta, cid=cid, at_ms=occurred_at)

        if event_type == "ORDER_REJECTED" or child["state"] == "REJECTED":
            self._emit(event=event, incident_type="SUBMIT_REJECT_CONFIRMED", child=child)
        elif child["state"] in {"CANCELED", "EXPIRED", "FAILED"}:
            incident = (
                "TERMINAL_PARTIAL_FILL_CONFIRMED"
                if cumulative > EPS and requested - cumulative > EPS
                else "TERMINAL_ZERO_FILL_CONFIRMED"
            )
            self._emit(event=event, incident_type=incident, child=child)
        elif child["state"] == "FILLED":
            incident = (
                "TERMINAL_PARTIAL_FILL_CONFIRMED"
                if requested - cumulative > EPS
                else "TERMINAL_FILL_CONFIRMED"
            )
            self._emit(event=event, incident_type=incident, child=child)
        elif event_type == "ORDER_PARTIAL_FILL" or child["state"] == "PARTIAL_FILL":
            self._emit(event=event, incident_type="PARTIAL_FILL_CONFIRMED", child=child)
        elif child["state"] in UNKNOWN_STATES:
            self._emit(event=event, incident_type="WRITE_OUTCOME_UNKNOWN", child=child)

        if child["terminal"]:
            self._open_terminal_obligation(child, event)
        return True

    def _emit_live_age_incidents(self, at_ms: int) -> None:
        for child in self.children.values():
            if child.get("terminal") or child.get("state") not in LIVE_STATES:
                continue
            if _number(child.get("confirmedFilledShares")) > EPS:
                continue
            age = max(0, int(at_ms) - _integer(child.get("createdAtMs")))
            if age < 5_000:
                continue
            incident_type = "LIVE_NO_FILL_STALL" if age >= 15_000 else "LIVE_NO_FILL_DELAY"
            synthetic = {
                "client_order_id": child.get("clientOrderId"),
                "event_type": incident_type,
                "occurred_at_ms": int(at_ms),
                "state": child.get("state"),
                "detail": f"confirmed live child has zero fill for {age}ms",
            }
            # Bucket the event ID so the inbox records a transition without
            # emitting the same warning on every controller tick.
            synthetic["seq"] = 0
            synthetic["occurred_at_ms"] = int(at_ms // 5_000 * 5_000)
            self._emit(event=synthetic, incident_type=incident_type, child=child)

    def snapshot(
        self,
        *,
        at_ms: int,
        actual_inventory: dict[str, float],
        pending_cancels: set[str] | list[str] | tuple[str, ...],
        orphan_count: int,
        engine_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._emit_live_age_incidents(int(at_ms))
        pending = {str(cid) for cid in pending_cancels}
        current_children = [row for row in self.children.values() if not bool(row.get("terminal"))]
        unknown = [row for row in current_children if row.get("state") in UNKNOWN_STATES]
        cancel_pending = [row for row in current_children if row.get("clientOrderId") in pending or row.get("cancelPending")]
        partial = [row for row in current_children if _number(row.get("confirmedFilledShares")) > EPS]
        residual = {
            side: sum(
                max(0.0, _number(row.get("residualQty")))
                for row in self.obligations.values()
                if row.get("side") == side
            )
            for side in ("UP", "DOWN")
        }
        actual = {side: max(0.0, _number(actual_inventory.get(side))) for side in ("UP", "DOWN")}
        target = {side: actual[side] + residual[side] for side in ("UP", "DOWN")}
        if unknown:
            ownership = "UNKNOWN_CHILD"
            situation = "UNKNOWN_QUARANTINE"
        elif len(current_children) > 1:
            ownership = "MULTIPLE_CURRENT_CHILDREN"
            situation = "CANCEL_PENDING" if cancel_pending else ("LIVE_PARTIAL" if partial else "LIVE_NO_FILL")
        elif len(current_children) == 1:
            ownership = "CURRENT_CHILD"
            situation = "CANCEL_PENDING" if cancel_pending else ("LIVE_PARTIAL" if partial else "LIVE_NO_FILL")
        elif residual["UP"] > EPS or residual["DOWN"] > EPS:
            ownership = "RELEASED_WITH_REMAINDER"
            situation = "TERMINAL_REMAINDER"
        else:
            ownership = "NO_CHILD"
            situation = "IDLE"

        engine = engine_state if isinstance(engine_state, dict) else {}
        visible_incidents = [copy.deepcopy(row) for row in self.incidents if _integer(row.get("atMs")) <= int(at_ms)]
        obligations = [
            {**copy.deepcopy(row), "actionRecommendation": None}
            for row in sorted(
                self.obligations.values(),
                key=lambda row: (_integer(row.get("faultAtMs")), str(row.get("obligationId") or "")),
            )
            if _number(row.get("residualQty")) > EPS
        ]
        current_summary = {
            "asOfMs": int(at_ms),
            "ownershipState": ownership,
            "terminalCertainty": not current_children,
            "situationCode": situation,
            "remainingObligation": copy.deepcopy(residual),
            "effectiveTargetRevision": copy.deepcopy(self.target_revision),
            "latestEventId": None if not visible_incidents else visible_incidents[-1]["eventId"],
        }
        previous_summary = copy.deepcopy(self.previous_summary)
        sequence_summary = {
            "informationOnly": True,
            "actionAuthority": False,
            "previous": previous_summary,
            "current": copy.deepcopy(current_summary),
            "elapsedSincePriorObservationMs": None if previous_summary is None else max(0, int(at_ms)-_integer(previous_summary.get("asOfMs"))),
            "ownershipChanged": previous_summary is not None and previous_summary.get("ownershipState") != ownership,
            "situationChanged": previous_summary is not None and previous_summary.get("situationCode") != situation,
            "targetRevisionChanged": previous_summary is not None and previous_summary.get("effectiveTargetRevision") != self.target_revision,
        }
        self.previous_summary = copy.deepcopy(current_summary)
        return {
            "version": "R2.1",
            "contractVersion": VERSION,
            "mode": "INFORMATION_ONLY",
            "actionAuthority": False,
            "orderMutationAuthority": False,
            "desiredPortfolioMutationAuthority": False,
            "executorCallbackAllowed": False,
            "source": self.source_id,
            "marketId": self.market_id,
            "asOfMs": int(at_ms),
            "lastEventSeq": int(self.last_event_seq),
            "actualConfirmedInventory": actual,
            "targetBySide": target,
            "effectiveTargetRevision": dict(self.target_revision),
            "remainingObligation": residual,
            "ownershipState": ownership,
            "terminalCertainty": not current_children,
            "situationCode": situation,
            "entryWriteFrozen": bool(engine.get("entryWriteFrozen")),
            "pendingCancels": sorted(pending),
            "orphanCount": int(orphan_count),
            "filteredForeignEventCount": int(self.filtered_foreign_events),
            "children": [copy.deepcopy(row) for row in self.children.values()],
            "obligationResidualBeliefs": {
                "version": "R21_OBLIGATION_RESIDUAL_LEDGER_LIVE_V1",
                "informationOnly": True,
                "actionAuthority": False,
                "obligations": obligations,
            },
            "totalIncidentCount": len(visible_incidents),
            "latestEventId": None if not visible_incidents else visible_incidents[-1]["eventId"],
            "incidents": visible_incidents[-16:],
            "stateSequenceSummary": sequence_summary,
        }
