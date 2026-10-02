# Predict.fun direct one-shot actual latency

The account owner submitted one ETH5M BUY UP MARKET order, with signed maker
commitment at most 1 USDT. The create-order call returned HTTP 201 and the
canonical hash/order ID ACK. Native wallet events matched that identity:
orderAccepted, orderTransactionSubmitted and orderTransactionSuccess. A separate
read-only verification then confirmed the exact same market, named UP token,
BUY side, MARKET strategy, status FILLED and a full reported fill. The agent sent
zero financial orders and did not repeat the owner's request.

The original CLI outcome API_REJECTED was misleading: two later GET order
queries received HTTP 429. The order POST succeeded. SUMMARY.json records both
the untouched original outcome and corrected PREDICT_FILLED_CONFIRMED outcome.
The raw original run is preserved privately; no guard was reset or released.

Observed timing from one host, in milliseconds:

| Measurement | ms |
| --- | ---: |
| Binance aggTrade E to host receipt, median / p95 | 27.892 / 31.779 |
| Binance depth E to host receipt, median / p95 | 27.077 / 30.912 |
| Native book receipt to order API call entry | 84.296 |
| Native book receipt to local HTTP body write completion | 217.334 |
| Order API call entry to Predict ACK observation | 258.871 |
| Native book receipt to Predict ACK observation | 343.167 |
| Native book receipt to orderAccepted notification receipt | 503.962 |
| Native book receipt to settlement-submitted notification receipt | 700.326 |
| Native book receipt to settlement-success notification receipt | 3263.395 |

TCP connect and TLS handshake took 61.764 and 70.312 ms inside the order request.
Thus API call entry is earlier than actual local HTTP write completion. HTTP
write completion is a library marker, not packet arrival at Predict. Notification
receipt and fresh FILLED verification are observation times, not internal
acceptance or matching clocks. Those internal clocks remain UNKNOWN.

Binance ages use the original before/after public server-time calibration.
Shortest-RTT midpoint model uncertainties were +/-28.164 and +/-26.559 ms;
offset drift was -4.738 ms. They include publication delay and are not a pure
network one-way measurement or a proven network-asymmetry bound. Local monotonic
durations do not have this cross-clock calibration uncertainty.

216 native book frames and 3123 Binance frames were received, with zero WS
errors, over 48.250 seconds. The public timeline has 3578 contiguous rows and
relative times only. Original files, account addresses, identifiers, exact fill
quantities, signatures, credentials, epoch clocks and private host paths stay
local. Anonymized canonical outcome checks are included because they establish
the requested test result; unrelated account/approval/balance state is omitted.
The run did not record a source-code hash; the pre-test published preparation
commit is a reference, not proof of the executing source bytes.

The originating workspace is already repaired. FIX.patch applies to the original
published preparation source and changes only the standalone direct collector,
its tests and runbook. It preserves successful ACK/fill/settlement evidence after
a read error, replaces rapid polling with native-event waiting and at most one
REST snapshot, and writes reconciliation into a new child directory so the
original experiment stays immutable. The same consumed submit guard remains in
effect; do not submit this one-shot again.

```powershell
git apply --check research_pack/live_execution_latency/predict_direct_one_shot_actual_20261003_v1/FIX.patch
git apply research_pack/live_execution_latency/predict_direct_one_shot_actual_20261003_v1/FIX.patch
python -m pytest tests/test_predict_direct_one_shot_latency_v1.py -q
```

21 offline tests passed both in the workspace and after applying the frozen fix
to a separate copy of the original published source. No extra live trade was
used for validation. SHA256SUMS.json covers every other pack file.

Official event meanings:
[native wallet topics](https://dev.predict.fun/subscription-topics-1915507m0),
[exact own-order query](https://dev.predict.fun/get-order-by-hash-25326901e0).
