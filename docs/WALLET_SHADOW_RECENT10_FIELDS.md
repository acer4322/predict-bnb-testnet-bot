# Recent 10 Taker comparison fields

Per market the export keeps:

- Target Maker/Taker parent counts.
- Shadow Maker quote/fill-proxy and Taker intent counts.
- Target and Shadow Taker UP/DOWN shares, delta, residual side.
- Target Taker parent timeline: first/last event time, side, average price, shares, fill legs.
- Shadow Taker timeline: event time, side, price, shares, core side/source, reason.
- Nearest Shadow Taker event by time and same-side timing.
- Side agreement within five seconds.
- Same-side timing agreement within three seconds.
- Shadow minus Target price difference.
- Shadow/Target quantity ratio.
- Target Taker occurrence after same-side Target Maker within five seconds.

This is designed to diagnose whether a mismatch is caused by cadence, direction, timing, price, or sizing rather than treating all Taker disagreement as one error.
