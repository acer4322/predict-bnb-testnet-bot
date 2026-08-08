from __future__ import annotations

from . import cross_oracle_strategy_resilient as strategy_module
from .poly_quote_canary_exit_sim_v3 import DurableExitSimulatedPolyQuoteCanary


# GapAwareCrossOraclePaperEngine resolves PolyQuoteCanary from its module globals
# when each engine instance is constructed. Inject V3: ENTRY keeps the real
# signed BUY get-quote path; EXIT never requests a signed SELL quote and instead
# persists best/worst execution scenarios, including a clearly-labelled upper
# bound fallback when first-level depth is unavailable.
strategy_module.PolyQuoteCanary = DurableExitSimulatedPolyQuoteCanary


def main() -> int:
    return strategy_module.main()


if __name__ == "__main__":
    raise SystemExit(main())
