from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot.execution_tape_archive_v1 import load_archive  # noqa: E402
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots  # noqa: E402

OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
ARCHIVE_DIR = ROOT / "data/execution_tape_v1/markets"
VERSION = "FROZEN_R2_DECISION_BOOK_FROM_EXECUTION_TAPE_V1_PREFLIGHT"
MARKET_ID = 1522232
EPS = 1e-9


def apply_changes(book: dict[str, dict[float, float]], changes: dict[str, Any]) -> None:
    for side in ("bids", "asks"):
        levels = book[side]
        for item in changes.get(side, []) or []:
            price, _before, after, _delta = map(float, item)
            if after <= EPS:
                levels.pop(price, None)
            else:
                levels[price] = after


def state_hash(rows: list[dict[str, Any]]) -> str:
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def main() -> None:
    path = ARCHIVE_DIR / f"{MARKET_ID}.json.xz"
    tape = load_archive(path)
    updates = list(tape.get("updates") or [])
    snapshots = load_public_snapshots(MARKET_ID)
    updates.sort(key=lambda row: (int(row[0]), int(row[1])))
    checkpoints = [row for row in updates if int(row[3]) == 1 and row[4] is not None and row[5] is not None]
    book: dict[str, dict[float, float]] = {"bids": {}, "asks": {}}
    update_index = 0
    last_source_ms: int | None = None
    last_received_ms: int | None = None
    rows = []
    for snapshot in snapshots:
        sampled_at = int(snapshot["sampledAtMs"])
        while update_index < len(updates) and int(updates[update_index][0]) <= sampled_at:
            update = updates[update_index]
            source_ms, received_ms = int(update[0]), int(update[1])
            if int(update[3]) == 1 and update[4] is not None and update[5] is not None:
                book = {
                    "bids": {float(k): float(v) for k, v in (update[4] or {}).items()},
                    "asks": {float(k): float(v) for k, v in (update[5] or {}).items()},
                }
            else:
                apply_changes(book, update[6] or {})
            last_source_ms = source_ms
            last_received_ms = received_ms
            update_index += 1
        ready = bool(book["bids"] and book["asks"] and last_source_ms is not None)
        native_bid = max(book["bids"]) if ready else None
        native_ask = min(book["asks"]) if ready else None
        expected_bid = snapshot.get("predictUpBid")
        expected_ask = snapshot.get("predictUpAsk")
        bid_error = None if native_bid is None or expected_bid is None else abs(native_bid - float(expected_bid))
        ask_error = None if native_ask is None or expected_ask is None else abs(native_ask - float(expected_ask))
        rows.append(
            {
                "sampledAtMs": sampled_at,
                "ready": ready,
                "lastSourceMs": last_source_ms,
                "lastReceivedMs": last_received_ms,
                "sourceAgeMs": None if last_source_ms is None else sampled_at - last_source_ms,
                "receiptAgeMs": None if last_received_ms is None else sampled_at - last_received_ms,
                "nativeBid": native_bid,
                "nativeAsk": native_ask,
                "snapshotPredictUpBid": expected_bid,
                "snapshotPredictUpAsk": expected_ask,
                "bidAbsError": bid_error,
                "askAbsError": ask_error,
                "topExactOneTick": bool(
                    bid_error is not None
                    and ask_error is not None
                    and bid_error <= 0.010000001
                    and ask_error <= 0.010000001
                ),
                "sourceStrictPast": last_source_ms is not None and last_source_ms <= sampled_at,
                "receiptAvailableByDecision": last_received_ms is not None and last_received_ms <= sampled_at,
            }
        )
    count = len(rows)
    ready_count = sum(row["ready"] for row in rows)
    exact_count = sum(row["topExactOneTick"] for row in rows)
    source_past_count = sum(row["sourceStrictPast"] for row in rows)
    receipt_past_count = sum(row["receiptAvailableByDecision"] for row in rows)
    ready_fraction = ready_count / count if count else 0.0
    exact_fraction = exact_count / count if count else 0.0
    source_past_fraction = source_past_count / count if count else 0.0
    receipt_past_fraction = receipt_past_count / count if count else 0.0
    support = (
        bool(updates)
        and bool(checkpoints)
        and ready_fraction >= 0.99
        and exact_fraction >= 0.99
        and source_past_fraction == 1.0
    )
    decision = (
        "KEEP_BUILD_ARCHIVE_BACKED_FROZEN_R2_DECISION_BOOK_ADAPTER"
        if support
        else "REJECT_ARCHIVE_DECISION_BOOK_FALLBACK_INSUFFICIENT_FIDELITY"
    )
    payload = {
        "version": VERSION,
        "researchOnly": True,
        "graduationEligible": False,
        "preregistration": "frozen_r2_decision_book_from_execution_tape_v1_preflight_preregistered.json",
        "marketId": MARKET_ID,
        "archive": str(path.relative_to(ROOT)),
        "archiveVersion": tape.get("version"),
        "hypothesis": "Predict Execution Tape V1 preserved the source-time checkpoints/deltas pruned from the live 72-hour public-book DB and can reconstruct the Frozen R2 decision book without changing HFT receipt-time execution semantics.",
        "counts": {
            "updates": len(updates),
            "checkpoints": len(checkpoints),
            "r2DecisionSnapshots": count,
            "readySnapshots": ready_count,
            "topWithinOneTickSnapshots": exact_count,
            "sourceStrictPastSnapshots": source_past_count,
            "receiptAvailableByDecisionSnapshots": receipt_past_count,
        },
        "coverage": {
            "readyFraction": ready_fraction,
            "topWithinOneTickFraction": exact_fraction,
            "sourceStrictPastFraction": source_past_fraction,
            "receiptAvailableByDecisionFractionAudit": receipt_past_fraction,
        },
        "stateDigest": state_hash(rows),
        "firstRows": rows[:5],
        "mismatchRows": [row for row in rows if not row["topExactOneTick"]][:10],
        "gates": {
            "archiveHasUpdatesAndCheckpoints": bool(updates) and bool(checkpoints),
            "decisionBookReadyAtLeast99Pct": ready_fraction >= 0.99,
            "snapshotTopWithinOneTickAtLeast99Pct": exact_fraction >= 0.99,
            "sourceTimestampStrictPast": source_past_fraction == 1.0,
        },
        "decision": decision,
        "boundary": "Preflight only. No R2 decisions, orders, fill simulation, WAIT/ACT policy, PnL or oracle value are produced. Source-time reconstruction preserves the historical Frozen R2 input convention; receipt-time availability is reported separately and must not be confused with live causal availability.",
        "next": "If KEEP, add an archive-backed decision-book implementation behind an explicit research-only fallback and require exact reproduction of the stored V7 market 1522232 before using it for R2.1 cancel-race cooperation.",
    }
    output = OUT / "frozen_r2_decision_book_from_execution_tape_v1_preflight_report.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"decision": decision, **payload["counts"], **payload["coverage"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
