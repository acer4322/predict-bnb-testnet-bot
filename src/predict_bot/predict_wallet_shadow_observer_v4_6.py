from __future__ import annotations

import json
import sqlite3
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_5 as v4_5
from . import predict_wallet_taker_signal_collector as collector


VERSION = "PREDICT_WALLET_SHADOW_V0_8_PRIVATE_TAKER_RESEARCH"
ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "artifacts" / "wallet_profit_strategy" / "target_taker_private_reconstruction_report.json"
MODEL_PATH = ROOT / "artifacts" / "wallet_profit_strategy" / "target_taker_private_model_v1.json"
DATASET_PATH = ROOT / "artifacts" / "wallet_profit_strategy" / "target_taker_private_research.db"


class WalletShadowObserver(v4_5.WalletShadowObserver):
    """Retires Taker V2 and exposes target-specific offline inference evidence."""

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=v4_5.v4_4.v4_3.v4_2.SIMULATION_DB_PATH) -> None:
        self.signal_strategy_enabled = False
        self._private_report_mtime_ns: int | None = None
        self._private_report: dict[str, Any] = {}
        super().__init__(db_path, simulation_db_path)

    def _load_private_report(self) -> dict[str, Any]:
        try:
            stat = REPORT_PATH.stat()
            if self._private_report_mtime_ns != stat.st_mtime_ns:
                self._private_report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
                self._private_report_mtime_ns = stat.st_mtime_ns
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._private_report = {"error": str(exc)[:300]}
        return dict(self._private_report)

    @staticmethod
    def _archive_status() -> dict[str, Any]:
        try:
            db = sqlite3.connect(f"file:{collector.PRIVATE_ARCHIVE_DB_PATH}?mode=ro", uri=True, timeout=1.0)
            row = db.execute(
                "SELECT COUNT(*) samples,COUNT(DISTINCT market_id) markets,MAX(sampled_at_ms) latest FROM wallet_taker_private_signal_archive"
            ).fetchone()
            db.close()
            return {"samples": int(row[0]), "markets": int(row[1]), "latestSampleMs": int(row[2]) if row[2] else None, "status": "ARCHIVING"}
        except (OSError, sqlite3.Error) as exc:
            return {"samples": 0, "markets": 0, "latestSampleMs": None, "status": "WAITING", "error": str(exc)[:300]}

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        report = self._load_private_report()
        interpretation = report.get("interpretation") if isinstance(report.get("interpretation"), dict) else {}
        test = (report.get("model") or {}).get("untouchedChronologicalTest") if isinstance(report.get("model"), dict) else {}
        if "takerSignalConsensusV2" in payload:
            payload["takerSignalConsensusV2"]["status"] = "RETIRED_NO_NEW_EVENTS"
            payload["takerSignalConsensusV2"]["promotion"] = "historical audit only; replaced by target-specific offline inference research"
        payload["targetTakerPrivateInference"] = {
            "name": "TARGET_TAKER_PRIVATE_INFERENCE_V1",
            "status": "OFFLINE_HOLDOUT_BELOW_80_GATE" if not interpretation.get("testMeetsSimilarityTarget") else "READY_FOR_NEW_FORWARD_PAPER",
            "paperOnly": True, "offlineOnly": True, "forwardRuntimeActive": False,
            "oldTakerStrategyUsed": False, "targetEventsRequiredAtInference": False,
            "liveOrdersAffected": False, "report": report,
            "testSimilarity": test.get("overallSimilarity") if isinstance(test, dict) else None,
            "archive": self._archive_status(),
            "artifacts": {"dataset": str(DATASET_PATH), "model": str(MODEL_PATH), "report": str(REPORT_PATH)},
            "promotion": "requires at least 80% untouched chronological similarity before a new isolated forward paper cohort is created",
        }
        return payload


class _Handler(base._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_6Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        "old Taker V2 retired; target-specific inference offline only; Maker grids retained; liveOrdersAffected=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        observer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
