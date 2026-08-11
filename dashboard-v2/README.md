# BTC 5M Lab Dashboard V2

A read-only UI migration that keeps the existing Python trading/execution services unchanged.

## Safety boundary

Dashboard V2 intentionally exposes no write proxy in its first phase. It only reads:

- `8766 /api/realtime`
- `8767 /state`
- `8769 /state`

It does **not** expose runtime enable/disable, stake changes, strategy changes, manual SELL, Shotgun settings, Leader Guard settings, or any other Echtgeld write action.

## Run locally

```powershell
cd .\dashboard-v2
npm install
npm run dev
```

Open `http://localhost:4320`.

The existing dashboard on port `4310` remains separate and unchanged.

## Refresh model

A single Zustand store polls the three read-only service snapshots once per second while the browser tab is visible. Pages consume the shared snapshot instead of starting independent polling loops. On a transient request failure, the last successful payload is retained while the affected service is marked offline.

## UI attribution

The layout direction and frontend stack are inspired by [WrBug/PolyHermes](https://github.com/WrBug/PolyHermes), which is distributed under the MIT License (Copyright (c) 2024 WrBug). Dashboard V2 is not a deployment of the PolyHermes backend and does not import its Polymarket account/copy-trading runtime.
