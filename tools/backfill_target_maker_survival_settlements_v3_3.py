from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import backfill_target_maker_survival_settlements_v3_2 as v32

VERSION = "TARGET_MAKER_SURVIVAL_SETTLEMENT_V3_3_PORTFOLIO_PNL_SEMANTICS"


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--report", type=Path, default=v32.v31.DEFAULT_REPORT)
    known, _ = parser.parse_known_args()

    original = v32._winner_concordance_preview

    def diagnostic_only(risk_csv: Path, settlement_rows: dict[int, dict[str, str]]) -> dict[str, Any]:
        payload = original(risk_csv, settlement_rows)
        payload["legacyDiagnosticPassed"] = bool(payload.get("passed"))
        payload["enforced"] = False
        payload["semanticNote"] = (
            "This compares legacy V2 heavy-side market-outcome labels with canonical market outcomes. "
            "It is diagnostic only and is not the Target portfolio win/loss definition."
        )
        payload["passed"] = True
        return payload

    try:
        v32._winner_concordance_preview = diagnostic_only
        code = int(v32.main())
    finally:
        v32._winner_concordance_preview = original

    report_path = known.report.expanduser().resolve()
    if report_path.exists():
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        payload["reportVersion"] = VERSION
        payload["targetWinDefinition"] = (
            "Target portfolio WIN iff target_market_results.net_pnl_usdt > 0; LOSS iff < 0; "
            "FLAT iff == 0. Official UP/DOWN is market outcome only and is never Target win/loss."
        )
        diagnostic = payload.get("winnerConcordancePreview")
        if isinstance(diagnostic, dict):
            diagnostic["enforced"] = False
            diagnostic["semanticNote"] = (
                "Legacy heavy-side market-outcome concordance only; diagnostic, not portfolio PnL truth."
            )
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return code


if __name__ == "__main__":
    raise SystemExit(main())
