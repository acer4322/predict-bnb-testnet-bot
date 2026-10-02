# Predict.fun direct one-shot latency preparation

Prepared for the account owner's ETH5M BUY UP test, with a maximum 1 USDT maker
commitment, MARKET strategy, isFillOrKill=true and 1 bps slippage. Trade submission
uses Predict.fun's own REST API and official Python SDK. Public Binance ETHUSDT
streams provide the event-to-host timing sample.

INSTALL.patch adds five isolated files to a clean research checkout. It is also
already installed in the originating workspace. Existing Binance test artifacts,
guard, collectors, live strategies and armed/enabled states are preserved.

```powershell
git apply --check research_pack/live_execution_latency/predict_direct_one_shot_20261003_v1/INSTALL.patch
git apply research_pack/live_execution_latency/predict_direct_one_shot_20261003_v1/INSTALL.patch
python tools/run_predict_direct_one_shot_latency_v1.py --mode prepare
```

The account owner runs the actual financial request, near the beginning of a
five-minute window:

```powershell
python tools/run_predict_direct_one_shot_latency_v1.py --mode submit --confirm-buy-up-1-usdt
```

See RUNBOOK.md for measurements, prerequisites and exact-hash read-only recovery.
There is no automatic retry or guard reset. The direct guard is independent of
the previously completed Binance test. Failed or ambiguous order attempts keep
it consumed. Existing native USDT balance and approvals are checked without
transfers or approval transactions. The SDK may round spend slightly below 1 USDT.

READ_ONLY_PROBE.json proves authentication, existing approvals/balance, both
native subscriptions, received public data and local order signing/validation.
It contains zero submitted orders. Venue minimum order size and real order
acceptance are untested. The read-only probe is not a live trade result.

Timing includes native book receipt, fixed decision, SDK amount/build/sign,
durable guard, HTTP write/response, canonical ACK and wallet/order observations.
Notification receipt is distinct from internal acceptance or actual fill time.
Exact internal clocks remain UNKNOWN. Binance E age includes publication delay
and clock-model uncertainty. Predict updateTimestampMs is not a proven send time.

Official route and request-field definitions were checked against
[Create order](https://dev.predict.fun/create-an-order-32534694e0),
[CreateOrderData](https://dev.predict.fun/createorderdata-14037465d0),
[wallet/book topics](https://dev.predict.fun/subscription-topics-1915507m0),
[SDK/slippage guide](https://dev.predict.fun/how-to-create-or-cancel-orders-679306m0),
and [GET own order by hash](https://dev.predict.fun/get-order-by-hash-25326901e0).
The official schema declares isFillOrKill as a boolean; this package requests
true. Preparation does not test the matching engine's FOK behavior. The official
self-trade enum includes CANCEL_TAKER; that is requested to avoid cancelling an
existing maker order. No raw schema copy, credentials, JWTs, signatures, wallet
addresses, order hashes/IDs, epoch timestamps or private host paths are exported.

VALIDATION.json records 19 focused offline tests and a second 19-test run after
applying the frozen patch in a separate installation. SOURCE_SHA256.json uses
LF-normalized source bytes. SHA256SUMS.json covers every other pack file.
