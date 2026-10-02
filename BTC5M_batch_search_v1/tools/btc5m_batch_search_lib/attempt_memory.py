"""Read-only receipt-conditioned attempt memory; no send/cancel authority.

Only canonical ACTIVE terminal zero fills count as failed repair attempts.
Partial fills, pending orders and UNKNOWN transport are never labelled zero.
Failure attribution covers only FIFO lots present when the attempt was sent.
Completed lots automatically leave the active signal; no fixed cooldown or ban.
"""
from __future__ import annotations
from collections import deque
import math

EPS = 1e-8
SIDES = ("UP", "DOWN")


def effective_config(config: dict) -> dict:
    c = dict(config)
    if c["history"] == "attempts":
        c["age_weight"] = 0.
    elif c["history"] == "age":
        c["failure_weight"] = 0.
    elif c["history"] != "attempts_age":
        raise ValueError("Unknown history family")
    if c["curve"] not in ("linear", "sqrt", "saturating"):
        raise ValueError("Unknown formula family")
    if any(not math.isfinite(c[k]) or c[k] < 0 for k in ("failure_weight", "age_weight")):
        raise ValueError("Invalid coefficient")
    return c


def curve(value: float, kind: str) -> float:
    if value < 0 or not math.isfinite(value):
        raise ValueError("Invalid nonnegative history signal")
    if kind == "linear":
        return value
    if kind == "sqrt":
        return math.sqrt(value)
    if kind == "saturating":
        return value / (1. + value)
    raise ValueError("Unknown curve")


def extra_cost(config: dict, signal: dict, uncertainty_per_share: float, new_risk: float) -> float:
    c = effective_config(config)
    if not all(math.isfinite(v) and v >= 0 for v in (uncertainty_per_share, new_risk)):
        raise ValueError("Invalid uncertainty or risk")
    pressure = (c["failure_weight"] * curve(signal["failed_coverage_per_open_share"], c["curve"])
                + c["age_weight"] * curve(signal["fill_age_over_remaining_horizon"], c["curve"]))
    return uncertainty_per_share * pressure * new_risk


