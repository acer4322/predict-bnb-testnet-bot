"""1 Hz series (best bid/ask of the UP book, signed trade quantity, trade volume) rebuilt from a Predict V1 event file; shared by flow_signal_check and simple_vs_engine."""
import json
import numpy as np
from hftbacktest import DEPTH_EVENT, DEPTH_SNAPSHOT_EVENT, TRADE_EVENT, BUY_EVENT, SELL_EVENT
def series(p):
    meta = json.load(open(p / 'META.json')); ws = int(meta['window_start_ms']) // 1000; ev = np.load(p / 'events.npz')['data']; order = np.argsort(ev['local_ts'], kind='stable'); ev = ev[order]
    bids, asks = {}, {}; bb = np.full(301, np.nan); ba = np.full(301, np.nan); sg = np.zeros(301); vol = np.zeros(301); sec_idx = 0
    ts = ev['local_ts'] // 1_000_000_000 - ws
    def snap(s):
        bb[s] = max((p_ for p_, q in bids.items() if q > 0), default=np.nan); ba[s] = min((p_ for p_, q in asks.items() if q > 0), default=np.nan)
    last = -1
    for i in range(len(ev)):
        t = int(ts[i]); e = int(ev['ev'][i]); px = float(ev['px'][i]); q = float(ev['qty'][i])
        while last < min(t, 300) - 1 and last < 300:
            last += 1
            if last >= 0: snap(last)
        if (e & TRADE_EVENT) == TRADE_EVENT:
            if 0 <= t <= 300: sg[t] += q if (e & BUY_EVENT) == BUY_EVENT else -q; vol[t] += q
        elif (e & DEPTH_SNAPSHOT_EVENT) == DEPTH_SNAPSHOT_EVENT or (e & DEPTH_EVENT) == DEPTH_EVENT:
            d = bids if (e & BUY_EVENT) == BUY_EVENT else asks
            if (e & DEPTH_SNAPSHOT_EVENT) == DEPTH_SNAPSHOT_EVENT and q == 0: pass
            d[px] = q
    while last < 300: last += 1; snap(last)
    return int(p.name), bb, ba, sg, vol
