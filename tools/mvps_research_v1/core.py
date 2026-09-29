"""Dependency-free accounting and conservative, policy-level research screens.

The FIFO lots below are economic allocations, NOT controller responsibilities.
They have no action authority. A same-clock match is explicitly not repayment of
a previously born liability. Unknown evidence fails closed, not as numeric zero.
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from decimal import Decimal, InvalidOperation
from pathlib import Path

SIDES = ("UP", "DOWN")
ZERO = Decimal(0)
TOL = Decimal("0.0000001")  # reconciliation tolerance, never trading authority


def number(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not an accounting number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("invalid accounting number") from None
    if not result.is_finite():
        raise ValueError("non-finite accounting number")
    return result


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    def reject(value):
        raise ValueError(f"non-standard JSON number: {value}")
    def unique_pairs(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"duplicate JSON key: {key}")
            out[key] = value
        return out
    with Path(path).open(encoding="utf-8-sig") as handle:
        return json.load(handle, parse_constant=reject, object_pairs_hook=unique_pairs)


def write_json_new(path, value):
    """Never overwrite a user's artifact."""
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


class EconomicLedger:
    def __init__(self, start_ms, emit=None):
        self.start_ms = self.clock = int(start_ms)
        self.emit = emit or (lambda kind, row: None)
        self.inventory = {side: ZERO for side in SIDES}
        self.lots = {side: deque() for side in SIDES}
        self.cash = self.pair_qty = self.pair_profit = ZERO
        self.abs_net_integral = self.peak_abs_net = ZERO
        self.peak_gross_cost = ZERO
        self.fill_ids = set()
        self.order_cum = {}
        self.order_terms = {}
        self.fill_count = self.alternations = self.pair_links = 0
        self.last_side = None
        self.price_fallbacks = 0

    def advance(self, receipt_ms):
        receipt_ms = int(receipt_ms)
        if receipt_ms < self.clock:
            raise ValueError("receipt clock regressed")
        gap = abs(self.inventory["UP"] - self.inventory["DOWN"])
        self.abs_net_integral += gap * (receipt_ms - self.clock)
        self.clock = receipt_ms

    def fill(self, row):
        required = {"fillId", "orderId", "receiptMs", "placedMs", "side",
                    "qty", "price", "orderQty", "cumQty", "priceAuthority"}
        if not required <= row.keys():
            raise ValueError("incomplete confirmed-fill identity")
        side = row["side"]
        if side not in SIDES or not row["fillId"] or not row["orderId"]:
            raise ValueError("invalid fill identity or side")
        if row["fillId"] in self.fill_ids:
            raise ValueError("duplicate fill identity")
        q, p, oq, cq = (number(row[k]) for k in ("qty", "price", "orderQty", "cumQty"))
        old = self.order_cum.get(row["orderId"], ZERO)
        terms = (side, oq, int(row["placedMs"]))
        if row["orderId"] in self.order_terms and self.order_terms[row["orderId"]] != terms:
            raise ValueError("immutable order terms changed")
        if q <= 0 or not ZERO < p < 1 or oq <= 0:
            raise ValueError("invalid binary-claim fill")
        if abs((cq - old) - q) > TOL or cq > oq + TOL:
            raise ValueError("cumulative fill mismatch or overfill")
        if int(row["receiptMs"]) < int(row["placedMs"]):
            raise ValueError("fill precedes submit")
        if row["priceAuthority"] not in {"EXCHANGE_SNAPSHOT", "INHERITED_LIMIT_FALLBACK"}:
            raise ValueError("unknown fill-price authority")
        self.advance(row["receiptMs"])
        self.fill_ids.add(row["fillId"])
        self.order_cum[row["orderId"]] = cq
        self.order_terms[row["orderId"]] = terms
        self.price_fallbacks += row["priceAuthority"] == "INHERITED_LIMIT_FALLBACK"
        self.inventory[side] += q
        self.cash += q * p
        self.fill_count += 1
        self.alternations += self.last_side is not None and self.last_side != side
        self.last_side = side
        opposite = "DOWN" if side == "UP" else "UP"
        remaining = q
        while remaining > 0 and self.lots[opposite]:
            lot = self.lots[opposite][0]
            matched = min(remaining, lot["remaining"])
            pnl = matched * (1 - p - lot["price"])
            self.pair_qty += matched
            self.pair_profit += pnl
            self.pair_links += 1
            lot["remaining"] -= matched
            remaining -= matched
            self.emit("allocations", {
                "receiptMs": self.clock, "paidFillId": lot["fillId"],
                "payingFillId": row["fillId"], "qty": str(matched),
                "pairedGrossProfit": str(pnl),
                "kind": "SAME_CLOCK_PAIR" if lot["bornMs"] == self.clock else "PRIOR_FIFO_LOT_MATCH",
                "authority": "ECONOMIC_ACCOUNTING_ONLY",
            })
            if lot["remaining"] == 0:
                self.lots[opposite].popleft()
        if remaining > 0:
            self.lots[side].append({"fillId": row["fillId"], "bornMs": self.clock,
                                    "price": p, "remaining": remaining})
        self.peak_abs_net = max(self.peak_abs_net, abs(self.inventory["UP"] - self.inventory["DOWN"]))
        self.peak_gross_cost = max(self.peak_gross_cost, self.cash)
        self.emit("fills", dict(row))

    def summary(self, end_ms):
        self.advance(end_ms)
        residual_cost = sum((lot["remaining"] * lot["price"]
                             for queue in self.lots.values() for lot in queue), ZERO)
        residual_qty = {s: sum((x["remaining"] for x in self.lots[s]), ZERO) for s in SIDES}
        endpoints = {s: self.inventory[s] - self.cash for s in SIDES}
        decomposed = {s: self.pair_profit + residual_qty[s] - residual_cost for s in SIDES}
        if any(abs(endpoints[s] - decomposed[s]) > TOL for s in SIDES):
            raise ValueError("paired-plus-residual accounting failed")
        return {
            "startMs": self.start_ms, "endMs": self.clock,
            "upQty": str(self.inventory["UP"]), "downQty": str(self.inventory["DOWN"]),
            "buyNotional": str(self.cash), "fillEvents": self.fill_count,
            "fillSideAlternations": self.alternations, "pairLinks": self.pair_links,
            "pairedQty": str(self.pair_qty), "pairedGrossProfit": str(self.pair_profit),
            "residualQty": {k: str(v) for k, v in residual_qty.items()},
            "residualCost": str(residual_cost),
            "endpointGross": {k: str(v) for k, v in endpoints.items()},
            "receiptClockPeakAbsNet": str(self.peak_abs_net),
            "absNetShareMs": str(self.abs_net_integral),
            "peakGrossBuyCost": str(self.peak_gross_cost),
            "priceFallbackFillCount": self.price_fallbacks,
            "ledgerReconciled": True,
            "limitations": ["Economic lots are not responsibility ownership",
                            "Receipt-clock exposure, not exchange-intraclock risk",
                            "Paired profit is settlement accounting, not released cash"],
        }


