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
CLONES = {
    "ETH": {
        "symbol": "ETHUSDT",
        "port": 8774,
        "binanceDb": "wallet_maker_clone_eth.db",
        "predictDb": "wallet_maker_clone_predict_direct_eth.db",
        "normalPort": 8772,
    },
    "BNB": {
        "symbol": "BNBUSDT",
        "port": 8775,
        "binanceDb": "wallet_maker_clone_bnb.db",
        "predictDb": "wallet_maker_clone_predict_direct_bnb.db",
        "normalPort": 8773,
    },
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
    print("multi-asset live: starting live-grade read-only BTC/ETH/BNB observer V2 on 8770", flush=True)
    return subprocess.Popen([sys.executable, "-m", "predict_bot.multi_prediction_observer_v2"])


def _asset_environment(asset: str) -> dict[str, str]:
    config = ASSETS[asset]
    env = os.environ.copy()
    master_name = f"PREDICT_{asset}_POLY_GAP_LIVE_ENABLED"
    env["PREDICT_POLY_GAP_LIVE_ENABLED"] = env.get(master_name, "true")
    env["PREDICT_POLY_GAP_LIVE_ASSET"] = asset
    env["PREDICT_POLY_GAP_LIVE_SYMBOL"] = str(config["symbol"])
    env["PREDICT_POLY_GAP_LIVE_PORT"] = str(config["port"])
    env["PREDICT_POLY_GAP_LIVE_HOST"] = "127.0.0.1"
    env["PREDICT_POLY_GAP_LIVE_DB"] = str(ROOT / "data" / str(config["db"]))
    env["PREDICT_MULTI_PREDICTION_STATE_URL"] = f"http://127.0.0.1:{OBSERVER_PORT}/state"
    env["PREDICT_CROSS_ORACLE_STATE_URL"] = f"http://127.0.0.1:{OBSERVER_PORT}/state"
    env.pop("BINANCE_API_KEY", None)
    env.pop("BINANCE_API_SECRET", None)
    return env


def _clone_venue(asset: str) -> str:
    raw = os.environ.get(
        f"PREDICT_{asset}_WALLET_MAKER_CLONE_VENUE",
        os.environ.get("PREDICT_WALLET_MAKER_CLONE_VENUE", "BINANCE"),
    )
    venue = str(raw or "BINANCE").strip().upper().replace("-", "_")
    if venue == "PREDICT":
        venue = "PREDICT_DIRECT"
    if venue not in {"BINANCE", "PREDICT_DIRECT"}:
        raise RuntimeError(
            f"invalid {asset} wallet maker clone venue {raw!r}; expected BINANCE or PREDICT_DIRECT"
        )
    return venue


def _clone_environment(asset: str, venue: str) -> dict[str, str]:
    config = CLONES[asset]
    env = os.environ.copy()
    master_name = f"PREDICT_{asset}_WALLET_MAKER_CLONE_ENABLED"
    env["PREDICT_WALLET_MAKER_CLONE_ENABLED"] = env.get(master_name, "true")
    env["PREDICT_WALLET_MAKER_CLONE_ASSET"] = asset
    env["PREDICT_WALLET_MAKER_CLONE_SYMBOL"] = str(config["symbol"])
    env["PREDICT_WALLET_MAKER_CLONE_PORT"] = str(config["port"])
    env["PREDICT_WALLET_MAKER_CLONE_HOST"] = "127.0.0.1"
    env["PREDICT_WALLET_MAKER_CLONE_VENUE"] = venue
    db_name = str(config["predictDb"] if venue == "PREDICT_DIRECT" else config["binanceDb"])
    env["PREDICT_WALLET_MAKER_CLONE_DB"] = str(ROOT / "data" / db_name)
    env["PREDICT_WALLET_MAKER_CLONE_NORMAL_LIVE_URL"] = (
        f"http://127.0.0.1:{int(config['normalPort'])}/state"
    )
    env.pop("BINANCE_API_KEY", None)
    env.pop("BINANCE_API_SECRET", None)
    return env


def _asset_process(asset: str) -> subprocess.Popen[bytes] | None:
    config = ASSETS[asset]
    port = int(config["port"])
    if _ready(port):
        print(f"multi-asset live: using existing {asset} live engine on {port}", flush=True)
        return None
    master = os.environ.get(f"PREDICT_{asset}_POLY_GAP_LIVE_ENABLED", "true")
    print(
        f"multi-asset live: starting {asset} {config['symbol']} live engine V3 on {port}; "
        f"master={master}; runtime remains separately controlled in Dashboard V2",
        flush=True,
    )
    return subprocess.Popen(
        [sys.executable, "-m", "predict_bot.poly_gap_multi_asset_live_v3"],
        env=_asset_environment(asset),
    )


def _clone_process(asset: str) -> subprocess.Popen[bytes] | None:
    config = CLONES[asset]
    port = int(config["port"])
    if _ready(port):
        print(f"multi-asset live: using existing {asset} wallet maker clone on {port}", flush=True)
        return None
    master = os.environ.get(f"PREDICT_{asset}_WALLET_MAKER_CLONE_ENABLED", "true")
    venue = _clone_venue(asset)
    if venue == "PREDICT_DIRECT":
        module = "predict_bot.wallet_maker_clone_predict_direct_v8_1"
        label = "V8.1 Predict-direct bounded paired-risk (fixed discovery)"
    else:
        module = "predict_bot.wallet_maker_clone_live_v8"
        label = "V8 Binance Prediction bounded paired-risk"
    print(
        f"multi-asset live: starting {asset} wallet maker clone {label} on {port}; "
        f"venue={venue}; master={master}; runtime is force-paused on every process start",
        flush=True,
    )
    return subprocess.Popen(
        [sys.executable, "-m", module],
        env=_clone_environment(asset, venue),
    )


def main() -> int:
    observer: subprocess.Popen[bytes] | None = _observer_process()
    children: dict[str, subprocess.Popen[bytes] | None] = {
        asset: _asset_process(asset) for asset in ASSETS
    }
    clones: dict[str, subprocess.Popen[bytes] | None] = {
        asset: _clone_process(asset) for asset in CLONES
    }
    next_restart: dict[str, float] = {
        "OBSERVER": 0.0,
        **{asset: 0.0 for asset in ASSETS},
        **{f"CLONE_{asset}": 0.0 for asset in CLONES},
    }

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

            for asset, config in CLONES.items():
                port = int(config["port"])
                if _ready(port):
                    continue
                child = clones.get(asset)
                key = f"CLONE_{asset}"
                dead = child is None or child.poll() is not None
                if dead and now >= next_restart[key]:
                    next_restart[key] = now + RESTART_SECONDS
                    clones[asset] = _clone_process(asset)
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 130
    finally:
        for child in clones.values():
            _stop(child)
        for child in children.values():
            _stop(child)
        _stop(observer)


if __name__ == "__main__":
    raise SystemExit(main())
