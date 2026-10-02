"""SHADOW logger for the spot-latency taker (NO ORDERS, read-only).  See docs/research_specs/SPOT_LATENCY_SHADOW_SPEC_20261003_ZH.md.

Records, with host receive times, (a) Predict.fun native order-book frames (predictOrderbook/<marketId>, top 10 levels, updateTimestampMs) for the
current 5-minute market of ASSET, and (b) Binance spot aggTrades (E event time, T trade time, price).  Prints a live count of would-be signals.
Analysis is offline: tools/spot_latency_shadow_report.py.

usage (Windows / Linux):
  set PREDICT_FUN_API_KEY=...            (read-only key, same as predict_fun_observer)
  python tools/spot_latency_shadow.py --asset BTC --out data/shadow_spot_latency --hours 6
Requires: httpx, websocket-client (both already used by src/predict_bot/predict_fun_observer.py).
This script never imports or calls any order / signing / wallet code."""
import argparse, gzip, json, math, os, sys, threading, time
from collections import deque
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / 'src'))
import httpx, websocket
from predict_bot.predict_fun_observer import (API_BASE, WS_URL, API_KEY_ENV, current_bucket, expected_slug, normalize_predict_category_payload,
                                              select_predict_market, ASSETS)
SYMBOL = {'BTC': 'btcusdt', 'ETH': 'ethusdt', 'BNB': 'bnbusdt'}
def now_ms(): return time.time() * 1000.

class Writer:
    def __init__(self, out: Path, asset: str):
        self.dir = out; self.dir.mkdir(parents=True, exist_ok=True); self.asset = asset; self.lock = threading.Lock(); self.hour = None; self.fh = None; self.n = 0
    def write(self, rec):
        h = time.strftime('%Y%m%d_%H', time.gmtime())
        with self.lock:
            if h != self.hour:
                if self.fh: self.fh.close()
                self.hour = h; self.fh = gzip.open(self.dir / f'shadow_{self.asset}_{h}.ndjson.gz', 'at')
            self.fh.write(json.dumps(rec, separators=(',', ':')) + '\n'); self.n += 1
    def close(self):
        with self.lock:
            if self.fh: self.fh.close()