ACTIVITY = ("fillEvents", "buyNotional", "fillSideAlternations", "pairLinks", "absNetShareMs")


def evaluate(run):
    """No calibration or winner-based candidate selection; outcomes are evaluation only."""
    if run.get("researchOnly") is not True or run.get("liveAuthority") is not False:
        raise ValueError("research-only result authority required")
    issues = []
    contract = run["evaluationContract"]
    rows = run["markets"]
    if not rows or len({r["marketId"] for r in rows}) != len(rows):
        raise ValueError("empty or duplicate market set")
    if [r["marketId"] for r in rows] != run["marketIds"]:
        raise ValueError("missing, extra, or reordered markets")
    if run.get("evidenceKind") != "REALISTIC_HFT_REPLAY":
        issues.append("SYNTHETIC_NOT_ECONOMIC_EVIDENCE")
    if not run.get("behaviorParityVerified"):
        issues.append("BEHAVIOR_PARITY_NOT_VERIFIED")
    if run.get("cohortKind") != "consumed_development":
        raise ValueError("locked or unsupported cohort")
    costs = contract.get("extraCostUpperByMarket")
    cost_ready = (contract.get("costScope") == "ALL_COSTS_NOT_ALREADY_IN_BUY_NOTIONAL"
                  and bool(contract.get("costProvenance")) and isinstance(costs, dict)
                  and set(costs) == {str(r["marketId"]) for r in rows})
    if not cost_ready:
        issues.append("FULL_NET_COST_UNRESOLVED")
    for r in rows:
        if not r.get("ledgerReconciled"):
            issues.append("LEDGER_NOT_RECONCILED")
        if r.get("priceFallbackFillCount", 0):
            issues.append("EXECUTION_PRICE_FALLBACK_UNRESOLVED")
        if r.get("winnerPostHocOnly") not in SIDES:
            raise ValueError("missing evaluation-only settlement")
        if r.get("openOrderCountAtHorizon") is None or r.get("cashAndReservationsPeak") is None:
            issues.append("PHYSICAL_LIFECYCLE_RISK_UNRESOLVED")
        elif r["openOrderCountAtHorizon"] != 0:
            issues.append("NONTERMINAL_ORDERS_AT_HORIZON")
    risk = contract.get("riskLimits") or {}
    risk_keys = ("maxWorstEndpointLoss", "maxReceiptPeakAbsNet", "maxTerminalSequenceDrawdown",
                 "maxCashPlusReservations")
    if any(risk.get(k) is None for k in risk_keys) or not contract.get("riskProvenance"):
        issues.append("RISK_BUDGET_UNSPECIFIED")
    retention = contract.get("minActivityRetention") or {}
    control = run.get("activityControl")
    if (not control or any(retention.get(k) is None for k in ACTIVITY)
            or contract.get("minTradeCoverage") is None or not contract.get("activityProvenance")):
        issues.append("ANTI_COLLAPSE_NOT_IDENTIFIED")
    else:
        if control.get("marketIds") != run["marketIds"] or control.get("policyId") == run["policyId"]:
            raise ValueError("activity control must be distinct and cover the identical cohort")
        if not control.get("provenance"):
            raise ValueError("missing activity-control provenance")
        for key in ACTIVITY:
            minimum = number(retention[key])
            if not ZERO < minimum <= 1:
                raise ValueError("activity floors must be independently frozen in (0,1]")
            base = number(control["totals"][key])
            observed = sum((number(r[key]) for r in rows), ZERO)
            if base < 0:
                raise ValueError("negative control activity")
            if base == 0:
                issues.append(f"ACTIVITY_DENOMINATOR_ZERO:{key}")
            elif observed / base < minimum:
                issues.append(f"FAIL_ACTIVITY_COLLAPSE:{key}")
    coverage = sum(r["fillEvents"] > 0 for r in rows) / len(rows)
    if coverage == 0:
        issues.append("NOT_EXERCISED_ZERO_ACTIVITY")
    if contract.get("minTradeCoverage") is not None:
        floor = number(contract["minTradeCoverage"])
        if not ZERO < floor <= 1:
            raise ValueError("invalid trade-coverage floor")
        if number(coverage) < floor:
            issues.append("FAIL_ACTIVITY_COLLAPSE:coverage")
    gross = [number(r["endpointGross"][r["winnerPostHocOnly"]]) for r in rows]
    result = {"grossAggregate": str(sum(gross, ZERO)), "tradeCoverage": coverage,
              "marketCount": len(rows), "netLowerAggregate": None,
              "liveEligible": False, "forwardEligible": False,
              "generalization": "CONSUMED_DEVELOPMENT_ONLY_NOT_STABLE_PROFIT_PROOF"}
    if cost_ready:
        charges = [number(costs[str(r["marketId"])]) for r in rows]
        if any(c < 0 for c in charges):
            raise ValueError("cost upper bounds cannot be negative")
        lower = [g - c for g, c in zip(gross, charges)]
        total = sum(lower, ZERO)
        # Settlement sequence is chronological, not implicit market-id order.
        ordered = sorted(zip(rows, lower), key=lambda x: (x[0]["endMs"], x[0]["marketId"]))
        equity = peak = drawdown = ZERO
        for row, pnl in ordered:
            equity += pnl
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        worst_endpoint = min(number(r["endpointGross"][s]) - c
                             for r, c in zip(rows, charges) for s in SIDES)
        result.update(netLowerAggregate=str(total), netLowerPerMarket=[str(x) for x in lower],
                      leaveOneBestOut=str(total - max(lower)),
                      terminalSequenceDrawdown=str(drawdown),
                      worstEndpointLower=str(worst_endpoint))
        if total <= 0:
            issues.append("NET_LOWER_BOUND_NOT_POSITIVE")
        if len(rows) < 2:
            issues.append("CONCENTRATION_NOT_ASSESSABLE")
        elif total - max(lower) <= 0:
            issues.append("OUTLIER_DOMINATED")
        observed_risk = {"maxWorstEndpointLoss": max(ZERO, -worst_endpoint),
                         "maxReceiptPeakAbsNet": max(number(r["receiptClockPeakAbsNet"]) for r in rows),
                         "maxTerminalSequenceDrawdown": drawdown}
        if all(r.get("cashAndReservationsPeak") is not None for r in rows):
            observed_risk["maxCashPlusReservations"] = max(number(r["cashAndReservationsPeak"]) for r in rows)
        for key, value in observed_risk.items():
            if risk.get(key) is not None:
                cap = number(risk[key])
                if cap < 0:
                    raise ValueError("negative risk budget")
                if value > cap:
                    issues.append(f"RISK_LIMIT_EXCEEDED:{key}")
    result["issues"] = sorted(set(issues))
    result["verdict"] = "DEVELOPMENT_SCREEN_PASS_NOT_PROMOTED" if not issues else "NOT_PASSED"
    result["limitations"] = ["No future-value estimate or live profitability guarantee",
                             "No winner-group payoff-retention gate",
                             "No common-flow rebate cancellation; rebate omitted only from absolute lower bound",
                             "No assertion of intramarket mark-to-market drawdown or capital reuse"]
    return result
