from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]
ASSETS = {
    "ETH": {"symbol": "ETHUSDT", "port": 8772, "db": "poly_gap_live_eth.db"},
    "BNB": {"symbol": "BNBUSDT", "port": 8773, "db": "poly_gap_live_bnb.db"},
}
OBSERVER_PORT = 8770
RESTART_SECONDS = 3.0


def _ready(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{int(port)}/state", timeout=0.5) as response:
            return int(response.status) == 200
    except Exception:
        return False


def _stop(child: subprocess.Popen[bytes] | None) -> None:
    if child is None or child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def _observer_process() -> subprocess.Popen[bytes] | None:
    if _ready(OBSERVER_PORT):
        print("multi-asset live: using existing 8770 observer", flush=True)
        return None
    print("multi-asset live: starting read-only BTC/ETH/BNB observer on 8770", flush=True)
    return subprocess.Popen([sys.executable, "-m", "predict_bot.multi_prediction_observer"])


def _asset_environment(asset: str) -> dict[str, str]:
    config = ASSETS[asset]
    env = os.environ.copy()
    master_name = f"PREDICT_{asset}_POLY_GAP_LIVE_ENABLED"
    env["PREDICT_POLY_GAP_LIVE_ENABLED"] = env.get(master_name, "false")
    env["PREDICT_POLY_GAP_LIVE_ASSET"] = asset
    env["PREDICT_POLY_GAP_LIVE_SYMBOL"] = str(config["symbol"])
    env["PREDICT_POLY_GAP_LIVE_PORT"] = str(config["port"])
    env["PREDICT_POLY_GAP_LIVE_HOST"] = "127.0.0.1"
    env["PREDICT_POLY_GAP_LIVE_DB"] = str(ROOT / "data" / str(config["db"]))
    env["PREDICT_MULTI_PREDICTION_STATE_URL"] = f"http://127.0.0.1:{OBSERVER_PORT}/state"
    # Keep this aligned even though the final asset subclass supplies its own
    # _poly_state.  Any inherited diagnostic that consults the base URL sees the
    # same asset observer rather than the BTC-only 8767 collector.
    env["PREDICT_CROSS_ORACLE_STATE_URL"] = f"http://127.0.0.1:{OBSERVER_PORT}/state"

    # Echtgeld execution must not silently fall back to the general/read-only
    # credential pair.  The inherited credential helper may support that fallback
    # for legacy BTC compatibility, so remove it in these isolated child processes.
    env.pop("BINANCE_API_KEY", None)
    env.pop("BINANCE_API_SECRET", None)
    return env


def _asset_process(asset: str) -> subprocess.Popen[bytes] | None:
    config = ASSETS[asset]
    port = int(config["port"])
    if _ready(port):
        print(f"multi-asset live: using existing {asset} live engine on {port}", flush=True)
        return None
    master = os.environ.get(f"PREDICT_{asset}_POLY_GAP_LIVE_ENABLED", "false")
    print(
        f"multi-asset live: starting {asset} {config['symbol']} live engine on {port}; "
        f"master={master}; runtime remains separately controlled in Dashboard V2",
        flush=True,
    )
    return subprocess.Popen(
        [sys.executable, "-m", "predict_bot.poly_gap_multi_asset_live_v1"],
        env=_asset_environment(asset),
    )


def main() -> int:
    observer: subprocess.Popen[bytes] | None = _observer_process()
    children: dict[str, subprocess.Popen[bytes] | None] = {
        asset: _asset_process(asset) for asset in ASSETS
    }
    next_restart: dict[str, float] = {"OBSERVER": 0.0, **{asset: 0.0 for asset in ASSETS}}

    try:
        while True:
            now = time.monotonic()
            if not _ready(OBSERVER_PORT):
                dead = observer is None or observer.poll() is not None
                if dead and now >= next_restart["OBSERVER"]:
                    next_restart["OBSERVER"] = now + RESTART_SECONDS
                    observer = _observer_process()

            for asset, config in ASSETS.items():
                port = int(config["port"])
                if _ready(port):
                    continue
                child = children.get(asset)
                dead = child is None or child.poll() is not None
                if dead and now >= next_restart[asset]:
                    next_restart[asset] = now + RESTART_SECONDS
                    children[asset] = _asset_process(asset)
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 130
    finally:
        for child in children.values():
            _stop(child)
        _stop(observer)


if __name__ == "__main__":
    raise SystemExit(main())
