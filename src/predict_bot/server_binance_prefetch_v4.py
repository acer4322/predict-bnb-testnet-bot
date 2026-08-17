from __future__ import annotations

from typing import Any

from . import server as server
from . import server_binance_prefetch_v3 as previous


_BASE_REALTIME_DASHBOARD_STATE = server.realtime_dashboard_state


def _realtime_dashboard_state_with_market_reference() -> dict[str, Any]:
    """Expose the collector's already-validated Binance market identity locally.

    The dashboard collector and the dedicated live executor run in separate
    processes.  Re-discovering the same Binance Prediction topic independently
    in the live process can fail or time out even while the dashboard collector
    is already publishing a healthy trajectory.  This local-only reference lets
    the live process reuse the exact market/topic/token identity that 8766 has
    already validated.
    """

    payload = _BASE_REALTIME_DASHBOARD_STATE()
    reference = server.current_m_market_reference()
    payload["binanceMarketReference"] = reference
    payload["binanceMarketReferenceMeta"] = {
        "source": "8766_COLLECTOR_CURRENT_M_MARKET_REFERENCE",
        "localOnlyConsumer": True,
        "containsTradingCredentials": False,
        "containsOnlyMarketMetadata": True,
        "exactCurrentCollectorIdentity": True,
    }
    return payload


server.realtime_dashboard_state = _realtime_dashboard_state_with_market_reference


def main() -> int:
    return previous.main()


if __name__ == "__main__":
    raise SystemExit(main())
