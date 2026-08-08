from __future__ import annotations

from . import cross_oracle_strategy_resilient as strategy_module
from .poly_quote_canary_exit_sim_v4 import SafeDurableExitSimulatedPolyQuoteCanary


# GapAwareCrossOraclePaperEngine resolves PolyQuoteCanary from its module globals
# when each engine instance is constructed. ENTRY keeps the real signed BUY
# get-quote path; EXIT never requests a signed SELL quote and instead persists
# best/worst execution scenarios. Legacy migration is limited to the known
# Paper-only SELL -9000 / available-shares failure class.
strategy_module.PolyQuoteCanary = SafeDurableExitSimulatedPolyQuoteCanary


def main() -> int:
    return strategy_module.main()


if __name__ == "__main__":
    raise SystemExit(main())
