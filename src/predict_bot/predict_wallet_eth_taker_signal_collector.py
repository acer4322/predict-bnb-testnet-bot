from __future__ import annotations

import json
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import microstructure as micro
from . import predict_wallet_taker_signal_collector as base


ROOT = Path(__file__).resolve().parents[2]
ASSET = "ETH"
VERSION = "PREDICT_WALLET_ETH_TAKER_SIGNAL_COLLECTOR_V1"
HOST = "127.0.0.1"
PORT = 8780

# Reuse the battle-tested BTC signal collector in a separate process, but isolate
# every database and switch only its public Binance streams to ETHUSDT. These
# assignments happen before the collector instance is created and therefore do
# not affect the BTC 8777 process.
base.DB_PATH = ROOT / "data" / "wallet_eth_taker_signals.db"
base.MICRO_DB_PATH = ROOT / "data" / "wallet_eth_taker_microstructure.db"
base.PRIVATE_ARCHIVE_DB_PATH = ROOT / "data" / "wallet_eth_taker_private_archive.db"
base.VERSION = VERSION
base.HOST = HOST
base.PORT = PORT

micro.SPOT_TRADE_URL = (
    "wss://stream.binance.com:9443/stream?streams=ethusdt@trade"
)
micro.SPOT_BOOK_URL = (
    "wss://stream.binance.com:9443/stream?streams="
    "ethusdt@bookTicker/ethusdt@depth10@100ms"
)
micro.FUTURES_PUBLIC_URL = (
    "wss://fstream.binance.com/public/stream?streams="
    "ethusdt@bookTicker/ethusdt@depth10@100ms"
)
micro.FUTURES_MARKET_URL = (
    "wss://fstream.binance.com/market/stream?streams=ethusdt@aggTrade"
)


class EthTakerSignalCollector(base.TakerSignalCollector):
    """Forward-only ETH public-signal recorder for Target Taker research.

    The collector never reads target-wallet events. It records only public
    Predict.fun ETH state plus public Binance ETH spot/futures microstructure.
    Target events are joined later by offline research tools.
    """

    def _refresh_predict(self) -> None:
        response = self.client.get(base.PREDICT_STATE_URL)
        response.raise_for_status()
        payload = base._record(response.json())
        state = base._record(payload.get("state") or payload.get("data") or payload)
        asset_state = base._record(base._record(state.get("assets")).get(ASSET))
        market = base._record(asset_state.get("market"))
        market_id = int(market.get("id")) if market.get("id") is not None else None
        with self.lock:
            self.current_predict = asset_state
            changed = market_id is not None and market_id != self.current_market_id
            self.current_market_id = market_id
            cached_variant = base._record(self.current_market_detail.get("variantData"))
            needs_detail = changed or base._number(cached_variant.get("startPrice")) is None
        if market_id is not None and needs_detail:
            try:
                detail_response = self.predict_client.get(f"{base.PREDICT_API_BASE}/v1/markets/{market_id}")
                detail_response.raise_for_status()
                detail_payload = base._record(detail_response.json())
                detail = base._record(detail_payload.get("data") or detail_payload)
                with self.lock:
                    self.current_market_detail = detail
            except Exception as exc:
                with self.lock:
                    self.current_market_detail = {}
                    self.last_error = f"ETH market detail {market_id}: {exc}"[:500]

    def _chainlink_loop(self) -> None:
        # The inherited Chainlink parser is BTC-specific. Fail closed rather than
        # contaminating ETH features with BTC Chainlink prices. ETH research uses
        # spot/futures/Prediction features until an ETH-specific oracle is added.
        with self.lock:
            self.chainlink.update(
                status="DISABLED_FOR_ETH",
                price=None,
                sourceTimestampMs=None,
                receivedTimestampMs=None,
                error=None,
            )
        while not self.stop_event.wait(1.0):
            pass

    def state(self) -> dict[str, Any]:
        result = super().state()
        result.update(
            {
                "asset": ASSET,
                "version": VERSION,
                "port": PORT,
                "timestampBoundary": (
                    "offline Target joins must use only snapshots strictly before "
                    "the second-quantized target event bucket"
                ),
            }
        )
        return result


def main() -> int:
    collector = EthTakerSignalCollector()
    collector.start()
    handler = type("WalletEthTakerSignalHandler", (base.Handler,), {"collector": collector})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(f"{VERSION} listening on http://{HOST}:{PORT}/state; research-only", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
        collector.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
