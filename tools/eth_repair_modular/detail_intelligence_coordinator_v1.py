"""Research-only, deterministic ranking; no execution, oracle, training or admission."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class DetailCandidate:
    candidate_id: str
    side: str
    role: str
    price: float
    qty: float
    opposite_lots: tuple[tuple[float, float], ...]
    pending_same_side_qty: float
    bid: float
    ask: float
    up_imbalance: float
    baseline_preferred: bool
    source: str = "INHERITED_FIRST_UNUSED_PAIR_LEGAL_LEVEL"
    route: str = "PASSIVE"


def score_candidate(candidate: DetailCandidate) -> dict:
    """Continuous preference only; book mark and distance are uncalibrated proxies."""
    c = candidate
    left = c.qty
    matched = credit = 0.0
    for lot_qty, lot_price in c.opposite_lots:
        paid = min(left, lot_qty)
        credit += paid * (1.0 - lot_price - c.price)
        matched += paid
        left -= paid
        if left <= 0.0:
            break
    spread = max(0.0, c.ask - c.bid)
    imbalance = max(-1.0, min(1.0, c.up_imbalance))
    signed_imbalance = imbalance if c.side == "UP" else -imbalance
    mark = (c.bid + c.ask) / 2.0 + signed_imbalance * spread / 2.0
    gap = sum(q for q, _ in c.opposite_lots)
    # Pending orders are competition, NOT confirmed payments. No debt is released here.
    competition_discount = gap / (gap + c.pending_same_side_qty) if gap > 0 else 1.0
    overflow_value = left * (mark - c.price)
    distance = max(0.0, c.bid - c.price)
    distance_discount = spread / (spread + distance) if spread + distance > 0 else 1.0
    # A strictly positive discount preserves ranking; never makes a candidate illegal.
    distance_discount = max(distance_discount, math.ulp(1.0))
    notional = c.price * c.qty
    total = (credit * competition_discount + overflow_value) / notional * distance_discount
    components = {
        "matchedQty": matched, "overflowQty": left,
        "oppositeUnmatchedQty": gap,
        "oppositeUnmatchedAverage": sum(q * p for q, p in c.opposite_lots) / gap if gap > 0 else 0.0,
        "pairMarginAgainstUnmatchedAverage": 1.0 - c.price - sum(q * p for q, p in c.opposite_lots) / gap if gap > 0 else 0.0,
        "fifoMarginalPairCredit": credit,
        "pendingCompetitionDiscount": competition_discount,
        "discountedPairValue": credit * competition_discount,
        "bookMarkProxy": mark, "overflowMarkValue": overflow_value,
        "priceDistance": distance, "priceDistanceDiscount": distance_discount,
        "notional": notional,
    }
    if not all(math.isfinite(v) for v in (*components.values(), total)):
        raise ValueError("Non-finite detail score")
    return {"candidate": asdict(c), "scoreComponents": components, "totalScore": total}


def rank_candidates(candidates: list[DetailCandidate]) -> list[dict]:
    """Never drops a legal candidate; negative scores remain eligible."""
    scored = [score_candidate(candidate) for candidate in candidates]
    return sorted(scored, key=lambda row: (
        -row["totalScore"], not row["candidate"]["baseline_preferred"],
        row["candidate"]["candidate_id"],
    ))
