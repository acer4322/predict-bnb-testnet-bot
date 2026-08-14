from __future__ import annotations

import json
import math
from typing import Any, Iterable


WEI = 10**18


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _wei(value: Any) -> float:
    return _finite(value) / WEI


def _participant(raw: dict[str, Any], *, wallet: str, role: str, order_hash: str | None) -> dict[str, Any]:
    candidates = [raw.get("taker")] if role.upper() == "TAKER" else raw.get("makers", [])
    if not isinstance(candidates, list):
        candidates = []
    wallet = wallet.lower()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        signer = str(candidate.get("signer") or "").lower()
        candidate_hash = str(candidate.get("hash") or "")
        if signer == wallet and (not order_hash or candidate_hash == order_hash):
            return candidate
    for candidate in candidates:
        if isinstance(candidate, dict) and str(candidate.get("signer") or "").lower() == wallet:
            return candidate
    return {}


def fee_for_event(event: dict[str, Any], wallet: str) -> tuple[str | None, float]:
    raw_value = event.get("raw_json")
    try:
        raw = json.loads(raw_value) if isinstance(raw_value, str) else dict(raw_value or {})
    except (TypeError, ValueError, json.JSONDecodeError):
        raw = {}
    participant = _participant(
        raw,
        wallet=wallet,
        role=str(event.get("role") or ""),
        order_hash=str(event.get("order_hash") or "") or None,
    )
    fee = participant.get("fee") if isinstance(participant.get("fee"), dict) else {}
    fee_type = str(fee.get("type") or "").upper() or None
    return fee_type, max(0.0, _wei(fee.get("amount")))


def account_target_market(events: Iterable[dict[str, Any]], *, winner: str, wallet: str) -> dict[str, Any]:
    """Reconstruct cash flow and settlement inventory for retained target fills.

    BID is treated as buying outcome shares and ASK as selling them. Predict share
    fees reduce received/remaining shares; collateral fees reduce cash proceeds.
    """

    events = [dict(event) for event in events]
    winner = winner.upper()
    if winner not in {"UP", "DOWN"}:
        raise ValueError(f"unsupported winner: {winner}")

    totals = {
        "UP": {"grossBought": 0.0, "grossSold": 0.0, "shareFees": 0.0, "netShares": 0.0},
        "DOWN": {"grossBought": 0.0, "grossSold": 0.0, "shareFees": 0.0, "netShares": 0.0},
    }
    roles = {
        "MAKER": {"cashFlow": 0.0, "collateralFees": 0.0, "netShares": {"UP": 0.0, "DOWN": 0.0}, "events": 0, "notional": 0.0},
        "TAKER": {"cashFlow": 0.0, "collateralFees": 0.0, "netShares": {"UP": 0.0, "DOWN": 0.0}, "events": 0, "notional": 0.0},
    }
    cash_flow = 0.0
    collateral_fees = 0.0
    buy_notional = 0.0
    sell_proceeds = 0.0
    event_count = 0

    for source in events:
        event = dict(source)
        side = str(event.get("side") or "").upper()
        role = str(event.get("role") or "").upper()
        quote_type = str(event.get("quote_type") or "").upper()
        if side not in totals or role not in roles or quote_type not in {"BID", "ASK"}:
            continue
        shares = max(0.0, _finite(event.get("shares")))
        price = min(1.0, max(0.0, _finite(event.get("price"))))
        notional = shares * price
        fee_type, fee_amount = fee_for_event(event, wallet)
        share_fee = fee_amount if fee_type == "SHARES" else 0.0
        collateral_fee = fee_amount if fee_type == "COLLATERAL" else 0.0

        inventory_delta = shares - share_fee if quote_type == "BID" else -(shares + share_fee)
        event_cash_flow = -notional if quote_type == "BID" else notional
        event_cash_flow -= collateral_fee

        totals[side]["grossBought" if quote_type == "BID" else "grossSold"] += shares
        totals[side]["shareFees"] += share_fee
        totals[side]["netShares"] += inventory_delta
        roles[role]["cashFlow"] += event_cash_flow
        roles[role]["collateralFees"] += collateral_fee
        roles[role]["netShares"][side] += inventory_delta
        roles[role]["events"] += 1
        roles[role]["notional"] += notional
        cash_flow += event_cash_flow
        collateral_fees += collateral_fee
        event_count += 1
        if quote_type == "BID":
            buy_notional += notional
        else:
            sell_proceeds += notional

    payout = totals[winner]["netShares"]
    net_pnl = cash_flow + payout
    capital_at_risk = buy_notional + collateral_fees
    status = "NO_TRADE" if not event_count else "WIN" if net_pnl > 1e-9 else "LOSS" if net_pnl < -1e-9 else "FLAT"

    role_results: dict[str, Any] = {}
    for role, values in roles.items():
        role_payout = values["netShares"][winner]
        role_pnl = values["cashFlow"] + role_payout
        role_results[role.lower()] = {
            "events": values["events"],
            "notionalUsdt": values["notional"],
            "collateralFeesUsdt": values["collateralFees"],
            "payoutUsdt": role_payout,
            "netPnlUsdt": role_pnl,
        }

    up_shares = totals["UP"]["netShares"]
    down_shares = totals["DOWN"]["netShares"]
    share_side = "UP" if up_shares > down_shares else "DOWN" if down_shares > up_shares else None
    # Cost-weighted conviction intentionally ignores cheap high-share insurance.
    up_cost = sum(
        max(0.0, _finite(x.get("shares"))) * min(1.0, max(0.0, _finite(x.get("price"))))
        for x in events if str(x.get("side") or "").upper() == "UP" and str(x.get("quote_type") or "").upper() == "BID"
    )
    down_cost = sum(
        max(0.0, _finite(x.get("shares"))) * min(1.0, max(0.0, _finite(x.get("price"))))
        for x in events if str(x.get("side") or "").upper() == "DOWN" and str(x.get("quote_type") or "").upper() == "BID"
    )
    capital_side = "UP" if up_cost > down_cost else "DOWN" if down_cost > up_cost else None

    return {
        "eventCount": event_count,
        "buyNotionalUsdt": buy_notional,
        "sellProceedsUsdt": sell_proceeds,
        "collateralFeesUsdt": collateral_fees,
        "payoutUsdt": payout,
        "netPnlUsdt": net_pnl,
        "netRoi": net_pnl / capital_at_risk if capital_at_risk else None,
        "status": status,
        "up": totals["UP"],
        "down": totals["DOWN"],
        "maker": role_results["maker"],
        "taker": role_results["taker"],
        "shareConvictionSide": share_side,
        "capitalConvictionSide": capital_side,
        "shareDirectionCorrect": share_side == winner if share_side else None,
        "capitalDirectionCorrect": capital_side == winner if capital_side else None,
        "accountingMode": "RETAINED_MATCH_CASHFLOW_PLUS_OFFICIAL_PAYOUT",
        "assumptions": {
            "bid": "buy shares; SHARE fee reduces received shares; COLLATERAL fee increases cash cost",
            "ask": "sell shares; SHARE fee reduces remaining inventory; COLLATERAL fee reduces proceeds",
            "currentCoverage": "retained dataset is BID-only; ASK path is implemented for future observations",
        },
    }
