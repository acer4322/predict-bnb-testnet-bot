from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UPDOWN_TITLE = re.compile(
    r"^(Bitcoin|BTC|Ethereum|ETH|BNB) Up or Down - .+,\s*(\d{1,2})(?::(\d{2}))?(AM|PM)-(\d{1,2})(?::(\d{2}))?(AM|PM) ET$",
    re.I,
)
ASSET_ALIASES = {
    "BITCOIN": "BTC",
    "BTC": "BTC",
    "ETHEREUM": "ETH",
    "ETH": "ETH",
    "BNB": "BNB",
}


def minute_of_day(hour: int, minute: int, ampm: str) -> int:
    hour %= 12
    if ampm.upper() == "PM":
        hour += 12
    return hour * 60 + minute


def market_descriptor(title: Any) -> tuple[str, int] | None:
    match = UPDOWN_TITLE.match(str(title or "").strip())
    if not match:
        return None
    asset = ASSET_ALIASES.get(str(match.group(1) or "").upper())
    if not asset:
        return None
    sh, sm, sap, eh, em, eap = (
        int(match.group(2)), int(match.group(3) or 0), match.group(4),
        int(match.group(5)), int(match.group(6) or 0), match.group(7),
    )
    start = minute_of_day(sh, sm, sap)
    end = minute_of_day(eh, em, eap)
    if end < start:
        end += 1440
    duration = end - start
    return asset, duration if duration > 0 else 0


def is_crypto_duration(title: Any, assets: set[str], durations: set[int]) -> bool:
    descriptor = market_descriptor(title)
    return bool(descriptor and descriptor[0] in assets and descriptor[1] in durations)


# Backward-compatible helper retained for callers/tests using the old name.
def is_eth_bnb_5m(title: Any) -> bool:
    return is_crypto_duration(title, {"BTC", "ETH", "BNB"}, {5})


def parse_assets(value: str) -> set[str]:
    rows = {str(x).strip().upper() for x in str(value).split(",") if str(x).strip()}
    aliases = {ASSET_ALIASES.get(x, x) for x in rows}
    return {x for x in aliases if x in {"BTC", "ETH", "BNB"}}


def parse_durations(value: str) -> set[int]:
    out: set[int] = set()
    for part in str(value).split(","):
        text = part.strip().lower().removesuffix("m")
        if text:
            out.add(int(text))
    return {x for x in out if x > 0}


