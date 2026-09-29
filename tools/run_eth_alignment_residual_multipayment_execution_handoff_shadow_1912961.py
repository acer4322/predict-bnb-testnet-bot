from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import joblib


ROOT = Path.cwd().resolve() if (Path.cwd() / "tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EPS = 1e-9
MID = 1912961


def load_sibling(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load_sibling(
    "residual_multipayment_behavior_for_execution_shadow",
    "run_eth_alignment_v90d_residual_composite_multipayment_behavior_smoke_1912961.py",
)
v90 = base.v90
v80 = base.v80
v38 = base.v38
v1 = base.v1


class ResidualMultipaymentExecutionHandoffShadow(base.V90DResidualCompositeMultipayment):
    def __init__(self, *args, **kwargs):
        self.executionShadowRows: list[dict] = []
        self._multipaymentSubmittedAt: dict[str, int] = {}
        super().__init__(*args, **kwargs)

    def _submit_authorized(self, t, qv, proposal, roles_this_tick):
        before = set(self.residualMultipaymentKeys)
        result = super()._submit_authorized(t, qv, proposal, roles_this_tick)
        for key in set(self.residualMultipaymentKeys) - before:
            self._multipaymentSubmittedAt[str(key)] = int(t)
        return result

    def process(self, t):
        super().process(t)
        if not self.residualMultipaymentKeys or len(self.executionShadowRows) >= 1600:
            return
        self._refresh_carrier_ledger(int(t))
        quotes = v1.quotes(self.book)
        for key in self.residualMultipaymentKeys:
            entry = self.carrierLedger.get(key)
            if not entry:
                continue
            side = str(entry.get("side") or "")
            if side not in ("UP", "DOWN") or not quotes:
                continue
            parent_id = int(entry.get("parentId") or -1)
            submitted_at = int(self._multipaymentSubmittedAt.get(key, t))
            actual_filled = float(entry.get("actualFilled") or 0.0)
            try:
                remaining = float(self._ledger_remaining(entry))
            except Exception:
                remaining = max(0.0, float(entry.get("submittedQty") or 0.0) - actual_filled)
            same_parent_unresolved = [
                str(other_key)
                for other_key, other_entry, other_remaining in self.unresolved()
                if int(other_entry.get("parentId") or -1) == parent_id and float(other_remaining) > EPS
            ]
            order = self.orders.get(key)
            try:
                snapshot = self.snap(order) if order is not None else {}
            except Exception:
                snapshot = {}
            venue_status = snapshot.get("status")
            venue_live = bool(order is not None and v1.live(venue_status))
            authoritative = self.auth_inv()
            opposite = "DOWN" if side == "UP" else "UP"
            residual = max(0.0, float(authoritative[opposite]) - float(authoritative[side]))
            truth_inventory = getattr(self, "truthInv", authoritative)
            truth_role = "REPAIR" if float(truth_inventory[side]) < float(truth_inventory[opposite]) - EPS else "EXPAND"
            bid = float(quotes[side].get("bid") or 0.0)
            ask = float(quotes[side].get("ask") or 0.0)
            active_qty = 1.0 / ask if ask > EPS else math.inf
            floor_before, up_qty, down_qty, cost = self._raw_floor()
            repair_allocation = min(residual, active_qty) if math.isfinite(active_qty) else 0.0
            overflow = max(0.0, active_qty - residual) if math.isfinite(active_qty) else math.inf
            if math.isfinite(active_qty):
                hyp_up = float(up_qty) + (active_qty if side == "UP" else 0.0)
                hyp_down = float(down_qty) + (active_qty if side == "DOWN" else 0.0)
                hyp_cost = float(cost) + active_qty * ask
                hyp_floor = min(hyp_up, hyp_down) - hyp_cost
                recursive = self.recursivePolicy.evaluate(
                    base.recoverability.RecursiveCompositeRecoverabilityContext(
                        floor_before_expand=float(floor_before),
                        floor_after_expand=float(hyp_floor),
                        debt_side=side,
                        repair_debt=abs(hyp_up - hyp_down),
                        up_bid=float(quotes["UP"].get("bid") or 0.0),
                        down_bid=float(quotes["DOWN"].get("bid") or 0.0),
                        max_carriers=4,
                        max_venue_qty=12.0,
                    )
                )
            else:
                hyp_floor = None
                recursive = None
            state = str(venue_status or entry.get("lastStatus") or "UNKNOWN")
            terminal_confirmed = bool(entry.get("terminalConfirmed"))
            passive_live = bool(venue_live and remaining > EPS)
            release_confirmed = bool(terminal_confirmed and remaining <= EPS)
            seconds_left = (int(self.capEnd) - int(t)) / 1000.0
            eligible = bool(
                seconds_left > 180.0
                and residual > EPS
                and actual_filled <= EPS
                and release_confirmed
                and not passive_live
                and not same_parent_unresolved
                and truth_role == "REPAIR"
                and math.isfinite(active_qty)
                and active_qty <= 12.0 + EPS
                and recursive is not None
                and recursive.recoverable
            )
            self.executionShadowRows.append(
                {
                    "t": int(t),
                    "secondsLeft": seconds_left,
                    "key": key,
                    "parentId": parent_id,
                    "side": side,
                    "ageMs": int(t) - submitted_at,
                    "state": state,
                    "cancelRequested": bool(entry.get("cancelRequested")),
                    "terminalConfirmed": terminal_confirmed,
                    "venueLive": venue_live,
                    "releaseConfirmed": release_confirmed,
                    "actualFilled": actual_filled,
                    "remaining": remaining,
                    "sameParentUnresolvedKeys": same_parent_unresolved,
                    "passiveLive": passive_live,
                    "orderPrice": entry.get("price"),
                    "currentBid": bid,
                    "currentAsk": ask,
                    "truthRole": truth_role,
                    "residualDebt": residual,
                    "activeVenueMinQty": active_qty,
                    "repairAllocationIfActiveFull": repair_allocation,
                    "overflowIfActiveFull": overflow,
                    "floorBefore": float(floor_before),
                    "floorAfterHypActiveCarrier": hyp_floor,
                    "recursiveRecoverable": bool(recursive.recoverable) if recursive else False,
                    "recursiveReason": recursive.reason if recursive else "NO_ACTIVE_GEOMETRY",
                    "recursiveRecoveredStep": recursive.recovered_step if recursive else None,
                    "eligibleAfterPassiveRelease": eligible,
                }
            )

    def run_shadow(self, models, winner):
        result = self.run_candidate(models, winner)
        result["executionShadowRows"] = self.executionShadowRows
        return result


def main():
    parser = argparse.ArgumentParser()
    for name in [
        "bundle",
        "lifecycle-model",
        "capability-model",
        "dagger-cache",
        "timing-model",
        "economic-model",
        "price-model",
        "surplus-model",
        "v44-model",
        "v47-model",
    ]:
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--market-id", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.market_id != MID:
        raise ValueError(args.market_id)
    output_path = (
        Path(os.environ["BTC5M_LAN_RESULT_DIR"]) / "result.json"
        if args.output.upper() == "AUTO"
        else Path(args.output)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="eth_alignment_residual_execution_shadow_"))
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(10):
            print(json.dumps({"heartbeat": "RESIDUAL_MULTIPAYMENT_EXECUTION_SHADOW", "ts": time.time()}), flush=True)

    threading.Thread(target=heartbeat, daemon=True).start()
    print(json.dumps({"heartbeat": "RESIDUAL_MULTIPAYMENT_EXECUTION_SHADOW_START", "market": MID}), flush=True)
    try:
        zipfile.ZipFile(args.bundle).extractall(temporary)
        cohort = {
            int(row["marketId"]): row
            for row in json.load(open(temporary / "cohort.json", encoding="utf-8"))["rows"]
        }
        models, life, capability, timing, economic, price, surplus = v38.v36.v34.v30.load_runtime(args)
        teacher = joblib.load(args.v44_model)["models"]["EVENT_VALUE_NORM"]
        generation_teacher = joblib.load(args.v47_model)["models"]["GENERATION_AWARE_NORM"]
        simulator = base.make_simulator(
            ResidualMultipaymentExecutionHandoffShadow,
            temporary / "tapes" / f"{MID}.json.xz",
            models,
            life,
            capability,
            timing,
            economic,
            price,
            surplus,
            teacher,
            generation_teacher,
        )
        try:
            result = simulator.run_shadow(models, cohort[MID]["winner"])
        finally:
            simulator.close()
        rows = result.get("executionShadowRows", []) or []
        early = [row for row in rows if float(row.get("secondsLeft") or 0.0) > 180.0]
        released = [row for row in early if row.get("releaseConfirmed")]
        eligible = [row for row in early if row.get("eligibleAfterPassiveRelease")]
        if eligible:
            decision = "PASS_TO_ONE_MARKET_BOUNDED_ACTIVE_HANDOFF_BEHAVIOR_SMOKE"
        elif released:
            decision = "PASSIVE_RELEASED_BUT_ACTIVE_GEOMETRY_INELIGIBLE"
        elif early:
            decision = "PASSIVE_CARRIER_NEVER_RELEASED_BEFORE_180S"
        else:
            decision = "NO_POST_SUBMIT_EARLY_EXECUTION_CLOCKS"
        output = {
            "version": "RESIDUAL_MULTIPAYMENT_EXECUTION_HANDOFF_SHADOW_V2_1912961",
            "date": "2026-09-04",
            "researchOnly": True,
            "runtimeAuthority": False,
            "behaviorMutationBeyondPassedRouterCandidate": False,
            "marketId": MID,
            "decision": decision,
            "summary": {
                "rows": len(rows),
                "earlyRows": len(early),
                "passiveLiveEarlyRows": sum(bool(row.get("passiveLive")) for row in early),
                "releasedUnfilledEarlyRows": len(released),
                "eligibleActiveHandoffRows": len(eligible),
                "firstReleasedT": released[0]["t"] if released else None,
                "firstReleasedSecondsLeft": released[0]["secondsLeft"] if released else None,
                "firstEligibleT": eligible[0]["t"] if eligible else None,
                "firstEligibleSecondsLeft": eligible[0]["secondsLeft"] if eligible else None,
                "behaviorFills": result.get("actualFillEvents"),
                "terminalFloor": result.get("floor"),
            },
            "firstEligibleRows": eligible[:20],
            "firstReleasedRows": released[:20],
            "rows": rows,
            "safety": v90.safety_summary(result),
            "boundary": [
                "shadow only after the passed residual-multipayment Router candidate",
                "no added submit, cancel, reprice, delay, or Active action",
                "candidate passive carrier remains authoritative",
                "global persistent ownership and live venue snapshot replace current-parent lane membership as the release source of truth",
                "Active eligibility requires venue-terminal-confirmed passive release, truth Repair role, >180s, venue legality, and bounded current-coordinate recovery",
                "strict-past OUR state only",
                "no Target runtime input",
                "no dream fills",
                "no 8781",
            ],
        }
        output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "decision": decision, "summary": output["summary"], "firstEligible": eligible[:3], "firstReleased": released[:3]}, ensure_ascii=False), flush=True)
    finally:
        stop.set()
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    main()
