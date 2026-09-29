from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
SOURCE_REPORT = OUT_DIR / "hft_target_forced_action_memory_smoke3_v1_report.json"
SETTLEMENT_SOURCE = ROOT / "data" / "research" / "8784_r2_vs_8786_cap100_fresh_v1_markets.csv"
REPORT = OUT_DIR / "hft_target_forced_action_memory_smoke3_v1_settlement_audit.json"


def load_winners(market_ids: set[int]) -> dict[int, str]:
    winners: dict[int, str] = {}
    with SETTLEMENT_SOURCE.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            market_id = int(row["marketId"])
            winner = str(row["winner"]).upper()
            if market_id in market_ids and winner in {"UP", "DOWN"}:
                winners[market_id] = winner
    return winners


def settlement_pnl(portfolio: dict[str, Any], winner: str) -> float:
    winning_shares = float(portfolio["upShares"] if winner == "UP" else portfolio["downShares"])
    return float(portfolio["cash"]) + winning_shares


def main() -> None:
    source = json.loads(SOURCE_REPORT.read_text(encoding="utf-8"))
    if source.get("reportVersion") != "HFT_TARGET_FORCED_ACTION_MEMORY_SMOKE3_V1":
        raise RuntimeError("unexpected source report version")
    market_reports = list(source["marketReports"])
    market_ids = {int(row["marketId"]) for row in market_reports}
    winners = load_winners(market_ids)
    if set(winners) != market_ids:
        raise RuntimeError(f"missing offline settlement: {sorted(market_ids - set(winners))}")

    rows: list[dict[str, Any]] = []
    for row in market_reports:
        market_id = int(row["marketId"])
        winner = winners[market_id]
        own = row["finalOwnPortfolio"]
        target = row["targetActualPortfolioAudit"]
        own_pnl = settlement_pnl(own, winner)
        target_pnl = settlement_pnl(target, winner)
        rows.append(
            {
                "marketId": market_id,
                "winnerOfflineOnly": winner,
                "fillRealizationRate": row["fillRealizationRate"],
                "ownHftSettlementPnl": own_pnl,
                "targetActualSettlementPnl": target_pnl,
                "ownMinusTargetPnl": own_pnl - target_pnl,
                "ownWorstCaseFloor": own["worstCaseFloor"],
                "targetActualWorstCaseFloor": target["worstCaseFloor"],
                "ownWinningShares": own["upShares"] if winner == "UP" else own["downShares"],
                "targetActualWinningShares": target["upShares"] if winner == "UP" else target["downShares"],
            }
        )

    own_total = sum(float(row["ownHftSettlementPnl"]) for row in rows)
    target_total = sum(float(row["targetActualSettlementPnl"]) for row in rows)
    report = {
        "reportVersion": "HFT_TARGET_FORCED_ACTION_MEMORY_SMOKE3_SETTLEMENT_AUDIT_V1",
        "researchOnly": True,
        "postHocDiagnostic": True,
        "sourceReport": SOURCE_REPORT.name,
        "settlementSource": str(SETTLEMENT_SOURCE.resolve()),
        "boundary": "Winner is read only after replay/model scoring and is used only for offline economic evaluation. It is not a runtime feature, model input, threshold selector, or action trigger.",
        "rows": rows,
        "aggregate": {
            "markets": len(rows),
            "ownHftSettlementPnl": own_total,
            "targetActualSettlementPnl": target_total,
            "ownMinusTargetPnl": own_total - target_total,
            "ownPositiveMarkets": sum(float(row["ownHftSettlementPnl"]) > 0.0 for row in rows),
            "targetActualPositiveMarkets": sum(float(row["targetActualSettlementPnl"]) > 0.0 for row in rows),
        },
        "interpretation": "This audit diagnoses execution fidelity only and does not override the preregistered REJECT decision or authorize autonomous deployment.",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
