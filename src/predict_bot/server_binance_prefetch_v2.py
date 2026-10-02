from __future__ import annotations

# Import first so every BinancePredictionClient used by the 8766 server gets
# periodic clock refresh, wall-clock jump detection and safe GET -1021 recovery.
from . import binance_time_sync_hardening as _binance_time_sync_hardening  # noqa: F401
from . import server_binance_prefetch as base


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
