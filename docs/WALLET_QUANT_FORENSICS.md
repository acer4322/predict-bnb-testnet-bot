# Wallet Quant Forensics

Read-only collector for reverse-engineering repeatable Predict.fun wallet execution patterns without touching live-trading code.

## Target example

```text
0x9ddbdc8bdf41ec79cd4f5c4dec14050f4b591a18
```

## What it collects

The tool calls Predict.fun mainnet read-only endpoints:

```text
GET /v1/orders/matches?signerAddress=...
GET /v1/positions/{address}
```

It writes:

```text
data/wallet_forensics/<address>/matches_raw.json
data/wallet_forensics/<address>/positions_raw.json
data/wallet_forensics/<address>/fills.csv
data/wallet_forensics/<address>/summary.json
data/wallet_forensics/<address>/report.md
```

`fills.csv` normalizes each wallet-attributed fill leg into:

- market ID/title
- execution timestamp
- maker/taker role
- BID/ASK and inferred BUY/SELL action
- outcome
- executed price
- fill size when the event supports a wallet-specific size inference
- notional value
- resolution fields returned by Predict
- transaction/order hashes
- `seconds_left` when an exact/local market end can be established
- a `timing_basis` field explaining how T-minus was obtained
- a `size_confidence` field explaining how reliable the sizing value is

## API key

Predict.fun mainnet requires an API key. Keep it local; never commit it.

PowerShell:

```powershell
$env:PREDICT_API_KEY="YOUR_PREDICT_API_KEY"
```

Run the target wallet:

```powershell
python .\tools\wallet_quant_forensics.py 0x9ddbdc8bdf41ec79cd4f5c4dec14050f4b591a18
```

Then clear the key when finished:

```powershell
Remove-Item Env:PREDICT_API_KEY
```

## Exact 5-minute T-minus enrichment

By default the tool looks for:

```text
data/binance_production_observations.csv
```

The existing BTC 5M collector already writes `timestamp`, `market_id`, and `seconds_left`. For any matching market ID, the forensics tool infers the market end using:

```text
market_end ~= observation_timestamp + seconds_left
```

The median inferred end across observations is used. Those rows are labeled:

```text
timing_basis=local_observation_inferred_end
```

If Predict later exposes an explicit end field in the match market object, that takes priority and is labeled:

```text
timing_basis=api_market_end
```

Do not silently assume a UTC-aligned five-minute boundary. If a rough estimate is useful for unmatched historical markets, opt in explicitly:

```powershell
python .\tools\wallet_quant_forensics.py 0x9ddbdc8bdf41ec79cd4f5c4dec14050f4b591a18 --estimate-aligned-5m
```

Estimated rows are labeled:

```text
timing_basis=assumed_utc_aligned_5m
```

and must not be treated as exact timing evidence.

## Multi-maker sizing protection

A Predict match can contain multiple makers. The match-level `amountFilled` is safe for the taker and for a single-maker match, but it is not automatically the wallet-specific fill size when several makers participated.

The collector therefore uses:

```text
exact_taker_match
exact_single_maker_match
aggregate_only_multi_maker
```

For `aggregate_only_multi_maker`, `filled_shares` and `notional_usdt` are intentionally left blank while `match_filled_shares` remains available for reference. This prevents false position-sizing fingerprints.

## First-pass quant fingerprint

`summary.json` and `report.md` calculate conservative descriptive signals including:

- first BUY price distribution and 0.05-price-bin concentration
- first BUY T-minus distribution and 10-second-bin concentration
- maker/taker share
- repeated same-outcome BUYs in a market
- markets containing SELL fills
- markets buying both outcomes
- exact-notional mode concentration when sizing is reliable

Candidate labels are only emitted after minimum sample thresholds. They are hypotheses for later market-state alignment, not claims about the trader's private model.

## Offline analysis

If match JSON has already been downloaded, no API key is needed:

```powershell
python .\tools\wallet_quant_forensics.py 0x9ddbdc8bdf41ec79cd4f5c4dec14050f4b591a18 `
  --offline-matches .\data\matches.json `
  --offline-positions .\data\positions.json
```

The accepted JSON shape is either a bare list or an object containing a `data` list.

## Tests

```powershell
python -m pytest -q tests/test_wallet_quant_forensics.py
```

The tests cover decimal/wei normalization, maker/taker extraction, multi-maker sizing protection, local T-minus inference, and repeatable fingerprint detection.

## Next research step

After enough fills have been collected, join `fills.csv` to BTC 5M observation/microstructure data around each execution timestamp (for example -5s to +5s). The useful comparisons are:

- spot distance from strike
- spot/futures return over 250 ms, 500 ms, 1 s, 3 s
- Prediction mid/microprice change
- OFI and cumulative OFI
- spread/depth
- wall pull/flip events

That second-stage event study is where we can distinguish fixed-time/fixed-price entry rules from momentum, mean reversion, prediction-lag arbitrage, or market-making inventory control.
