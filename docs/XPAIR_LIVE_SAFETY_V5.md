# XPAIR Live Safety v5

The XPAIR BTC/ETH executor remains non-atomic. Version 5 adds a durable safety
boundary around every real placement attempt without adding automatic cancel,
chase, unwind, or loss insurance.

## Normal monitoring

The service continuously reads BTC and ETH books and requests signed quotes in
the configured entry window. It does not place an order until the operator arms
one live attempt.

## Placement tracking lock

When both placement calls return exchange order IDs, the service writes a
single-row durable lock to the existing XPAIR SQLite database and enters:

```text
SUBMITTED_TRACKING
LIVE_ORDER_TRACKING
```

A background watcher checks both order IDs once per second. A new live arm is
blocked while this tracking lock exists. Restarting the API restores the lock
and resumes reconciliation.

The tracking lock clears automatically only when:

- both orders are fully filled; or
- both orders reach terminal no-fill states.

## Persistent incident lock

The lock becomes `INCIDENT` when any of the following occurs:

- one or both placement responses are rejected or ambiguous;
- only one leg has a fill after the one-second grace period;
- both legs have fills but filled-share mismatch exceeds 0.25%;
- one or both order IDs remain unavailable for 45 seconds;
- the market ends before the pair reaches a safe terminal state; or
- the durable tracking row is missing an order identifier.

An incident forces the visible phase to:

```text
XPAIR_INCIDENT_LOCKED
```

Background signed-quote monitoring may continue, but every new live arm is
rejected. The incident survives process and machine restarts because it is
stored in `data/xpair_btc_eth_canary.db`.

## Manual reconciliation

Open:

```text
http://localhost:3000/xpair-canary/safety
```

Check the BTC and ETH order IDs and statuses against Binance. The tracking lock
cannot be manually cleared. An incident can be cleared only after entering:

```text
I_RECONCILED_XPAIR_INCIDENT
```

Clearing the incident does not cancel an order, sell a position, or hedge an
exposure. It only permits a future arm.

## PAIR_ARB exchange minimum

The normal live engine now installs a PAIR_ARB-only runtime safety patch:

- each configured PAIR_ARB total stake must be at least 2.00 USDT;
- each calculated or dynamically sized leg must be at least 1.00 USDT;
- an equal-share requote is blocked before Binance if either revised leg would
  fall below 1.00 USDT; and
- profitable-depth sizing is rejected if either final leg is below 1.00 USDT.

The global minimum for non-pair strategies is unchanged.

## Startup

```powershell
git switch feature/regime-guard
git pull
python -m predict_bot.xpair_canary_dashboard_server_cedefi
```

The compatibility entry point now starts XPAIR autopilot v5.
