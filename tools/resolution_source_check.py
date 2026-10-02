"""Which price feed does Predict's label follow?  For the 519 true-labelled markets compare the label (UP/DOWN) with sign(price at window end - price at window start) from different public sources at 1-minute resolution:
Binance BTCUSDT 1 m (data-api.binance.vision), Coinbase BTC-USD 1 m (api.exchange.coinbase.com), Kraken XBTUSD 1 m.  Start price = OPEN of the minute starting at the window start, end price = OPEN of the minute starting at window end
(also the CLOSE of the minute ending at window end, which is the same value up to the last tick).  Ties (end == start) are reported separately.  usage: python tools/resolution_source_check.py PUBLIC_ROOT LABELS.json"""
import sys, json, pathlib, urllib.request, time, collections
import real_replay_stop as rr
H = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
def get(u):
    for k in range(5):
        try: return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=25).read())
        except Exception: time.sleep(1 + k)
lab = {int(r['market_id']): r['winner'] for r in rr.jl(sys.argv[2])['records']}; WS = {}
for p in pathlib.Path(sys.argv[1]).rglob('public_*.json.gz'):
    if 'parity' in str(p): continue
    m = int(p.name.split('_')[1].split('.')[0])
    if m in lab and m not in WS: WS[m] = int(rr.jl(p)['market']['window_start_ms']) // 1000
lo, hi = min(WS.values()) - 120, max(WS.values()) + 420
opens = {'binance': {}, 'coinbase': {}, 'kraken': {}}
t = lo - lo % 60
while t < hi:   # binance 1000 per call
    r = get('https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=1000&startTime=%d&endTime=%d' % (t * 1000, min(t + 60000, hi) * 1000))
    for x in r or []: opens['binance'][int(x[0]) // 1000] = float(x[1])
    t += 60000
t = lo - lo % 60
while t < hi:   # coinbase 300 per call; candle = [time, low, high, open, close, volume]
    r = get('https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60&start=%s&end=%s' % (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t)), time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(min(t + 299 * 60, hi)))))
    for x in r or []: opens['coinbase'][int(x[0])] = float(x[3])
    t += 300 * 60
since = lo - lo % 60
while since < hi:   # kraken: 720 per call; [time, open, high, low, close, vwap, volume, count]
    r = get('https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1&since=%d' % since); res = (r or {}).get('result', {}); rows = next((v for k, v in res.items() if k != 'last'), [])
    for x in rows: opens['kraken'][int(x[0])] = float(x[1])
    if not rows or int(rows[-1][0]) + 60 <= since: break
    since = int(rows[-1][0]) + 60
    time.sleep(.3)
print({k: len(v) for k, v in opens.items()})
for src, o in opens.items():
    c = collections.Counter()
    for m, w in WS.items():
        a, b = o.get(w), o.get(w + 300)
        if a is None or b is None: c['missing'] += 1; continue
        if a == b: c['tie'] += 1; continue
        c['agree' if ((b > a) == (lab[m] == 'UP')) else 'disagree'] += 1
    n = c['agree'] + c['disagree']; print('%-8s agree %d / %d = %.1f%% | ties %d | missing %d' % (src, c['agree'], n, 100 * c['agree'] / max(1, n), c['tie'], c['missing']))
