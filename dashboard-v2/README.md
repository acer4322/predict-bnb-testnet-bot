# BTC 5M Lab Dashboard V2

A read-only UI migration that keeps the existing Python trading/execution services unchanged.

## Safety boundary

Dashboard V2 intentionally exposes no write proxy in its first phase. It only reads:

- `8766 /api/realtime`
- `8767 /state`
- `8768 /state` (Paper strategies + Poly/Binance lead validation)
- `8769 /state` (V44 Echtgeld)
- `8770 /state` (optional BTC/ETH/BNB Polymarket + Binance comparison observer)
- `8771 /state` (optional BTC/ETH/BNB Predict.fun observer)

It does **not** expose runtime enable/disable, stake changes, strategy changes, manual SELL, Shotgun settings, Leader Guard settings, Predict.fun order/JWT actions, or any other Echtgeld write action.

The `8770` observer remains unchanged: BTC is mirrored from 8766/8767 while ETH/BNB use dedicated Polymarket market subscriptions plus Binance Prediction read-only order books. The independent `8771` observer uses `PREDICT_FUN_API_KEY` only for mainnet market discovery and websocket market data. Neither observer is wired into the V44 Echtgeld engine.

## Run locally

Start the optional Polymarket/Binance multi-market observer:

```powershell
python -m predict_bot.multi_prediction_observer
```

For Predict.fun mainnet, set the API key in the process environment and start the independent observer:

```powershell
$env:PREDICT_FUN_API_KEY="YOUR_PREDICT_FUN_API_KEY"
python -m predict_bot.predict_fun_observer
```

Do not put the real key in `.env.example` or commit it to Git.

Then run Dashboard V2:

```powershell
cd .\dashboard-v2
npm install
npm run dev
```

Open `http://localhost:4320`.

The existing dashboard on port `4310` remains separate and unchanged. The 8770 and 8771 observers are deliberately not auto-started by the Echtgeld supervisor, so enabling the UI cannot silently add external market-data traffic to the live hot path.

## Pages

- **Overview**: BTC Echtgeld market + BTC/ETH/BNB UP/DOWN Polymarket vs Binance Prediction vs Predict.fun trajectories.
- **Echtgeld**: V44 runtime decision, risk/loss state, active position, entry/exit latency, recent rounds and events.
- **Poly Gap**: source freshness, V40 reversal re-entry, V38 Leader Guard, V42 take-profit same-market lock and related events.
- **Strategies**: 8768 Paper strategy performance, open positions, current cross-venue runtime and lead-validation regime.
- **Trades**: separate tabs for Echtgeld rounds, V44 execution attempts/action keys and 8768 Paper trades.
- **Diagnostics**: structured service/feed/discovery health with raw snapshots collapsed by default.

## Multi-market trajectory

Overview displays one compact five-minute comparison card for BTC, ETH and BNB. Each card separately plots **UP** and **DOWN** trajectories for three venues:

- Polymarket
- Binance Prediction
- Predict.fun

The cards show the three current mids plus Poly−Binance, Predict−Binance and Poly−Predict gaps. The existing Poly/Binance observer stores native UP and DOWN books. Predict.fun exposes a YES-based order book, so 8771 derives the complementary DOWN/NO top of book at the market's declared decimal precision before plotting it.

8770 samples remain in `data/multi_prediction_observer.db`; Predict.fun samples are independently persisted in `data/predict_fun_observer.db`.

## Refresh model

The fast Dashboard snapshot layer polls local 8766/8767/8769/8770/8771 state once per second while the browser tab is visible. 8771 itself does not poll Predict.fun books once per second: external live prices arrive over its persistent websocket. The heavier 8768 strategy/research snapshot remains in a separate shared slow store at two seconds. On a transient local request failure, each store retains its last successful payload while marking the service offline.

## UI attribution

The layout direction and frontend stack are inspired by [WrBug/PolyHermes](https://github.com/WrBug/PolyHermes), which is distributed under the MIT License (Copyright (c) 2024 WrBug). Dashboard V2 is not a deployment of the PolyHermes backend and does not import its Polymarket account/copy-trading runtime.
