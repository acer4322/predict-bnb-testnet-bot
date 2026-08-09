from __future__ import annotations

import json
import time
from typing import Any

from . import server as server
from . import server_binance_prefetch_v4 as previous


_ORIGINAL_DO_GET = server.Handler.do_GET


def _current_lightweight_market_reference() -> dict[str, Any] | None:
    """Return only collector-owned Binance Prediction market identity metadata.

    Unlike current_m_market_reference(), this intentionally does not require
    startPrice or build any dashboard/microstructure state.  The dedicated Live
    executor only needs market/topic/start/end and explicit UP/DOWN token IDs.
    """

    collector = server.COLLECTOR
    with collector.market_lock:
        market = dict(collector.market or {})
    if not market:
        return None
    try:
        selected = market["_selectedMarket"]
        market_node = selected["market"]
        up = selected["up"]
        down = selected["down"]
        reference = {
            "market_id": int(market_node["marketId"]),
            "topic_id": int(market["marketTopicId"]),
            "start_ms": int(market["startDate"]),
            "end_ms": int(market["endDate"]),
            "fee_bps": int(market.get("feeRateBps") or market_node.get("feeRateBps") or 200),
            "up_token_id": str(up["tokenId"]),
            "down_token_id": str(down["tokenId"]),
        }
    except (KeyError, TypeError, ValueError):
        return None
    if (
        reference["market_id"] <= 0
        or reference["topic_id"] <= 0
        or not reference["up_token_id"]
        or not reference["down_token_id"]
        or reference["up_token_id"] == reference["down_token_id"]
        or reference["start_ms"] <= 0
        or reference["end_ms"] <= reference["start_ms"]
    ):
        return None
    return reference


def _do_get_with_lightweight_binance_reference(self: server.Handler) -> None:
    request_path = self.path.split("?", 1)[0]
    if request_path != "/api/binance-market-reference":
        return _ORIGINAL_DO_GET(self)

    reference = _current_lightweight_market_reference()
    payload = {
        "ok": reference is not None,
        "generatedAtMs": int(time.time() * 1000),
        "collectorStatus": str(server.COLLECTOR.status or ""),
        "collectorError": server.COLLECTOR.error,
        "marketReference": reference,
        "source": "8766_COLLECTOR_MARKET_LIGHTWEIGHT",
        "containsTradingCredentials": False,
    }
    self._headers(200)
    self.wfile.write(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


server.Handler.do_GET = _do_get_with_lightweight_binance_reference


def main() -> int:
    return previous.main()


if __name__ == "__main__":
    raise SystemExit(main())
