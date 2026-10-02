# Predict.fun direct one-shot ETH5M latency

The account owner runs one BUY UP MARKET request with a maker commitment capped
at 1 USDT and `isFillOrKill=true`. It goes directly to
`https://api.predict.fun/v1/orders`, using the official Python SDK and the
existing Predict.fun account. Public Binance ETHUSDT streams are collected for
timing only. No Binance Prediction trading API is called.

The tool is isolated from strategies, services, schedulers and live state.
Default mode observes only. Automated validation uses `prepare`, which performs
authentication, checks existing approvals and USDT balance, subscribes to native
public books and own wallet events, then builds/signs an order locally. Preparation
does not submit an order or prove that the venue accepts a 1 USDT order.

Run from the repository root with its existing Python environment:

```powershell
python tools/run_predict_direct_one_shot_latency_v1.py --mode prepare
```

The account owner executes the financial request:

```powershell
python tools/run_predict_direct_one_shot_latency_v1.py --mode submit --confirm-buy-up-1-usdt
```

The direct test has its own persistent `once.sqlite` under
`data/research/predict_direct_one_shot_latency_v1/`. It does not reuse or reset
the completed Binance test's guard. Submit consumes the guard durably before the
one order request. A failed or ambiguous attempt keeps it consumed, and there is
no reset option or automatic retry. HTTP redirects, transport retries and auth
replay of order POSTs are disabled. The SDK's five-significant-digit rounding may
spend slightly less than 1 USDT; slippage deflates minimum shares out rather than
inflating the USDT commitment. Insufficient depth, balance or existing approvals
blocks the request. No approval, deposit, withdrawal, transfer, redemption or
cancellation is performed. Self-trade prevention requests `CANCEL_TAKER`.

If the current market has less than warmup + 45 seconds remaining, it exits with
`MARKET_WINDOW_TOO_SHORT_RUN_MANUALLY_IN_NEXT_WINDOW` and zero order requests.
Run again near the start of the next five-minute window only when the reported
place count is zero and no submit attempt consumed the guard. An attempted POST
is never repeated, including after timeouts, 401 or 5xx responses.

Every run saves private `SUMMARY.json`, `timeline.json`, `HOST_PRIVATE.json` and
`PRIVATE_REFERENCE.json`. A positive canonical order read additionally saves
`PRIVATE_ORDER_OBSERVATION.json`. Preserve these locally; they may contain wallet,
order, token and source-clock identifiers. Never publish raw files or signatures.

Read-only recovery after an attempted order uses its exact signed hash, which is
durable before POST even if no ACK arrived:

```powershell
python tools/run_predict_direct_one_shot_latency_v1.py --mode reconcile --run-dir "<run_dir>"
```

The timeline captures receipt before JSON parsing, fixed decision, amount
calculation, SDK build/signing, guard persistence, HTTP call, body write, response
headers/JSON, canonical Predict ACK, native `orderAccepted`, settlement submitted,
transaction success/failure, and exact-hash REST order observations. Wallet events
must match the signed hash or returned order ID; title, amount, direction and
nearby timestamps are never used to guess identity. REST evidence also verifies
market ID, named UP token and BUY side. Positive `amountFilled` is observed fill
evidence. UNKNOWN and pending results remain unresolved.

All on-host durations use `monotonic_ns`; cross-boot reconciliation leaves
cross-domain durations unknown. Binance event-to-receipt ages use before/after
five-sample `/api/v3/time` midpoint calibration, taking the shortest RTT with
RTT/2 + 1 ms model uncertainty. This is not a proven bound on network asymmetry.
Binance `E` age includes publication delay. Predict's `updateTimestampMs` has no
documented exact network-send cutpoint. The `orderAccepted` notification means
placed in the book; MARKET orders may instead go directly to match/settlement
events. Notification receipt, API ACK and actual matching/acceptance clocks are
distinct. Exact internal Predict acceptance and fill timestamps stay UNKNOWN.

Validation uses fake HTTP transports and SDK public arithmetic only:

```powershell
python -m pytest tests/test_predict_direct_one_shot_latency_v1.py -q
```

Official references:

- [Create order](https://dev.predict.fun/create-an-order-32534694e0)
- [CreateOrderData schema](https://dev.predict.fun/createorderdata-14037465d0)
- [SDK order/slippage guide](https://dev.predict.fun/how-to-create-or-cancel-orders-679306m0)
- [Native WS topics and wallet events](https://dev.predict.fun/subscription-topics-1915507m0)
- [Get own order by hash](https://dev.predict.fun/get-order-by-hash-25326901e0)
- [Official Python SDK](https://github.com/PredictDotFun/sdk-python)
