"""Fetch Polymarket BTC 5-minute 'Up or Down' trades (public data-api) for the window starts of our markets: slug btc-updown-5m-<window_start_unix>; gamma -> conditionId; data-api trades paginated
(limit 500, offset steps).  Output gz json {window_start: {conditionId, resolved_outcomes, trades: [[ts, up_price, size, side, outcome]]}}.   usage: python tools/fetch_polymarket_trades.py window_starts.json out.json.gz [--max-offset 20000] [--limit-markets N]"""
import sys, json, gzip, time, urllib.request, concurrent.futures as cf
H = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
def g(u):
    for k in range(5):
        try: return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=20).read())
        except Exception as e:
            if '404' in str(e): return None
            time.sleep(1 + k)
    return 'ERR'
def one(ws):
    ev = g('https://gamma-api.polymarket.com/events?slug=btc-updown-5m-%d' % ws)
    if not ev or ev == 'ERR': return ws, None
    m = ev[0]['markets'][0]; cid = m['conditionId']; rows = []; off = 0
    while off <= MAXOFF:
        t = g('https://data-api.polymarket.com/trades?market=%s&limit=500&offset=%d' % (cid, off))
        if t == 'ERR' or not isinstance(t, list): break
        rows += [[int(x['timestamp']), float(x['price']) if x['outcome'] == 'Up' else 1 - float(x['price']), float(x['size']), x['side'], x['outcome']] for x in t]
        if len(t) < 500: break
        off += 500
    return ws, dict(conditionId=cid, outcomePrices=m.get('outcomePrices'), closed=m.get('closed'), n=len(rows), trades=rows)
if __name__ == '__main__':
    ws = json.load(open(sys.argv[1])); out = sys.argv[2]; MAXOFF = int(sys.argv[sys.argv.index('--max-offset') + 1]) if '--max-offset' in sys.argv else 20000
    if '--limit-markets' in sys.argv: n = int(sys.argv[sys.argv.index('--limit-markets') + 1]); step = max(1, len(ws) // n); ws = ws[::step][:n]
    res = {}
    with cf.ThreadPoolExecutor(6) as ex:
        for w, r in ex.map(one, ws): res[w] = r
    ok = {w: r for w, r in res.items() if r}; print('requested', len(ws), 'got', len(ok), 'trades total', sum(r['n'] for r in ok.values()), 'median/market', sorted(r['n'] for r in ok.values())[len(ok) // 2] if ok else 0)
    json.dump(ok, gzip.open(out, 'wt'))
else:
    MAXOFF = 20000
