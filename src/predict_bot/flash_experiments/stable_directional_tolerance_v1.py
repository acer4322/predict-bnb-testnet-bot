from __future__ import annotations

from typing import Any

from predict_bot import predict_wallet_maker_ebm_strategy_v1 as maker_ebm

OPEN_MID_MIN_SECONDS_LEFT = 60.0
GRID = float(maker_ebm.GRID)
SHARES = float(maker_ebm.SHARES_PER_ORDER)
MAX_PAIR = float(maker_ebm.MAX_PAIR_PRICE_SUM)
MIN_TICK = int(round(float(maker_ebm.MIN_PRICE) / GRID))

_state: dict[int, dict[str, Any]] = {}


def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return x


def _market_state(market_id: int) -> dict[str, Any]:
    st = _state.get(market_id)
    if st is None:
        st = {
            "seedMirrored": False,
            "seedSide": None,
            "seedShares": 0.0,
        }
        _state.clear()  # plugin is one-market-at-a-time; avoid stale carryover
        _state[market_id] = st
    return st


def _maker_only_shares(portfolio: dict[str, Any], st: dict[str, Any]) -> tuple[float, float]:
    up = float(_num(portfolio.get("upShares")) or 0.0)
    down = float(_num(portfolio.get("downShares")) or 0.0)
    if st.get("seedMirrored"):
        sh = float(st.get("seedShares") or 0.0)
        if st.get("seedSide") == "UP":
            up = max(0.0, up - sh)
        elif st.get("seedSide") == "DOWN":
            down = max(0.0, down - sh)
    return up, down


def _dominant_side(portfolio: dict[str, Any], st: dict[str, Any]) -> str | None:
    up, down = _maker_only_shares(portfolio, st)
    if abs(up - down) <= 1e-9:
        return None
    return "UP" if up > down else "DOWN"


