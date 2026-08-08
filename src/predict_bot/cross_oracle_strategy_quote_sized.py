from __future__ import annotations

from . import cross_oracle_strategy_resilient as strategy_module
from .poly_quote_canary_live_sizing import LiveSizedPolyQuoteCanary


# GapAwareCrossOraclePaperEngine resolves PolyQuoteCanary from its module globals
# when each engine instance is constructed. Replace that constructor dependency
# without altering the Paper strategy semantics or the continuity guard.
strategy_module.PolyQuoteCanary = LiveSizedPolyQuoteCanary


def main() -> int:
    return strategy_module.main()


if __name__ == "__main__":
    raise SystemExit(main())
