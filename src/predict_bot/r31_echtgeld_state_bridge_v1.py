from __future__ import annotations

import copy
from typing import Any

from .r21_echtgeld_state_bridge_v1 import R21EchtgeldStateBridgeV1, EPS, _number, _integer

VERSION = "R31_ECHTGELD_STATE_BRIDGE_RESEARCH_V1"
INBOX_FIELD = "r31FormationCooperationInbox"


class R31EchtgeldStateBridgeV1(R21EchtgeldStateBridgeV1):
    """Research-only R3.1 cooperation bridge.

    Inherits the proven R2.1 ownership/terminal semantics and adds deterministic
    Formation/ADD/Recovery execution summaries for R3-S. This bridge is
    information-only and has no action/executor authority.
    """

    def snapshot(
        self,
        *,
        at_ms: int,
        actual_inventory: dict[str, float],
        pending_cancels,
        orphan_count: int,
        engine_state: dict[str, Any] | None = None,
        portfolio_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base = super().snapshot(
            at_ms=at_ms,
            actual_inventory=actual_inventory,
            pending_cancels=pending_cancels,
            orphan_count=orphan_count,
            engine_state=engine_state,
        )
        now = int(at_ms)
        current = [r for r in self.children.values() if not bool(r.get("terminal"))]
        by_side: dict[str, dict[str, float]] = {}
        for side in ("UP", "DOWN"):
            rows = [r for r in current if str(r.get("side") or "").upper() == side]
            makers = [r for r in rows if str(r.get("role") or "").upper() == "MAKER"]
            takers = [r for r in rows if str(r.get("role") or "").upper() == "TAKER"]
            def agg(rr):
                req = sum(max(0.0, _number(r.get("requestedShares"))) for r in rr)
                fill = sum(max(0.0, _number(r.get("confirmedFilledShares"))) for r in rr)
                rem = max(0.0, req-fill)
                ages = [max(0, now-_integer(r.get("createdAtMs"), now)) for r in rr]
                partial = sum(_number(r.get("confirmedFilledShares")) > EPS and _number(r.get("confirmedFilledShares")) + EPS < _number(r.get("requestedShares")) for r in rr)
                unknown = sum(str(r.get("state") or "").upper() in {"UNKNOWN_SUBMISSION","CANCEL_UNKNOWN"} for r in rr)
                return {"count":len(rr),"requestedQty":req,"confirmedFilledQty":fill,"unresolvedQty":rem,"maxAgeMs":max(ages) if ages else 0,"partialCount":int(partial),"unknownCount":int(unknown)}
            by_side[side] = {"maker":agg(makers),"taker":agg(takers)}

        up = max(0.0, _number(actual_inventory.get("UP")))
        dn = max(0.0, _number(actual_inventory.get("DOWN")))
        gross = up + dn
        net = up - dn
        weak_side = "UP" if up < dn-EPS else "DOWN" if dn < up-EPS else "BALANCED"
        pc = 2.0 * min(up,dn) / gross if gross > EPS else 1.0
        pcx = portfolio_context if isinstance(portfolio_context, dict) else {}
        recent = [r for r in (base.get("incidents") or []) if now - _integer(r.get("atMs"), now) <= 15000]
        formation = {
            "actualGross": gross,
            "actualNet": net,
            "actualAbsNet": abs(net),
            "actualPairedCoverage": pc,
            "weakSide": weak_side,
            "liveBySide": by_side,
            "liveMakerUnresolvedQty": {s: by_side[s]["maker"]["unresolvedQty"] for s in ("UP","DOWN")},
            "liveTakerUnresolvedQty": {s: by_side[s]["taker"]["unresolvedQty"] for s in ("UP","DOWN")},
            "recent15sIncidentCount": len(recent),
            "recent15sFillDeltaCount": sum(str(r.get("incidentType") or "").upper() in {"FILL_CONFIRMED","LATE_FILL_AFTER_DELAY","FILL_DURING_CANCEL"} for r in recent),
            "recent15sStallCount": sum(str(r.get("incidentType") or "").upper() in {"LIVE_NO_FILL_DELAY","LIVE_NO_FILL_STALL"} for r in recent),
            "portfolio": {
                "worstCaseFloor": pcx.get("worst_case_floor"),
                "bestCasePnl": pcx.get("best_case_pnl"),
                "combinedAbsNet": pcx.get("combined_abs_net"),
                "combinedPairedCoverage": pcx.get("combined_paired_coverage"),
            },
        }
        base.update({
            "version": "R3.1",
            "contractVersion": VERSION,
            "mode": "INFORMATION_ONLY",
            "actionAuthority": False,
            "orderMutationAuthority": False,
            "desiredPortfolioMutationAuthority": False,
            "executorCallbackAllowed": False,
            "formationExecutionContext": formation,
            "r3sCompatibility": {
                "formationVisible": True,
                "pendingChildrenVisible": True,
                "partialFillVisible": True,
                "unknownWriteVisible": True,
                "terminalAckSemanticsInheritedFromR21": True,
                "postAddRecoveryModelAuthority": False,
                "containmentAuthority": False,
            },
        })
        return base