def _prior_order_by_side(previous: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(previous, dict):
        return {}
    md = previous.get("makerDecision")
    if not isinstance(md, dict):
        return {}
    rows = md.get("orders")
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        side = str(row.get("side") or "").upper()
        if side in {"UP", "DOWN"} and row.get("priceTick") is not None and row.get("price") is not None:
            out[side] = dict(row)
    return out


def _fresh_quote(snapshot: dict[str, Any], side: str) -> dict[str, Any] | None:
    tick = maker_ebm._quote_tick(snapshot, side, 1)
    if tick is None:
        return None
    return {
        "side": side,
        "priceTick": int(tick),
        "price": round(int(tick) * GRID, 2),
        "shares": SHARES,
        "offsetTicks": 1,
        "origin": "STABLE_DIRECTIONAL_TOLERANCE_V1",
    }


def _pair_cap(rows: list[dict[str, Any]], prior_sides: set[str]) -> list[dict[str, Any]]:
    if len(rows) != 2:
        return rows
    if sum(float(r["price"]) for r in rows) <= MAX_PAIR + 1e-9:
        return rows

    # Prefer preserving already-resting quotes. If only one side is new, move only the new side deeper.
    new_rows = [r for r in rows if str(r["side"]) not in prior_sides]
    candidate = new_rows[0] if len(new_rows) == 1 else max(rows, key=lambda r: float(r["price"]))
    while sum(float(r["price"]) for r in rows) > MAX_PAIR + 1e-9:
        nxt = int(candidate["priceTick"]) - 1
        if nxt < MIN_TICK:
            return []
        candidate["priceTick"] = nxt
        candidate["price"] = round(nxt * GRID, 2)
        candidate["offsetTicks"] = int(candidate.get("offsetTicks") or 1) + 1
    return rows


def evaluate(context: dict[str, Any]) -> dict[str, Any] | None:
    market_id = int(context.get("marketId") or 0)
    if market_id <= 0:
        return None
    st = _market_state(market_id)

    snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
    portfolio = context.get("portfolio") if isinstance(context.get("portfolio"), dict) else {}
    direction = context.get("direction") if isinstance(context.get("direction"), dict) else {}
    base_state = context.get("baseState") if isinstance(context.get("baseState"), dict) else {}
    previous = context.get("previousDecision") if isinstance(context.get("previousDecision"), dict) else None

    # Mirror the already-observed 8784 OPEN_SEED once so forward comparison keeps the replay's seed boundary.
    seed = base_state.get("seed") if isinstance(base_state.get("seed"), dict) else None
    if seed and not st.get("seedMirrored"):
        side = str(seed.get("side") or "").upper()
        price = _num(seed.get("ask"))
        shares = _num(seed.get("shares"))
        if side in {"UP", "DOWN"} and price is not None and shares is not None and 0.0 < price <= 1.0 and shares > 0.0:
            st["seedMirrored"] = True
            st["seedSide"] = side
            st["seedShares"] = float(shares)
            return {
                "desiredPortfolioAction": "OPEN_SEED",
                "executionChoice": "TAKER",
                "primaryReason": "MIRROR_BASE_OPEN_SEED_ONCE",
                "takerFill": {
                    "side": side,
                    "price": float(price),
                    "shares": float(shares),
                    "purpose": "OPEN_SEED_MIRROR",
                },
                "seedPolicy": "MIRROR_8784_ONCE",
                "collected": {
                    "makerOnlyDominantSide": _dominant_side(portfolio, st),
                    "simple3Side": str(direction.get("side") or "NEUTRAL"),
                },
            }

    seconds_left = _num(snapshot.get("secondsLeft"))
    if seconds_left is None or seconds_left <= OPEN_MID_MIN_SECONDS_LEFT:
        # Tail stays exactly on the sandbox/base Maker EBM path.
        return {"seedPolicy": "MIRROR_8784_ONCE" if st.get("seedMirrored") else "WAITING_BASE_SEED"}

    dom = _dominant_side(portfolio, st)
    dir_side = str(direction.get("side") or "NEUTRAL").upper()
    blocked: str | None = None
    if dom in {"UP", "DOWN"} and dir_side in {"UP", "DOWN"} and dom != dir_side:
        blocked = dom

    desired_sides = [s for s in ("UP", "DOWN") if s != blocked]
    prior = _prior_order_by_side(previous)
    rows: list[dict[str, Any]] = []
    prior_sides: set[str] = set()
    for side in desired_sides:
        if side in prior:
            row = dict(prior[side])
            row["shares"] = SHARES
            row["origin"] = "STABLE_DIRECTIONAL_TOLERANCE_V1_RESTING"
            rows.append(row)
            prior_sides.add(side)
        else:
            row = _fresh_quote(snapshot, side)
            if row is not None:
                rows.append(row)

    rows = _pair_cap(rows, prior_sides)
    decision = "QUOTE" if rows else "IDLE"
    reason = "HEADWIND_DOMINANT_BLOCK" if blocked else "TAILWIND_OR_FLAT_STABLE_BOTH"

    return {
        "desiredPortfolioAction": "PASSIVE_MAINTAIN" if rows else "HOLD",
        "executionChoice": "MAKER" if rows else "WAIT",
        "primaryReason": reason,
        "makerDecision": {
            "decision": decision,
            "reason": reason,
            "orders": rows,
            "secondsLeft": seconds_left,
            "inventoryPolicy": "MAKER_ONLY_DOMINANT_DIRECTIONAL_TOLERANCE",
            "restingPolicy": "PRESERVE_PREVIOUS_SIDE_PRICE_WHILE_SIDE_REMAINS_DESIRED",
            "dominantSide": dom,
            "directionSide": dir_side,
            "blockedSide": blocked,
            "paperOnly": True,
            "targetEventsUsed": False,
        },
        "seedPolicy": "MIRROR_8784_ONCE" if st.get("seedMirrored") else "WAITING_BASE_SEED",
        "collected": {
            "makerOnlyDominantSide": dom,
            "simple3Side": dir_side,
            "headwindDominantBlocked": blocked is not None,
            "stableDesiredSides": desired_sides,
        },
    }
