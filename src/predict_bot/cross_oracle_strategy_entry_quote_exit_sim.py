from __future__ import annotations

from . import cross_oracle_strategy_resilient as strategy_module
from .poly_quote_canary_exit_sim import ExitSimulatedPolyQuoteCanary


# Keep the gap-aware Paper engine unchanged. Only replace its canary dependency:
# real signed get-quote for ENTRY, counterfactual execution scenarios for EXIT.
strategy_module.PolyQuoteCanary = ExitSimulatedPolyQuoteCanary


def main() -> int:
    return strategy_module.main()


if __name__ == "__main__":
    raise SystemExit(main())
