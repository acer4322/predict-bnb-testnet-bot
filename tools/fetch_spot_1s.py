"""Fetch BTCUSDT 1-second klines from data-api.binance.vision for the windows of our markets (window start - LOOKBACK .. window end), merged into intervals, written as gz json {sec: [close, quote_volume, trades, taker_buy_quote_volume]}.
usage: python tools/fetch_spot_1s.py window_starts.json out.json.gz [--lookback 1800]   (public data, no credentials)"""
import sys, json, gzip, time, urllib.request, concurrent.futures as cf
ws = json.load(open(sys.argv[1])); out = sys.argv[2]; LB = int(sys.argv[sys.argv.index('--lookback') + 1]) * 1000 if '--lookback' in sys.argv else 1_800_000
iv = sorted((w - LB, w + 300_000) for w in ws); merged = []
for a, b in iv:
    if merged and a <= merged[-1][1]: merged[-1][1] = max(merged[-1][1], b)
    else: merged.append([a, b])
jobs = [(s, min(s + 999_000, b)) for a, b in merged for s in range(a, b, 1_000_000)]
print('intervals', len(merged), 'calls', len(jobs))
def get(j):
    s, e = j; url = 'https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1s&limit=1000&startTime=%d&endTime=%d' % (s, e)
    for k in range(5):
        try: return json.loads(urllib.request.urlopen(url, timeout=20).read())
        except Exception: time.sleep(1 + k)
    return None
data = {}; bad = 0
with cf.ThreadPoolExecutor(8) as ex:
    for res in ex.map(get, jobs):
        if res is None: bad += 1; continue
        for r in res: data[int(r[0]) // 1000] = [float(r[4]), float(r[7]), int(r[8]), float(r[10])]
print('seconds', len(data), 'failed calls', bad)
json.dump(data, gzip.open(out, 'wt'))
