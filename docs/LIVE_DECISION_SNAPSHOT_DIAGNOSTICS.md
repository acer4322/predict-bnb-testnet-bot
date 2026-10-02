# Live blocked-decision snapshot diagnostics

This diagnostic is read-only. It does not change strategy gates, quote limits,
order placement, or the final allow/block decision.

It captures a fresh verified Prediction book when an early live decision is
blocked by one of these statuses:

- `BLOCKED_DRAWDOWN_CONTROL`
- `BLOCKED_STRATEGY_OBSERVER`
- `BLOCKED_FUTURES_LEAD_OBSERVER`

The snapshot is stored in the blocked live order's existing `response_json` and
is exposed as the sanitized `decision_snapshot` object in `/api/live-details`.
Raw exchange responses remain hidden.

## View the latest captured rows in PowerShell

```powershell
$live = Invoke-RestMethod "http://127.0.0.1:8766/api/live-details"

$live.orders |
  Where-Object { $_.decision_snapshot } |
  Select-Object -First 30 `
    id, updated_at, strategy, market_id, side, signal_price, status, `
    @{Name='SignalAsk'; Expression={$_.decision_snapshot.signalAsk}}, `
    @{Name='DecisionAsk'; Expression={$_.decision_snapshot.decisionLatestAsk}}, `
    @{Name='AskDelta'; Expression={$_.decision_snapshot.decisionAskDelta}}, `
    @{Name='BookAgeMs'; Expression={$_.decision_snapshot.latestLocalBookAgeMs}}, `
    @{Name='EventToDecisionMs'; Expression={$_.decision_snapshot.eventToDecisionSnapshotMs}}, `
    @{Name='SignalMarket'; Expression={$_.decision_snapshot.signalMarketId}}, `
    @{Name='BookMarket'; Expression={$_.decision_snapshot.latestMarketId}}, `
    @{Name='CurrentMarket'; Expression={$_.decision_snapshot.currentMarketId}}, `
    @{Name='Capture'; Expression={$_.decision_snapshot.decisionCaptureStatus}} |
  Format-Table -AutoSize
```

To inspect one complete snapshot:

```powershell
($live.orders | Where-Object { $_.decision_snapshot } | Select-Object -First 1).decision_snapshot |
  Format-List
```

## Interpretation

- `decisionLatestAsk` is the current verified ask captured at the block decision.
- `signalAsk` is the ask carried by the strategy signal.
- `decisionAskDelta = decisionLatestAsk - signalAsk`.
- A large delta with a small `eventToDecisionSnapshotMs` and small
  `latestLocalBookAgeMs` indicates rapid market movement or a stale signal,
  rather than a stale current book.
- A large `latestLocalBookAgeMs` indicates the local Prediction book itself was
  stale at decision time.
- Different `signalMarketId`, `latestMarketId`, or `currentMarketId` values
  indicate a market rollover mismatch.
- `decisionCaptureStatus = UNAVAILABLE` means the fail-closed verified-book
  reader could not provide a usable current ask. Check `decisionBookBlockStatus`,
  `decisionBookErrorKind`, and `decisionBookMessage`.

The process must be restarted after updating the branch because the patch is
installed when the `predict_bot` package is imported.
