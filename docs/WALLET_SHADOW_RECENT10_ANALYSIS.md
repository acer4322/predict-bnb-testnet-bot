# Wallet Shadow Recent 10 Analysis

Use the local Wallet Shadow SQLite database to export the latest markets with event-level Taker diagnostics.

```powershell
python tools/analyze_wallet_shadow_recent.py --limit 10
```

Outputs:

- `wallet_shadow_recent10.json` — full machine-readable Target/Shadow Taker timelines.
- `wallet_shadow_recent10.md` — compact per-market comparison table.

The report compares:

- Target Taker parent count vs Shadow `TAKER_INTENT` count.
- Shadow/Target event-count ratio.
- nearest Taker side match within ±5 seconds.
- same-side timing match within ±3 seconds.
- median nearest event time gap.
- median Shadow minus Target price difference.
- median Shadow/Target quantity ratio.
- Target vs Shadow Taker residual side.
- Target Taker events preceded by same-side Target Maker activity within 5 seconds.

The tool reads only `data/predict_wallet_shadow.db` and does not contact Predict.fun or submit orders.
