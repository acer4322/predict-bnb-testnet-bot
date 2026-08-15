from __future__ import annotations

import os
from pathlib import Path


# Set the isolated cohort identity and shared official target truth source before
# importing the generic collector. 8779 no longer performs its own duplicate
# /v1/orders/matches polling; 8776 is the single BTC/ETH target-fill producer.
os.environ["PREDICT_WALLET_MAKER_BOOK_ASSET"] = "ETH"
os.environ["PREDICT_WALLET_MAKER_BOOK_PORT"] = "8779"
root = Path(__file__).resolve().parents[2]
os.environ["PREDICT_WALLET_MAKER_BOOK_DB"] = os.environ.get(
    "PREDICT_WALLET_MAKER_BOOK_ETH5M_DB",
    str(root / "data" / "wallet_maker_book_inference_eth5m.db"),
)
os.environ["PREDICT_WALLET_SHADOW_DB"] = os.environ.get(
    "PREDICT_TARGET_WALLET_OFFICIAL_DB",
    str(root / "data" / "target_wallet_official_v1.db"),
)

from . import predict_wallet_maker_book_inference_collector as base  # noqa: E402


class OfficialLedgerEthMakerCollector(base.MakerBookInferenceCollector):
    """ETH book inference using 8776's retained target fills as the sole truth source."""

    def _target_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._ingest_new_target_events()
                self._match_pending_events()
            except Exception as exc:
                self.last_error = f"target-ledger: {str(exc)[:350]}"
            self.stop_event.wait(0.5)


def main() -> int:
    collector = OfficialLedgerEthMakerCollector(base.DB_PATH, base.TARGET_DB_PATH)
    collector.start()
    handler = type("MakerBookInferenceEth5mOfficialLedgerHandler", (base.Handler,), {"collector": collector})
    server = base.ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"{base.VERSION} listening on http://{base.HOST}:{base.PORT}/state; asset=ETH; "
        f"targetSource={base.TARGET_DB_PATH}; readOnly=true; liveOrdersAffected=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        collector.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
