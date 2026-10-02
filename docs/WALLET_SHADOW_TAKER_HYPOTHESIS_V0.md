# Wallet Shadow Taker mismatch hypothesis V0

The current Shadow Taker rule is intentionally simple: when the inferred Maker residual points opposite the current core side, emit a corrective `TAKER_INTENT` sized in 18-share blocks.

Historical 6DA6 analysis suggests this can miss the target in at least four ways:

1. **Cadence** — the target produces many incremental Taker parent orders per market; Shadow may collapse this into a few coarse rebalance events.
2. **Timing** — target Taker activity often follows same-side Maker activity quickly, so the hidden trigger may be local inventory/adverse-selection repair rather than only a market-level residual sign change.
3. **Sizing** — target Taker parent sizes vary and may be tied to the just-filled Maker amount or current imbalance, rather than ceiling(abs(residual)/18)*18.
4. **Direction source** — target Taker residual is often opposite Maker residual, but that does not prove the target uses the same `8768.runtime.binanceDirection` source as Shadow.

Use `tools/analyze_wallet_shadow_recent.py --limit 10` on forward data to distinguish these failure modes before changing the rule.
