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


v90d = load_sibling(
    "eth_v90d_for_residual_multipayment_funnel",
    "run_eth_repair_v90d_min_legal_incremental_repair_1912961.py",
)
v90 = v90d.v90
v80 = v90d.v80
v38 = v90d.v38
v1 = v90d.v1


class V90DResidualMultipaymentFunnel(v90d.V90DMinLegalIncremental):
    def __init__(self, *args, **kwargs):
        self.funnelRows: list[dict] = []
        self.chooseRows: list[dict] = []
        self.budgetRows: list[dict] = []
        self.submitRows: list[dict] = []
        self.postFillStateRows: list[dict] = []
        self.firstV90DFillObservedAt: int | None = None
        super().__init__(*args, **kwargs)

    def _residual_context(self, t: int | None = None) -> dict | None:
        now = int(t if t is not None else getattr(self, "now", 0) or 0)
        self._refresh_carrier_ledger(now)
        paid_keys = list(getattr(self, "v90dSubmitKeys", []) or [])
        filled = sum(float(self.carrierLedger.get(key, {}).get("actualFilled") or 0.0) for key in paid_keys)
        if filled <= EPS:
            return None
        if self.firstV90DFillObservedAt is None:
            self.firstV90DFillObservedAt = now
        parent = getattr(self, "repairParent", None)
        if not isinstance(parent, dict):
            return None
        parent_id = int(parent.get("id") or -1)
        side = str(parent.get("side") or "")
        if parent_id not in set(getattr(self, "v90dPaidParents", set()) or set()) or side not in ("UP", "DOWN"):
            return None
        inventory = self.auth_inv()
        opposite = "DOWN" if side == "UP" else "UP"
        debt = max(0.0, float(inventory[opposite]) - float(inventory[side]))
        if debt <= EPS:
            return None
        quotes = v1.quotes(self.book)
        bid = float(quotes[side].get("bid") or 0.0) if quotes and side in quotes else 0.0
        legal = 1.0 / bid if bid > EPS else math.inf
        repair_unresolved = [
            {
                "key": str(key),
                "side": entry.get("side"),
                "parentId": entry.get("parentId"),
                "remaining": float(remaining),
                "state": entry.get("state"),
            }
            for key, entry, remaining in self.lane_unresolved("REPAIR")
        ]
        return {
            "t": now,
            "secondsLeft": (int(self.capEnd) - now) / 1000.0,
            "parentId": parent_id,
            "parentBornAt": int(parent.get("bornAt") or -1),
            "side": side,
            "authoritativeResidualDebt": debt,
            "v90dFilledQty": filled,
            "bid": bid,
            "venueMinQty": legal,
            "venueMinFitsDebt": math.isfinite(legal) and legal <= debt + EPS,
            "parentMarkedPaidByV90D": parent_id in set(getattr(self, "v90dPaidParents", set()) or set()),
            "parentMarkedRecursive": parent_id in set(getattr(self, "v89RecursiveParents", set()) or set()),
            "repairUnresolved": repair_unresolved,
            "floor": float(self._raw_floor()[0]),
        }

    def choose_authorized(self, t, end, proposed_side, proposed_qty):
        result = super().choose_authorized(t, end, proposed_side, proposed_qty)
        context = self._residual_context(int(t))
        if context is not None and len(self.chooseRows) < 1200:
            row = {
                **context,
                "stage": "CHOOSE_AUTHORIZED",
                "proposedSide": str(proposed_side),
                "proposedQty": float(proposed_qty),
                "authorizationReturned": result is not None,
                "authorization": {
                    "side": result[0],
                    "qty": float(result[1]),
                    "priceOrState": result[2]
                    if isinstance(result[2], (str, int, float, bool)) or result[2] is None
                    else {"type": type(result[2]).__name__},
                    "role": str(result[3]),
                    "objectiveId": result[4],
                }
                if result is not None
                else None,
            }
            self.chooseRows.append(row)
            self.funnelRows.append(row)
        return result

    def _repair_payoff_budget(self, price):
        context = self._residual_context()
        result = super()._repair_payoff_budget(price)
        if context is not None and len(self.budgetRows) < 1200:
            row = {
                **context,
                "stage": "REPAIR_PAYOFF_BUDGET",
                "inputPrice": float(price) if price is not None else None,
                "result": dict(result) if isinstance(result, dict) else result,
            }
            self.budgetRows.append(row)
            self.funnelRows.append(row)
        return result

    def _submit_authorized(self, t, qv, proposal, roles_this_tick):
        context = self._residual_context(int(t))
        before = {
            "submits": int(getattr(self, "submits", 0) or 0),
            "payoffInfeasiblePriceBlocks": int(getattr(self, "payoffInfeasiblePriceBlocks", 0) or 0),
            "remainingCapBlocks": int(getattr(self, "remainingCapBlocks", 0) or 0),
            "globalOwnershipBlocks": int(getattr(self, "globalOwnershipBlocks", 0) or 0),
            "v90dAllows": int(getattr(self, "v90dAllows", 0) or 0),
        }
        result = super()._submit_authorized(t, qv, proposal, roles_this_tick)
        if context is not None and len(self.submitRows) < 1200:
            after = {
                "submits": int(getattr(self, "submits", 0) or 0),
                "payoffInfeasiblePriceBlocks": int(getattr(self, "payoffInfeasiblePriceBlocks", 0) or 0),
                "remainingCapBlocks": int(getattr(self, "remainingCapBlocks", 0) or 0),
                "globalOwnershipBlocks": int(getattr(self, "globalOwnershipBlocks", 0) or 0),
                "v90dAllows": int(getattr(self, "v90dAllows", 0) or 0),
            }
            row = {
                **context,
                "stage": "SUBMIT_AUTHORIZED",
                "proposal": {
                    "side": proposal[0],
                    "qty": float(proposal[1]),
                    "priceOrState": proposal[2]
                    if isinstance(proposal[2], (str, int, float, bool)) or proposal[2] is None
                    else {"type": type(proposal[2]).__name__},
                    "role": str(proposal[3]),
                    "objectiveId": proposal[4],
                }
                if proposal is not None
                else None,
                "return": bool(result),
                "counterDelta": {key: after[key] - before[key] for key in before if after[key] != before[key]},
            }
            self.submitRows.append(row)
            self.funnelRows.append(row)
        return result

    def process(self, t):
        super().process(t)
        context = self._residual_context(int(t))
        if context is not None and len(self.postFillStateRows) < 1200:
            row = {**context, "stage": "POST_FILL_STATE"}
            self.postFillStateRows.append(row)
            self.funnelRows.append(row)

    def run_funnel(self, models, winner):
        result = self.run_v90d(models, winner)
        result.update(
            {
                "funnelRows": self.funnelRows,
                "chooseRows": self.chooseRows,
                "budgetRows": self.budgetRows,
                "submitRows": self.submitRows,
                "postFillStateRows": self.postFillStateRows,
                "firstV90DFillObservedAt": self.firstV90DFillObservedAt,
            }
        )
        return result