def parse_iso(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test which inventory objective best explains crypto Up/Down maker parent completions. Read-only."
    )
    parser.add_argument("--input", default="data/target_maker_hash_profile.json")
    parser.add_argument("--output", default="data/target_inventory_objective_profile.json")
    parser.add_argument("--assets", default="BTC,ETH,BNB", help="Comma-separated assets; default BTC,ETH,BNB")
    parser.add_argument("--durations", default="5", help="Comma-separated minute durations; e.g. 5 or 5,15")
    args = parser.parse_args()

    assets = parse_assets(args.assets)
    durations = parse_durations(args.durations)
    if not assets:
        raise SystemExit("--assets must include one or more of BTC,ETH,BNB")
    if not durations:
        raise SystemExit("--durations must include at least one positive minute value")

    source = Path(args.input)
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = payload.get("parentOrdersByMakerHash")
    if not isinstance(rows, list):
        raise SystemExit("input does not contain parentOrdersByMakerHash")

    recognized_scope: Counter[str] = Counter()
    for row in rows:
        if not isinstance(row, dict):
            continue
        descriptor = market_descriptor(row.get("marketTitle"))
        if descriptor:
            recognized_scope[f"{descriptor[0]}-{descriptor[1]}m"] += 1

    filtered = [
        r for r in rows
        if isinstance(r, dict) and is_crypto_duration(r.get("marketTitle"), assets, durations)
    ]
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in filtered:
        by_market[int(row.get("marketId") or 0)].append(row)

    share_reducing = cost_reducing = pp_reducing = 0
    directional = 0
    immediate_pairing = fully_pairing = partial_pairing = no_pairing = 0
    wc_improving = wc_worsening = 0
    total_parent_shares = 0.0
    total_immediate_paired_shares = 0.0
    wc_deltas: list[float] = []
    final_wc: list[float] = []
    final_best: list[float] = []
    final_pair_coverage: list[float] = []
    final_cost_imbalance: list[float] = []
    final_pp_imbalance: list[float] = []

    examples: list[dict[str, Any]] = []

    for market_id, stream in by_market.items():
        stream.sort(key=lambda r: parse_iso(r.get("lastExecutedAt") or r.get("firstExecutedAt")))
        shares = {"UP": 0.0, "DOWN": 0.0}
        costs = {"UP": 0.0, "DOWN": 0.0}
        pps = {"UP": 0.0, "DOWN": 0.0}

        for row in stream:
            side = str(row.get("outcome") or "").upper()
            if side not in {"UP", "DOWN"}:
                continue
            qty = float(row.get("totalShares") or 0.0)
            price = float(row.get("price") or 0.0)
            cost = float(row.get("totalCostUsdtApprox") or qty * price)
            pp = float(row.get("totalPotentialProfitUsdtApprox") or qty * (1.0 - price))

            before_share_gap = abs(shares["UP"] - shares["DOWN"])
            before_cost_gap = abs(costs["UP"] - costs["DOWN"])
            before_pp_gap = abs(pps["UP"] - pps["DOWN"])
            before_paired = min(shares["UP"], shares["DOWN"])
            before_total_cost = costs["UP"] + costs["DOWN"]
            before_wc = before_paired - before_total_cost

            shares[side] += qty
            costs[side] += cost
            pps[side] += pp

            after_share_gap = abs(shares["UP"] - shares["DOWN"])
            after_cost_gap = abs(costs["UP"] - costs["DOWN"])
            after_pp_gap = abs(pps["UP"] - pps["DOWN"])
            after_paired = min(shares["UP"], shares["DOWN"])
            after_total_cost = costs["UP"] + costs["DOWN"]
            after_wc = after_paired - after_total_cost

            if before_share_gap > 1e-12 or before_cost_gap > 1e-12 or before_pp_gap > 1e-12:
                directional += 1
                if after_share_gap < before_share_gap - 1e-12:
                    share_reducing += 1
                if after_cost_gap < before_cost_gap - 1e-12:
                    cost_reducing += 1
                if after_pp_gap < before_pp_gap - 1e-12:
                    pp_reducing += 1

            paired_increment = max(0.0, after_paired - before_paired)
            total_parent_shares += qty
            total_immediate_paired_shares += paired_increment
            if paired_increment <= 1e-12:
                no_pairing += 1
            else:
                immediate_pairing += 1
                if qty > 0 and paired_increment >= qty * 0.99:
                    fully_pairing += 1
                else:
                    partial_pairing += 1

            delta_wc = after_wc - before_wc
            wc_deltas.append(delta_wc)
            if delta_wc > 1e-12:
                wc_improving += 1
            elif delta_wc < -1e-12:
                wc_worsening += 1

            if len(examples) < 100 and before_share_gap > 0 and after_share_gap > before_share_gap and delta_wc > 0:
                examples.append(
                    {
                        "marketId": market_id,
                        "marketTitle": row.get("marketTitle"),
                        "side": side,
                        "price": price,
                        "shares": qty,
                        "beforeShareGap": before_share_gap,
                        "afterShareGap": after_share_gap,
                        "pairedIncrement": paired_increment,
                        "worstCasePnlDelta": delta_wc,
                        "note": "raw share imbalance increased while pooled worst-case settlement PnL improved",
                    }
                )

        total_cost = costs["UP"] + costs["DOWN"]
        pair_shares = min(shares["UP"], shares["DOWN"])
        total_shares = shares["UP"] + shares["DOWN"]
        final_wc.append(pair_shares - total_cost)
        final_best.append(max(shares["UP"], shares["DOWN"]) - total_cost)
        final_pair_coverage.append((2.0 * pair_shares / total_shares) if total_shares > 0 else 0.0)
        final_cost_imbalance.append(abs(costs["UP"] - costs["DOWN"]))
        final_pp_imbalance.append(abs(pps["UP"] - pps["DOWN"]))

    total_parents = len(filtered)
    filter_text = f"assets={','.join(sorted(assets))}; durations={','.join(str(x) for x in sorted(durations))}m target maker parents only"
    summary = {
        "source": str(source),
        "filter": filter_text,
        "recognizedSourceScopeParentCounts": dict(sorted(recognized_scope.items())),
        "markets": len(by_market),
        "parentOrdersApprox": total_parents,
        "balanceObjectiveTests": {
            "directionalDecisions": directional,
            "reducesAbsoluteShareImbalanceShare": share_reducing / directional if directional else None,
            "reducesAbsoluteCostImbalanceShare": cost_reducing / directional if directional else None,
            "reducesAbsolutePotentialProfitUnitImbalanceShare": pp_reducing / directional if directional else None,
        },
        "pooledPairing": {
            "parentsImmediatelyIncreasingPairedShares": immediate_pairing,
            "shareParentsImmediatelyIncreasingPairedShares": immediate_pairing / total_parents if total_parents else None,
            "fullyPairingParents": fully_pairing,
            "partiallyPairingParents": partial_pairing,
            "noImmediatePairingParents": no_pairing,
            "shareOfObservedParentSharesImmediatelyPaired": total_immediate_paired_shares / total_parent_shares if total_parent_shares else None,
        },
        "worstCaseSettlementPnlResponse": {
            "improvingParents": wc_improving,
            "worseningParents": wc_worsening,
            "improvingShare": wc_improving / total_parents if total_parents else None,
            "medianParentDeltaUsdt": median(wc_deltas),
        },
        "marketFinalObservedState": {
            "medianWorstCaseSettlementPnlUsdt": median(final_wc),
            "marketsWithPositiveWorstCaseSettlementPnl": sum(1 for x in final_wc if x > 0),
            "shareMarketsWithPositiveWorstCaseSettlementPnl": sum(1 for x in final_wc if x > 0) / len(final_wc) if final_wc else None,
            "medianBestCaseSettlementPnlUsdt": median(final_best),
            "medianPairedShareCoverage": median(final_pair_coverage),
            "medianAbsoluteCostImbalanceUsdt": median(final_cost_imbalance),
            "medianAbsolutePotentialProfitUnitImbalanceUsdt": median(final_pp_imbalance),
        },
    }

    output = {
        "summary": summary,
        "examplesShareGapWorsenedButWorstCasePnlImproved": examples,
        "caveats": [
            "All quantities are observed filled amounts reconstructed from public matches, not original requested parent sizes.",
            "Worst-case settlement PnL here is min(observed UP shares, observed DOWN shares) minus observed cumulative fill cost; it ignores maker rebates and any activity not present in the input match sample.",
            "A controller can maintain resting orders on both outcomes while public fills appear one-sided, so fill sequence alone cannot prove which quotes were live or cancelled.",
        ],
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"wrote {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())