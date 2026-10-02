import lzma, json, math

TAPE = r'data/execution_tape_v1/markets/1916869.json.xz'
T0 = 1788450022673
QTY = 2.1649963710093214
LEGAL_P = 1.0 / QTY

with lzma.open(TAPE, 'rt', encoding='utf-8') as fh:
    d = json.load(fh)

bids = {}
asks = {}
rows = []
for u in d['updates']:
    source_ms, received_ms, order_count, is_checkpoint, bid_map, ask_map, changes = u
    if is_checkpoint:
        bids = {float(k): float(v) for k, v in (bid_map or {}).items()}
        asks = {float(k): float(v) for k, v in (ask_map or {}).items()}
    else:
        for x in changes.get('bids', []):
            p, new = float(x[0]), float(x[2])
            if new <= 1e-12: bids.pop(p, None)
            else: bids[p] = new
        for x in changes.get('asks', []):
            p, new = float(x[0]), float(x[2])
            if new <= 1e-12: asks.pop(p, None)
            else: asks[p] = new
    if received_ms >= T0 and asks:
        up_best_ask = min(asks)
        implied_down_best_bid = round(1.0 - up_best_ask, 10)
        rows.append({
            'receivedMs': received_ms,
            'sourceMs': source_ms,
            'upBestAsk': up_best_ask,
            'impliedDownBestBid': implied_down_best_bid,
            'legalAtBest': implied_down_best_bid + 1e-12 >= LEGAL_P,
            'askChanges': changes.get('asks', []),
            'orderCount': order_count,
        })

first_legal = next((r for r in rows if r['legalAtBest']), None)
# First cent tick that makes QTY legal.
legal_tick = math.ceil(LEGAL_P * 100 - 1e-12) / 100.0
up_equiv_ask = round(1.0 - legal_tick, 2)
# Track strictly-past visible queue at equivalent UP ask and subsequent reductions/removal.
queue_events = []
prev = None
for r in rows:
    for x in r['askChanges']:
        if abs(float(x[0]) - up_equiv_ask) < 1e-9:
            old, new = float(x[1]), float(x[2])
            queue_events.append({'receivedMs': r['receivedMs'], 'old': old, 'new': new, 'delta': new-old, 'orderCount': r['orderCount']})

print(json.dumps({
    'authorizedQty': QTY,
    'continuousLegalPrice': LEGAL_P,
    'firstLegalCentTick': legal_tick,
    'equivalentUpAskForDownPassiveBid': up_equiv_ask,
    'firstImpliedDownBestBidLegal': first_legal,
    'delayMsFromEpoch': None if first_legal is None else first_legal['receivedMs'] - T0,
    'queueEventsAtEquivalentUpAsk': queue_events[:50],
    'queueEventCount': len(queue_events),
    'matchesCount': len(d.get('matches') or []),
    'matchesSample': (d.get('matches') or [])[:10],
}, indent=2))