def classify(result: dict) -> str:
    states = result.get("postFillStateRows", []) or []
    choices = result.get("chooseRows", []) or []
    budgets = result.get("budgetRows", []) or []
    submits = result.get("submitRows", []) or []
    repair_choices = [
        row
        for row in choices
        if isinstance(row.get("authorization"), dict) and row["authorization"].get("role") == "REPAIR"
    ]
    if not states:
        return "NO_CONFIRMED_RESIDUAL_DEBT_STATE"
    if not repair_choices:
        return "RESIDUAL_DEBT_NOT_REAUTHORIZED_AS_REPAIR"
    if any(isinstance(row.get("result"), dict) and bool(row["result"].get("feasible")) for row in budgets):
        if any(row.get("return") for row in submits):
            return "REPAIR_RESUBMITTED_CHECK_FILL_OR_CHURN"
        return "FEASIBLE_BUDGET_BLOCKED_BEFORE_PHYSICAL_SUBMIT"
    if budgets:
        return "RESIDUAL_REPAIR_BUDGET_INFEASIBLE"
    return "REPAIR_AUTHORIZED_BUT_ROUTER_NOT_EVALUATED"


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
    temporary = Path(tempfile.mkdtemp(prefix="eth_alignment_v90d_residual_funnel_"))
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(10):
            print(json.dumps({"heartbeat": "V90D_RESIDUAL_MULTIPAYMENT_FUNNEL", "ts": time.time()}), flush=True)

    threading.Thread(target=heartbeat, daemon=True).start()
    print(json.dumps({"heartbeat": "V90D_RESIDUAL_MULTIPAYMENT_FUNNEL_START", "market": MID}), flush=True)
    try:
        zipfile.ZipFile(args.bundle).extractall(temporary)
        cohort = {
            int(row["marketId"]): row
            for row in json.load(open(temporary / "cohort.json", encoding="utf-8"))["rows"]
        }
        models, life, capability, timing, economic, price, surplus = v38.v36.v34.v30.load_runtime(args)
        teacher = joblib.load(args.v44_model)["models"]["EVENT_VALUE_NORM"]
        generation_teacher = joblib.load(args.v47_model)["models"]["GENERATION_AWARE_NORM"]
        simulator = V90DResidualMultipaymentFunnel(
            temporary / "tapes" / f"{MID}.json.xz",
            "BOOK_IMBALANCE",
            models,
            life,
            0,
            0,
            capability=capability,
            timing=timing,
            economic=economic,
            price_envelope=price,
            surplus_value=surplus,
            teacher=teacher,
            genTeacher=generation_teacher,
            policy_profile=v80.economic_v1_profile(),
        )
        try:
            result = simulator.run_funnel(models, cohort[MID]["winner"])
        finally:
            simulator.close()
        classification = classify(result)
        states = result.get("postFillStateRows", []) or []
        choices = result.get("chooseRows", []) or []
        budgets = result.get("budgetRows", []) or []
        submits = result.get("submitRows", []) or []
        output = {
            "version": "V90D_RESIDUAL_MULTIPAYMENT_FUNNEL_1912961",
            "date": "2026-09-04",
            "researchOnly": True,
            "runtimeAuthority": False,
            "behaviorMutation": False,
            "marketId": MID,
            "winnerPostHocOnly": cohort[MID]["winner"],
            "classification": classification,
            "summary": {
                "postFillResidualClocks": len(states),
                "earlyResidualClocks": sum(float(row.get("secondsLeft") or 0.0) > 180.0 for row in states),
                "lateResidualClocks": sum(float(row.get("secondsLeft") or 0.0) <= 180.0 for row in states),
                "repairAuthorizationClocks": sum(
                    isinstance(row.get("authorization"), dict) and row["authorization"].get("role") == "REPAIR"
                    for row in choices
                ),
                "expandAuthorizationClocks": sum(
                    isinstance(row.get("authorization"), dict) and row["authorization"].get("role") == "EXPAND"
                    for row in choices
                ),
                "noAuthorizationClocks": sum(not row.get("authorizationReturned") for row in choices),
                "budgetEvaluations": len(budgets),
                "feasibleBudgets": sum(
                    isinstance(row.get("result"), dict) and bool(row["result"].get("feasible")) for row in budgets
                ),
                "submitEvaluations": len(submits),
                "successfulResubmits": sum(bool(row.get("return")) for row in submits),
                "terminalResidualDebt": result.get("v90dTerminalAbsGap"),
                "terminalFloor": result.get("floor"),
                "fills": result.get("actualFillEvents"),
            },
            "chooseRows": choices,
            "budgetRows": budgets,
            "submitRows": submits,
            "postFillStateRows": states,
            "boundary": [
                "instrumentation only; V90D behavior unchanged",
                "single development market realistic HFT",
                "strict-past OUR state only",
                "no Target runtime input",
                "no threshold, price, quantity, cadence, ownership, allocation, or routing mutation",
                "no dream fills",
                "no 8781",
            ],
        }
        output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "ok": True,
                    "classification": classification,
                    "summary": output["summary"],
                    "firstChoices": choices[:5],
                    "firstBudgets": budgets[:5],
                    "firstSubmits": submits[:5],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        stop.set()
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    main()
