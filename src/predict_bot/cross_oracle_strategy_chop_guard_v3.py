from __future__ import annotations

# Paper quote/canary paths can also use signed Binance endpoints.  Load the same
# clock hardening as 8766/8769 so a host clock correction cannot make the Paper
# guard appear unavailable and indirectly block live entries.
from . import binance_time_sync_hardening as _binance_time_sync_hardening  # noqa: F401
from . import cross_oracle_strategy_chop_guard_v2 as base
from . import cross_oracle_strategy_rolling_stats as _cross_oracle_strategy_rolling_stats  # noqa: F401


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
