"""Fetch Binance BTCUSDT aggTrades (ms timestamps) for each event-file market window [start-30 s, start+300 s] from data-api.binance.vision (public).  Output: one gz JSON {market_id: [[T_ms, price], ...]}.
usage: python tools/fetch_aggtrades.py OUT.json.gz EVENTS_BATCH_DIR... [--symbol BTCUSDT]"""
import sys, json, gzip, time, urllib.request, os
from pathlib import Path
args = [a for a in sys.argv[1:] if not a.startswith('--')]; sym = 'BTCUSDT'
if '--symbol' in sys.argv: sym = sys.argv[sys.argv.index('--symbol') + 1]; args = [a for a in args if a != sym]
out, dirs = args[0], args[1:]; res = json.load(gzip.open(out, 'rt')) if os.path.exists(out) else {}
def get(url):
    for k in range(5):
        try: return json.load(urllib.request.urlopen(url, timeout=30))
        except Exception as e: time.sleep(2 ** k)
    raise RuntimeError(url)
for d in dirs:
    base = Path(d) / 'markets' if (Path(d) / 'markets').is_dir() else Path(d)   # event batch dir or a public_markets root
    for p in sorted(base.iterdir()):
        if p.name in res: continue
        if (p / 'META.json').exists(): ws = int(json.load(open(p / 'META.json'))['window_start_ms'])
        elif (p / ('public_%s.json.gz' % p.name)).exists(): ws = int(json.load(gzip.open(p / ('public_%s.json.gz' % p.name), 'rt'))['market']['window_start_ms'])
        else: continue
        a, b = ws - 30_000, ws + 300_000; rows = []
        batch = get('https://data-api.binance.vision/api/v3/aggTrades?symbol=%s&startTime=%d&endTime=%d&limit=1000' % (sym, a, min(b, a + 3_599_000)))
        while batch:
            rows += [[x['T'], float(x['p'])] for x in batch if x['T'] <= b]
            if batch[-1]['T'] >= b or len(batch) < 1000: break
            batch = get('https://data-api.binance.vision/api/v3/aggTrades?symbol=%s&fromId=%d&limit=1000' % (sym, batch[-1]['a'] + 1))
        res[p.name] = rows
        if len(res) % 25 == 0: gzip.open(out, 'wt').write(json.dumps(res)); print('checkpoint', len(res), flush=True)
    gzip.open(out, 'wt').write(json.dumps(res)); print(d, 'markets so far', len(res))
