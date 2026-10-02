from __future__ import annotations

import json
import os
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import microstructure as micro
from . import public_research_archive_v1 as base

ROOT = Path(__file__).resolve().parents[2]
VERSION = "PUBLIC_RESEARCH_ARCHIVE_BNB5M_V1"
HOST = "127.0.0.1"
PORT = 8797
DB_PATH = ROOT / "data" / "public_research_archive_bnb5m_v1.db"
ASSET = "BNB"

# Bounded load: same feature family as BTC/ETH, but compact 500 ms snapshots,
# no raw websocket persistence, 30-day automatic retention.
base.SAMPLE_INTERVAL_MS = max(250, int(os.environ.get("PREDICT_BNB_PUBLIC_INTERVAL_MS", "500")))
base.RETENTION_DAYS = max(1.0, float(os.environ.get("PREDICT_BNB_PUBLIC_RETENTION_DAYS", "30")))

micro.SPOT_TRADE_URL = "wss://stream.binance.com:9443/stream?streams=bnbusdt@trade"
micro.SPOT_BOOK_URL = "wss://stream.binance.com:9443/stream?streams=bnbusdt@bookTicker/bnbusdt@depth10@100ms"
micro.FUTURES_PUBLIC_URL = "wss://fstream.binance.com/public/stream?streams=bnbusdt@bookTicker/bnbusdt@depth10@100ms"
micro.FUTURES_MARKET_URL = "wss://fstream.binance.com/market/stream?streams=bnbusdt@aggTrade"


class BnbPublicResearchArchive(base.PublicResearchArchive):
    def _write_meta(self) -> None:
        policy = {
            "version": VERSION,
            "asset": ASSET,
            "targetBlind": True,
            "targetWalletInputs": False,
            "officialTruthInputs": False,
            "strategyOutputsUsed": False,
            "sampleIntervalMs": base.SAMPLE_INTERVAL_MS,
            "retentionDays": base.RETENTION_DAYS,
            "rawMicrostructurePersisted": False,
            "legacyCompatibleTable": "wallet_taker_signal_snapshots",
            "predictStateUrl": base.PREDICT_STATE_URL,
        }
        with self.db_lock:
            values = {
                "version": VERSION,
                "created_at_ms": str(self.started_at_ms),
                "policy_json": json.dumps(policy, separators=(",", ":"), sort_keys=True),
            }
            for key, value in values.items():
                self.db.execute(
                    "INSERT INTO public_research_archive_meta(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
            self.db.commit()

    def _refresh_predict(self) -> None:
        response = self.client.get(base.PREDICT_STATE_URL)
        response.raise_for_status()
        state = base._unwrap(response.json())
        asset_state = base._record(base._record(state.get("assets")).get(ASSET))
        market = base._record(asset_state.get("market"))
        market_id = base._positive_int(market.get("id") or market.get("marketId"))
        with self.lock:
            previous_market_id = self.current_market_id
            self.current_predict = asset_state
            self.current_market_id = market_id
            self.last_predict_fetch_ms = int(time.time() * 1000)
            cached_start = base._number(base._record(self.current_market_detail.get("variantData")).get("startPrice"))
            needs_detail = market_id is not None and (market_id != previous_market_id or cached_start is None)
        if market_id is None or not needs_detail:
            return
        try:
            detail_response = self.predict_api_client.get(f"{base.PREDICT_API_BASE}/v1/markets/{market_id}")
            detail_response.raise_for_status()
            payload = base._record(detail_response.json())
            detail = base._record(payload.get("data") or payload)
            with self.lock:
                if self.current_market_id == market_id:
                    self.current_market_detail = detail
        except Exception as exc:
            with self.lock:
                if self.current_market_id == market_id:
                    self.current_market_detail = {}
                self.last_error = f"BNB market detail {market_id}: {exc}"[:500]

    def _chainlink_loop(self) -> None:
        # Existing Chainlink parser is BTC-specific. Fail closed for BNB.
        with self.lock:
            self.chainlink.update(
                status="DISABLED_FOR_BNB",
                price=None,
                sourceTimestampMs=None,
                receivedTimestampMs=None,
                error=None,
            )
        while not self.stop_event.wait(1.0):
            pass

    def state(self) -> dict[str, Any]:
        result = super().state()
        result["version"] = VERSION
        result["asset"] = ASSET
        result["port"] = PORT
        result["resourcePolicy"] = {
            "sampleIntervalMs": base.SAMPLE_INTERVAL_MS,
            "retentionDays": base.RETENTION_DAYS,
            "rawMicrostructurePersisted": False,
        }
        return result

    def health(self) -> dict[str, Any]:
        result = super().health()
        result["version"] = VERSION
        result["asset"] = ASSET
        result["port"] = PORT
        return result


def main() -> int:
    archive = BnbPublicResearchArchive(DB_PATH)
    archive.start()
    handler = type("BnbPublicResearchArchiveHandler", (base.Handler,), {"archive": archive})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}/state; db={DB_PATH}; "
        f"intervalMs={base.SAMPLE_INTERVAL_MS}; retentionDays={base.RETENTION_DAYS}; targetBlind=true",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
        archive.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
