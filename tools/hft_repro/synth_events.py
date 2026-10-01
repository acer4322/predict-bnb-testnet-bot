"""Build engine events from public top-5 books ONLY (no trade tape): depth snapshot + per-frame level diffs (clock = received_ms, as feed V1) + INFERRED trades.
Inferred trade rule (calibrated against the 3 fixtures that have real trades): when the best bid/ask level persists and its quantity falls by q between consecutive frames, emit a trade of alpha*q
(best ask decrease = BUY aggressor; best bid decrease = SELL aggressor); when the best level disappears and the opposite touch... is NOT treated as a trade (ambiguous with cancels).
Trade events are stamped at the frame time, depth events 1 microsecond later, so a trade consumes queue before the depth update caps it."""
import gzip, json
import numpy as np

def load_books(path):
    d = json.load(gzip.open(path)); return d['market'], [b for b in d['books'] if b.get('best_bid') is not None and b.get('best_ask') is not None]

def build(books, ex, alpha=1.0, trade_levels=1):
    out = []; prev = None; inferred = []
    bks = sorted(books, key=lambda b: (int(b['received_ms']), int(b['source_ms'])))
    for i, b in enumerate(bks):
        ts = int(b['received_ms']); bids = {float(p): float(q) for p, q in b['bids']}; asks = {float(p): float(q) for p, q in b['asks']}
        if prev is None:
            for p, q in bids.items(): out.append((ex.DEPTH_SNAPSHOT_EVENT | ex.BUY_EVENT, ts * 1_000_000, p, q))
            for p, q in asks.items(): out.append((ex.DEPTH_SNAPSHOT_EVENT | ex.SELL_EVENT, ts * 1_000_000, p, q))
        else:
            pb, pa = prev
            for flag, cur, old in ((ex.BUY_EVENT, bids, pb), (ex.SELL_EVENT, asks, pa)):
                for p in set(cur) | set(old):
                    if abs(cur.get(p, 0.) - old.get(p, 0.)) > 1e-9: out.append((ex.DEPTH_EVENT | flag, ts * 1_000_000 + 1000, p, cur.get(p, 0.)))
            # inferred trades at persisting touch levels
            for flag, cur, old, best_of in ((ex.SELL_EVENT, asks, pa, min), (ex.BUY_EVENT, bids, pb, max)):
                lv = sorted(old, reverse=(flag == ex.BUY_EVENT))[:trade_levels]
                for p in lv:
                    if p in cur and cur[p] < old[p] - 1e-9:
                        q = alpha * (old[p] - cur[p]); out.append((ex.TRADE_EVENT | flag, ts * 1_000_000, p, q)); inferred.append((ts, p, q, 'BUY' if flag == ex.SELL_EVENT else 'SELL'))
        prev = (bids, asks)
    arr = np.zeros(len(out), ex.event_dtype)
    for k, (ev, t_ns, p, q) in enumerate(out): arr[k]['ev'] = int(ev | ex.EXCH_EVENT | ex.LOCAL_EVENT); arr[k]['exch_ts'] = t_ns; arr[k]['local_ts'] = t_ns; arr[k]['px'] = p; arr[k]['qty'] = q
    arr.sort(order=['local_ts', 'exch_ts'], kind='stable'); return arr, inferred
