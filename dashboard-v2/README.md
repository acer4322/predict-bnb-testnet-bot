# BTC 5M Lab Dashboard V2

A read-only UI migration that keeps the existing Python trading/execution services unchanged.

## Safety boundary

Dashboard V2 intentionally exposes no write proxy in its first phase. It only reads:

- `8766 /api/realtime`
- `8767 /state`
- `8768 /state` (Paper strategies + Poly/Binance lead validation)
- `8769 /state` (V44 Echtgeld)
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

## Pages

- **Overview**: BTC Echtgeld market + BTC/ETH/BNB UP/DOWN Polymarket vs Binance Prediction trajectories.
- **Echtgeld**: V44 runtime decision, risk/loss state, active position, entry/exit latency, recent rounds and events.
- **Poly Gap**: source freshness, V40 reversal re-entry, V38 Leader Guard, V42 take-profit same-market lock and related events.
- **Strategies**: 8768 Paper strategy performance, open positions, current cross-venue runtime and lead-validation regime.
- **Trades**: separate tabs for Echtgeld rounds, V44 execution attempts/action keys and 8768 Paper trades.
- **Diagnostics**: structured service/feed/discovery health with raw snapshots collapsed by default.

## Multi-market trajectory

Overview displays one compact five-minute comparison card for each asset:

- BTC
- ETH
- BNB

Each card separately plots **UP** and **DOWN** trajectories. Each trajectory compares the actual Polymarket side mid with the actual Binance Prediction side mid rather than deriving DOWN as `1 - UP`. The cards also show side-specific mid gaps and executable edges, source age, market countdown, and source health. Current-market samples are persisted in `data/multi_prediction_observer.db` so a Dashboard refresh does not erase the trajectory.

## Refresh model

The fast shared Zustand store polls 8766/8767/8769/8770 once per second while the browser tab is visible. The heavier 8768 strategy/research snapshot is kept in a separate shared slow store and polls every two seconds. Pages consume these shared snapshots instead of starting independent per-panel polling loops. On a transient request failure, the last successful payload is retained while the affected service is marked offline.

## UI attribution

The layout direction and frontend stack are inspired by [WrBug/PolyHermes](https://github.com/WrBug/PolyHermes), which is distributed under the MIT License (Copyright (c) 2024 WrBug). Dashboard V2 is not a deployment of the PolyHermes backend and does not import its Polymarket account/copy-trading runtime.
