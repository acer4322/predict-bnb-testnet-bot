import lzma, json

p = r'data/execution_tape_v1/markets/1916869.json.xz'
t0 = 1788450022673
with lzma.open(p, 'rt', encoding='utf-8') as fh:
    d = json.load(fh)

bids = {}
asks = {}
out = []
for u in d['updates']:
    source_ms, received_ms, order_count, is_checkpoint, bid_map, ask_map, changes = u
    if is_checkpoint:
        bids = {float(k): float(v) for k, v in (bid_map or {}).items()}
        asks = {float(k): float(v) for k, v in (ask_map or {}).items()}
    else:
        for x in changes.get('bids', []):
            price, new = float(x[0]), float(x[2])
            if new <= 1e-12:
                bids.pop(price, None)
            else:
                bids[price] = new
        for x in changes.get('asks', []):
            price, new = float(x[0]), float(x[2])
            if new <= 1e-12:
                asks.pop(price, None)
            else:
                asks[price] = new
    if t0 - 3000 <= received_ms <= t0 + 30000:
        out.append({
            'receivedMs': received_ms,
            'sourceMs': source_ms,
            'bestBid': max(bids) if bids else None,
            'bestAsk': min(asks) if asks else None,
            'bid047': bids.get(0.47, 0.0),
            'bid046': bids.get(0.46, 0.0),
            'bidChanges': changes.get('bids', []),
            'askChanges': changes.get('asks', []),
        })

print(json.dumps({'count': len(out), 'rows': out}, indent=2))
