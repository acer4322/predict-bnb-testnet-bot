from __future__ import annotations

from . import cross_oracle_strategy_resilient as strategy_module
from .poly_quote_canary_scenario_summary import ScenarioSummaryPolyQuoteCanary


# This is the module supervisor actually launches. Keep the gap-aware Paper
# engine unchanged, but inject the complete V5 canary here so /state exposes:
# - real signed BUY quote quality
# - simulated EXIT best/worst envelopes
# - strategy-level scenarioPortfolioSummaries used by the three native Poly cards.
strategy_module.PolyQuoteCanary = ScenarioSummaryPolyQuoteCanary


def main() -> int:
    return strategy_module.main()


if __name__ == "__main__":
    raise SystemExit(main())