class Shadow:
    def __init__(self, asset, out, hours):
        self.asset = asset; self.key = os.environ.get(API_KEY_ENV, '').strip()
        if not self.key: sys.exit(f'{API_KEY_ENV} is not set (read-only Predict API key needed for the order-book websocket)')
        self.w = Writer(Path(out), asset); self.stop_at = time.time() + hours * 3600; self.stop = threading.Event()
        self.http = httpx.Client(timeout=httpx.Timeout(3.0, connect=1.5), headers={'accept': 'application/json', 'x-api-key': self.key})
        self.market = None; self.pws = None; self.spot = deque(maxlen=4000); self.sig = {1: 0, 2: 0, 3: 0}; self.last_sig = {1: 0., 2: 0., 3: 0.}
        self.cnt = {'pb': 0, 'st': 0}
    # ---------- market discovery ----------
    def discover(self, bucket):
        slug = expected_slug(self.asset, bucket)
        try:
            r = self.http.get(f'{API_BASE}/v1/categories/{slug}')
            if r.status_code == 200:
                c = select_predict_market(normalize_predict_category_payload(r.json(), slug=slug), asset=self.asset, bucket=bucket)
                if c: return c
        except Exception as e: print('category discovery error', e, flush=True)
        try:
            r = self.http.get(f'{API_BASE}/v1/search', params={'query': ASSETS[self.asset]['query'], 'includeResolved': 'false', 'limit': 20}); r.raise_for_status()
            return select_predict_market(r.json(), asset=self.asset, bucket=bucket)
        except Exception as e: print('search discovery error', e, flush=True); return None
    def market_loop(self):
        cur = None
        while not self.stop.is_set():
            b = current_bucket()
            if b != cur:
                m = self.discover(b)
                if m:
                    cur = b; old = self.market; self.market = m
                    self.w.write({'k': 'market', 'recv': now_ms(), 'id': m['id'], 'ws': m['windowStartMs'], 'we': m['windowEndMs'], 'dp': m['decimalPrecision'], 'slug': m.get('categorySlug')})
                    print(time.strftime('%H:%M:%S'), 'market', m['id'], m.get('categorySlug'), flush=True)
                    try:
                        if self.pws and old: self.pws.send(json.dumps({'method': 'unsubscribe', 'requestId': 2, 'params': [f"predictOrderbook/{old['id']}"]}))
                        if self.pws: self.pws.send(json.dumps({'method': 'subscribe', 'requestId': 3, 'params': [f"predictOrderbook/{m['id']}"]}))
                    except Exception as e: print('resubscribe error', e, flush=True)
            if time.time() > self.stop_at: self.stop.set()
            self.stop.wait(1.0)
    # ---------- Predict websocket ----------
    def p_open(self, ws):
        if self.market: ws.send(json.dumps({'method': 'subscribe', 'requestId': 1, 'params': [f"predictOrderbook/{self.market['id']}"]}))
    def p_msg(self, ws, raw):
        t = now_ms()
        try: p = json.loads(raw)
        except Exception: return
        if not isinstance(p, dict) or p.get('type') != 'M': return
        topic = str(p.get('topic') or ''); d = p.get('data')
        if topic == 'heartbeat':
            try: ws.send(json.dumps({'method': 'heartbeat', 'data': d}))
            except Exception: pass
            return
        if not topic.startswith('predictOrderbook/'): return
        d = d.get('data') if isinstance(d, dict) and isinstance(d.get('data'), dict) else d
        if not isinstance(d, dict): return
        lv = lambda x: [[float(a[0]), float(a[1])] for a in (x or [])[:10] if isinstance(a, (list, tuple)) and len(a) >= 2]
        self.w.write({'k': 'pb', 'recv': t, 'src': d.get('updateTimestampMs'), 'id': int(topic.split('/')[1]), 'asks': lv(d.get('asks')), 'bids': lv(d.get('bids'))}); self.cnt['pb'] += 1
    def p_loop(self):
        while not self.stop.is_set():
            self.pws = websocket.WebSocketApp(WS_URL, header=[f'x-api-key: {self.key}'], on_open=self.p_open, on_message=self.p_msg,
                                              on_error=lambda ws, e: print('predict ws error', e, flush=True))
            self.pws.run_forever(ping_interval=20)
            if self.stop.wait(2): return
    # ---------- Binance websocket ----------
    def s_msg(self, ws, raw):
        t = now_ms()
        try: p = json.loads(raw)
        except Exception: return
        if p.get('e') != 'aggTrade': return
        px = float(p['p']); self.w.write({'k': 'st', 'recv': t, 'E': p['E'], 'T': p['T'], 'p': px}); self.cnt['st'] += 1
        self.spot.append((t, px))
        while self.spot and self.spot[0][0] < t - 600: self.spot.popleft()
        if self.spot:
            r = math.log(px / self.spot[0][1]) * 1e4
            for x in (1, 2, 3):
                if abs(r) >= x and t - self.last_sig[x] >= 2000: self.sig[x] += 1; self.last_sig[x] = t
    def s_loop(self):
        url = f'wss://stream.binance.com:9443/ws/{SYMBOL[self.asset]}@aggTrade'
        while not self.stop.is_set():
            ws = websocket.WebSocketApp(url, on_message=self.s_msg, on_error=lambda ws, e: print('binance ws error', e, flush=True)); self.sws = ws
            ws.run_forever(ping_interval=20)
            if self.stop.wait(2): return
    def run(self):
        ths = [threading.Thread(target=f, daemon=True) for f in (self.market_loop, self.p_loop, self.s_loop)]
        [t.start() for t in ths]
        try:
            while not self.stop.is_set():
                time.sleep(30); print(time.strftime('%H:%M:%S'), 'frames predict %d spot %d | would-be signals (host view, 500 ms) >=1bp %d >=2bp %d >=3bp %d' % (self.cnt['pb'], self.cnt['st'], *self.sig.values()), flush=True)
        except KeyboardInterrupt: self.stop.set()
        for ws in (self.pws, getattr(self, 'sws', None)):
            try: ws and ws.close()
            except Exception: pass
        self.w.close(); print('done, records', self.w.n)

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--asset', default='BTC', choices=list(SYMBOL)); ap.add_argument('--out', default=str(ROOT / 'data' / 'shadow_spot_latency')); ap.add_argument('--hours', type=float, default=6.)
    a = ap.parse_args(); Shadow(a.asset, a.out, a.hours).run()
