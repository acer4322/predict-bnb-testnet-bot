# XPAIR Canary Autopilot

## Goal

Remove the operator-click latency between observing an eligible BTC/ETH pair and
requesting/placing the two legs.

The dashboard server now starts an always-on monitor. By default it is strictly
quote-only:

1. discover aligned BTC and ETH five-minute markets;
2. wait for the configured entry window;
3. read all four required order books;
4. build the configured opposite-side equal-share plan;
5. request fresh signed quotes automatically;
6. record the latest modeled and signed cost;
7. never place an order unless one live attempt has been explicitly armed.

## One-shot arm semantics

Pressing **武裝下一次符合條件正式單** does not place immediately. It creates one
in-memory arm token.

The token remains armed while:

- the modeled pair is not eligible;
- signed quote capacity is too low;
- signed cost per share exceeds the configured limit;
- the selected payment account has insufficient balance;
- an existing Prediction order or position makes live placement unsafe.

When a fresh quote pair passes and the strict live preflight is safe, the arm is
consumed before placement and both legs are sent concurrently. The arm is not
restored after success, rejection, partial placement, an ambiguous response, or
a one-sided fill.

The current market is no longer quoted after a live placement attempt. This
preserves the placement/fill result in the SQLite row and prevents a later
`AUTO_QUOTE_READY` update from overwriting it. Monitoring resumes on the next
aligned BTC/ETH market.

## Latency order

When armed, the slow wallet/order/position safety checks run before requesting
the short-lived signed quotes. After both final quotes pass, placement follows
immediately without another preflight round trip.

## Safety boundaries

- Default state is always disarmed.
- Restarting the server clears the arm.
- One arm permits at most one placement attempt.
- Maximum pair budget remains 3 USDT.
- Default pair budget remains 2 USDT.
- Signed total cost must remain at or below the configured cap, default 0.98.
- No automatic retry, cancel, chase, hedge, or unwind is performed.
- Another live executor must be stopped before arming.
- Existing exposure does not stop quote monitoring, but it blocks live placement.

## Start

```powershell
git fetch origin
git switch feature/regime-guard-xpair-canary-dashboard
git pull

python -m predict_bot.xpair_canary_dashboard_server_cedefi
```

The existing command now starts the autopilot implementation. Open:

```text
http://localhost:3000/xpair-canary
```

The page should show `QUOTE MONITORING`. During the 180-to-170 second entry
window it will request fresh signed quotes automatically.

## First live validation

1. Leave pair budget at 2.00 USDT.
2. Confirm automatic quotes reach `AUTO_QUOTE_READY`.
3. Stop every other process that can place Prediction orders.
4. Confirm there are no active orders or positions.
5. Enter `I_ACCEPT_NON_ATOMIC_TWO_LEG_RISK`.
6. Press the one-shot arm button.
7. Watch for `PLACE_ATTEMPTED`, `SUBMITTED_BOTH`, and the reconciliation result.

If the page shows `ARMED_WAITING_SAFE_WALLET`, the arm remains active but no
order is sent. Resolve the existing order/position or cancel the arm.
