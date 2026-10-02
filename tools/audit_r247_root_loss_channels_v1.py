"""Bounded, existing-artifact diagnostic; never imports a controller or replay backend.

FIFO here is a reporting decomposition of confirmed purchased shares, NOT a
replacement for the runtime responsibility/payment ledger. No model or policy.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import math
from pathlib import Path
import time

ALLOWED = (1945866, 1945869, 1945898, 1945986, 1946036, 1946298, 1946317, 1946448)
CELL = "MS4_R247_BOUNDED_CORE_SERVICE_FAVORABLE_RECYCLE"
EPS = 1e-8  # Reconstruction tolerance, never a trading threshold.
INPUT_CAP = 4 * 1024 * 1024
OUTPUT_CAP = 32 * 1024


def decompose(row: dict, *, allow_limit_annotations: bool = False) -> dict:
    mid = int(row["marketId"])
    if mid not in ALLOWED or row["cell"] != CELL:
        raise ValueError("Outside frozen consumed-development allowlist/policy")
    lots = {"UP": deque(), "DOWN": deque()}
    qty = {"UP": 0.0, "DOWN": 0.0}
    cost = pair_gain = pair_positive = pair_negative = paired_qty = 0.0
    negative_matches = weak_crosses = repair_crosses = 0
    fills = [e for e in row["splitEvents"] if e.get("event") == "ROLE_FILL_SPLIT"]
    fills = sorted(enumerate(fills), key=lambda item: (int(item[1]["t"]), item[0]))
    for _, event in fills:
        side = event["side"]
        if side not in lots:
            raise ValueError("Unknown side")
        other = "DOWN" if side == "UP" else "UP"
        price, amount = float(event["price"]), float(event["fillInc"])
        if not (math.isfinite(price) and math.isfinite(amount) and 0 < price < 1 and amount > 0):
            raise ValueError("Invalid confirmed buy fill")
        gap = qty[other] - qty[side]
        if gap > EPS and amount > gap + EPS:
            weak_crosses += 1
            repair_crosses += int(event.get("role") in {"ECONOMIC_CORE", "SATELLITE_REPAIR"})
        cost += price * amount
        qty[side] += amount
        remaining = amount
        while remaining > EPS and lots[other]:
            old = lots[other][0]
            matched = min(remaining, old["qty"])
            gain = matched * (1.0 - price - old["price"])
            pair_gain += gain
            pair_positive += max(0.0, gain)
            pair_negative += min(0.0, gain)
            negative_matches += int(gain < -EPS)
            paired_qty += matched
            old["qty"] -= matched
            remaining -= matched
            if old["qty"] <= EPS:
                lots[other].popleft()
        if remaining > EPS:
            lots[side].append({"qty": remaining, "price": price})
    residual_qty = sum(lot["qty"] for side in lots.values() for lot in side)
    residual_cost = sum(lot["qty"] * lot["price"] for side in lots.values() for lot in side)
    limit_cost = cost
    # V8 ROLE_FILL_SPLIT emits order.price, whereas base Sim.record_fill uses
    # snapshot execPrice (DOWN: 1-execPrice). Never silently assign that total
    # price difference to individual fills or call the limit-based FIFO actual.
    if allow_limit_annotations:
        cost = min(qty.values()) - float(row["floor"])
    costing_gap = limit_cost - cost
    up, down = qty["UP"] - cost, qty["DOWN"] - cost
    floor, best = min(up, down), max(up, down)
    winner = row["winnerPostHocOnly"]  # Read only AFTER winner-free decomposition.
    if winner not in qty:
        raise ValueError("Unrecognized diagnostic settlement label")
    pnl = qty[winner] - cost
    checks = {
        "fillCount": len(fills) == int(row["fillEvents"]),
        "floor": abs(floor - float(row["floor"])) <= EPS,
        "best": abs(best - float(row["best"])) <= EPS,
        "pnl": abs(pnl - float(row["pnlDiagnosticOnly"])) <= EPS,
        "limitFloorDecomposition": abs(min(qty.values()) - limit_cost - (pair_gain - residual_cost)) <= EPS,
        "limitBestDecomposition": abs(max(qty.values()) - limit_cost - (pair_gain + residual_qty - residual_cost)) <= EPS,
        "quantityConservation": abs(sum(qty.values()) - 2 * paired_qty - residual_qty) <= EPS,
        "savedFilledQuantity": abs(sum(qty.values()) - float(row.get("filledQty", sum(qty.values())))) <= EPS,
        "nonnegativeNativeCost": cost >= -EPS,
        "sourceCorrectness": bool(row.get("r247ServiceCorrectnessPass"))
        and float(row.get("unauthorizedOverflowQty", math.inf)) <= EPS
        and float(row.get("repairQuotaExcessMax", math.inf)) <= EPS,
    }
    if not all(checks.values()):
        raise ValueError(f"Reconstruction failed for {mid}: {checks}")
    authorizations = [e for e in row["splitEvents"] if e.get("event") == "REPAIR_OVERFLOW_SPLIT_AUTHORIZED"]
    composite = [e for e in authorizations if float(e.get("overflowQty", 0)) > EPS]
    first_composite = None
    if composite:
        e = min(composite, key=lambda value: int(value["t"]))
        first_composite = {key: e.get(key) for key in (
            "t", "key", "role", "side", "price", "orderQty", "repairQty", "overflowQty",
            "repairOnlyFloor", "fullFloor", "debt", "reservedRepairBefore",
        )}
    return {
        "marketId": mid, "checks": checks, "fills": len(fills), "submits": int(row["submits"]),
        "buyNotionalImpliedByNativeEndpoints": cost, "upQty": qty["UP"], "downQty": qty["DOWN"],
        "grossPnl": pnl, "grossFloor": floor, "grossBest": best,
        "perFillCashProvenance": "NOT_SAVED_IN_ROLE_FILL_SPLIT",
        "limitNotionalMinusImpliedNativeNotional": costing_gap,
        "pairedQty": paired_qty, "residualQty": residual_qty,
        "conditionalActualPairGainLower": pair_gain if costing_gap >= -EPS else None,
        "conditionalActualPairGainUpper": pair_gain + max(0.0, costing_gap) if costing_gap >= -EPS else None,
        "conditionalResidualCostLower": max(0.0, residual_cost - max(0.0, costing_gap)) if costing_gap >= -EPS else None,
        "conditionalResidualCostUpper": residual_cost if costing_gap >= -EPS else None,
        "limitPriceDiagnosticOnly": {
            "fifoPairedGrossPnl": pair_gain, "fifoPositivePairGain": pair_positive,
            "fifoNegativePairLoss": pair_negative, "fifoNegativeMatchSegments": negative_matches,
            "residualAcquisitionCost": residual_cost, "buyNotional": limit_cost,
        },
        "weakSideCrossingFills": weak_crosses, "repairRoleCrossingFills": repair_crosses,
        "compositeAuthorizations": len(composite),
        "oneDollarCompositeAuthorizations": sum(abs(float(e["orderQty"]) * float(e["price"]) - 1.0) <= EPS for e in composite),
        "firstComposite": first_composite,
        "repairCreditValue_NOT_profit": row["totalRepairCreditValue"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-limit-annotations", action="store_true",
                        help="Explicit source-verified V8 order.price vs base execPrice distinction")
    args = parser.parse_args()
    started = time.monotonic()
    if args.output.exists():
        raise FileExistsError("Never overwrite an existing audit")
    if args.input.stat().st_size > INPUT_CAP:
        raise ValueError("Input size cap exceeded")
    raw = args.input.read_bytes()
    data = json.loads(raw)
    selected = [r for r in data["rows"] if r.get("cell") == CELL and r.get("marketId") in ALLOWED]
    if sorted(r["marketId"] for r in selected) != list(ALLOWED):
        raise ValueError("Missing or duplicate allowed market")
    rows = [decompose(r, allow_limit_annotations=args.allow_limit_annotations)
            for r in sorted(selected, key=lambda r: r["marketId"])]
    names = ("fills", "submits", "buyNotionalImpliedByNativeEndpoints", "grossPnl", "grossFloor", "grossBest",
             "limitNotionalMinusImpliedNativeNotional", "weakSideCrossingFills",
             "repairRoleCrossingFills", "compositeAuthorizations", "oneDollarCompositeAuthorizations")
    aggregate = {key: sum(r[key] for r in rows) for key in names}
    aggregate["worstGrossPnl"] = min(r["grossPnl"] for r in rows)
    aggregate["leaveOneBestOutGross"] = aggregate["grossPnl"] - max(r["grossPnl"] for r in rows)
    peak = equity = drawdown = 0.0
    for r in rows:
        equity += r["grossPnl"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    aggregate["chronologicalMarketCloseGrossDrawdown"] = drawdown
    for key in ("conditionalActualPairGainLower", "conditionalActualPairGainUpper",
                "conditionalResidualCostLower", "conditionalResidualCostUpper"):
        aggregate[key] = sum(r[key] for r in rows) if all(r[key] is not None for r in rows) else None
    out = {
        "version": "R247_ROOT_LOSS_CHANNELS_V1_20260910", "researchOnly": True,
        "runtimeAuthority": False, "newBE": 0, "trainingRuns": 0,
        "source": str(args.input), "sourceSha256": hashlib.sha256(raw).hexdigest(),
        "inputBytes": len(raw), "allowedMarkets": list(ALLOWED), "aggregate": aggregate,
        "priceAnnotationContractExplicit": args.allow_limit_annotations,
        "cashReconstructionStatus": "NATIVE_ENDPOINT_IDENTITIES_ONLY_NOT_INDEPENDENT_CASH_RECEIPTS",
        "rows": rows, "elapsedSeconds": time.monotonic() - started,
        "limits": {"inputBytesMax": INPUT_CAP, "outputBytesMax": OUTPUT_CAP, "secondsMax": 30},
        "boundaries": [
            "Existing saved confirmed-fill records, not a new experiment or replay.",
            "Only frozen allowed eight rows processed; protected rows not scored or trained.",
            "FIFO matched inventory is reporting attribution, not responsibility genealogy or causal value.",
            "Conditional pair/residual intervals assume every actual buy fill cost is between zero and its submitted limit cost; aggregate consistency is not per-fill proof.",
            "Native total cost is inferred from saved endpoints, not independently reconstructed; original limit-price mismatch is retained.",
            "One-dollar observations do not prove that venue constraints forced these choices.",
            "Gross ledger only; active fees and other applicable costs remain unreconciled.",
            "No Target decisions, public database, tape, fresh market or production process read.",
        ],
    }
    if out["elapsedSeconds"] > 30:
        raise TimeoutError("Analysis budget exceeded")
    rendered = json.dumps(out, ensure_ascii=False, indent=2).encode("utf-8")
    if len(rendered) > OUTPUT_CAP:
        raise ValueError("Output cap exceeded")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as stream:
        stream.write(rendered)
    print(json.dumps({"output": str(args.output), "bytes": len(rendered), "aggregate": aggregate,
                      "elapsedSeconds": out["elapsedSeconds"], "correctness": "PASS"}))


if __name__ == "__main__":
    main()
