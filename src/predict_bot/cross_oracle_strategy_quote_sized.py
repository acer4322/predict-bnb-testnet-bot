from __future__ import annotations

from . import cross_oracle_strategy_resilient as strategy_module
from .poly_quote_canary_scenario_summary import ScenarioSummaryPolyQuoteCanary


# GapAwareCrossOraclePaperEngine resolves PolyQuoteCanary from its module globals
# when each engine instance is constructed. V5 keeps real signed BUY quote
# measurements, simulated EXIT envelopes, and strategy-level best/worst gross
# portfolio summaries for direct comparison with the original Paper cards.
strategy_module.PolyQuoteCanary = ScenarioSummaryPolyQuoteCanary


def main() -> int:
    return strategy_module.main()


if __name__ == "__main__":
    raise SystemExit(main())
