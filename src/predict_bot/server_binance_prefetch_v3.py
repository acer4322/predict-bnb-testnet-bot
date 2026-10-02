from __future__ import annotations

# Keep the signed-request clock hardening from V2.
from . import binance_time_sync_hardening as _binance_time_sync_hardening  # noqa: F401
from . import server_binance_prefetch as base


# The base wrapper was deliberately limited to page 1.  That can falsely report
# WAITING_FOR_MARKET when the exact BTC 5m topic exists deeper in the globally
# END_DATE-sorted market/list result.  Widen discovery without enabling any
# nearest-window fallback.
base.SERVER_BINANCE_EXACT_MAX_PAGES = 5
base.SERVER_BINANCE_CURRENT_RETRY_MS = 1000


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