class AttemptMemory:
    def __init__(self, fee_function):
        self.fee = fee_function
        self.orders = {}
        self.receipts = {}
        self.lots = {s: deque() for s in SIDES}
        self.terminals = {}
        self.last_received_ms = None

    def register(self, key: str, op: dict, observation: dict):
        key = str(key)
        if key in self.orders:
            raise ValueError("Duplicate order registration")
        side = op["side"]
        other = "DOWN" if side == "UP" else "UP"
        net_request = op["qty"] - (self.fee(op["qty"], op["price"]) if op["route"] == "ACTIVE" else 0.)
        remaining = net_request
        covered = {}
        if op["route"] == "ACTIVE":
            for lot in self.lots[other]:
                take = min(remaining, lot["remaining"])
                if take > EPS:
                    covered[lot["key"]] = covered.get(lot["key"], 0.) + take
                    remaining -= take
                if remaining <= EPS:
                    break
        self.orders[key] = {"side": side, "route": op["route"], "qty": op["qty"],
                            "price": op["price"], "birth_ms": observation["now_ms"],
                            "targeted_fifo_lots": covered, "holding_side": other}

    def consume(self, rows: list[dict], now_ms: int):
        for row in rows:
            seq = row["sequence"]
            if seq in self.receipts:
                if self.receipts[seq] != row:
                    raise ValueError("Conflicting duplicate receipt")
                continue
            received = row["receive_ts"] / 1e6
            if received > now_ms + .001:
                raise ValueError("Future receipt")
            if self.last_received_ms is not None and received < self.last_received_ms - .001:
                raise ValueError("Noncausal receipt ordering")
            key = str(row["order_id"])
            origin = self.orders[key]
            side = "UP" if row["side"] == 1 else "DOWN"
            if side != origin["side"] or received < origin["birth_ms"] - .001:
                raise ValueError("Receipt side/time mismatch")
            p = row["price"] if side == "UP" else 1. - row["price"]
            net = row["qty"] - (0. if row["maker"] else self.fee(row["qty"], p))
            if net <= 0:
                raise ValueError("Nonpositive received net shares")
            other = "DOWN" if side == "UP" else "UP"
            qty = net
            while qty > EPS and self.lots[other]:
                lot = self.lots[other][0]
                take = min(qty, lot["remaining"])
                qty -= take
                lot["remaining"] -= take
                if lot["remaining"] <= EPS:
                    self.lots[other].popleft()
            if qty > EPS:
                self.lots[side].append({"key": key, "remaining": qty, "received_ms": received})
            self.receipts[seq] = dict(row)
            self.last_received_ms = received

    def terminal(self, key: str, status: str, gross_filled: float, now_ms: int):
        key = str(key)
        if status not in ("FILLED", "CANCELED", "EXPIRED"):
            return False  # Pending/cancel request/UNKNOWN are not terminal evidence.
        origin = self.orders[key]
        if gross_filled < -EPS or gross_filled > origin["qty"] + EPS or now_ms < origin["birth_ms"]:
            raise ValueError("Invalid terminal evidence")
        if status == "FILLED" and abs(gross_filled - origin["qty"]) > 1e-6:
            raise ValueError("FILLED status does not match full quantity")
        identity = {"status": status, "gross_filled": gross_filled}
        if key in self.terminals:
            previous = self.terminals[key]
            if previous["status"] != status or abs(previous["gross_filled"] - gross_filled) > 1e-8:
                raise ValueError("Conflicting terminal evidence")
            return False
        self.terminals[key] = dict(identity, available_ms=now_ms,
            confirmed_active_zero=origin["route"] == "ACTIVE" and gross_filled <= EPS,
            partial=EPS < gross_filled < origin["qty"] - EPS)
        return True

    def snapshot(self, observation: dict, model=None, latency_seconds: float = .25) -> dict:
        now = observation["now_ms"]
        if latency_seconds <= 0:
            raise ValueError("Positive physical latency required")
        horizon = max(0., observation["remaining_seconds"]) + latency_seconds
        result = {}
        for side in SIDES:
            lots = list(self.lots[side])
            qty = sum(l["remaining"] for l in lots)
            if any(l["received_ms"] > now + .001 for l in lots):
                raise ValueError("Future lot")
            by_order = {}
            for lot in lots:
                by_order[lot["key"]] = by_order.get(lot["key"], 0.) + lot["remaining"]
            failed = 0.
            for key, terminal in self.terminals.items():
                if terminal["available_ms"] > now:
                    raise ValueError("Future terminal")
                origin = self.orders[key]
                if terminal["confirmed_active_zero"] and origin["holding_side"] == side:
                    failed += sum(min(by_order.get(k, 0.), q) for k, q in origin["targeted_fifo_lots"].items())
            age = (sum(l["remaining"] * (now - l["received_ms"]) / 1000. for l in lots) / qty) if qty > EPS else 0.
            if model is not None:
                expected = sum(l["remaining"] for l in model.queues[side])
                if abs(expected - qty) > 1e-6:
                    raise ValueError("Read-only FIFO feature diverged from authoritative net FIFO")
            result[side] = {"open_net_qty": qty, "weighted_fill_age_seconds": age,
                "failed_coverage_per_open_share": failed / qty if qty > EPS else 0.,
                "fill_age_over_remaining_horizon": age / horizon,
                "has_open_lots": qty > EPS, "timed_lot_count": len(lots)}
        return {"asof_ms": now, "sides": result,
                "confirmed_zero_attempts": sum(t["confirmed_active_zero"] for t in self.terminals.values()),
                "partial_terminals": sum(t["partial"] for t in self.terminals.values()),
                "pending_attempts_not_zero": sum(o["route"] == "ACTIVE" and k not in self.terminals for k, o in self.orders.items()),
                "failure_scope": "ONLY_STILL_OPEN_FIFO_LOTS_PRESENT_AT_SEND",
                "no_fixed_delay_or_permanent_retry_ban": True}
