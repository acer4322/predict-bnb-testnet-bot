# Recent 10 export usage

From the repository root after Dashboard V2 has accumulated Wallet Shadow data:

```powershell
python tools/analyze_wallet_shadow_recent.py --limit 10
```

Upload `wallet_shadow_recent10.json` when requesting a detailed event-by-event comparison. The smaller `wallet_shadow_recent10.md` is useful for a quick visual review.
