# BTC 5M Lab Dashboard V2

A read-only UI migration that keeps the existing Python trading/execution services unchanged.

## Safety boundary

Dashboard V2 intentionally exposes no write proxy in its first phase. It only reads:

- `8766 /api/realtime`
- `8767 /state`
- `8769 /state`
- `8770 /state` (optional BTC/ETH/BNB comparison observer)

It does **not** expose runtime enable/disable, stake changes, strategy changes, manual SELL, Shotgun settings, Leader Guard settings, or any other Echtgeld write action.

The `8770` multi-market observer is also read-only. BTC is mirrored from the existing 8766/8767 services so it does not duplicate BTC exchange traffic. ETH and BNB use dedicated Polymarket market subscriptions plus Binance Prediction read-only order-book requests. ETH/BNB are observation-only and are not wired into the V44 Echtgeld engine.

## Run locally

Start the optional multi-market observer in a separate PowerShell first:

```powershell
python -m predict_bot.multi_prediction_observer
```

Then run Dashboard V2:

```powershell
cd .\dashboard-v2
npm install
npm run dev
```

Open `http://localhost:4320`.

The existing dashboard on port `4310` remains separate and unchanged. The multi-market observer is deliberately not auto-started by the Echtgeld supervisor in this first version, so enabling the new UI cannot silently add exchange request load to the live hot path.

## Multi-market trajectory

Overview displays one compact five-minute comparison card for each asset:

- BTC
- ETH
- BNB

Each card plots Polymarket UP mid against Binance Prediction UP mid and shows the current mid gap, executable UP edge, source age, market countdown, and source health. Current-market samples are persisted in `data/multi_prediction_observer.db` so a Dashboard refresh does not erase the trajectory.

## Refresh model

A single Zustand store polls the read-only service snapshots once per second while the browser tab is visible. Pages consume the shared snapshot instead of starting independent polling loops. On a transient request failure, the last successful payload is retained while the affected service is marked offline.

## UI attribution

The layout direction and frontend stack are inspired by [WrBug/PolyHermes](https://github.com/WrBug/PolyHermes), which is distributed under the MIT License (Copyright (c) 2024 WrBug). Dashboard V2 is not a deployment of the PolyHermes backend and does not import its Polymarket account/copy-trading runtime.
