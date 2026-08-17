from __future__ import annotations

from collections import defaultdict
from typing import Any

import analyze_target_maker_taker_inventory_lifecycle_v1 as base

REPORT_VERSION = "TARGET_MAKER_TAKER_INVENTORY_LIFECYCLE_V1_1_DEDUP_PARENT_ACTIONS"
_BASE_REPLAY = base._replay


def _dedupe_action_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    seen: set[str] = set()
    duplicates = 0
    deduped: list[dict[str, Any]] = []
    for row in rows:
        parent_id = str(row["parent_id"])
        if parent_id in seen:
            duplicates += 1
            continue
        seen.add(parent_id)
        deduped.append(dict(row))

    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in deduped:
        by_market[int(row["market_id"])].append(row)
    for market_rows in by_market.values():
        market_rows.sort(
            key=lambda row: (
                int(row["first_event_ms"]),
                int(row["last_event_ms"]),
                str(row["parent_id"]),
            )
        )
        for index, row in enumerate(market_rows, 1):
            row["taker_index_market"] = index
            row["is_first_taker"] = int(index == 1)

    deduped.sort(
        key=lambda row: (
            int(row["first_event_ms"]),
            int(row["market_id"]),
            str(row["parent_id"]),
        )
    )
    return deduped, {
        "inputRows": len(rows),
        "outputRows": len(deduped),
        "duplicateRowsRemoved": duplicates,
        "uniqueParents": len(seen),
    }


def _rebuild_market_action_fields(
    market_payloads: dict[int, dict[str, Any]],
    actions: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in actions:
        by_market[int(row["market_id"])].append(row)

    for market_id, payload in market_payloads.items():
        rows = by_market.get(int(market_id), [])
        payload["takerParents"] = len(rows)
        payload["takerBalanceActions"] = sum(
            str(row["semantic_primary"]) == "BALANCE" for row in rows
        )
        payload["takerFlipActions"] = sum(
            str(row["semantic_primary"]) == "FLIP" for row in rows
        )
        payload["takerAddActions"] = sum(
            str(row["semantic_primary"]) == "ADD" for row in rows
        )
        payload["takerOpposeMakerHeavyActions"] = sum(
            str(row["maker_relation"]) == "OPPOSE_MAKER_HEAVY" for row in rows
        )
        payload["takerBalanceComponentShares"] = sum(
            float(row["balance_component_shares"]) for row in rows
        )
        payload["takerMakerOffsetComponentShares"] = sum(
            float(row["maker_offset_component_shares"]) for row in rows
        )
        payload["takerRepairNotionalUsdt"] = sum(
            float(row["notional_usdt"])
            for row in rows
            if str(row["maker_relation"]) == "OPPOSE_MAKER_HEAVY"
        )
    return market_payloads


def _replay(
    events: list[dict[str, Any]],
    parents: list[dict[str, Any]],
    *,
    cohort: dict[int, str],
    phase_index: dict[int, dict[str, Any]],
    market_results: dict[int, dict[str, Any]],
    max_phase_lag_ms: int,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    raw_actions, market_payloads = _BASE_REPLAY(
        events,
        parents,
        cohort=cohort,
        phase_index=phase_index,
        market_results=market_results,
        max_phase_lag_ms=max_phase_lag_ms,
    )
    actions, audit = _dedupe_action_rows(raw_actions)
    market_payloads = _rebuild_market_action_fields(market_payloads, actions)

    expected = {
        str(row["parent_id"])
        for row in parents
        if str(row.get("role") or "") == "TAKER"
    }
    observed = {str(row["parent_id"]) for row in actions}
    if observed != expected:
        missing = len(expected - observed)
        unexpected = len(observed - expected)
        raise RuntimeError(
            "Taker parent/action invariant failed: "
            f"expected={len(expected)} observed={len(observed)} "
            f"missing={missing} unexpected={unexpected}"
        )
    if len(actions) != len(observed):
        raise RuntimeError("Taker parent/action invariant failed: duplicate parent_id remained")

    print(
        "      parent-action dedupe: "
        f"raw={audit['inputRows']:,} unique={audit['outputRows']:,} "
        f"removed={audit['duplicateRowsRemoved']:,}",
        flush=True,
    )
    return actions, market_payloads


def main() -> int:
    original_replay = base._replay
    original_version = base.REPORT_VERSION
    base._replay = _replay
    base.REPORT_VERSION = REPORT_VERSION
    try:
        return base.main()
    finally:
        base._replay = original_replay
        base.REPORT_VERSION = original_version


if __name__ == "__main__":
    raise SystemExit(main())
