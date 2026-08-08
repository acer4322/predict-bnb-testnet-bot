from __future__ import annotations

from . import cross_oracle_strategy_resilient as strategy_module
from .poly_quote_canary_exit_sim import ExitSimulatedPolyQuoteCanary


# GapAwareCrossOraclePaperEngine resolves PolyQuoteCanary from its module globals
# when each engine instance is constructed. Inject the V2 canary that keeps
# real signed BUY quote measurements while modelling EXIT locally. This avoids
# structurally invalid SELL get-quote calls for Paper positions that never own
# real Prediction shares.
strategy_module.PolyQuoteCanary = ExitSimulatedPolyQuoteCanary


def main() -> int:
    return strategy_module.main()


if __name__ == "__main__":
    raise SystemExit(main())
