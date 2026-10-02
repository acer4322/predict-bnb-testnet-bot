from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_safe_floor_contingent_pair_smoke_v1 import run_offset


OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT_DIR / "hft_safe_floor_entry_value_dataset60_v1_preregistered.json"
DATASET = OUT_DIR / "hft_safe_floor_entry_value_dataset60_v1.json"
MARKETS = (
    1580542, 1580739, 1580927, 1581413, 1581667, 1582470, 1582740, 1582926, 1583117, 1583318,
    1583704, 1583892, 1584087, 1584277, 1584510, 1584710, 1584901, 1585096, 1585299, 1585536,
    1585729, 1585935, 1586130, 1586378, 1586570, 1586771, 1586980, 1587218, 1587422, 1587617,
    1587827, 1588018, 1588243, 1588443, 1588637, 1588831, 1589143, 1589334, 1589527, 1589729,
    1589920, 1590183, 1590383, 1590618, 1590773, 1591088, 1591246, 1591473, 1591673, 1591881,
    1592460, 1592662, 1592981, 1593203, 1593403, 1593594, 1594180, 1594380, 1594599, 1594792,
)


def main() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if list(MARKETS) != prereg["markets"]:
        raise RuntimeError("collector market list differs from preregistration")
    rows = []
    for index, market_id in enumerate(MARKETS, 1):
        result = run_offset(market_id, 1)
        actual = result["actualExecution"]
        row = {
            "marketId": market_id,
            "chronologicalIndex": index - 1,
            "split": "train" if index <= 30 else "validation" if index <= 45 else "unseenHoldout",
            "terminalWorstCaseFloor": float(actual["worstCaseFloor"]),
            "settledRealizedPnlAudit": float(actual["realizedPnl"]),
            "tail": int(float(actual["worstCaseFloor"]) < 0.0),
            "terminalState": actual["terminalState"],
            "makerFilledShares": float(actual["makerUp"] + actual["makerDown"]),
            "takerFilledShares": float(actual["takerUp"] + actual["takerDown"]),
            "takerFeesUsdt": float(actual["takerFeesUsdt"]),
            "cycleInvariantViolationCount": int(result["cycleInvariantViolationCount"]),
            "strictPastEntryState": result["strictPastEntryState"],
        }
        rows.append(row)
        print(json.dumps({"progress": f"{index}/{len(MARKETS)}", **{key: row[key] for key in ("marketId", "split", "terminalWorstCaseFloor", "tail", "terminalState")}}, ensure_ascii=False), flush=True)
    report = {
        "datasetVersion": "HFT_SAFE_FLOOR_ENTRY_VALUE_DATASET60_V1",
        "researchOnly": True,
        "preregisteredContract": PREREG.name,
        "fixedAction": "CONTINGENT_PAIR_OFFSET1",
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queue": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "ownStatePollMs": 250,
            "partialFills": True,
            "actualFillInventory": True,
            "dreamFill": False,
        },
        "rows": rows,
        "aggregateBySplit": {
            split: {
                "markets": len(selected),
                "value": sum(float(row["terminalWorstCaseFloor"]) for row in selected),
                "positive": sum(float(row["terminalWorstCaseFloor"]) > 0.0 for row in selected),
                "zero": sum(abs(float(row["terminalWorstCaseFloor"])) <= 1e-9 for row in selected),
                "tail": sum(int(row["tail"]) for row in selected),
                "violations": sum(int(row["cycleInvariantViolationCount"]) for row in selected),
            }
            for split in ("train", "validation", "unseenHoldout")
            for selected in [[row for row in rows if row["split"] == split]]
        },
    }
    DATASET.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["aggregateBySplit"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
